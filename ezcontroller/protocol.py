"""Wire protocol shared by the browser client and the server.

The phone sends a small JSON object at display-refresh rate.  This module
turns that object into a compact, allocation-light :class:`InputState` so the
hot path stays out of the way of input latency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Keep this order stable: it defines the button bit positions.
BUTTON_KEYS: tuple[str, ...] = (
    "handbrake",
    "y_btn",
    "x_btn",
    "a_btn",
    "lb",
    "rb",
    "view_btn",
    "menu_btn",
    "guide_btn",
    "dpad_up",
    "dpad_down",
    "dpad_left",
    "dpad_right",
)

BUTTON_BITS: dict[str, int] = {name: 1 << i for i, name in enumerate(BUTTON_KEYS)}


@dataclass(slots=True)
class InputState:
    """Fully normalised controller state.

    ``steering`` is the racing-profile axis. ``ls_x``/``ls_y`` are the full
    left-stick vector used by gamepad profiles such as the cozy/Stardew layout.
    Old clients that only send ``steering`` keep working because ``ls_x``
    falls back to it while parsing.
    """

    steering: float = 0.0
    ls_x: float = 0.0
    ls_y: float = 0.0
    throttle: float = 0.0
    brake: float = 0.0
    rs_x: float = 0.0
    rs_y: float = 0.0
    buttons: int = 0


def _number(
    data: dict[str, Any],
    key: str,
    default: float = 0.0,
    low: float = -1.0,
    high: float = 1.0,
) -> float:
    try:
        value = float(data.get(key, default))
    except (TypeError, ValueError):
        return default
    # Reject NaN without importing math for one check.
    if value != value:  # noqa: PLR0124 - NaN check
        return default
    if value < low:
        return low
    if value > high:
        return high
    return value


def parse_message(raw: str | bytes) -> InputState:
    """Parse one client frame.

    Raises ``json.JSONDecodeError``/``UnicodeDecodeError`` for malformed input;
    the server treats those as a dropped frame rather than a disconnect.
    """

    data = json.loads(raw)
    if not isinstance(data, dict):
        return InputState()

    buttons = 0
    for name, bit in BUTTON_BITS.items():
        value = data.get(name, 0)
        if value:
            buttons |= bit

    steering = _number(data, "steering")
    return InputState(
        steering=steering,
        # Backward compatible: older clients only send "steering".
        ls_x=_number(data, "ls_x", steering),
        ls_y=_number(data, "ls_y"),
        throttle=_number(data, "throttle", 0.0, 0.0, 1.0),
        brake=_number(data, "brake", 0.0, 0.0, 1.0),
        rs_x=_number(data, "rs_x"),
        rs_y=_number(data, "rs_y"),
        buttons=buttons,
    )
