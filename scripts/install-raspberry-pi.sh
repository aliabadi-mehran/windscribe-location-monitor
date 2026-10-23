#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ARCH=$(uname -m)

if [ "$ARCH" != "aarch64" ] && [ "$ARCH" != "arm64" ]; then
  echo "This installer requires a 64-bit ARM Raspberry Pi OS (detected: $ARCH)." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it with: sudo apt install python3" >&2
  exit 1
fi

if ! command -v windscribe-cli >/dev/null 2>&1; then
  echo "Windscribe CLI is not installed. Use the official ARM64 CLI-only package:" >&2
  echo "https://windscribe.com/install/desktop/linux_deb_arm64_cli" >&2
  exit 1
fi

MONITOR_HOME=${WSMON_HOME:-"$HOME/.local/state/windscribe-monitor"}
mkdir -p "$MONITOR_HOME"
chmod 700 "$MONITOR_HOME"

if [ ! -f "$MONITOR_HOME/locations.txt" ]; then
  cp "$PROJECT_DIR/config/locations.full.txt" "$MONITOR_HOME/locations.txt"
  chmod 600 "$MONITOR_HOME/locations.txt"
fi

echo "Checking Windscribe and monitor configuration..."
"$PROJECT_DIR/bin/windscribe-monitor" --home "$MONITOR_HOME" doctor || true
echo
echo "Next, run these interactive commands (credentials remain on the Pi):"
echo "  windscribe-cli login"
echo "  $PROJECT_DIR/bin/windscribe-monitor --home '$MONITOR_HOME' telegram-setup --chat-id YOUR_CHANNEL_ID"
echo "  $PROJECT_DIR/bin/windscribe-monitor --home '$MONITOR_HOME' doctor"
echo "  $PROJECT_DIR/bin/windscribe-monitor --home '$MONITOR_HOME' run --limit 3"
echo "  $PROJECT_DIR/bin/windscribe-monitor --home '$MONITOR_HOME' install-systemd"
echo
echo "For runs after logout/reboot, enable user lingering once:"
echo "  sudo loginctl enable-linger '$USER'"

