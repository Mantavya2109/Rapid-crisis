"""
led_driver.py  (v2 — upgraded)
-------------------------------
Direct WS2812B (NeoPixel) LED strip control via Raspberry Pi GPIO.
Uses rpi_ws281x for hardware PWM on GPIO 18 (configurable).

UPGRADE SUMMARY vs v1
──────────────────────
New states:
  NORMAL          → solid GREEN
  PREDICTIVE_FIRE → orange breathing wave  ← NEW
  WARNING         → solid YELLOW
  DANGER          → slow pulse RED  (1 s period)
  CRITICAL_FIRE   → fast blink RED  (250 ms on/off)  ← NEW
  OFFLINE         → dim BLUE solid
  EXIT            → solid WHITE (evacuation path)

Zone mapping:
  - Zones registered via register_zones({ nodeId → (start, end) })
  - Dynamic remapping at runtime (no restart) via update_zone_layout()
  - Node types (room / hallway / exit) stored alongside zone indices

Thread safety:
  - One unified animation thread handles all animated states
  - Transitions are atomic: stop → reconfigure → restart
  - MockStrip at all unchanged public APIs on dev machines
"""

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from config.settings import (
    LED_GPIO_PIN,
    LED_COUNT,
    LED_FREQ_HZ,
    LED_DMA,
    LED_INVERT,
    LED_BRIGHTNESS,
    LED_CHANNEL,
)
from logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Colour palette  (R, G, B)
# ─────────────────────────────────────────────────────────────────────

COLOR_GREEN   = (0,   200,   0)
COLOR_YELLOW  = (220, 180,   0)
COLOR_RED     = (255,   0,   0)
COLOR_ORANGE  = (255,  80,   0)
COLOR_WHITE   = (255, 255, 255)
COLOR_BLUE    = (0,     0,  80)
COLOR_OFF     = (0,     0,   0)

# Maps zone status → (R, G, B) base colour
STATUS_COLORS: Dict[str, Tuple[int, int, int]] = {
    "NORMAL":          COLOR_GREEN,
    "PREDICTIVE_FIRE": COLOR_ORANGE,
    "WARNING":         COLOR_YELLOW,
    "DANGER":          COLOR_RED,
    "CRITICAL_FIRE":   COLOR_RED,
    "OFFLINE":         COLOR_BLUE,
    "EXIT":            COLOR_WHITE,
    "OFF":             COLOR_OFF,
}

# Animated states: these need the animation thread
_ANIMATED_STATES = frozenset({"DANGER", "CRITICAL_FIRE", "PREDICTIVE_FIRE"})

VALID_STATUSES = frozenset(STATUS_COLORS.keys())


# ─────────────────────────────────────────────────────────────────────
# Hardware / mock
# ─────────────────────────────────────────────────────────────────────

try:
    from rpi_ws281x import PixelStrip, Color as _WsColor
    _HW_AVAILABLE = True
    log.info("✅ rpi_ws281x available — hardware LED control ENABLED (GPIO %d).", LED_GPIO_PIN)
except ImportError:
    _HW_AVAILABLE = False
    log.warning(
        "⚠️  rpi_ws281x not installed — MOCK mode. "
        "All LED state is tracked in-memory only."
    )


def _make_color(r: int, g: int, b: int) -> Any:
    return _WsColor(r, g, b) if _HW_AVAILABLE else (r, g, b)


class _MockStrip:
    def __init__(self, count, pin, *args, **kwargs):
        self._count = count
        self._pixels = [(0, 0, 0)] * count
    def begin(self): pass
    def numPixels(self): return self._count
    def setPixelColor(self, i, c):
        if 0 <= i < self._count: self._pixels[i] = c
    def show(self): log.debug("MockStrip.show() — %d px", self._count)
    def setBrightness(self, b): pass


def _build_strip():
    S = PixelStrip if _HW_AVAILABLE else _MockStrip
    s = S(LED_COUNT, LED_GPIO_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL)
    s.begin()
    return s


# ─────────────────────────────────────────────────────────────────────
# Module-level state
# ─────────────────────────────────────────────────────────────────────

_zones:      Dict[str, Tuple[int, int]] = {}    # nodeId → (start, end)
_node_types: Dict[str, str]             = {}    # nodeId → "room"|"hallway"|"exit"
_zone_status: Dict[str, str]            = {}    # nodeId → status string

_lock  = threading.RLock()
_strip = None

# Animation thread (single shared thread for all animated zones)
_anim_thread:  Optional[threading.Thread] = None
_anim_stop     = threading.Event()
_anim_running  = False


