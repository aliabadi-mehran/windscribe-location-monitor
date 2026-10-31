from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import Config


class Telegram:
    def __init__(self, config: Config):
        self.config = config

    def token(self) -> str | None:
        if platform.system() == "Darwin":
            proc = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", self.config.telegram_keychain_service,
                 "-a", self.config.telegram_keychain_account, "-w"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
            )
            value = proc.stdout.strip()
            return value or None
        path_value = self.config.telegram_token_file or os.environ.get("WSMON_TELEGRAM_TOKEN_FILE", "")
        if not path_value:
            return None
        path = Path(path_value).expanduser()
        if not path.is_file():
            return None
        if path.stat().st_mode & 0o077:
            raise RuntimeError(f"Telegram token file permissions are too open: {path}; expected mode 600")
        value = path.read_text(encoding="utf-8").strip()
        return value or None

    def call(self, method: str, values: dict[str, str] | None = None) -> dict:
        token = self.token()
        if not token:
            raise RuntimeError("Telegram bot token is not configured in the platform secret store")
        body = urllib.parse.urlencode(values or {}).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/{method}", data=body,
            headers={"User-Agent": "WindscribeMonitor/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.http_timeout_seconds) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {payload.get('description', 'unknown error')}")
        return payload

    def send(self, text: str, parse_mode: str | None = None) -> None:
        if not self.config.telegram_chat_id:
            raise RuntimeError("Telegram channel id is not configured")
        values = {"chat_id": self.config.telegram_chat_id, "text": text}
        if parse_mode:
            values["parse_mode"] = parse_mode
        self.call("sendMessage", values)


def queue_message(home: Path, text: str, parse_mode: str | None = None) -> Path:
    queue = home / "queue"
    queue.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = queue / f"{int(time.time() * 1000)}.json"
    path.write_text(json.dumps({"text": text, "parse_mode": parse_mode}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    path.chmod(0o600)
    return path


def flush_queue(home: Path, telegram: Telegram) -> tuple[int, int]:
    sent = 0
    failed = 0
    for path in sorted((home / "queue").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            telegram.send(payload["text"], payload.get("parse_mode"))
            path.unlink()
            sent += 1
        except Exception:
            failed += 1
            break
    return sent, failed

