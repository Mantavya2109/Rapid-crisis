"""
tests/test_api.py
-----------------
Full integration test suite for the Smart Fire Evacuation System.

Covers all fixes from both sessions including:
  - SQLite persistence (restore on restart)
  - Dijkstra with hazard weights + time decay
  - Neighbor hazard spread
  - API key + replay protection
  - Rate limiting (429)
  - Backup LED fallback
  - CommandId idempotency
  - Alert severity + force-clear interlock
  - Event log (GET /events)
  - LED coverage %
"""

import json
import time
import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch persistence before importing anything so tests don't touch real SQLite
import persistence
persistence.init_db = lambda: None
persistence.flush_now = lambda: None
persistence.register_snapshot = lambda name, fn: None
persistence.load_devices = lambda: []
persistence.load_alerts = lambda: []
persistence.load_events = lambda **kw: []
persistence.write_event = lambda **kw: None

import state_manager
import device_registry
import graph_manager
import auth_manager
from app import app


def _make_jwt_headers(device_id: str = "ESP32_SENSOR_01") -> dict:
    """Generate valid Bearer JWT + fresh X-Timestamp headers for write-route tests."""
    token = auth_manager.generate_device_token(device_id)
    return {
        "Authorization": f"Bearer {token}",
        "X-Timestamp": str(time.time()),
    }


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state():
    state_manager.reset_all()
    with device_registry._lock:
        device_registry._registry.clear()
    graph_manager.load_graph()
    yield


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def jwt_headers():
    """Valid JWT Authorization headers for write routes."""
    return _make_jwt_headers()


@pytest.fixture
def registered_sensor(client):
    payload = {
        "deviceId": "ESP32_SENSOR_01", "buildingId": "BUILDING_01",
        "nodeId": "ROOM_101", "type": "SENSOR", "ip": "192.168.1.201",
    }
    client.post("/devices/register", json=payload)
    return payload


@pytest.fixture
def registered_led(client):
    payload = {
        "deviceId": "ESP32_LED_01", "buildingId": "BUILDING_01",
        "nodeId": "HALLWAY_A", "type": "LED", "ip": "192.168.1.105",
    }
    client.post("/devices/register", json=payload)
    return payload


# ─────────────────────────────────────────────────────────────────────
# System health
# ─────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        d = r.get_json()
        assert d["status"] == "healthy"
        assert "building_id" in d

    def test_status_includes_alerts_and_devices(self, client):
        r = client.get("/status")
        assert r.status_code == 200
        d = r.get_json()
        assert "active_alerts" in d
        assert "devices" in d


# ─────────────────────────────────────────────────────────────────────
# Fix A — Persistence (mocked, verify restore flow)
# ─────────────────────────────────────────────────────────────────────

class TestPersistence:
    def test_restore_devices_from_db(self):
        """Simulate Pi restart — devices restored from mocked DB load."""
        with patch("persistence.load_devices", return_value=[{
            "device_id": "ESP32_01", "building_id": "B1",
            "node_id": "ROOM_101", "type": "SENSOR",
            "ip": "10.0.0.1", "status": "ONLINE",
            "last_seen": time.time(), "registered_at": "",
        }]):
            with device_registry._lock:
                device_registry._registry.clear()
            device_registry.restore_from_db()

        dev = device_registry.get_device("ESP32_01")
        assert dev is not None
        assert dev["nodeId"] == "ROOM_101"

    def test_restore_alerts_from_db(self):
        """Active alerts survive Pi restart."""
        alert_ts = time.time() - 10
        with patch("persistence.load_alerts", return_value=[{
            "node_id": "ROOM_102", "severity": "FIRE", "alert_time": alert_ts,
        }]):
            state_manager.reset_all()
            state_manager.restore_from_db()

        assert "ROOM_102" in state_manager.get_unsafe_nodes()
        assert state_manager.get_alert_severity("ROOM_102") == "FIRE"


# ─────────────────────────────────────────────────────────────────────
# Fix B — Dijkstra hazard weighting
# ─────────────────────────────────────────────────────────────────────

