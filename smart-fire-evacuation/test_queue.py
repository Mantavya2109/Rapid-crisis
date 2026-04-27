import time
import threading
from processing_queue import ProcessingQueue

def dummy_sensor_cb(topic, payload):
    pass

def dummy_hb_cb(topic, payload):
    pass

q = ProcessingQueue(
    sensor_callback=dummy_sensor_cb,
    heartbeat_callback=dummy_hb_cb
)
q.start()

# Simulate 50 nodes sending 10 bursts each (500 messages instantly)
import concurrent.futures

def sim_node(node_id):
    sent = 0
    dropped = 0
    for i in range(10):
        topic = f"sensors/data/{node_id}"
        payload = {"deviceId": node_id, "temperature": 25, "smoke": 100} # NORMAL payload
        enqueued = q.enqueue_sensor(topic, payload)
        if enqueued:
            sent += 1
        else:
            dropped += 1
    return sent, dropped

print("Starting burst simulation for 50 nodes...")
t0 = time.time()
sent_total = 0
dropped_total = 0

with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
    futures = [ex.submit(sim_node, f"NODE_{i}") for i in range(50)]
    for f in futures:
        s, d = f.result()
        sent_total += s
        dropped_total += d

print(f"Simulation done in {time.time()-t0:.3f}s")
print(f"Sent (enqueued): {sent_total}")
print(f"Dropped (rate limited): {dropped_total}")

# Let workers process
time.sleep(1)
stats = q.get_stats()
print("Queue Stats:", stats)
q.stop()
