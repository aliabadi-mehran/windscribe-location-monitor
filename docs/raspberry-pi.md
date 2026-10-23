# Raspberry Pi 4 deployment

## Supported baseline

- Raspberry Pi 4 with a 64-bit Raspberry Pi OS / Debian-compatible userspace (`aarch64`).
- Python 3.9 or newer.
- Windscribe's official Linux CLI-only ARM64 package.
- A persistent user session through `loginctl enable-linger` so the user-level Windscribe service and timer can run after logout.

Windscribe officially publishes an ARM64 Debian/Ubuntu CLI-only installer. However, its official CLI protocol table marks IKEv2 as Windows/macOS-only. The Pi deployment must therefore use a supported Linux protocol. The monitor defaults to `wireguard:443` on Linux; `udp`, `tcp`, `stealth`, or `wstunnel` can instead be selected in the private `config.json` after confirming supported ports with `windscribe-cli ports <protocol>`.

Do not claim Pi results are IKEv2 measurements. The report header uses the configured protocol.

## Installation

Use 64-bit Raspberry Pi OS. Clone the private repository, then install the current official Windscribe ARM64 CLI-only package from:

```text
https://windscribe.com/install/desktop/linux_deb_arm64_cli
```

Install the downloaded Debian package with `sudo apt install ./windscribe-cli_*.deb`, then run:

```sh
./scripts/install-raspberry-pi.sh
windscribe-cli login
./bin/windscribe-monitor telegram-setup --chat-id YOUR_PRIVATE_CHANNEL_ID
./bin/windscribe-monitor doctor
```

The Telegram bot token is entered through a hidden prompt and stored outside the repository at `~/.local/state/windscribe-monitor/secrets/telegram-bot-token`, mode `600`. Never paste it into Git, a command-line argument, or a systemd unit.

## Safe proof-of-concept

Confirm Windscribe is disconnected, then explicitly approve the network disruption and run:

```sh
./bin/windscribe-monitor run --limit 3
```

After confirming the Telegram report and a final disconnected state, enable the schedule:

```sh
sudo loginctl enable-linger "$USER"
./bin/windscribe-monitor install-systemd
systemctl --user list-timers windscribe-monitor.timer
```

The timer runs at 03:00, 09:00, 15:00, and 21:00 in the Pi's local timezone and catches up after downtime. The per-run lock prevents overlap. Set the Pi's timezone before enabling the timer if those hours must correspond to a specific region.

## Operations

```sh
./bin/windscribe-monitor status
journalctl --user -u windscribe-monitor.service -n 100 --no-pager
systemctl --user start windscribe-monitor.service
./bin/windscribe-monitor disable
./bin/windscribe-monitor uninstall
```

The official Windscribe client remains responsible for routes and DNS. The monitor refuses to start when it detects an existing VPN tunnel, disconnects only tunnels it owns, and verifies a stable disconnected state during cleanup.