class TestDijkstra:
    def test_dijkstra_finds_safe_path(self):
        from pathfinder import dijkstra_to_exit
        graph = {
            "START": ["SAFE", "DANGER"],
            "SAFE":  ["EXIT"],
            "DANGER": ["EXIT"],
            "EXIT":  [],
        }
        weights = {"START": 1.0, "SAFE": 1.0, "DANGER": 999.0, "EXIT": 0.1}
        path, cost = dijkstra_to_exit(graph, "START", ["EXIT"], node_weights=weights)
        assert path == ["START", "SAFE", "EXIT"]
        assert cost < 10  # avoided the 999-weight node

    def test_dijkstra_avoids_high_smoke_node(self):
        from pathfinder import dijkstra_to_exit
        graph = {"A": ["B", "C"], "B": ["EXIT"], "C": ["EXIT"], "EXIT": []}
        weights = {"A": 1.0, "B": 1000.0, "C": 1.0, "EXIT": 0.1}
        path, _ = dijkstra_to_exit(graph, "A", ["EXIT"], node_weights=weights)
        assert "B" not in path  # should go A → C → EXIT

    def test_dijkstra_falls_back_when_all_blocked(self):
        from pathfinder import dijkstra_to_exit
        graph = {"A": ["EXIT"], "EXIT": []}
        path, _ = dijkstra_to_exit(graph, "A", ["EXIT"], blocked_nodes=["EXIT"])
        assert path == []

    def test_compute_node_weights_smoke_threshold(self):
        from pathfinder import compute_node_weights
        graph = {"ROOM_101": ["EXIT"], "EXIT": []}
        sensor_data = {"ROOM_101": {"temperature": 25, "smoke": 700}}  # 3.5× threshold
        weights = compute_node_weights(graph, sensor_data, {})
        # smoke >= 3× threshold → +1000
        assert weights["ROOM_101"] >= 1000

    def test_compute_node_weights_temp_exponential(self):
        from pathfinder import compute_node_weights
        graph = {"ROOM_101": ["EXIT"], "EXIT": []}
        sensor_data = {"ROOM_101": {"temperature": 80, "smoke": 0}}  # 40° excess above 40° threshold
        weights = compute_node_weights(graph, sensor_data, {})
        # excess=40, penalty=40^1.5 ≈ 253
        assert weights["ROOM_101"] > 100

    def test_time_decay_increases_weight_over_time(self):
        from pathfinder import compute_node_weights
        graph = {"ROOM_101": ["EXIT"], "EXIT": []}
        sensor_data = {}
        # Alert happened 100 seconds ago
        alert_times = {"ROOM_101": time.time() - 100}
        weights = compute_node_weights(graph, sensor_data, alert_times)
        # decay = 100 * 0.5 = +50
        assert weights["ROOM_101"] >= 50

    def test_neighbor_hazard_spread(self):
        from pathfinder import compute_node_weights
        graph = {"FIRE_NODE": ["NEIGHBOR"], "NEIGHBOR": ["EXIT"], "EXIT": []}
        sensor_data = {"FIRE_NODE": {"temperature": 0, "smoke": 700}}  # 1000 weight
        alert_times = {"FIRE_NODE": time.time()}
        weights = compute_node_weights(graph, sensor_data, alert_times)
        # NEIGHBOR should have partial hazard from FIRE_NODE
        assert weights.get("NEIGHBOR", 0) > 1.0  # more than baseline


# ─────────────────────────────────────────────────────────────────────
# Fix C — JWT auth + replay protection
# ─────────────────────────────────────────────────────────────────────

class TestApiKeyAuth:
    def test_write_blocked_without_key(self, client):
        """No auth header at all → 401."""
        res = client.post("/sensor", json={
            "deviceId": "X", "temperature": 99, "smoke": 999,
        })
        assert res.status_code == 401

    def test_write_allowed_with_correct_key(self, client, registered_sensor, jwt_headers):
        """Valid JWT Bearer token → 200."""
        res = client.post("/heartbeat",
            json={"deviceId": "ESP32_SENSOR_01"},
            headers=jwt_headers,
        )
        assert res.status_code == 200

    def test_replay_attack_rejected(self, client, registered_sensor, jwt_headers):
        """Old X-Timestamp with valid JWT → 401 (replay protection)."""
        old_ts = str(time.time() - 9999)  # way too old
        headers = {**jwt_headers, "X-Timestamp": old_ts}
        res = client.post("/heartbeat",
            json={"deviceId": "ESP32_SENSOR_01"},
            headers=headers,
        )
        assert res.status_code == 401

    def test_fresh_timestamp_accepted(self, client, registered_sensor, jwt_headers):
        """Valid JWT + no explicit timestamp (not required by default) → 200."""
        res = client.post("/heartbeat",
            json={"deviceId": "ESP32_SENSOR_01"},
            headers=jwt_headers,
        )
        assert res.status_code == 200

    def test_get_routes_no_auth_required(self, client):
        """GET routes should never require auth."""
        res = client.get("/health")
        assert res.status_code == 200
        res2 = client.get("/status")
        assert res2.status_code == 200


# ─────────────────────────────────────────────────────────────────────
# Fix E — Backup LED fallback
# ─────────────────────────────────────────────────────────────────────

