"""Home Assistant app entry point and periodic backup runner."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

from archive import Archive, Stopping, write_json
from client import BackupError, HttpError, KidsnoteClient, identifier
from kidsnote_auth import AuthError, login, normalize_cookie, normalize_password, normalize_username
from viewer import viewer_server

LOG = logging.getLogger(__name__)


def read_options(path: Path):
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise BackupError("App options must be a JSON object.")
    hours = raw.get("interval_hours", 24)
    if type(hours) is not int or not 1 <= hours <= 168:
        raise BackupError("interval_hours must be an integer from 1 to 168.")
    children = raw.get("child_ids", [])
    if not isinstance(children, list) or any(type(value) is not int or value < 1 for value in children):
        raise BackupError("child_ids must be a list of positive integers.")
    options = {"interval_hours": hours, "child_ids": {str(value) for value in children}}
    for key in ("username", "password", "session_cookie"):
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise BackupError(f"{key} must be a string.")
        options[key] = value or ""
    for key in ("include_albums", "include_notices", "include_menus"):
        value = raw.get(key, True)
        if type(value) is not bool:
            raise BackupError(f"{key} must be true or false.")
        options[key] = value
    return options


def list_children(client, options):
    if client.token:
        try:
            return list(client.items("/api/v1/me/children/", alternate="children"))
        except HttpError as exc:
            if exc.status not in (401, 403):
                raise
            client.token = ""

    if not (normalize_username(options["username"]) and normalize_password(options["password"])):
        if options["session_cookie"]:
            raise BackupError("Set a valid session_cookie in the app configuration and restart.")
        raise BackupError("Set a Kidsnote username and password or session_cookie, then restart.")
    try:
        client.token = login(options["username"], options["password"])
    except AuthError as exc:
        hints = {
            "2fa_enabled": "Sign in with two-step verification in your browser and set session_cookie.",
            "invalid_credentials": "Check the username and password in the app configuration.",
            "blocked": "Check the state of your Kidsnote account in the browser.",
        }
        hint = hints.get(exc.reason, "Check the connection and try again later.")
        raise BackupError(f"Kidsnote login failed ({exc.reason}). {hint}") from exc
    return list(client.items("/api/v1/me/children/", alternate="children"))


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_status(path, status):
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    if status["state"] == "success":
        status["last_success_at"] = status["finished_at"]
    elif isinstance(previous, dict):
        status["last_success_at"] = previous.get("last_success_at")
    write_json(path, status)


def run_once(client, root, options, stop):
    started = timestamp()
    archive = Archive(client, root, stop)
    state = "failed"
    try:
        archive.check_stop()
        children = list_children(client, options)
        selected = options["child_ids"]
        available = {identifier(child.get("id")): child for child in children}
        if selected and selected - available.keys():
            raise BackupError("Some configured child IDs were not returned by the account.")
        children = [child for key, child in available.items() if not selected or key in selected]
        if not children:
            raise BackupError("Kidsnote returned no children for this account.")
        LOG.info("Starting backup for %d children.", len(children))
        archive.run(children, options)
        state = "partial" if archive.errors else "success"
    except Stopping:
        state = "stopped"
    except (BackupError, OSError) as exc:
        archive.errors += 1
        if isinstance(exc, HttpError) and exc.status == 401:
            client.token = ""
        message = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
        LOG.error("Backup failed: %s", message)
    try:
        write_status(root / "status.json", {
            "state": state,
            "started_at": started,
            "finished_at": timestamp(),
            "records": archive.records,
            "new_attachments": archive.downloaded,
            "errors": archive.errors,
        })
    except OSError as exc:
        LOG.error("Could not write backup status: %s", type(exc).__name__)
        state = "failed"
    LOG.info("Backup %s: %d records, %d new attachments, %d errors.",
             state, archive.records, archive.downloaded, archive.errors)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Back up once and exit.")
    parser.add_argument("--options", type=Path, default=Path("/data/options.json"))
    parser.add_argument("--archive", type=Path, default=Path("/config/backup"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.umask(0o077)
    try:
        options = read_options(args.options)
        args.archive.mkdir(parents=True, exist_ok=True)
        for path in args.archive.rglob(".kidsnote-*.part"):
            if path.is_file():
                path.unlink()
    except (BackupError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
        LOG.error("App setup failed: %s", message)
        return 1
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    server = None
    if not args.once:
        try:
            server = viewer_server(args.archive)
        except OSError:
            LOG.error("Could not start the archive viewer.")
            return 1
        Thread(target=server.run, name="archive-viewer", daemon=True).start()
    try:
        with KidsnoteClient() as client:
            client.token = normalize_cookie(options["session_cookie"])
            while not stop.is_set():
                state = run_once(client, args.archive, options, stop)
                if args.once:
                    return 0 if state == "success" else 1
                stop.wait(options["interval_hours"] * 3600)
    finally:
        if server is not None:
            server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
