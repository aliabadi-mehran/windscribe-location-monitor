#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer explicitly with sudo." >&2
  exit 1
fi

CALLING_USER=${SUDO_USER:-}
if [ -z "$CALLING_USER" ] || [ "$CALLING_USER" = root ]; then
  echo "Run with sudo from the unprivileged account that will run the monitor." >&2
  exit 1
fi

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SOURCE="$PROJECT_DIR/scripts/windscribe-monitor-netns"
TARGET=/usr/local/libexec/windscribe-monitor-netns
SUDOERS=/etc/sudoers.d/windscribe-monitor-netns
EXPECTED="$CALLING_USER ALL=(root) NOPASSWD: $TARGET *"

if [ -e "$SUDOERS" ] && ! grep -Fqx "$EXPECTED" "$SUDOERS"; then
  echo "$SUDOERS already exists with different content; refusing to overwrite it." >&2
  exit 1
fi

install -d -m 0755 /usr/local/libexec
install -o root -g wheel -m 0755 "$SOURCE" "$TARGET" 2>/dev/null || \
  install -o root -g root -m 0755 "$SOURCE" "$TARGET"

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT HUP INT TERM
printf '%s\n' "$EXPECTED" > "$TMP"
chmod 0440 "$TMP"
visudo -cf "$TMP"
install -o root -g root -m 0440 "$TMP" "$SUDOERS"

echo "Installed the root-owned namespace helper for $CALLING_USER."
echo "No Windscribe profile, host route, or active interface was changed."
