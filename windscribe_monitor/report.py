from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def _number(value: float | None, suffix: str, digits: int = 1) -> str:
    return "نامشخص" if value is None else f"{value:.{digits}f}{suffix}"


def persian_report(run: dict, timezone: str) -> str:
    timestamp = datetime.fromtimestamp(run["finished_at"], ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S")
    protocol = str(run.get("protocol", "ikev2")).upper()
    lines = [
        f"گزارش پایش Windscribe ({protocol})",
        f"زمان: {timestamp}",
        f"فعال: {run['working']} | ناموفق: {run['failed']} | کل: {run['tested']}",
        "",
    ]
    if not run.get("top"):
        lines.append("هیچ موقعیت سالمی پیدا نشد.")
        return "\n".join(lines)
    for index, item in enumerate(run["top"], 1):
        lines.extend([
            f"{index}) {item['location']}",
            f"   اتصال: موفق | پایداری: {_number(item.get('reliability_percent'), '٪')}",
            f"   تاخیر: {_number(item.get('latency_avg_ms'), ' ms')} | اتلاف بسته: {_number(item.get('packet_loss_percent'), '٪')}",
            f"   سرعت دانلود: {_number(item.get('throughput_mbps'), ' Mbps')}",
        ])
    return "\n".join(lines)

