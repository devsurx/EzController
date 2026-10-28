"""Virtual gamepad backends.

Linux uses the kernel ``uinput`` interface directly (no third-party module and
no ViGEmBus).  Windows keeps the original ``vgamepad``/ViGEmBus backend.

The Linux device advertises the exact vendor/product IDs and name used by the
in-kernel ``xpad`` driver so SDL, Steam, Proton and native games map it as a
standard Xbox 360 pad without any manual configuration.
"""

from __future__ import annotations

import fcntl
import glob
import os
import struct
import sys
import time

from .protocol import BUTTON_BITS, InputState


class PadError(RuntimeError):
    """Raised when a virtual pad cannot be created."""


# ---------------------------------------------------------------------------
# Linux uinput constants (linux/uinput.h, linux/input-event-codes.h)
# ---------------------------------------------------------------------------

UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503
UI_ABS_SETUP = 0x401C5504
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567

BUS_USB = 0x03
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0x00

ABS_X = 0x00
ABS_Y = 0x01
ABS_Z = 0x02
ABS_RX = 0x03
ABS_RY = 0x04
ABS_RZ = 0x05

BTN_A = 0x130
BTN_B = 0x131
BTN_X = 0x133
BTN_Y = 0x134
BTN_TL = 0x136
BTN_TR = 0x137
BTN_SELECT = 0x13A
BTN_START = 0x13B
BTN_MODE = 0x13C
BTN_DPAD_UP = 0x220
BTN_DPAD_DOWN = 0x221
BTN_DPAD_LEFT = 0x222
BTN_DPAD_RIGHT = 0x223

# Same identity as a wired Xbox 360 controller.  This is what makes SDL and
# games apply the standard xpad mapping automatically.
DEVICE_NAME = "Microsoft X-Box 360 pad"
VENDOR_ID = 0x045E
PRODUCT_ID = 0x028E
VERSION_ID = 0x0110

ABS_SPECS: tuple[tuple[int, int, int], ...] = (
    (ABS_X, -32767, 32767),
    (ABS_Y, -32767, 32767),
    (ABS_RX, -32767, 32767),
    (ABS_RY, -32767, 32767),
    (ABS_Z, 0, 255),
    (ABS_RZ, 0, 255),
)

BUTTON_CODES: dict[int, int] = {
    BUTTON_BITS["a_btn"]: BTN_A,
    BUTTON_BITS["handbrake"]: BTN_B,
    BUTTON_BITS["x_btn"]: BTN_X,
    BUTTON_BITS["y_btn"]: BTN_Y,
    BUTTON_BITS["lb"]: BTN_TL,
    BUTTON_BITS["rb"]: BTN_TR,
    BUTTON_BITS["view_btn"]: BTN_SELECT,
    BUTTON_BITS["menu_btn"]: BTN_START,
    BUTTON_BITS["guide_btn"]: BTN_MODE,
    BUTTON_BITS["dpad_up"]: BTN_DPAD_UP,
    BUTTON_BITS["dpad_down"]: BTN_DPAD_DOWN,
    BUTTON_BITS["dpad_left"]: BTN_DPAD_LEFT,
    BUTTON_BITS["dpad_right"]: BTN_DPAD_RIGHT,
}

_EVENT = struct.Struct("@llHHi")  # struct input_event, 24 bytes on x86_64/arm64
_SYN = _EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0)


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def _axis(value: float) -> int:
    return int(round(_clamp(value, -1.0, 1.0) * 32767.0))


def _trigger(value: float) -> int:
    return int(round(_clamp(value, 0.0, 1.0) * 255.0))


