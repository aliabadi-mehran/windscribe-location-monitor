# Windscribe Location Monitor for macOS and Raspberry Pi

A local, standard-library-only Python monitor for testing Windscribe locations through real VPN tunnels, ranking the working locations, and notifying a private Telegram channel with an English HTML report. macOS continues to use the official Windscribe client with IKEv2. Raspberry Pi/Linux uses authorized WireGuard configuration files inside isolated network namespaces.

The scheduler is intentionally **not enabled by installation alone**. First import the official location names, configure Telegram, run a limited proof-of-concept, confirm the message, and only then enable `launchd` (macOS) or a user `systemd` timer (Linux).

## Raspberry Pi 4

Raspberry Pi tests do not call `windscribe-cli connect` and never replace the host default route. Each existing Windscribe WireGuard `.conf` file is brought up in a short-lived network namespace. The WireGuard interface is created in the host namespace and moved before it is configured, so its UDP socket retains host reachability while all test traffic, DNS routes, IPv4/IPv6 routes, and the tunnel default route stay inside the namespace. The host SSH route and any existing host WireGuard interface are left untouched.

The Raspberry Pi installer sets `wireguard_endpoint_port_override` to UDP `80`, while retaining every authorized profile's endpoint host, public key, and private key. Set the value to `0` in the private config to use each profile's original port instead.

See [Raspberry Pi deployment](docs/raspberry-pi.md) for installation, private token storage, proof-of-concept, systemd scheduling, and operations.

Quick Raspberry Pi deployment after authorized profiles are in `~/.config/windscribe/wireguard`:

```sh
git clone https://github.com/aliabadi-mehran/windscribe-location-monitor.git && cd windscribe-location-monitor && ./scripts/install-raspberry-pi.sh
```

The installer is interactive, reuses the existing Telegram bot/channel, requires an isolated three-profile proof-of-concept, and then enables a local daily 06:00 `systemd` timer. ChatGPT/Codex is not involved at runtime.

## Supported environment

- A current supported macOS release with Windscribe's official desktop app and CLI.
- A 64-bit Debian-compatible Linux ARM64 system with `wireguard-tools`, `iproute2`, `curl`, and authorized Windscribe WireGuard profiles.
- Python 3.9 or newer.
- An active Windscribe account; credentials remain entirely inside the official client.

Windscribe's current macOS app ships a supported CLI. The monitor uses only its documented `status`, `connect`, `disconnect`, and `ports` commands. It does not automate clicks, call private Windscribe APIs, or handle Windscribe credentials.

