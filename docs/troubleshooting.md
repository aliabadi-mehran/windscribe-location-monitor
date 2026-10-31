# Troubleshooting

## “Windscribe was already connected”

This is a safety stop, not a failure to clean up. The monitor will not disconnect a tunnel it did not create. Disconnect it yourself only if you intend to allow a sweep, then rerun.

## Empty location list

On macOS with the GUI backend, `windscribe-cli locations` intentionally opens the location picker instead of printing names. Export the supported Rename Locations JSON and import it as described in the README. Do not scrape private APIs or guess nicknames.

On Raspberry Pi, run `wireguard-discover`. An empty result means there are no authorized `.conf` files below `wireguard_config_dir`. Download/generate the missing per-location profiles through Windscribe's supported configuration workflow. The monitor cannot turn one profile into another location and will not invent endpoints or keys.

## Location does not exist or is disabled

Refresh/export the Windscribe location list and re-import it. A renamed or retired datacenter may no longer match. The failure is recorded and does not stop the rest of the sweep.

## IKEv2 fails on a restrictive network

The monitor is intentionally testing IKEv2:500 as requested. UDP filtering, NAT behavior, transient route interference, or a location outage can make it fail. A failed raw-port response alone is inconclusive; only the actual tunnel attempt is authoritative. Windscribe recommends trying other protocols on restrictive networks, but this monitor will not silently change the requested protocol.

## Telegram is unreachable

Reports are placed in `~/Library/Application Support/WindscribeMonitor/queue/` with mode 0600. A later successful run tries the oldest queued message first. You can also run `flush-queue` while connectivity is available.

## Telegram channel cannot be discovered

Confirm the bot is a channel administrator, post a new channel message after adding it, then run `telegram-discover` again. Bots cannot read messages that predate their channel membership.

## Keychain prompts or access failure

Run `telegram-test` once interactively from the same macOS login session. If macOS asks whether the process may read the Keychain item, allow it for this local tool. Do not move the token to `config.json` as a workaround.

## Mac slept through a run

This is expected: software cannot execute while the machine is asleep. The LaunchAgent runs after login/wake. `caffeinate` only prevents idle sleep during a run that has already started. Wake scheduling requires an explicit system power-management decision. The Raspberry Pi systemd timer uses `Persistent=true` and catches up after downtime.

## Raspberry Pi helper or permission failure

Run `doctor`. The Pi backend requires `ip`, `wg`, `curl`, `ping`, `sudo`, mode-`600` profiles owned by the monitor user, and the reviewed root-owned helper at `/usr/local/libexec/windscribe-monitor-netns`. Re-run the helper installer only after reviewing it; it refuses to overwrite a different sudoers file.

## A `wsmon-*` namespace remains

Normal signal/finally cleanup removes it. First ensure no monitor process is still active. Then use the installed helper's constrained cleanup action with the exact namespace name. Do not delete host WireGuard interfaces or host routes:

```sh
sudo /usr/local/libexec/windscribe-monitor-netns cleanup --namespace wsmon-1234abcd
```

If cleanup fails, preserve `ip netns list`, the service journal, and monitor logs for diagnosis rather than changing the host network remotely.

## DNS or IPv6 isolation check failed

Each run records the namespace DNS servers, whether the DNS route uses the temporary WireGuard interface, whether a DNS lookup succeeded, and whether IPv4/IPv6 default routes point through the tunnel. A profile without DNS can still use host-resolved HTTPS probe addresses, but the missing DNS diagnostic remains visible. Add only DNS servers supplied by the authorized profile or explicitly configured in the private `wireguard_dns_servers` list. The namespace has no physical uplink, so a missing IPv6 tunnel default route cannot fall back to the host interface.

## A run was interrupted

On macOS, signal handlers and `finally` cleanup disconnect monitor-owned tunnels. Confirm with:

```sh
/usr/local/bin/windscribe-cli status
```

If Windscribe itself is unresponsive, use the app UI; do not force-delete routes or DNS settings. The monitor never edits those directly.

On Raspberry Pi, the signal handler removes only the active `wsmon-*` namespace. Scheduled runs retain `in-progress.json` and resume the same discovered-profile order after a service timeout.

## Logs and evidence

- Human-readable log: `~/Library/Application Support/WindscribeMonitor/logs/monitor.log`
- LaunchAgent stdout/stderr: same `logs/` directory
- Full measurements: `runs/<unix-time>.json`
- Last completed run: `last-run.json`
- Long-term success counts: `history.json`

These local files may include public IP addresses and location names but never Windscribe credentials or the Telegram bot token.