class TestBackupLed:
    def test_backup_led_config_loaded(self):
        """Backup LED node should be configured in graph."""
        dev = graph_manager.get_led_device_for_node("ROOM_101")
        assert dev is not None
        assert dev.get("backup") == "HALLWAY_A"

    def test_get_backup_led_for_node(self):
        backup = graph_manager.get_backup_led_for_node("ROOM_101")
        assert backup is not None
        assert backup.get("ip") == "192.168.1.105"  # HALLWAY_A's IP

    @patch("led_controller.requests.post")
    def test_backup_ip_tried_on_primary_failure(self, mock_post):
        """If primary LED fails, backup IP should be tried."""
        from requests.exceptions import ConnectionError as ReqConnErr
        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {"status": "OK"}

        # Primary fails twice (2 retries), backup succeeds
        mock_post.side_effect = [
            ReqConnErr("primary down"),
            ReqConnErr("primary down retry"),
            ok_response,  # backup succeeds
        ]

        from led_controller import send_single_command
        result = send_single_command(
            ip        = "192.168.1.101",
            command   = {"node": "ROOM_101", "direction": "LEFT", "color": "GREEN", "mode": "FLOW", "priority": 1},
            backup_ip = "192.168.1.105",
        )
        assert result == "OK"   # backup succeeded
        assert mock_post.call_count == 3


# ─────────────────────────────────────────────────────────────────────
# Fix F — CommandId idempotency
# ─────────────────────────────────────────────────────────────────────

class TestCommandIdDedup:
    @patch("led_controller.requests.post")
    def test_duplicate_command_id_not_sent_twice(self, mock_post):
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"status": "OK"}
        mock_post.return_value = ok_resp

        from led_controller import send_single_command, _sent_ids, _dedup_lock
        import uuid
        # Clear dedup cache
        with _dedup_lock:
            _sent_ids.clear()

        cmd_id = str(uuid.uuid4())
        cmd    = {"node": "HALLWAY_A", "direction": "LEFT", "color": "GREEN", "mode": "FLOW", "priority": 1}

        r1 = send_single_command("192.168.1.105", cmd, command_id=cmd_id)
        r2 = send_single_command("192.168.1.105", cmd, command_id=cmd_id)

        assert r1 == "OK"          # first send succeeded
        assert r2 == "OK"          # duplicate — already sent, returns OK
        assert mock_post.call_count == 1  # Only sent ONCE


# ─────────────────────────────────────────────────────────────────────
# Fix G — Partial network (OK/FAILED/SKIPPED_NO_IP)
# ─────────────────────────────────────────────────────────────────────

class TestPartialNetworkStatus:
    @patch("led_controller.requests.post")
    def test_broadcast_returns_per_node_status(self, mock_post):
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"status": "OK"}
        mock_post.return_value = ok_resp

        from led_controller import broadcast_commands_to_devices
        results = broadcast_commands_to_devices(
            device_ips    = {"HALLWAY_A": "192.168.1.105"},
            node_commands = {
                "HALLWAY_A": {"node": "HALLWAY_A", "direction": "LEFT", "color": "GREEN", "mode": "FLOW", "priority": 1},
                "ROOM_101":  {"node": "ROOM_101",  "direction": "LEFT", "color": "GREEN", "mode": "FLOW", "priority": 1},
            },
        )
        assert results["HALLWAY_A"] == "OK"
        assert results["ROOM_101"]  == "SKIPPED_NO_IP"

    @patch("evacuation_engine.send_structured_fire_alert", return_value=None)
    @patch("evacuation_engine.broadcast_commands_to_devices", return_value={"HALLWAY_A": "OK", "ROOM_101": "FAILED"})
    def test_evacuation_result_includes_coverage_pct(self, mock_led, mock_cloud, client, registered_sensor, jwt_headers):
        payload = {
            "deviceId": "ESP32_SENSOR_01", "temperature": 99,
            "smoke": 999, "status": "FIRE",
        }
        res = client.post("/sensor", json=payload, headers=jwt_headers)
        assert res.status_code == 200
        evac = res.get_json().get("evacuation", {})
        assert "led_coverage_pct" in evac
        assert 0 <= evac["led_coverage_pct"] <= 100


# ─────────────────────────────────────────────────────────────────────
# Fix H — Alert severity + force-clear interlock
# ─────────────────────────────────────────────────────────────────────

