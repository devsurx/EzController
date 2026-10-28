#!/usr/bin/env python3
"""EzController server.

Serves the phone UI, accepts a WebSocket stream, and drives a virtual Xbox 360
pad.  The hot path is intentionally tiny:

    JSON frame -> normalised state -> one batched uinput write -> SYN_REPORT

No smoothing, interpolation, compression or artificial sleeps are used.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from ezcontroller import __version__
from ezcontroller.pad import Pad, PadError, create_pad
from ezcontroller.protocol import InputState, parse_message

ROOT = Path(__file__).resolve().parent
INDEX_FILE = ROOT / "index.html"
JSON = "application/json; charset=utf-8"
HTML = "text/html; charset=utf-8"
TEXT = "text/plain; charset=utf-8"

WATCHDOG_SECONDS = 1.0


def _lan_ip() -> str:
    """Best-effort LAN address (doesn't send any packets)."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _remote(connection: ServerConnection) -> str:
    address = connection.remote_address
    if not address:
        return "unknown"
    host, port = address[0], address[1]
    return f"{host}:{port}"


class ControllerServer:
    def __init__(self, pad: Pad, host: str, port: int) -> None:
        self.pad = pad
        self.host = host
        self.port = port
        self.active: ServerConnection | None = None
        self.last_message = 0.0
        self.started = time.monotonic()
        self.messages = 0
        self._index_mtime = -1
        self._index_bytes = b""
        self._bad_frames = 0
        self._apply_errors = 0
        self._watchdog = asyncio.create_task(self._watchdog_loop())

    # -- HTTP ---------------------------------------------------------------

    def _index(self) -> bytes:
        try:
            stat = INDEX_FILE.stat()
        except OSError:
            return b"index.html is missing from the EzController directory."
        if stat.st_mtime_ns != self._index_mtime:
            self._index_bytes = INDEX_FILE.read_bytes()
            self._index_mtime = stat.st_mtime_ns
        return self._index_bytes

    def process_request(
        self, connection: ServerConnection, request: Any
    ) -> Response | None:
        path = request.path.split("?", 1)[0].split("#", 1)[0]

        if path in ("/ws", "/ws/"):
            return None  # let websockets complete the handshake

        if path in ("/", "/index.html"):
            body = self._index()
            status = 200 if body.startswith(b"<!") else 500
            headers = Headers(
                {
                    "Content-Type": HTML,
                    "Content-Length": str(len(body)),
                    "Cache-Control": "no-store, max-age=0",
                    "X-Content-Type-Options": "nosniff",
                    "Permissions-Policy": "accelerometer=(self), gyroscope=(self), magnetometer=(self)",
                }
            )
            return Response(status, "OK" if status == 200 else "Error", headers, body)

        if path == "/health":
            payload = {
                "ok": True,
                "version": __version__,
                "pad": self.pad.description,
                "event_node": getattr(self.pad, "event_node", None),
                "clients": 1 if self.active is not None else 0,
                "messages": self.messages,
                "uptime": round(time.monotonic() - self.started, 1),
                "pid": os.getpid(),
            }
            body = json.dumps(payload, separators=(",", ":")).encode()
            headers = Headers(
                {
                    "Content-Type": JSON,
                    "Content-Length": str(len(body)),
                    "Cache-Control": "no-store, max-age=0",
                }
            )
            return Response(200, "OK", headers, body)

        if path == "/favicon.ico":
            return Response(204, "No Content", Headers(), b"")

        body = b"EzController: not found\n"
        headers = Headers(
            {"Content-Type": TEXT, "Content-Length": str(len(body)), "Connection": "close"}
        )
        return Response(404, "Not Found", headers, body)

    # -- WebSocket ----------------------------------------------------------

    async def handler(self, connection: ServerConnection) -> None:
        # Explicitly disable Nagle. websockets normally does this too, but being
        # explicit keeps the low-latency guarantee on every transport.
        transport = getattr(connection, "transport", None)
        if transport is not None:
            sock = transport.get_extra_info("socket")
            if sock is not None:
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass

        previous = self.active
        if previous is not None and previous is not connection:
            # A reconnecting phone always wins. Drop stale controls first so the
            # old connection can't leave the throttle pinned.
            self.pad.reset()
        self.active = connection
        self.last_message = time.monotonic()
        print(f"[+] controller connected from {_remote(connection)}", flush=True)

        if previous is not None and previous is not connection:
            try:
                await previous.close(4001, "replaced by a newer controller")
            except Exception:
                pass

        try:
            async for raw in connection:
                if self.active is not connection:
                    break
                try:
                    state = parse_message(raw)
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    self._bad_frames += 1
                    continue

                self.last_message = time.monotonic()
                self.messages += 1
                try:
                    self.pad.apply(state)
                except Exception as exc:  # never let one bad frame kill the stream
                    self._apply_errors += 1
                    if self._apply_errors <= 3:
                        print(f"[!] pad update failed: {exc}", file=sys.stderr, flush=True)
        except ConnectionClosed:
            pass
        except Exception as exc:
            print(f"[!] connection error: {exc}", file=sys.stderr, flush=True)
        finally:
            if self.active is connection:
                self.active = None
                self.last_message = 0.0
                self.pad.reset()
                print(f"[-] controller disconnected ({_remote(connection)})", flush=True)

    async def _watchdog_loop(self) -> None:
        """Neutralise controls if the phone stops sending (screen lock, crash)."""

        while True:
            await asyncio.sleep(0.2)
            if (
                self.active is not None
                and self.last_message
                and time.monotonic() - self.last_message > WATCHDOG_SECONDS
            ):
                self.last_message = 0.0
                self.pad.reset()
                print("[!] no data for 1s - controls reset to neutral", flush=True)

    def close(self) -> None:
        self._watchdog.cancel()
        try:
            self.pad.close()
        except Exception:
            pass


async def run(args: argparse.Namespace) -> int:
    try:
        pad = create_pad(args.pad)
    except PadError as exc:
        print(f"[!] {exc}", file=sys.stderr, flush=True)
        return 2

    server = ControllerServer(pad, args.host, args.port)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    pidfile: Path | None = None
    if args.pidfile:
        pidfile = Path(args.pidfile).expanduser()
        try:
            pidfile.parent.mkdir(parents=True, exist_ok=True)
            pidfile.write_text(str(os.getpid()), encoding="ascii")
        except OSError as exc:
            print(f"[!] cannot write pidfile {pidfile}: {exc}", file=sys.stderr, flush=True)
            pidfile = None

    lan = _lan_ip() if args.host in ("0.0.0.0", "::", "") else "127.0.0.1"
    scheme = "http"
    print("=" * 64, flush=True)
    print(f"  EzController v{__version__}  -  low-latency virtual Xbox 360 pad", flush=True)
    print("=" * 64, flush=True)
    print(f"  Pad    : {pad.description}", flush=True)
    print(f"  Local  : {scheme}://127.0.0.1:{args.port}", flush=True)
    if lan != "127.0.0.1":
        print(f"  Phone  : {scheme}://{lan}:{args.port}", flush=True)
    print("  Tunnel : run `controller tunnel` for HTTPS (mobile gyro)", flush=True)
    print("  Mode   : TCP_NODELAY - no compression - 1-frame queue", flush=True)
    print("=" * 64, flush=True)

    try:
        async with serve(
            server.handler,
            args.host,
            args.port,
            process_request=server.process_request,
            compression=None,
            max_queue=1,
            write_limit=0,
            ping_interval=20,
            ping_timeout=20,
            max_size=64 * 1024,
            reuse_address=True,
            server_header="EzController",
        ):
            print("[*] ready - waiting for controller...", flush=True)
            await stop.wait()
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"[!] port {args.port} is already in use. "
                "Run `controller status` / `controller stop`.",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(f"[!] server failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        server.close()
        if pidfile is not None:
            try:
                if pidfile.read_text(encoding="ascii").strip() == str(os.getpid()):
                    pidfile.unlink()
            except OSError:
                pass
        print("[*] shutdown complete", flush=True)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="Low-latency virtual Xbox 360 controller server.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("EZC_HOST", "0.0.0.0"),
        help="bind address (default: 0.0.0.0, env EZC_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("EZC_PORT", "5000")),
        help="TCP port (default: 5000, env EZC_PORT)",
    )
    parser.add_argument(
        "--pad",
        choices=("auto", "linux", "windows", "mock"),
        default=os.environ.get("EZC_PAD", "auto"),
        help="virtual pad backend (default: auto, env EZC_PAD)",
    )
    parser.add_argument("--pidfile", help="write the server PID to this file")
    parser.add_argument("--version", action="version", version=f"EzController {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