# ─────────────────────────────────────────────────────────────────────
# Initialisation
# ─────────────────────────────────────────────────────────────────────

def init() -> None:
    """Init LED strip hardware. Call ONCE at startup."""
    global _strip
    _strip = _build_strip()
    log.info("💡 LED strip ready: %d LEDs, GPIO %d.", LED_COUNT, LED_GPIO_PIN)
    _all_off()


def register_zones(zones: Dict[str, Tuple[int, int]]) -> None:
    """
    Register zone layout: { nodeId → (start_led, end_led) } inclusive, 0-indexed.
    Existing zones not in the new dict are removed.
    All newly registered zones start as OFFLINE.
    """
    with _lock:
        _zones.clear()
        _zones.update(zones)
        for node in zones:
            _zone_status.setdefault(node, "OFFLINE")
        # Remove orphaned statuses
        for dead in [n for n in list(_zone_status) if n not in zones]:
            del _zone_status[dead]

    used = sum(e - s + 1 for s, e in zones.values())
    log.info("🗺️  LED zones: %d nodes → %d/%d LEDs", len(zones), used, LED_COUNT)
    _render()


def update_zone_layout(
    zones:      Dict[str, Tuple[int, int]],
    node_types: Optional[Dict[str, str]] = None,
) -> None:
    """
    Replace the zone layout at runtime (Upgrade #7 — dynamic mapping).

    Parameters
    ----------
    zones      : { nodeId → (start_led, end_led) }
    node_types : { nodeId → "room" | "hallway" | "exit" }
    """
    with _lock:
        _zones.clear()
        _zones.update(zones)
        _node_types.clear()
        if node_types:
            _node_types.update(node_types)
        # Preserve existing statuses; new nodes default OFFLINE
        for node in zones:
            _zone_status.setdefault(node, "OFFLINE")
        for dead in [n for n in list(_zone_status) if n not in zones]:
            del _zone_status[dead]

    log.info("🔄 Dynamic zone layout updated: %d nodes.", len(zones))
    _render()
    _sync_animation()


# ─────────────────────────────────────────────────────────────────────
# Status control  (public API)
# ─────────────────────────────────────────────────────────────────────

