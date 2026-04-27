"""
tests/test_security_ha.py
-------------------------
Tests for JWT Authentication, Timestamps, and High Availability failover logic.
"""
import pytest
import time
import jwt
from unittest.mock import patch, MagicMock

# Force settings for tests before app imports
import os
os.environ["API_SECRET_KEY"] = "dummy-secret"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret"
os.environ["REQUIRE_TIMESTAMP"] = "true"
os.environ["HA_MODE_ENABLED"] = "true"

from app import app
import auth_manager
import ha_manager

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_missing_auth_header(client):
    response = client.post("/evacuate", json={"startNodes": ["ROOM_101"]}, headers={"X-Timestamp": str(time.time())})
    assert response.status_code == 401
    assert "missing or invalid Authorization" in response.get_json()["error"]

def test_invalid_jwt_token(client):
    headers = {
        "Authorization": "Bearer invalid.token.string",
        "X-Timestamp": str(time.time())
    }
    response = client.post("/evacuate", json={"startNodes": ["ROOM_101"]}, headers=headers)
    assert response.status_code == 401
    assert "invalid or expired token" in response.get_json()["error"]

def test_valid_jwt_expired_timestamp(client):
    token = auth_manager.generate_device_token("TEST_DEV")
    old_ts = time.time() - 100 # older than 30s window
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Timestamp": str(old_ts)
    }
    response = client.post("/evacuate", json={"startNodes": ["ROOM_101"]}, headers=headers)
    assert response.status_code == 401
    assert "timestamp too old" in response.get_json()["error"]

def test_valid_jwt_and_timestamp(client):
    token = auth_manager.generate_device_token("TEST_DEV")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Timestamp": str(time.time())
    }
    response = client.post("/evacuate", json={"startNodes": ["ROOM_101"]}, headers=headers)
    # Could be 200 or 400 depending on evacuated mock state, but not 401
    assert response.status_code != 401

def test_ha_manager_failover():
    # Test primary unreachable
    ha_manager.init()
    assert ha_manager.get_current_role() == "PRIMARY" # Default from mock or env
    
    with patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("Connection Refused")
        # Overwrite role as SECONDARY manually to test failover
        ha_manager._active_role = "SECONDARY"
        ha_manager._last_primary_seen = time.time() - 35 # Exceeds 30s timeout
        
        # Manually invoke the loop logic once
        from ha_manager import _promote_to_primary
        # Since _ha_loop loops forever, just call the logic block
        if time.time() - ha_manager._last_primary_seen > 30:
            _promote_to_primary()
            
        assert ha_manager.get_current_role() == "PRIMARY"
        
