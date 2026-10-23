from __future__ import annotations

import argparse
import getpass
import json
import logging
import platform
import shutil
import subprocess
import sys
import time
import os
from pathlib import Path

from .config import Config, default_home, ensure_dirs
from .locations import import_rename_export, load_locations
from .runner import Monitor
from .telegram import Telegram, flush_queue, queue_message
from .windscribe import Windscribe


def setup_logging(home: Path, verbose: bool = False) -> None:
    ensure_dirs(home)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers.append(logging.FileHandler(home / "logs" / "monitor.log", encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def store_telegram_token(config: Config, home: Path) -> None:
    if platform.system() == "Darwin":
        print("At both Keychain prompts below, enter the Telegram BOT TOKEN (not your Mac password).")
        print("The input is hidden. Enter the same bot token for 'password data' and 'retype password'.")
        subprocess.run([
            "/usr/bin/security", "add-generic-password", "-U",
            "-s", config.telegram_keychain_service,
            "-a", config.telegram_keychain_account,
            "-w",
        ], check=True)
        return
    first = getpass.getpass("Telegram bot token (hidden): ").strip()
    second = getpass.getpass("Retype Telegram bot token: ").strip()
    if not first or first != second:
        raise RuntimeError("Telegram bot tokens did not match")
    secret_dir = home / "secrets"
    secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path = secret_dir / "telegram-bot-token"
    token_path.write_text(first + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    config.telegram_token_file = str(token_path)
    config.save(home)


def cmd_doctor(config: Config, home: Path) -> int:
    data = {
        "python": sys.version.split()[0],
        "windscribe_cli": config.windscribe_cli,
        "windscribe_cli_exists": bool(shutil.which(config.windscribe_cli) or Path(config.windscribe_cli).exists()),
        "home": str(home),
        "locations": len(load_locations(home / "locations.txt")),
        "telegram_token_in_keychain": bool(Telegram(config).token()),
        "telegram_chat_configured": bool(config.telegram_chat_id),
    }
    try:
        data["windscribe_status"] = Windscribe(config).status().__dict__
    except Exception as exc:
        data["windscribe_error"] = str(exc)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0 if data["windscribe_cli_exists"] else 1


def cmd_status(home: Path) -> int:
    path = home / "last-run.json"
    if not path.exists():
        print("No completed run yet.")
        return 1
    run = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({k: run[k] for k in ("started_at", "finished_at", "tested", "working", "failed", "top")},
                     indent=2, ensure_ascii=False))
    return 0


def cmd_due(config: Config, home: Path, args: argparse.Namespace) -> int:
    path = home / "last-run.json"
    if args.command == "run-if-due" and path.exists() and not args.force:
        run = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - run.get("started_at", 0) < config.interval_seconds:
            print("Not due yet.")
            return 0
    monitor = Monitor(config, home)
    selected_locations = args.locations
    if args.command in ("run-if-due", "run-scheduled") and not selected_locations:
        selected_locations = config.scheduled_locations or None
    try:
        run = monitor.run(home / "locations.txt", limit=args.limit, notify=not args.no_notify,
                          only=selected_locations, resume=args.resume)
    except Exception as exc:
        if not args.no_notify:
            message = f"هشدار پایش Windscribe\nاجرای پایش ناموفق بود: {exc}"
            try:
                Telegram(config).send(message)
            except Exception:
                queue_message(home, message)
        raise
    print(json.dumps({k: run[k] for k in ("tested", "working", "failed", "top")}, indent=2, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="windscribe-monitor")
    root.add_argument("--home", type=Path, default=default_home())
    root.add_argument("--verbose", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("status")
    commands.add_parser("flush-queue")
    commands.add_parser("telegram-test")
    commands.add_parser("telegram-discover")
    bootstrap = commands.add_parser("telegram-bootstrap")
    bootstrap.add_argument("--via-location", required=True)
    flush_via = commands.add_parser("flush-via-vpn")
    flush_via.add_argument("--location", required=True)
    commands.add_parser("install-launchd")
    commands.add_parser("install-systemd")
    commands.add_parser("install-scheduler")
    commands.add_parser("disable")
    commands.add_parser("uninstall")
    setup = commands.add_parser("telegram-setup")
    setup.add_argument("--chat-id")
    import_locations = commands.add_parser("import-locations")
    import_locations.add_argument("path", type=Path)
    for name in ("run", "run-if-due", "run-scheduled"):
        run = commands.add_parser(name)
        run.add_argument("--limit", type=int)
        run.add_argument("--locations", nargs="+")
        run.add_argument("--no-notify", action="store_true")
        run.add_argument("--force", action="store_true")
        run.add_argument("--resume", action="store_true")
    logs = commands.add_parser("logs")
    logs.add_argument("--lines", type=int, default=80)
    return root


def launchd_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.local.windscribe-monitor.plist"


def install_launchd(home: Path) -> Path:
    if platform.system() != "Darwin":
        raise RuntimeError("launchd installation is available only on macOS")
    project = Path(__file__).resolve().parents[1]
    template = project / "launchd" / "com.local.windscribe-monitor.plist.template"
    content = template.read_text(encoding="utf-8")
    content = content.replace("__PYTHON__", sys.executable)
    content = content.replace("__HOME__", str(home))
    content = content.replace("__PROJECT__", str(project))
    path = launchd_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/com.local.windscribe-monitor"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True)
    return path


def systemd_paths() -> tuple[Path, Path]:
    base = Path.home() / ".config" / "systemd" / "user"
    return base / "windscribe-monitor.service", base / "windscribe-monitor.timer"


def install_systemd(home: Path) -> tuple[Path, Path]:
    if platform.system() != "Linux":
        raise RuntimeError("systemd installation is available only on Linux")
    project = Path(__file__).resolve().parents[1]
    template_dir = project / "systemd"
    service_content = (template_dir / "windscribe-monitor.service.template").read_text(encoding="utf-8")
    service_content = service_content.replace("__PYTHON__", sys.executable)
    service_content = service_content.replace("__HOME__", str(home))
    service_content = service_content.replace("__PROJECT__", str(project))
    timer_content = (template_dir / "windscribe-monitor.timer").read_text(encoding="utf-8")
    service_path, timer_path = systemd_paths()
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(service_content, encoding="utf-8")
    timer_path.write_text(timer_content, encoding="utf-8")
    service_path.chmod(0o644)
    timer_path.chmod(0o644)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", timer_path.name], check=True)
    return service_path, timer_path


def disable_scheduler() -> None:
    if platform.system() == "Darwin":
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/com.local.windscribe-monitor"],
                       check=False)
    elif platform.system() == "Linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", "windscribe-monitor.timer"],
                       check=False)


def uninstall_scheduler() -> None:
    disable_scheduler()
    if platform.system() == "Darwin":
        launchd_path().unlink(missing_ok=True)
    elif platform.system() == "Linux":
        for path in systemd_paths():
            path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)