def set_zone_status(node: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        log.warning("⚠️  Unknown status '%s' for node '%s'.", status, node)
        return False

    with _lock:
        if node not in _zones:
            log.debug("⚠️  Zone '%s' not registered — ignoring status update.", node)
            return False
        old = _zone_status.get(node)
        if old == status:
            return True
        _zone_status[node] = status

    log.debug("💡 Zone '%s': %s → %s", node, old, status)
    _render()
    _sync_animation()
    return True


def set_all_zones(status: str) -> None:
    """Set every zone to the same status (e.g., OFF on shutdown)."""
    with _lock:
        for node in _zones:
            _zone_status[node] = status
    _render()
    _sync_animation()


def get_zone_states() -> Dict[str, str]:
    with _lock:
        return dict(_zone_status)


def get_zones() -> Dict[str, Dict]:
    with _lock:
        zc = dict(_zones)
        sc = dict(_zone_status)
        tc = dict(_node_types)
    return {
        node: {
            "start_led":  s,
            "end_led":    e,
            "led_count":  e - s + 1,
            "status":     sc.get(node, "OFFLINE"),
            "node_type":  tc.get(node, "room"),
            "color_rgb":  list(STATUS_COLORS.get(sc.get(node, "OFFLINE"), COLOR_OFF)),
        }
        for node, (s, e) in zc.items()
    }


def is_hw_available() -> bool:
    return _HW_AVAILABLE


# ─────────────────────────────────────────────────────────────────────
# Internal rendering
# ─────────────────────────────────────────────────────────────────────

def _set_range_locked(start: int, end: int, color: Any) -> None:
    """Paint LEDs [start..end] (caller ensures _lock and _strip check)."""
    for i in range(start, end + 1):
        if 0 <= i < LED_COUNT:
            _strip.setPixelColor(i, color)


def _render() -> None:
    """Full static render: paint each zone with its current status colour."""
    if _strip is None or _anim_running:
        return                              # Let animation thread own the strip

    with _lock:
        snap_zones  = dict(_zones)
        snap_status = dict(_zone_status)

    for node, (s, e) in snap_zones.items():
        st  = snap_status.get(node, "OFFLINE")
        rgb = STATUS_COLORS.get(st, COLOR_OFF)
        clr = _make_color(*rgb)
        with _lock:
            _set_range_locked(s, e, clr)

    _strip.show()


def _all_off() -> None:
    if _strip is None:
        return
    off = _make_color(*COLOR_OFF)
    for i in range(LED_COUNT):
        _strip.setPixelColor(i, off)
    _strip.show()


# ─────────────────────────────────────────────────────────────────────
# Animation system  (Upgrade #4 — pattern-based behaviour)
# ─────────────────────────────────────────────────────────────────────

def _sync_animation() -> None:
    """Start/stop the animation thread based on current zone states."""
    global _anim_running
    with _lock:
        need_anim = any(s in _ANIMATED_STATES for s in _zone_status.values())

    if need_anim and not _anim_running:
        _start_animation()
    elif not need_anim and _anim_running:
        _stop_animation()


def _start_animation() -> None:
    global _anim_thread, _anim_running
    _anim_stop.clear()
    _anim_running = True
    _anim_thread  = threading.Thread(
        target=_animation_loop, name="led-anim", daemon=True
    )
    _anim_thread.start()
    log.info("🎨 LED animation thread started.")


def _stop_animation() -> None:
    global _anim_running
    _anim_stop.set()
    if _anim_thread:
        _anim_thread.join(timeout=2.0)
    _anim_running = False
    _render()       # Restore static colours
    log.info("🎨 LED animation thread stopped.")


def _animation_loop() -> None:
    """
    Unified animation loop.  Each animated state has its own pattern:

    DANGER          → slow pulse red   (period 1.0 s, 20 steps)
    CRITICAL_FIRE   → fast blink red   (period 0.25 s on / 0.25 s off)
    PREDICTIVE_FIRE → orange breathing (period 2.0 s, smooth sine-ish)

    Non-animated zones retain their static colour throughout.
    """
    PULSE_PERIOD   = 1.0     # DANGER slow pulse (s)
    BLINK_HALF     = 0.25    # CRITICAL half-period (s)
    BREATHE_PERIOD = 2.0     # PREDICTIVE breathing (s)
    TICK           = 0.05    # Animation tick (s)  → 20 fps

    t_pulse   = 0.0
    t_breathe = 0.0
    blink_on  = True
    t_blink   = 0.0

    while not _anim_stop.is_set():
        t_pulse   += TICK
        t_breathe += TICK
        t_blink   += TICK

        if t_blink >= BLINK_HALF:
            t_blink  = 0.0
            blink_on = not blink_on

        # Pulse brightness 0→1→0 (triangle wave)
        pulse_phase = (t_pulse % PULSE_PERIOD) / PULSE_PERIOD
        pulse_b     = 1.0 - abs(2 * pulse_phase - 1.0)   # 0→1→0

        # Breathe brightness 0→1→0 (smoother sine approximation)
        breathe_phase = (t_breathe % BREATHE_PERIOD) / BREATHE_PERIOD
        breathe_b     = 0.5 * (1 - _cos_approx(2 * breathe_phase))   # 0→1→0

        with _lock:
            snap_zones  = dict(_zones)
            snap_status = dict(_zone_status)

        for node, (s, e) in snap_zones.items():
            st = snap_status.get(node, "NORMAL")

            if st == "DANGER":
                r = int(255 * max(0.08, pulse_b))
                clr = _make_color(r, 0, 0)

            elif st == "CRITICAL_FIRE":
                if blink_on:
                    clr = _make_color(255, 0, 0)
                else:
                    # Flash a brief white burst for maximum urgency
                    clr = _make_color(80, 0, 0)

            elif st == "PREDICTIVE_FIRE":
                r = int(255 * max(0.1, breathe_b))
                g = int(80  * max(0.1, breathe_b))
                clr = _make_color(r, g, 0)

            else:
                # Static colour for non-animated zones
                rgb = STATUS_COLORS.get(st, COLOR_OFF)
                clr = _make_color(*rgb)

            with _lock:
                _set_range_locked(s, e, clr)

        _strip.show()
        time.sleep(TICK)


def _cos_approx(x: float) -> float:
    """
    Cheap cosine approximation (Bhaskara I) for 0 ≤ x ≤ 1 (mapped to 0–2π).
    Avoids importing math in the tight animation loop.
    """
    # Map x ∈ [0,1] → θ ∈ [0, 2π] by quadrant
    # We just use a parabola approximation for half-cycle: cos(πx) ≈ 1 - 2x²
    if x <= 0.5:
        t = x * 2        # 0→1
        return 1 - 2 * t * t
    else:
        t = (x - 0.5) * 2   # 0→1
        return 2 * t * t - 1
