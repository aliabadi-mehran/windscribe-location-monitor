from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config
from .process import CommandResult, run_command


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class WindscribeStatus:
    raw: str
    connect_state: str
    logged_in: bool
    connected: bool
    disconnected: bool
    pending: bool
    location: str | None
    protocol: str | None
    port: int | None
    firewall: str | None
    public_ip: str | None


def clean_output(result: CommandResult) -> str:
    # JSON-looking diagnostic lines are emitted by current macOS builds on stderr.
    lines = []
    for line in (result.stdout or "").splitlines():
        if line.startswith('{"tm":'):
            continue
        lines.append(ANSI_RE.sub("", line).strip())
    return "\n".join(line for line in lines if line)


def parse_status(raw: str) -> WindscribeStatus:
    def value(label: str) -> str | None:
        match = re.search(rf"^{re.escape(label)}:\s*(.+)$", raw, re.MULTILINE)
        return match.group(1).strip() if match else None

    state = value("Connect state") or ""
    connected = "Connected" in state and "Disconnected" not in state
    pending = state.lstrip().startswith("*")
    location = None
    if connected:
        match = re.search(r"Connected:\s*([^\[]+)", state)
        location = match.group(1).strip(" *") if match else None
    protocol_value = value("Protocol")
    vpn_ip = value("VPN IP")
    protocol = None
    port = None
    if protocol_value:
        match = re.match(r"([^:]+):(\d+)", protocol_value)
        if match:
            protocol, port = match.group(1).strip().lower(), int(match.group(2))
    return WindscribeStatus(
        raw=raw,
        connect_state=state,
        logged_in=(value("Login state") == "Logged in"),
        connected=connected,
        disconnected=(
            state == "Disconnected"
            or (state.startswith("Error:") and not protocol_value and not vpn_ip)
        ),
        pending=pending,
        location=location,
        protocol=protocol,
        port=port,
        firewall=value("Firewall state"),
        public_ip=value("Public IP"),
    )


class Windscribe:
    def __init__(self, config: Config):
        self.config = config

    def command(self, *args: str, timeout: float | None = None) -> CommandResult:
        return run_command(
            [self.config.windscribe_cli, *args],
            timeout=timeout or self.config.command_timeout_seconds,
        )

    def status(self) -> WindscribeStatus:
        result = self.command("status")
        raw = clean_output(result)
        if result.timed_out or result.returncode != 0 or not raw:
            raise RuntimeError(f"Windscribe status failed: {raw or 'no output'}")
        return parse_status(raw)

    def connect(self, location: str) -> CommandResult:
        return self.command(
            "connect", "-n", location, f"{self.config.protocol}:{self.config.port}",
            timeout=self.config.command_timeout_seconds,
        )

    def disconnect(self) -> CommandResult:
        return self.command("disconnect", timeout=self.config.disconnect_timeout_seconds)

