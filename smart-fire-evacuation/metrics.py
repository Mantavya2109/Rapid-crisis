"""
metrics.py
----------
Defines Prometheus metrics for observability.
"""
from prometheus_client import Counter, Gauge, Histogram

# General API metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint"]
)

http_request_latency_seconds = Histogram(
    "http_request_latency_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"]
)

# Application specific metrics
mqtt_messages_received_total = Counter(
    "mqtt_messages_received_total",
    "Total MQTT messages received from topics",
    ["topic"]
)

queue_size_gauge = Gauge(
    "queue_size",
    "Current size of the processing queue"
)

evacuation_state_gauge = Gauge(
    "evacuation_active",
    "1 if evacuation is active, 0 otherwise"
)

danger_nodes_gauge = Gauge(
    "danger_nodes_total",
    "Number of nodes currently in DANGER or CRITICAL state"
)

ha_role_gauge = Gauge(
    "ha_role",
    "1 if PRIMARY, 0 if SECONDARY"
)

ha_failover_duration_seconds = Histogram(
    "ha_failover_duration_seconds",
    "Time taken from primary loss to active secondary execution in seconds"
)

queue_dropped_messages_total = Counter(
    "queue_dropped_messages_total",
    "Total MQTT messages dropped due to severe processing queue overload"
)

duplicate_events_dropped_total = Counter(
    "duplicate_events_dropped_total",
    "Total events dropped due to being out-of-order or duplicate sequence numbers",
    ["node"]
)

safety_mode_triggers_total = Counter(
    "safety_mode_triggers_total",
    "Total number of times the system actively engaged HARD SAFETY MODE"
)
