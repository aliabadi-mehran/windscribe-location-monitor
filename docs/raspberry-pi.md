# Raspberry Pi 4 WireGuard deployment

## Safety architecture

The Raspberry Pi backend never calls `windscribe-cli connect`, never disconnects an existing host tunnel, and never replaces the host default route. For each authorized Windscribe WireGuard profile it:

1. creates a uniquely named network namespace;
2. creates a WireGuard interface in the initial namespace, then moves it into the test namespace before configuration (the WireGuard UDP socket retains initial-namespace endpoint reachability);
3. applies addresses, allowed routes, and DNS only inside that namespace;
4. verifies the namespace contains no physical/uplink interface;
5. requires a fresh handshake, bidirectional transfer, HTTPS connectivity, and a changed public IP;
6. measures ping and loss inside the namespace;
7. deletes the entire namespace and its private `/etc/netns/<name>/resolv.conf` on success, failure, timeout, or signal.

The best ten functional profiles are re-created one at a time for capped download tests. The host's SSH route, main interface, `/etc/resolv.conf`, and any existing WireGuard interface are not modified.

## Requirements

- Raspberry Pi OS 64-bit or another Debian-compatible ARM64 system.
- Python 3.9 or newer.
- `wireguard-tools`, `iproute2`, `curl`, `iputils-ping`, `sudo`, and `systemd`.
- One authorized Windscribe WireGuard `.conf` file per location you want to test.
- The existing Telegram bot token and private-channel ID.

Install OS packages:

```sh
sudo apt update
sudo apt install wireguard-tools iproute2 curl iputils-ping sudo
```

## Existing profiles and discovery

The monitor does not overwrite the Pi's working WireGuard setup. Put copies or additional downloaded profiles in a dedicated private directory; the default is:

```text
~/.config/windscribe/wireguard/
```

Every profile must be owned by the monitor user, be a regular non-symlink file, and have mode `600`:

```sh
chmod 700 ~/.config/windscribe/wireguard
chmod 600 ~/.config/windscribe/wireguard/*.conf
```

Subdirectories are allowed. The relative path without `.conf` is used as the location label. The scanner reads only files that already exist; it does not call undocumented Windscribe APIs, scrape credentials, invent endpoints, or assume one profile can switch locations. If a subscription location has no individual profile, generate/download that location's WireGuard configuration through Windscribe's supported account/config generator and add it privately.

For the full country/city/server heading in Telegram, either organize profiles as `Country/City/Server.conf` or create a private `wireguard-locations.json` beside the profiles:

```json
{
  "profiles": {
    "Poland/Gdansk/Motlawa.conf": {
      "country": "Poland",
      "city": "Gdansk",
      "server": "Motlawa"
    }
  }
}
```

This metadata contains no key and stays outside the repository. Missing fields are not guessed; the exact profile path/name is used instead.

Set a non-default directory in the private `~/.local/state/windscribe-monitor/config.json`:

```json
{
  "linux_backend": "wireguard-configs",
  "wireguard_config_dir": "/home/USER/private-windscribe-configs",
  "wireguard_helper": "/usr/local/libexec/windscribe-monitor-netns",
  "wireguard_endpoint_port_override": 80,
  "top_size": 5,
  "interval_seconds": 86400
}
```

Do not commit this file or any WireGuard profile. Discover the available authorized locations locally:

```sh
./bin/windscribe-monitor wireguard-discover
```

## Installation

After placing the profiles in the private directory, the complete interactive deployment is one command from an existing clone:

```sh
./scripts/install-raspberry-pi.sh
```

For a first clone from GitHub, the equivalent one-liner is:

```sh
git clone https://github.com/aliabadi-mehran/windscribe-location-monitor.git && cd windscribe-location-monitor && ./scripts/install-raspberry-pi.sh
```

The installer checks packages, preserves/updates the private configuration, installs the constrained helper after a normal `sudo` prompt, reuses the existing Telegram bot/channel, requires the three-profile proof-of-concept, and only then offers to enable the daily timer. Re-running it is safe: existing profiles and Telegram secrets are not overwritten.

Review `scripts/windscribe-monitor-netns` and `scripts/install-wireguard-helper.sh`, then explicitly install the root-owned helper:

```sh
sudo ./scripts/install-wireguard-helper.sh
```

The installer copies the helper to `/usr/local/libexec/windscribe-monitor-netns` as root and creates a user-specific sudoers rule for that one helper. It refuses to overwrite a different existing sudoers file. The helper accepts only validated `wsmon-*` namespaces and four fixed actions: setup, probe, speed, and cleanup. It never prints private keys.

Reuse the existing Telegram bot and channel. If its token is not already present on the Pi, enter the same token through the hidden prompt and supply the existing numeric channel ID:

```sh
./bin/windscribe-monitor telegram-setup --chat-id YOUR_EXISTING_PRIVATE_CHANNEL_ID
./bin/windscribe-monitor telegram-test
./bin/windscribe-monitor doctor
```

The token is stored outside the repository at `~/.local/state/windscribe-monitor/secrets/telegram-bot-token`, mode `600`.

## Limited proof-of-concept

The proof-of-concept tests the first three discovered profiles and sends the normal English HTML Telegram report:

```sh
./bin/windscribe-monitor run --limit 3
```

Review `last-run.json` and confirm for every successful profile:

- `fresh_handshake` is true;
- `data_transfer_verified` is true and RX/TX counters are non-zero;
- `public_ip_changed` is true when a host baseline was available;
- `isolation.host_uplink_absent` and `isolation.ipv4_default_via_tunnel` are true;
- DNS servers, DNS route, IPv6 default-route state, latency, loss, and errors are recorded rather than invented.

Also verify that SSH stayed connected and the host route is unchanged:

```sh
ip route show default
ip -6 route show default
sudo ip netns list
```

No `wsmon-*` namespace should remain after the command. Do not run a complete sweep remotely until this limited test has been reviewed.

## Full sweep and ranking

```sh
./bin/windscribe-monitor run
```

All discovered profiles receive genuine tunnel tests with limited retries and timeouts. Only the best ten functional profiles receive a capped download (5 MB by default). Ranking is reliability first, then packet loss, latency, and throughput; the best five are sent to the existing Telegram channel. The listener port override defaults to WireGuard UDP `80`; set `wireguard_endpoint_port_override` to `0` to preserve ports embedded in profiles.

## Daily 06:00 scheduling

After the proof-of-concept and full manual run succeed:

```sh
sudo loginctl enable-linger "$USER"
./bin/windscribe-monitor install-systemd
systemctl --user list-timers windscribe-monitor.timer
```

The timer is provided by the Pi's own `systemd`; it does not call ChatGPT, Codex, or any cloud scheduler. It runs once per day at 06:00 in the Pi's local timezone and uses `Persistent=true`. A lock prevents overlap. If a run is interrupted, signal cleanup removes the active namespace, the checkpoint remains private, and the next scheduled run resumes it.

## Operations and rollback

```sh
./bin/windscribe-monitor status
./bin/windscribe-monitor logs --lines 100
journalctl --user -u windscribe-monitor.service -n 100 --no-pager
./bin/windscribe-monitor flush-queue
./bin/windscribe-monitor disable
./bin/windscribe-monitor uninstall
```

Disabling/uninstalling the scheduler preserves profiles, Telegram credentials, and run history. To remove the privileged helper later, first disable the timer, verify no run is active, then manually remove the root-owned helper and its exact sudoers entry. This is intentionally not automated as part of ordinary monitor uninstall.