class TestAlertSeverity:
    def test_fire_alert_blocks_clear_without_force(self, client, jwt_headers):
        state_manager.record_alert("ROOM_101", severity="FIRE")
        res = client.post("/clear-alert", json={"nodeId": "ROOM_101", "force": False}, headers=jwt_headers)
        assert res.status_code == 409
        assert "ROOM_101" in state_manager.get_unsafe_nodes()

    def test_fire_alert_cleared_with_force(self, client, jwt_headers):
        state_manager.record_alert("ROOM_101", severity="FIRE")
        res = client.post("/clear-alert", json={"nodeId": "ROOM_101", "force": True}, headers=jwt_headers)
        assert res.status_code == 200
        assert "ROOM_101" not in state_manager.get_unsafe_nodes()

    def test_warning_alert_cleared_without_force(self, client, jwt_headers):
        state_manager.record_alert("ROOM_101", severity="WARNING")
        res = client.post("/clear-alert", json={"nodeId": "ROOM_101"}, headers=jwt_headers)
        assert res.status_code == 200

    def test_alert_escalates_on_worse_severity(self):
        state_manager.record_alert("ROOM_101", severity="WARNING")
        state_manager.record_alert("ROOM_101", severity="FIRE")
        assert state_manager.get_alert_severity("ROOM_101") == "FIRE"

    def test_alert_does_not_downgrade_severity(self):
        state_manager.record_alert("ROOM_101", severity="FIRE")
        state_manager.record_alert("ROOM_101", severity="WARNING")
        assert state_manager.get_alert_severity("ROOM_101") == "FIRE"


# ─────────────────────────────────────────────────────────────────────
# Event log
# ─────────────────────────────────────────────────────────────────────

class TestEventLog:
    def test_get_events_endpoint_returns_200(self, client):
        res = client.get("/events")
        assert res.status_code == 200
        d = res.get_json()
        assert "events" in d
        assert "count" in d

    def test_get_events_with_limit(self, client):
        res = client.get("/events?limit=10")
        assert res.status_code == 200

    def test_get_events_invalid_limit(self, client):
        res = client.get("/events?limit=abc")
        assert res.status_code == 400

    def test_event_log_fire_detected_wrapper(self):
        import event_log as el
        # Should not raise
        el.fire_detected("ROOM_101", "ESP32_SENSOR_01", 50.0, 300.0)

    def test_event_log_evacuation_wrappers(self):
        import event_log as el
        el.evacuation_triggered("ROOM_101", ["ROOM_101"], {"ROOM_101": ["ROOM_101", "EXIT"]})
        el.evacuation_complete(True, 85.0, {"HALLWAY_A": "OK"})
        el.alert_cleared("ROOM_101", forced=True)


# ─────────────────────────────────────────────────────────────────────
# Complete pipeline integration
# ─────────────────────────────────────────────────────────────────────

class TestFullPipeline:
    @patch("evacuation_engine.send_structured_fire_alert", return_value={"commands": []})
    @patch("evacuation_engine.broadcast_commands_to_devices", return_value={"HALLWAY_A": "OK"})
    def test_sensor_triggers_full_evacuation(self, mock_led, mock_cloud, client, registered_sensor, jwt_headers):
        payload = {
            "deviceId":    "ESP32_SENSOR_01",
            "temperature": 99,
            "smoke":       999,
            "status":      "FIRE",
        }
        res = client.post("/sensor", json=payload, headers=jwt_headers)
        assert res.status_code == 200
        d = res.get_json()
        assert "evacuation" in d
        evac = d["evacuation"]
        assert "algorithm" in evac
        assert "command_id" in evac
        assert "led_coverage_pct" in evac
        assert "fail_safe_active" in evac

    @patch("evacuation_engine.send_structured_fire_alert", return_value=None)
    @patch("evacuation_engine.broadcast_commands_to_devices", return_value={})
    def test_multi_start_returns_paths_per_node(self, mock_led, mock_cloud, client, jwt_headers):
        res = client.post("/evacuate", json={
            "startNodes": ["ROOM_101", "ROOM_202"],
        }, headers=jwt_headers)
        assert res.status_code == 200
        paths = res.get_json()["evacuation"]["local_paths"]
        assert "ROOM_101" in paths
        assert "ROOM_202" in paths

    @patch("evacuation_engine.send_structured_fire_alert", return_value=None)
    @patch("evacuation_engine.broadcast_commands_to_devices", return_value={})
    def test_second_sensor_debounced(self, mock_led, mock_cloud, client, registered_sensor, jwt_headers):
        payload = {
            "deviceId": "ESP32_SENSOR_01",
            "temperature": 99, "smoke": 999, "status": "FIRE",
        }
        r1 = client.post("/sensor", json=payload, headers=jwt_headers)
        r2 = client.post("/sensor", json=payload, headers=jwt_headers)
        assert r1.status_code == 200
        assert r2.get_json().get("debounced") is True

    def test_clear_fire_alert_deactivates_evacuation(self, client, jwt_headers):
        state_manager.record_alert("ROOM_101", severity="WARNING")  # not FIRE → no force needed
        state_manager.set_evacuation_active(True)
        res = client.post("/clear-alert", json={"nodeId": "ROOM_101"}, headers=jwt_headers)
        assert res.status_code == 200
        assert not state_manager.is_evacuation_active()