class Pad:
    """Common interface implemented by every backend."""

    description: str = "virtual pad"

    def apply(self, state: InputState) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def reset(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        pass


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------


class LinuxPad(Pad):
    """Xbox 360 compatible virtual pad created through /dev/uinput."""

    description = "Linux uinput Xbox 360 pad"

    def __init__(self) -> None:
        self.fd = -1
        self.event_node: str | None = None
        self._last_abs: dict[int, int] = {code: 0 for code, _, _ in ABS_SPECS}
        self._last_buttons = 0
        self._write_errors = 0
        self._open_uinput()
        self.description = f"Linux uinput Xbox 360 pad ({self.event_node or '/dev/uinput'})"

    # -- device creation ----------------------------------------------------

    def _open_uinput(self) -> None:
        try:
            fd = os.open("/dev/uinput", os.O_WRONLY)
        except FileNotFoundError as exc:
            raise PadError(
                "/dev/uinput is missing. Run `controller setup` (it loads the "
                "uinput module and installs the udev rule)."
            ) from exc
        except PermissionError as exc:
            raise PadError(
                "/dev/uinput is not writable.\n"
                "One-time fix: run `controller setup` and enter your sudo password.\n"
                "It installs a udev rule so your user can create virtual gamepads."
            ) from exc
        except OSError as exc:
            raise PadError(f"Cannot open /dev/uinput: {exc}") from exc

        try:
            self._configure(fd)
        except Exception:
            os.close(fd)
            raise

        self.fd = fd
        self.event_node = self._find_event_node()

    def _configure(self, fd: int) -> None:
        for event_type in (EV_KEY, EV_ABS, EV_SYN):
            fcntl.ioctl(fd, UI_SET_EVBIT, event_type)

        for code, minimum, maximum in ABS_SPECS:
            fcntl.ioctl(fd, UI_SET_ABSBIT, code)
            abs_setup = struct.pack("@Hxxiiiiii", code, 0, minimum, maximum, 0, 0, 0)
            fcntl.ioctl(fd, UI_ABS_SETUP, abs_setup)

        for code in BUTTON_CODES.values():
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)

        name = DEVICE_NAME.encode("utf-8")[:79]
        device_setup = struct.pack(
            "@HHHH80sI", BUS_USB, VENDOR_ID, PRODUCT_ID, VERSION_ID, name, 0
        )
        fcntl.ioctl(fd, UI_DEV_SETUP, device_setup)
        fcntl.ioctl(fd, UI_DEV_CREATE)

    @staticmethod
    def _find_event_node(timeout: float = 1.5) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for name_path in glob.glob("/sys/class/input/event*/device/name"):
                try:
                    with open(name_path, encoding="utf-8", errors="replace") as handle:
                        if handle.read().strip() == DEVICE_NAME:
                            event = os.path.basename(os.path.dirname(os.path.dirname(name_path)))
                            return f"/dev/input/{event}"
                except OSError:
                    continue
            time.sleep(0.02)
        return None

    # -- hot path -----------------------------------------------------------

    def apply(self, state: InputState) -> None:
        if self.fd < 0:
            return

        events: list[bytes] = []
        last = self._last_abs

        values = (
            (ABS_X, _axis(state.steering)),
            (ABS_Y, 0),
            (ABS_RX, _axis(state.rs_x)),
            (ABS_RY, _axis(state.rs_y)),
            (ABS_Z, _trigger(state.brake)),
            (ABS_RZ, _trigger(state.throttle)),
        )
        for code, value in values:
            if last.get(code) != value:
                last[code] = value
                events.append(_EVENT.pack(0, 0, EV_ABS, code, value))

        changed = state.buttons ^ self._last_buttons
        if changed:
            for bit, code in BUTTON_CODES.items():
                if changed & bit:
                    events.append(
                        _EVENT.pack(0, 0, EV_KEY, code, 1 if state.buttons & bit else 0)
                    )
            self._last_buttons = state.buttons

        if events:
            events.append(_SYN)
            self._write(b"".join(events))

    def reset(self) -> None:
        if self.fd < 0:
            return
        events: list[bytes] = []
        for code, _, _ in ABS_SPECS:
            if self._last_abs.get(code) != 0:
                self._last_abs[code] = 0
                events.append(_EVENT.pack(0, 0, EV_ABS, code, 0))
        if self._last_buttons:
            for bit, code in BUTTON_CODES.items():
                if self._last_buttons & bit:
                    events.append(_EVENT.pack(0, 0, EV_KEY, code, 0))
            self._last_buttons = 0
        if events:
            events.append(_SYN)
            self._write(b"".join(events))

    def _write(self, payload: bytes) -> None:
        try:
            os.write(self.fd, payload)
            self._write_errors = 0
        except OSError as exc:
            # A transient error should never kill the WebSocket handler.
            self._write_errors += 1
            if self._write_errors == 1:
                print(f"[pad] uinput write failed: {exc}", file=sys.stderr, flush=True)

    def close(self) -> None:
        if self.fd < 0:
            return
        try:
            self.reset()
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        finally:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


