# Windscribe Location Monitor for macOS and Raspberry Pi

A local, standard-library-only Python monitor for testing Windscribe locations through real VPN tunnels, ranking the working locations, and notifying a private Telegram channel in Persian. macOS uses IKEv2; Linux ARM64 uses a protocol supported by Windscribe's official headless client.

The scheduler is intentionally **not enabled by installation alone**. First import the official location names, configure Telegram, run a limited proof-of-concept, confirm the message, and only then enable `launchd` (macOS) or a user `systemd` timer (Linux).

## Raspberry Pi 4

Windscribe publishes an official CLI-only Debian/Ubuntu ARM64 package suitable for a 64-bit Raspberry Pi OS. Its official protocol table says IKEv2 is Windows/macOS-only, so the Pi cannot reproduce IKEv2/UDP 500 tests. On Linux the monitor defaults to `wireguard:443`; select another officially supported Linux protocol only after checking it with `windscribe-cli ports <protocol>`.

See [Raspberry Pi deployment](docs/raspberry-pi.md) for installation, private token storage, proof-of-concept, systemd scheduling, and operations.

## Supported environment

- A current supported macOS release with Windscribe's official desktop app and CLI.
- A 64-bit Debian-compatible Linux ARM64 system with Windscribe's official headless CLI.
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
- The monitor does not toggle the Windscribe firewall, network rules, split tunneling, macOS security settings, or Wi-Fi.
- Speed tests run only on the best ten functional locations and read at most 5 MB each by default.
- Telegram tokens are read from macOS Keychain or a mode-`600` Linux secret file outside the repository; they are never stored in source files, logs, or Git.
- Undeliverable Telegram messages are queued as private local files and retried after later successful runs.

## Location inventory

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

The first live run deliberately tests only three imported locations:

```sh
./bin/windscribe-monitor run --limit 3
```

To test specific imported nicknames without changing the inventory:

```sh
./bin/windscribe-monitor run --locations Lygos Ghvino Khalifa
```

This command changes VPN connections and should be run only after confirming that no intentional VPN session is active. The program itself performs the same check and aborts if it finds one.

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

On Raspberry Pi/Linux, enable the equivalent persistent user timer after the proof-of-concept:

```sh
sudo loginctl enable-linger "$USER"
./bin/windscribe-monitor install-systemd
```

The timer uses the same 03:00, 09:00, 15:00, and 21:00 schedule in the server's local timezone. `Persistent=true` catches up after a reboot or outage.

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

# Retry queued Telegram messages through a known working location
./bin/windscribe-monitor flush-via-vpn --location Motlawa

# Disable scheduled execution, preserving data/configuration
./bin/windscribe-monitor disable

# Remove the LaunchAgent, preserving data and the Keychain token
./bin/windscribe-monitor uninstall
```

## Ranking

Only endpoints with a settled CLI-confirmed tunnel using the configured protocol, a working HTTP request, and (when available) a changed public IP are considered functional. Candidates are ordered by historical connection reliability, then packet loss, latency, and capped download throughput. The best ten receive speed tests; the best three are reported.

Measurements are deliberately described precisely:

- “Latency” and packet loss are ICMP measurements to the configured public target *through the tunnel*, not fabricated server-ping values.
- “Throughput” is the observed rate for a small capped HTTPS download, not an estimate of maximum line speed.
- A successful CLI tunnel plus HTTP/IP verification is distinct from a raw UDP port diagnostic.

## Portability

The measurement, ranking, state, and Telegram modules are shared across macOS and Linux. The scheduler and secret storage select the supported platform implementation. `locations.txt`, private configuration, history, queue, run records, and report logic remain portable.

See [Troubleshooting](docs/troubleshooting.md) for failure handling and recovery details.

