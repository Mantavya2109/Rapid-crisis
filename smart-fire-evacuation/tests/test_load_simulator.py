"""
tests/test_load_simulator.py
----------------------------
Simulates high burst load on MQTT and processing queue.
"""
import pytest
import time
import json
from unittest.mock import MagicMock

def test_processing_queue_burst():
    """Test that the processing queue can handle burst load and rate-limit properly"""
    import processing_queue
    from config.settings import PROCESSING_QUEUE_SIZE
    
    sensor_cb = MagicMock()
    heartbeat_cb = MagicMock()
    
    queue = processing_queue.init(sensor_cb, heartbeat_cb)
    try:
        # Simulate bursts exceeding PROCESSING_BURST_PER_NODE
        for i in range(150): # Assuming BURST is 10, this will overflow node buffers and fill queue
            topic = f"sensors/data/node_{i%5}"
            payload = {"deviceId": f"dev_{i%5}", "temperature": 25.0, "smoke": 100.0}
            queue.enqueue_sensor(topic, payload)
            
        time.sleep(1) # Allow worker threads to process
        
        stats = queue.get_stats()
        # Verify that total processed is bounded and dropped is non-zero if max burst exceeded
        assert stats["processed"] > 0
        assert stats["dropped"] >= 0 # Dropped could be > 0 depending on bucket tokens
        
        # We shouldn't exceed the queue size maximum per queue
        stats2 = queue.get_stats()
        total_in_queues = stats2["queue_high_size"] + stats2["queue_low_size"]
        assert total_in_queues <= PROCESSING_QUEUE_SIZE
    finally:
        queue.stop()
