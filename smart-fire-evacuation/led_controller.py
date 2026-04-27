"""
led_controller.py
-----------------
Sends rich evacuation commands to ESP32 LED strip controllers via HTTP.

New in this version:
  - commandId (UUID) per batch — ESP32 idempotency / dedup
  - Backup LED node fallback (tries neighbour's LED on ACK failure)
  - Per-send event logging (LED_SENT / LED_FAILED / LED_BACKUP_USED)
  - _sent_ids cache (100-entry LRU) prevents duplicate dispatches
"""

import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import requests

import event_log
from config.settings import (
    LED_ENDPOINT,
    LED_BATCH_ENDPOINT,
    LED_TIMEOUT_SEC,
    LED_RETRY_ATTEMPTS,
)
from logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────
# LED result status constants
# ─────────────────────────────────────────────────────────────────────
LED_OK              = "OK"
LED_FAILED          = "FAILED"
LED_FAILED_CRITICAL = "FAILED_CRITICAL"  # Both primary AND backup exhausted
LED_SKIPPED_NO_IP   = "SKIPPED_NO_IP"

# ─────────────────────────────────────────────────────────────────────
# Command ID dedup cache (last 100 IDs)
# ─────────────────────────────────────────────────────────────────────
_DEDUP_CACHE_SIZE = 100
_sent_ids: "OrderedDict[str, float]" = OrderedDict()
_dedup_lock = __import__("threading").Lock()


def _register_command_id(cmd_id: str) -> bool:
    """
    Returns True if this commandId is NEW (should be sent).
    Returns False if already seen (duplicate — skip).
    """
    with _dedup_lock:
        if cmd_id in _sent_ids:
            return False
        _sent_ids[cmd_id] = time.time()
        if len(_sent_ids) > _DEDUP_CACHE_SIZE:
            _sent_ids.popitem(last=False)  # evict oldest
        return True


# ─────────────────────────────────────────────────────────────────────
# LED command schema helpers
# ─────────────────────────────────────────────────────────────────────

VALID_COLORS     = {"GREEN", "RED", "YELLOW", "WHITE", "BLUE", "OFF"}
VALID_MODES      = {"FLOW", "BLINK", "SOLID", "CHASE", "PULSE"}
VALID_DIRECTIONS = {"LEFT", "RIGHT", "STRAIGHT", "UP", "DOWN", "EXIT", "NONE"}


def build_led_command(
    node:      str,
    direction: str,
    color:     str = "GREEN",
    mode:      str = "FLOW",
    priority:  int = 1,
) -> Dict[str, Any]:
    return {
        "node":      node,
        "direction": direction.upper() if direction else "NONE",
        "color":     color.upper(),
        "mode":      mode.upper(),
        "priority":  priority,
    }


def build_path_commands(
    path:          List[str],
    directions_map: Dict[str, str],
    color:         str = "GREEN",
    mode:          str = "FLOW",
    exit_color:    str = "WHITE",
    exit_mode:     str = "BLINK",
) -> List[Dict[str, Any]]:
    """
    Convert an ordered path + directions map into a list of rich LED commands.
    Exit node gets a distinct color/mode to visually distinguish it.
    """
    commands: List[Dict[str, Any]] = []
    for i, node in enumerate(path):
        if i < len(path) - 1:
            next_node = path[i + 1]
            direction = directions_map.get(f"{node}→{next_node}", "STRAIGHT")
            commands.append(build_led_command(node, direction, color, mode, priority=1))
        else:
            commands.append(build_led_command(node, "EXIT", exit_color, exit_mode, priority=2))
    return commands


# ─────────────────────────────────────────────────────────────────────
# HTTP send — with retry and ACK parsing
# ─────────────────────────────────────────────────────────────────────

def _post_with_retry(
    url:     str,
    payload: Dict,
    retries: int = LED_RETRY_ATTEMPTS,
    label:   str = "",
) -> bool:
    """POST payload with retry. Parses ESP32 ACK {status: "OK"}. Returns True on success."""
    for attempt in range(1, retries + 1):
        try:
            log.info("💡 LED attempt %d/%d → %s [%s]", attempt, retries, url, label)
            resp = requests.post(url, json=payload, timeout=LED_TIMEOUT_SEC)
            resp.raise_for_status()

            try:
                ack = resp.json()
                if ack.get("status") == "OK":
                    log.info("✅ LED ACK OK from %s (attempt %d)", url, attempt)
                    return True
                log.warning("⚠️  LED ACK unexpected: %s", ack)
            except ValueError:
                # Non-JSON 200 → treat as success
                return True

        except requests.exceptions.ConnectionError:
            log.error("❌ LED unreachable: %s (attempt %d/%d)", url, attempt, retries)
        except requests.exceptions.Timeout:
            log.error("❌ LED timeout %ds: %s (attempt %d/%d)", LED_TIMEOUT_SEC, url, attempt, retries)
        except requests.exceptions.HTTPError as exc:
            log.error("❌ LED HTTP error: %s (attempt %d/%d)", exc, attempt, retries)
        except Exception as exc:
            log.error("❌ LED unexpected error: %s → %s", url, exc)

        if attempt < retries:
            time.sleep(0.5 * attempt)

    return False


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────

