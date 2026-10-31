#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MONITOR="$PROJECT_DIR/bin/windscribe-monitor"
ARCH=$(uname -m)

if [ "$ARCH" != "aarch64" ] && [ "$ARCH" != "arm64" ]; then
  echo "This installer requires a 64-bit ARM Raspberry Pi OS (detected: $ARCH)." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required." >&2
  exit 1
fi

missing=""
for command in ip wg curl ping sudo; do
  command -v "$command" >/dev/null 2>&1 || missing="$missing $command"
done
if [ -n "$missing" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    echo "Installing required networking tools:$missing"
    sudo apt-get update
    sudo apt-get install -y wireguard-tools iproute2 curl iputils-ping sudo
  else
    echo "Missing required commands:$missing" >&2
    exit 1
  fi
fi

MONITOR_HOME=${WSMON_HOME:-"$HOME/.local/state/windscribe-monitor"}
CONFIG_DIR=${WSMON_WIREGUARD_CONFIG_DIR:-"$HOME/.config/windscribe/wireguard"}
mkdir -p "$MONITOR_HOME" "$CONFIG_DIR"
chmod 700 "$MONITOR_HOME" "$CONFIG_DIR"

"$MONITOR" --home "$MONITOR_HOME" configure-raspberry-pi \
  --config-dir "$CONFIG_DIR" --endpoint-port 80

if ! find "$CONFIG_DIR" -type f -name '*.conf' -print -quit | grep -q .; then
  echo
  echo "No authorized Windscribe WireGuard profiles were found in:"
  echo "  $CONFIG_DIR"
  echo "Copy one mode-600 .conf file per location there, then run this same installer again."
  echo "Nothing was scheduled and no active network interface was changed."
  exit 2
fi

echo "Installing the reviewed, root-owned namespace helper..."
sudo "$PROJECT_DIR/scripts/install-wireguard-helper.sh"

"$MONITOR" --home "$MONITOR_HOME" wireguard-discover
"$MONITOR" --home "$MONITOR_HOME" doctor

if ! "$MONITOR" --home "$MONITOR_HOME" telegram-test >/dev/null 2>&1; then
  echo
  echo "Configure the existing Telegram bot and private channel."
  printf 'Existing private channel numeric ID: '
  read -r CHAT_ID
  if [ -z "$CHAT_ID" ]; then
    echo "A channel ID is required; no new bot or channel will be created." >&2
    exit 1
  fi
  "$MONITOR" --home "$MONITOR_HOME" telegram-setup --chat-id "$CHAT_ID"
  "$MONITOR" --home "$MONITOR_HOME" telegram-test
else
  echo "Existing Telegram credentials and channel delivery are working."
fi

echo
echo "The next step creates only isolated temporary namespaces; it does not change the host default route."
printf 'Run the required three-profile proof-of-concept now? [y/N] '
read -r APPROVE_POC
case "$APPROVE_POC" in
  y|Y|yes|YES) ;;
  *)
    echo "Proof-of-concept was not approved. Nothing was scheduled."
    exit 3
    ;;
esac

"$MONITOR" --home "$MONITOR_HOME" run --limit 3

echo
printf 'Enable the independent daily 06:00 systemd timer now? [Y/n] '
read -r ENABLE_TIMER
case "$ENABLE_TIMER" in
  n|N|no|NO)
    echo "Installation validated, but scheduling was left disabled."
    exit 0
    ;;
esac

sudo loginctl enable-linger "$USER"
"$MONITOR" --home "$MONITOR_HOME" install-systemd

echo
echo "Installation complete. ChatGPT/Codex is not used at runtime."
echo "The Pi will run the full sweep daily at 06:00 local time and send the top five to Telegram."
systemctl --user list-timers windscribe-monitor.timer --no-pager
