from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Location:
    name: str
    hostname: str | None = None


def load_locations(path: Path) -> list[Location]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("locations", data.get("items", []))
        if not isinstance(data, list):
            raise ValueError("locations JSON must be a list or contain a locations list")
        result = []
        for item in data:
            if isinstance(item, str):
                result.append(Location(item.strip()))
            elif isinstance(item, dict) and item.get("name"):
                result.append(Location(str(item["name"]).strip(), item.get("hostname")))
        return _dedupe(result)
    result = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, hostname = line.partition("|")
        result.append(Location(name.strip(), hostname.strip() if separator and hostname.strip() else None))
    return _dedupe(result)


def _dedupe(items: list[Location]) -> list[Location]:
    seen: set[str] = set()
    result = []
    for item in items:
        key = item.name.casefold()
        if item.name and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def import_rename_export(source: Path, destination: Path) -> int:
    """Best-effort importer for Windscribe's supported Rename Locations JSON export."""
    data = json.loads(source.read_text(encoding="utf-8"))
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            nickname = value.get("nickname")
            if isinstance(nickname, str) and nickname.strip():
                found.append(nickname.strip())
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    names = sorted(set(found), key=str.casefold)
    if not names:
        raise ValueError("No nickname fields found; use a one-location-per-line text file instead")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.write_text("\n".join(names) + "\n", encoding="utf-8")
    destination.chmod(0o600)
    return len(names)