def send_single_command(
    ip:         str,
    command:    Dict[str, Any],
    command_id: Optional[str] = None,
    backup_ip:  Optional[str] = None,
) -> str:
    """
    Send a single rich LED command to an ESP32.

    Returns
    -------
    LED_OK              : primary succeeded
    LED_FAILED          : primary failed, no backup configured
    LED_FAILED_CRITICAL : primary failed AND backup also failed (no guidance possible)
    """
    cmd_id = command_id or str(uuid.uuid4())

    if not _register_command_id(cmd_id):
        log.info("⏭️  Duplicate commandId %s — skipped.", cmd_id)
        return LED_OK  # Already sent successfully

    payload   = {**command, "commandId": cmd_id}
    url       = f"http://{ip}{LED_ENDPOINT}"
    node      = command.get("node", "?")
    success   = _post_with_retry(url, payload, label=f"node={node}")

    if success:
        event_log.led_sent(node, ip, cmd_id)
        return LED_OK

    # ── Backup attempt ────────────────────────────────────────────────
    if backup_ip:
        log.warning("🔄 Primary LED failed for '%s' — trying backup IP %s", node, backup_ip)
        backup_url = f"http://{backup_ip}{LED_ENDPOINT}"
        success    = _post_with_retry(backup_url, payload, label=f"node={node} BACKUP")
        if success:
            event_log.led_sent(node, backup_ip, cmd_id, backup=True)
            return LED_OK

        # Both primary AND backup failed — CRITICAL: no LED guidance on this node
        log.critical(
            "🚨 CRITICAL: LED node '%s' has NO guidance — primary=%s backup=%s both unreachable.",
            node, ip, backup_ip,
        )
        event_log.led_failed(
            node, ip,
            reason=f"CRITICAL: primary={ip} AND backup={backup_ip} both failed",
        )
        event_log.log_event(
            event_log.LED_FAILED_CRITICAL,
            node_id=node,
            severity="CRITICAL",
            metadata={"primary_ip": ip, "backup_ip": backup_ip, "command_id": cmd_id},
        )
        return LED_FAILED_CRITICAL

    # No backup configured — single point of failure, but not "critical" in the schema
    event_log.led_failed(node, ip, reason="all retries exhausted (no backup configured)")
    return LED_FAILED


def send_led_commands(
    ip:         str,
    commands:   List[Dict[str, Any]],
    command_id: Optional[str] = None,
    backup_ip:  Optional[str] = None,
) -> str:
    """Send a batch of rich LED commands to a single ESP32.

    Returns LED_OK | LED_FAILED | LED_FAILED_CRITICAL.
    """
    if not commands:
        return LED_FAILED

    if len(commands) == 1:
        return send_single_command(ip, commands[0], command_id=command_id, backup_ip=backup_ip)

    cmd_id = command_id or str(uuid.uuid4())
    if not _register_command_id(cmd_id):
        log.info("⏭️  Duplicate batch commandId %s — skipped.", cmd_id)
        return LED_OK

    url     = f"http://{ip}{LED_BATCH_ENDPOINT}"
    payload = {"commands": commands, "commandId": cmd_id}
    success = _post_with_retry(url, payload, label=f"{len(commands)} cmds")

    if success:
        event_log.led_sent(commands[0].get("node", "?"), ip, cmd_id)
        return LED_OK

    if backup_ip:
        log.warning("🔄 Batch LED failed — trying backup %s", backup_ip)
        backup_url = f"http://{backup_ip}{LED_BATCH_ENDPOINT}"
        success    = _post_with_retry(backup_url, payload, label="BACKUP BATCH")
        if success:
            event_log.led_sent(commands[0].get("node", "?"), backup_ip, cmd_id, backup=True)
            return LED_OK

        # Both primary AND backup failed
        node = commands[0].get("node", "?")
        log.critical(
            "🚨 CRITICAL: Batch LED '%s' primary=%s backup=%s BOTH unreachable.",
            node, ip, backup_ip,
        )
        event_log.log_event(
            event_log.LED_FAILED_CRITICAL,
            node_id=node,
            severity="CRITICAL",
            metadata={"primary_ip": ip, "backup_ip": backup_ip, "command_id": cmd_id},
        )
        return LED_FAILED_CRITICAL

    event_log.led_failed(commands[0].get("node", "?"), ip, "batch failed (no backup)")
    return LED_FAILED


def broadcast_commands_to_devices(
    device_ips:   Dict[str, str],
    node_commands: Dict[str, Dict[str, Any]],
    backup_ips:   Optional[Dict[str, str]] = None,
    command_id:   Optional[str] = None,
) -> Dict[str, str]:
    """
    Dispatch LED commands to multiple ESP32 devices.

    Returns
    -------
    { nodeId → "OK" | "FAILED" | "FAILED_CRITICAL" | "SKIPPED_NO_IP" }

    FAILED_CRITICAL means BOTH primary and backup were tried and failed.
    The evacuation engine should surface FAILED_CRITICAL nodes prominently
    (UI alert, siren trigger, manual intervention required).
    """
    backup   = backup_ips or {}
    results: Dict[str, str] = {}

    critical_nodes: List[str] = []

    for node, command in node_commands.items():
        ip = device_ips.get(node)
        if not ip:
            log.warning("No IP for LED node '%s' — SKIPPED.", node)
            results[node] = LED_SKIPPED_NO_IP
            continue

        result = send_single_command(
            ip         = ip,
            command    = command,
            command_id = command_id,
            backup_ip  = backup.get(node),
        )
        results[node] = result
        if result == LED_FAILED_CRITICAL:
            critical_nodes.append(node)

    if critical_nodes:
        log.critical(
            "🚨 LED guidance LOST on %d node(s): %s — manual intervention required!",
            len(critical_nodes), critical_nodes,
        )

    log.info("LED broadcast: %s", results)
    return results