def discover_channel(config: Config, home: Path) -> int:
    payload = Telegram(config).call("getUpdates", {"allowed_updates": '["channel_post"]'})
    candidates = []
    for update in payload.get("result", []):
        post = update.get("channel_post")
        if post and post.get("chat", {}).get("type") == "channel":
            candidates.append(post["chat"])
    if not candidates:
        print("No channel post found. Post a message in the channel, then try again.")
        return 1
    chat = candidates[-1]
    config.telegram_chat_id = str(chat["id"])
    config.save(home)
    print(f"Configured private channel: {chat.get('title', '(untitled)')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    home = ensure_dirs(args.home.expanduser().resolve())
    setup_logging(home, args.verbose)
    config = Config.load(home)
    if args.command == "doctor":
        return cmd_doctor(config, home)
    if args.command == "status":
        return cmd_status(home)
    if args.command == "import-locations":
        source = args.path.expanduser().resolve()
        if source.suffix.lower() == ".json":
            count = import_rename_export(source, home / "locations.txt")
        else:
            shutil.copyfile(source, home / "locations.txt")
            count = len(load_locations(home / "locations.txt"))
        print(f"Imported {count} locations into {home / 'locations.txt'}")
        return 0
    if args.command == "telegram-setup":
        store_telegram_token(config, home)
        me = Telegram(config).call("getMe")
        print(f"Authenticated bot: @{me['result'].get('username', '')}")
        if args.chat_id:
            config.telegram_chat_id = args.chat_id
            config.save(home)
        else:
            print("Post one message in the private channel, then rerun with --chat-id after discovering its id.")
        return 0
    if args.command == "telegram-test":
        Telegram(config).send("آزمایش سامانه پایش Windscribe: ارسال تلگرام با موفقیت انجام شد.")
        print("Telegram test delivered.")
        return 0
    if args.command == "telegram-discover":
        return discover_channel(config, home)
    if args.command == "telegram-bootstrap":
        monitor = Monitor(config, home)
        initial = monitor.ws.status()
        if not initial.disconnected:
            raise RuntimeError(
                f"Windscribe is not exactly disconnected ({initial.connect_state}); refusing to interfere"
            )
        monitor.owns_connection = True
        try:
            command = monitor.ws.connect(args.via_location)
            if command.returncode != 0 or command.timed_out:
                raise RuntimeError("temporary VPN connection command failed")
            status, status_error = monitor.wait_for_tunnel()
            if not status:
                raise RuntimeError(
                    f"temporary {config.protocol} tunnel failed: {status_error or 'timeout'}"
                )
            result = discover_channel(config, home)
            if result != 0:
                raise RuntimeError("Telegram channel post was not found")
            # discover_channel persists the chat id, so reload before delivery.
            Telegram(Config.load(home)).send(
                "آزمایش سامانه پایش Windscribe: اتصال بات و کانال با موفقیت تأیید شد."
            )
            print("Telegram bootstrap test delivered.")
        finally:
            monitor.cleanup()
        final_status = monitor.ws.status()
        if not final_status.disconnected:
            raise RuntimeError(f"final VPN state is not disconnected: {final_status.connect_state}")
        print("Windscribe stable disconnected state verified.")
        return 0
    if args.command == "flush-via-vpn":
        monitor = Monitor(config, home)
        initial = monitor.ws.status()
        if not initial.disconnected:
            raise RuntimeError(
                f"Windscribe is not exactly disconnected ({initial.connect_state}); refusing to interfere"
            )
        sent, failed = monitor.flush_telegram_via_location(args.location)
        print(f"sent={sent} failed={failed}")
        if failed:
            return 1
        final_status = monitor.ws.status()
        if not final_status.disconnected:
            raise RuntimeError(f"final VPN state is not disconnected: {final_status.connect_state}")
        print("Windscribe stable disconnected state verified.")
        return 0
    if args.command == "install-launchd":
        path = install_launchd(home)
        print(f"Installed and enabled: {path}")
        return 0
    if args.command == "install-systemd":
        service_path, timer_path = install_systemd(home)
        print(f"Installed and enabled: {service_path} and {timer_path}")
        return 0
    if args.command == "install-scheduler":
        if platform.system() == "Darwin":
            path = install_launchd(home)
            print(f"Installed and enabled: {path}")
        elif platform.system() == "Linux":
            service_path, timer_path = install_systemd(home)
            print(f"Installed and enabled: {service_path} and {timer_path}")
        else:
            raise RuntimeError(f"unsupported scheduler platform: {platform.system()}")
        return 0
    if args.command == "disable":
        disable_scheduler()
        print("Scheduled monitor disabled; data and configuration preserved.")
        return 0
    if args.command == "uninstall":
        uninstall_scheduler()
        print("Scheduler removed; data and Telegram token preserved.")
        return 0
    if args.command == "flush-queue":
        sent, failed = flush_queue(home, Telegram(config))
        print(f"sent={sent} failed={failed}")
        return 0 if failed == 0 else 1
    if args.command == "logs":
        path = home / "logs" / "monitor.log"
        lines = path.read_text(encoding="utf-8").splitlines()[-args.lines:] if path.exists() else []
        print("\n".join(lines))
        return 0
    if args.command == "run":
        args.force = True
        return cmd_due(config, home, args)
    if args.command == "run-if-due":
        return cmd_due(config, home, args)
    if args.command == "run-scheduled":
        return cmd_due(config, home, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

