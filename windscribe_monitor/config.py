from __future__ import annotations

import json
import os
import platform
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


APP_NAME = "WindscribeMonitor"


def default_home() -> Path:
    override = os.environ.get("WSMON_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    state_home = os.environ.get("XDG_STATE_HOME")
    return (Path(state_home) if state_home else Path.home() / ".local" / "state") / "windscribe-monitor"


def default_windscribe_cli() -> str:
    found = shutil.which("windscribe-cli")
    if found:
        return found
    if platform.system() == "Darwin":
        return "/usr/local/bin/windscribe-cli"
    return "/usr/bin/windscribe-cli"


def default_protocol() -> str:
    return "ikev2" if platform.system() == "Darwin" else "wireguard"


def default_port() -> int:
    return 500 if platform.system() == "Darwin" else 443


def default_timezone() -> str:
    configured = os.environ.get("TZ", "").strip()
    if configured:
        return configured
    try:
        resolved = Path("/etc/localtime").resolve()
        parts = resolved.parts
        if "zoneinfo" in parts:
            return "/".join(parts[parts.index("zoneinfo") + 1:])
    except OSError:
        pass
    return "UTC"


@dataclass
class Config:
    windscribe_cli: str = field(default_factory=default_windscribe_cli)
    protocol: str = field(default_factory=default_protocol)
    port: int = field(default_factory=default_port)
    connect_timeout_seconds: int = 25
    disconnect_timeout_seconds: int = 15
    command_timeout_seconds: int = 12
    settle_seconds: float = 2.0
    retries: int = 2
    ping_count: int = 5
    ping_timeout_seconds: int = 12
    ping_target: str = "1.1.1.1"
    http_timeout_seconds: int = 12
    speed_test_bytes: int = 5_000_000
    speed_test_url: str = "https://speed.cloudflare.com/__down?bytes={bytes}"
    public_ip_url: str = "https://api.ipify.org"
    connectivity_url: str = "https://www.gstatic.com/generate_204"
    shortlist_size: int = 10
    top_size: int = 5
    scheduled_locations: list[str] = field(default_factory=list)
    interval_seconds: int = 10_800
    catchup_after_seconds: int = 11_700
    telegram_keychain_service: str = "com.local.windscribe-monitor.telegram"
    telegram_keychain_account: str = "bot-token"
    telegram_token_file: str = ""
    telegram_chat_id: str = ""
    timezone: str = field(default_factory=default_timezone)
    # Linux/Raspberry Pi uses downloaded, authorized WireGuard profiles inside
    # isolated network namespaces. macOS continues to use the official client.
    linux_backend: str = "wireguard-configs"
    wireguard_config_dir: str = "~/.config/windscribe/wireguard"
    wireguard_helper: str = "/usr/local/libexec/windscribe-monitor-netns"
    wireguard_handshake_timeout_seconds: int = 15
    wireguard_dns_servers: list[str] = field(default_factory=list)
    # Windscribe WireGuard supports multiple listener ports. This override keeps
    # the authorized endpoint host/key but uses the port that works on the ISP.
    # Set to 0 to preserve each profile's original endpoint port.
    wireguard_endpoint_port_override: int = 80

    @classmethod
    def load(cls, home: Path | None = None) -> "Config":
        home = home or default_home()
        path = home / "config.json"
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def save(self, home: Path | None = None) -> Path:
        home = home or default_home()
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = home / "config.json"
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path


def ensure_dirs(home: Path | None = None) -> Path:
    home = home or default_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("logs", "runs", "queue"):
        (home / name).mkdir(exist_ok=True, mode=0o700)
    return home