class WindowsPad(Pad):
    """Original vgamepad backend (requires ViGEmBus on Windows)."""

    description = "Windows ViGEmBus Xbox 360 pad"

    _ENUM_NAMES = {
        "handbrake": "XUSB_GAMEPAD_B",
        "y_btn": "XUSB_GAMEPAD_Y",
        "x_btn": "XUSB_GAMEPAD_X",
        "a_btn": "XUSB_GAMEPAD_A",
        "lb": "XUSB_GAMEPAD_LEFT_SHOULDER",
        "rb": "XUSB_GAMEPAD_RIGHT_SHOULDER",
        "view_btn": "XUSB_GAMEPAD_BACK",
        "menu_btn": "XUSB_GAMEPAD_START",
        "guide_btn": "XUSB_GAMEPAD_GUIDE",
        "dpad_up": "XUSB_GAMEPAD_DPAD_UP",
        "dpad_down": "XUSB_GAMEPAD_DPAD_DOWN",
        "dpad_left": "XUSB_GAMEPAD_DPAD_LEFT",
        "dpad_right": "XUSB_GAMEPAD_DPAD_RIGHT",
    }

    def __init__(self) -> None:
        try:
            import vgamepad as vg
        except ImportError as exc:
            raise PadError(
                "vgamepad is not installed. On Windows run: pip install -r requirements-windows.txt"
            ) from exc

        try:
            self._pad = vg.VX360Gamepad()
        except Exception as exc:
            raise PadError(
                f"Could not create the virtual Xbox pad (is ViGEmBus installed?): {exc}"
            ) from exc

        self._button_map: dict[int, object] = {}
        for key, enum_name in self._ENUM_NAMES.items():
            value = getattr(vg.XUSB_BUTTON, enum_name, None)
            if value is not None:
                self._button_map[BUTTON_BITS[key]] = value
        self._buttons = 0

    def apply(self, state: InputState) -> None:
        self._pad.left_joystick_float(x_value_float=state.steering, y_value_float=0.0)
        self._pad.right_joystick_float(x_value_float=state.rs_x, y_value_float=state.rs_y)
        self._pad.left_trigger_float(value_float=state.brake)
        self._pad.right_trigger_float(value_float=state.throttle)

        changed = state.buttons ^ self._buttons
        if changed:
            for bit, button in self._button_map.items():
                if changed & bit:
                    if state.buttons & bit:
                        self._pad.press_button(button=button)
                    else:
                        self._pad.release_button(button=button)
            self._buttons = state.buttons

        self._pad.update()

    def reset(self) -> None:
        self._buttons = 0
        self._pad.reset()
        self._pad.update()

    def close(self) -> None:
        self.reset()


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


class MockPad(Pad):
    """No-op backend used by tests and by ``controller doctor``."""

    description = "mock (no virtual device)"

    def __init__(self) -> None:
        self._last_print = 0.0
        self.last_state = InputState()

    def apply(self, state: InputState) -> None:
        self.last_state = state
        now = time.monotonic()
        if now - self._last_print >= 0.05:
            self._last_print = now
            pressed = [
                name for name, bit in BUTTON_BITS.items() if state.buttons & bit
            ]
            print(
                f"[mock] steer={state.steering:+.3f} thr={state.throttle:.3f} "
                f"brk={state.brake:.3f} rs=({state.rs_x:+.2f},{state.rs_y:+.2f}) "
                f"btn={','.join(pressed) or '-'}",
                flush=True,
            )

    def reset(self) -> None:
        self.last_state = InputState()
        print("[mock] reset -> neutral", flush=True)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_pad(kind: str = "auto") -> Pad:
    if kind == "mock":
        return MockPad()

    if kind == "auto":
        if sys.platform.startswith("linux"):
            kind = "linux"
        elif sys.platform == "win32":
            kind = "windows"
        else:
            raise PadError(f"Unsupported platform: {sys.platform}. Use --pad mock to test.")

    if kind == "linux":
        if not sys.platform.startswith("linux"):
            raise PadError("The Linux uinput backend only works on Linux.")
        return LinuxPad()

    if kind == "windows":
        return WindowsPad()

    raise PadError(f"Unknown pad backend: {kind}")


__all__ = [
    "Pad",
    "PadError",
    "LinuxPad",
    "WindowsPad",
    "MockPad",
    "create_pad",
]
