#!/usr/bin/env bash
# Install the `controller` command without root.
# The one-time /dev/uinput permission step is `controller setup`.

set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$BIN_DIR"
chmod +x "$REPO_DIR/controller" "$REPO_DIR/server.py" 2>/dev/null || true
ln -sfn "$REPO_DIR/controller" "$BIN_DIR/controller"

# The server needs websockets >= 14.  Use the system copy when it's new
# enough; otherwise create a private virtualenv next to the checkout.
if ! python3 -c 'import websockets; raise SystemExit(0 if int(websockets.__version__.split(".")[0]) >= 14 else 1)' 2>/dev/null; then
    printf '[*] Installing Python dependencies in %s/.venv ...\n' "$REPO_DIR"
    if python3 -m venv "$REPO_DIR/.venv" 2>/dev/null && \
       "$REPO_DIR/.venv/bin/pip" install -q -r "$REPO_DIR/requirements.txt"; then
        printf '[+] .venv ready (controller uses it automatically)\n'
    else
        printf '[!] Could not create .venv.\n'
        printf '    Install python3-venv, then run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n'
    fi
fi

printf '\n[+] Installed: %s/controller -> %s/controller\n' "$BIN_DIR" "$REPO_DIR"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        printf '[!] %s is not in PATH.\n' "$BIN_DIR"
        printf '    Add this to ~/.zshrc or ~/.bashrc:\n'
        printf '    export PATH="$HOME/.local/bin:$PATH"\n'
        ;;
esac

printf '\nNext step (one-time, asks for sudo):\n'
printf '    controller setup\n\n'
printf 'Then launch in the background:\n'
printf '    controller\n\n'
printf 'For mobile gyro over HTTPS:\n'
printf '    controller tunnel install   # downloads cloudflared, no root\n'
printf '    controller up\n\n'
