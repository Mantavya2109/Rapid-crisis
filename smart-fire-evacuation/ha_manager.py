"""
ha_manager.py
-------------
High Availability / Failover Manager.
Monitors the Primary Pi. If the Primary fails to respond to heartbeats
within the timeout window, the Secondary promotes itself.
"""

import threading
import time
import requests
import socket
from config.settings import HA_MODE_ENABLED, HA_ROLE, HA_PEER_URL, HA_HEARTBEAT_INTERVAL, HA_TAKEOVER_TIMEOUT, MQTT_BROKER_HOST, MQTT_BROKER_PORT
from logger import get_logger

log = get_logger("ha_manager")

_active_role = HA_ROLE
_is_running = False
_thread = None
_last_primary_seen = time.time()
_promotion_callback = None

def init(promotion_callback=None):
    global _promotion_callback, _active_role, _last_primary_seen
    _promotion_callback = promotion_callback
    _active_role = HA_ROLE
    _last_primary_seen = time.time()
    
    if not HA_MODE_ENABLED:
        log.info("HA Failover is DISABLED.")
        return

    log.info(f"HA Failover is ENABLED. Initial Role: {_active_role}")

def start():
    global _is_running, _thread
    if not HA_MODE_ENABLED:
        return
        
    if _is_running:
        return
        
    _is_running = True
    _thread = threading.Thread(target=_ha_loop, name="ha-manager", daemon=True)
    _thread.start()

def stop():
    global _is_running
    _is_running = False
    
def get_current_role() -> str:
    return _active_role
    
def is_primary() -> bool:
    return not HA_MODE_ENABLED or _active_role == "PRIMARY"

def _check_local_network() -> bool:
    """Check if the Mosquitto broker is reachable. If yes, we are not isolated."""
    try:
        with socket.create_connection((MQTT_BROKER_HOST, MQTT_BROKER_PORT), timeout=2.0):
            return True
    except OSError:
        return False

def _ha_loop():
    global _active_role, _last_primary_seen
    
    while _is_running:
        if _active_role == "SECONDARY":
            primary_visible = False
            try:
                # Ping the Primary's health endpoint
                resp = requests.get(f"{HA_PEER_URL}/health", timeout=3.0)
                if resp.status_code == 200:
                    _last_primary_seen = time.time()
                    primary_visible = True
            except requests.RequestException:
                pass # Suppress connection errors
                
            age = time.time() - _last_primary_seen
            if age > HA_TAKEOVER_TIMEOUT:
                log.warning(f"🚨 Primary Pi unreachable for {age:.1f}s (Timeout {HA_TAKEOVER_TIMEOUT}s).")
                # 3-Tier Check before blindly promoting
                if _check_local_network():
                    log.warning("✅ Local Broker reachable. Primary is down. Initiating TAKEOVER!")
                    _promote_to_primary()
                else:
                    log.error("💥 Total Local Isolation! Broker AND Primary unreachable. Triggering HARD SAFETY MODE!")
                    import state_manager
                    import evacuation_engine
                    state_manager.set_hard_safety_mode(True)
                    evacuation_engine.execute_hard_safety_mode()
                    # Cannot promote, we stay secondary but blast safety mode if we can
                    pass
        elif _active_role == "PRIMARY":
            # Just log that we're acting as primary (in a real setup, primary could ping secondary to check sync, etc.)
            pass
            
        time.sleep(HA_HEARTBEAT_INTERVAL)

def _promote_to_primary():
    global _active_role
    _active_role = "PRIMARY"
    log.info("👑 Promoted to PRIMARY role. Taking over system functions.")
    if _promotion_callback:
        # Start MQTT, processing logic, etc.
        _promotion_callback()