One macOS-specific limitation is important: when the GUI app is running, `windscribe-cli locations` opens the GUI location picker and returns no terminal list. Windscribe documents this behavior in its [official CLI integration instructions](https://github.com/Windscribe/Desktop-App/blob/master/skills/windscribe-cli/SKILL.md). The location inventory therefore comes from Windscribe's supported **Rename Locations → Export** function or a reviewed one-name-per-line file; the monitor never invents location names.

## Safety model

- A run aborts if Windscribe was already connected. It never disconnects a pre-existing user tunnel.
- A run disconnects every tunnel attempt it creates, including timed-out and interrupted attempts.
- Connection, status, ping, HTTP, and speed operations have finite timeouts.
- A non-blocking file lock prevents overlapping runs.
- Original macOS DNS/routes are left to the official Windscribe client, which restores them on disconnect.
- Raspberry Pi namespaces contain only loopback and the temporary WireGuard interface. Cleanup deletes the namespace and its private resolver file; no host default route, DNS file, or existing interface is modified.
- The monitor does not toggle the Windscribe firewall, network rules, split tunneling, macOS security settings, or Wi-Fi.
- Speed tests run only on the best ten functional locations and read at most 5 MB each by default.
- Telegram tokens are read from macOS Keychain or a mode-`600` Linux secret file outside the repository; they are never stored in source files, logs, or Git.
- Undeliverable Telegram messages are queued as private local files and retried after later successful runs.

## Location inventory

On Raspberry Pi, the inventory is exactly the set of mode-`600` `.conf` files below `wireguard_config_dir` (default `~/.config/windscribe/wireguard`). Subdirectories are supported and relative filenames become report labels. The program neither downloads profiles through an undocumented API nor derives missing endpoints or credentials. Run `wireguard-discover` to review the available authorized configurations. If a location is absent, generate/download its individual WireGuard profile through Windscribe's supported account/configuration workflow and add it privately on the Pi.

The macOS inventory workflow remains unchanged:

Preferred supported workflow:

1. In Windscribe, open **Preferences → Look & Feel → Rename Locations**.
2. Click **Export** and save the JSON file locally.
3. Import it:

   ```sh
   ./bin/windscribe-monitor import-locations /path/to/export.json
   ```

The importer extracts datacenter nicknames. Review the resulting private file at:

```text
~/Library/Application Support/WindscribeMonitor/locations.txt
```

Alternatively, import a UTF-8 text file containing one official datacenter nickname per line. An optional manual-config hostname can follow a pipe (`Nickname | hostname`). Hostnames must come from Windscribe's official IKEv2 config generator; never guess them.

The official CLI does not expose endpoint hostnames on macOS. Consequently, raw UDP 500/4500 reachability cannot be honestly determined for inventory entries that have only a nickname. The monitor records that diagnostic as unavailable and separately performs the decisive test: an authenticated IKEv2 tunnel through the official app. Windscribe documents that IKEv2 starts on UDP 500 and uses UDP 4500 for NAT traversal in its [IKEv2 guide](https://windscribe.com/knowledge-base/articles/what-is-the-ikev2-protocol).

## Telegram setup

1. In Telegram, create a new **private channel**.
2. Open `@BotFather`, send `/newbot`, and follow its prompts.
3. Copy the token. Do not paste it into a shell command, file, or chat transcript.
4. Add the bot to the private channel as an administrator with permission to **Post Messages** only.
5. Store and validate the token through the hidden prompt:

   ```sh
   ./bin/windscribe-monitor telegram-setup
   ```

6. Post one ordinary message in the private channel.
7. Discover and save the numeric private-channel ID without exposing the token:

   ```sh
   ./bin/windscribe-monitor telegram-discover
   ```

8. Validate actual delivery:

   ```sh
   ./bin/windscribe-monitor telegram-test
   ```

The channel ID is not a credential. On macOS the bot token remains in the login Keychain item `com.local.windscribe-monitor.telegram` / `bot-token`; on Linux it is stored in a private file outside the repository.

## Proof-of-concept and full run

Run the doctor first:

```sh
./bin/windscribe-monitor doctor
```

The first live run deliberately tests only three imported macOS locations or three discovered Raspberry Pi WireGuard profiles:

```sh
./bin/windscribe-monitor run --limit 3
```

To test specific imported nicknames without changing the inventory:

```sh
./bin/windscribe-monitor run --locations Lygos Ghvino Khalifa
```

On macOS this command changes the app-managed VPN connection and therefore refuses to run over an existing tunnel. On Raspberry Pi it creates only isolated namespaces and does not disconnect or reconfigure an existing host tunnel. The limited proof-of-concept must still be reviewed before a full sweep.

After reviewing the JSON evidence under `~/Library/Application Support/WindscribeMonitor/runs/` and confirming the Telegram message, run the full sweep manually:

```sh
./bin/windscribe-monitor run
```

No unmeasured number is substituted. Missing ping or speed values appear as `نامشخص` in Telegram.

## Scheduling

Enable the LaunchAgent only after the limited and full manual validations pass:

```sh
./bin/windscribe-monitor install-launchd
```

It runs at fixed local times every six hours, anchored to the original 15:00 start: 03:00, 09:00, 15:00, and 21:00. The file lock prevents overlap. `caffeinate -i` prevents idle system sleep while a run is already executing; it cannot wake a Mac that is asleep.

When `scheduled_locations` is empty in the private `config.json`, scheduled runs test the complete imported inventory. The bundled inventory contains 200 location names from the reviewed export used during development. Setting explicit names remains available for limited runs.

`launchd` timers do not guarantee execution at the exact missed time while the Mac sleeps. On wake/login, the agent runs and the due-time guard provides a safe catch-up. A guaranteed wake would require an explicit power-management schedule and should not be installed silently.

On Raspberry Pi/Linux, install the reviewed root-owned namespace helper and enable the persistent user timer only after the proof-of-concept:

```sh
sudo loginctl enable-linger "$USER"
./bin/windscribe-monitor install-systemd
```

The Raspberry Pi timer is a local `systemd` timer and has no ChatGPT/Codex dependency. It runs once per day at 06:00 in the server's local timezone. `Persistent=true` catches up after a reboot or outage. A file lock prevents overlap; an interrupted full sweep keeps a private checkpoint and the next scheduled run resumes it.

## Operations

```sh
# Manual full test
./bin/windscribe-monitor run

# Compact last-run status
./bin/windscribe-monitor status

# Recent monitor logs
./bin/windscribe-monitor logs --lines 100

# Retry queued Telegram messages
./bin/windscribe-monitor flush-queue

# macOS only: retry queued Telegram messages through a known working app location
./bin/windscribe-monitor flush-via-vpn --location Motlawa

# Disable scheduled execution, preserving data/configuration
./bin/windscribe-monitor disable

# Remove the LaunchAgent, preserving data and the Keychain token
./bin/windscribe-monitor uninstall
```

## Ranking

On Raspberry Pi, a location is functional only after a fresh `wg` handshake timestamp, non-zero bidirectional WireGuard transfer counters, HTTPS connectivity, a tunnel public IP distinct from the host baseline when available, and verified namespace route isolation. Candidates are ordered by historical connection reliability, then packet loss, latency, and capped download throughput. All profiles receive connectivity tests; only the best ten receive speed tests, and the best five are reported.

Measurements are deliberately described precisely:

- “Latency” and packet loss are ICMP measurements to the configured public target *through the tunnel*, not fabricated server-ping values.
- “Throughput” is the observed rate for a small capped HTTPS download, not an estimate of maximum line speed.
- A successful CLI tunnel plus HTTP/IP verification is distinct from a raw UDP port diagnostic.

## Portability

The ranking, state, Telegram, and report formats remain shared. macOS keeps the existing official-client backend; Linux selects the isolated WireGuard-profile backend. Telegram credentials, WireGuard profiles, history, queue, and run records remain outside Git.

See [Troubleshooting](docs/troubleshooting.md) for failure handling and recovery details.

