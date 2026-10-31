from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo


def _number(value: float | None, suffix: str, digits: int = 1) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}{suffix}"


def persian_report(run: dict, timezone: str) -> str:
    timestamp = datetime.fromtimestamp(run["finished_at"], ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S")
    protocol = str(run.get("protocol", "ikev2")).upper()
    lines = [
        "<b>Windscribe VPN Location Report</b>",
        f"🕒 Time: <code>{escape(timestamp)}</code>",
        f"🔐 Protocol: <b>{escape(protocol)}</b>",
        (f"✅ Working: <b>{run['working']}</b>  |  ❌ Failed: <b>{run['failed']}</b>"
         f"  |  📍 Total: <b>{run['tested']}</b>"),
        "",
    ]
    if not run.get("top"):
        lines.append("No functional VPN location was found.")
        return "\n".join(lines)
    lines.extend([f"🏆 <b>Top {len(run['top'])} Locations</b>", ""])
    for index, item in enumerate(run["top"], 1):
        label = escape(str(item.get("display_name") or item.get("location") or "Unknown server"))
        lines.extend([
            f"<b>{index}. {label}</b>",
            f"Status: Connected  •  Reliability: {_number(item.get('reliability_percent'), '%')}",
            (f"Latency: {_number(item.get('latency_avg_ms'), ' ms')}  •  "
             f"Packet loss: {_number(item.get('packet_loss_percent'), '%')}"),
            f"Download: {_number(item.get('throughput_mbps'), ' Mbps')}",
            "",
        ])
    return "\n".join(lines).rstrip()

