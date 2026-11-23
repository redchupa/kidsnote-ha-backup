"""Home Assistant app entry point and periodic backup runner."""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread

import requests

from archive import Archive, write_json
from client import BackupError, HttpError, KidsnoteClient, identifier
from kidsnote_auth import AuthError, login, normalize_cookie, normalize_password, normalize_username
from pacing import RequestDeferred, RequestPacer, Stopping
from viewer import viewer_server

LOG = logging.getLogger(__name__)
BACKUP_SPREAD_SECONDS = 30 * 60
INITIAL_STAGGER_SECONDS = 30


def read_options(path: Path):
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise BackupError("App options must be a JSON object.")
    hours = raw.get("interval_hours", 24)
    if type(hours) is not int or not 1 <= hours <= 168:
        raise BackupError("interval_hours must be an integer from 1 to 168.")
    children = raw.get("child_ids", [])
    if not isinstance(children, list):
        raise BackupError("child_ids must be a list. Example: child_ids: [1234567].")
    selected = set()
    for child in children:
        try:
            child_id = identifier(child)
        except BackupError as exc:
            raise BackupError("child_ids must contain only positive integer IDs.") from exc
        if child_id.startswith("0"):
            raise BackupError("child_ids must contain only positive integer IDs.")
        selected.add(child_id)
    options = {"interval_hours": hours, "child_ids": selected}
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
    for key in ("include_schedules", "include_medications", "include_return_homes", "include_temperatures"):
        value = raw.get(key, False)
        if type(value) is not bool:
            raise BackupError(f"{key} must be true or false.")
        options[key] = value
    notify_ha = raw.get("notify_ha", False)
    if type(notify_ha) is not bool:
        raise BackupError("notify_ha must be true or false.")
    options["notify_ha"] = notify_ha
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
        client.token = login(options["username"], options["password"], pacer=client.pacer)
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


def read_status(path):
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        return content if isinstance(content, dict) else {}
    except (OSError, ValueError):
        return {}


def notify_home_assistant(status):
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token or status.get("records", 0) <= 0:
        return False
    payload = {
        "state": status.get("state"),
        "records": status.get("records", 0),
        "new_attachments": status.get("new_attachments", 0),
        "errors": status.get("errors", 0),
        "finished_at": status.get("finished_at"),
    }
    try:
        with requests.Session() as session:
            session.trust_env = False
            resp = session.post(
                "http://supervisor/core/api/events/kidsnote_backup_completed",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10,
            )
            if resp.status_code >= 400:
                LOG.warning("Home Assistant event notification returned HTTP %d.", resp.status_code)
                return False
            return True
    except requests.RequestException as exc:
        LOG.warning("Could not notify Home Assistant: %s", type(exc).__name__)
        return False


class BackupController:
    """Thread-safe bridge between the viewer UI and the backup scheduler."""

    def __init__(self, pacer: RequestPacer):
        self.pacer = pacer
        self.wake = Event()
        self._lock = Lock()
        self.running = False
        self.next_run = 0.0
        self.phase = ""
        self.records = 0
        self.unchanged = 0
        self.downloaded = 0
        self.phase_seen = 0
        self.phase_total: int | None = None
        self.eta_seconds: int | None = None

    def set_running(self, value: bool):
        with self._lock:
            self.running = value
            if value:
                self.phase = ""
                self.records = 0
                self.unchanged = 0
                self.downloaded = 0
                self.phase_seen = 0
                self.phase_total = None
                self.eta_seconds = None

    def set_progress(
        self, phase: str, records: int, unchanged: int, downloaded: int,
        phase_seen: int = 0, phase_total: int | None = None, eta_seconds: int | None = None,
    ):
        with self._lock:
            self.phase = phase
            self.records = records
            self.unchanged = unchanged
            self.downloaded = downloaded
            self.phase_seen = phase_seen
            self.phase_total = phase_total
            self.eta_seconds = eta_seconds

    def set_next_run(self, when: float):
        with self._lock:
            self.next_run = when

    def snapshot(self):
        with self._lock:
            return {
                "running": self.running,
                "cooldown_until": self.pacer.cooldown_until,
                "next_run": self.next_run,
                "progress": {
                    "phase": self.phase,
                    "records": self.records,
                    "unchanged": self.unchanged,
                    "downloaded": self.downloaded,
                    "phase_seen": self.phase_seen,
                    "phase_total": self.phase_total,
                    "eta_seconds": self.eta_seconds,
                } if self.running else None,
            }

    def trigger(self):
        now = time.time()
        with self._lock:
            if self.running or self.wake.is_set():
                return 409, {"accepted": False, "reason": "already_running", "message": "이미 백업이 진행 중입니다."}
            if self.pacer.cooldown_until > now:
                remaining = max(1, int(math.ceil((self.pacer.cooldown_until - now) / 60)))
                return 429, {
                    "accepted": False,
                    "reason": "cooldown",
                    "cooldown_until": self.pacer.cooldown_until,
                    "message": f"키즈노트 서버 보호 대기 중입니다. 약 {remaining}분 후에 다시 시도할 수 있습니다.",
                }
            self.wake.set()
            return 202, {"accepted": True, "message": "수동 백업을 시작합니다."}


def write_status(path, status):
    previous = read_status(path)
    if status["state"] == "success":
        status["last_success_at"] = status["finished_at"]
    else:
        status["last_success_at"] = previous.get("last_success_at")
    write_json(path, status)


def run_once(client, root, options, stop, progress=None):
    started = timestamp()
    archive = Archive(client, root, stop, progress=progress)
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
        if not read_status(root / "status.json").get("last_success_at"):
            LOG.info(
                "첫 백업을 시작합니다. 키즈노트 서버 보호를 위해 요청 간격을 두고 천천히 내려받으므로, "
                "알림장·앨범 사진이 많으면 수십 분~수 시간 걸릴 수 있습니다. 완료될 때까지 앱을 중지하지 말아 주세요."
            )
        archive.run(children, options)
        state = "partial" if archive.errors else "success"
    except Stopping:
        state = "stopped"
    except RequestDeferred as exc:
        state = "deferred"
        LOG.warning("HTTP %d from Kidsnote or its media server. Pausing all downloads for at least %.0f minutes.",
                    exc.status, (client.pacer.cooldown_until - time.time()) / 60)
    except (BackupError, OSError) as exc:
        archive.errors += 1
        if isinstance(exc, HttpError) and exc.status == 401:
            client.token = ""
        message = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
        LOG.error("Backup failed: %s", message)
    try:
        status = {
            "state": state,
            "started_at": started,
            "finished_at": timestamp(),
            "records": archive.records,
            "unchanged_records": archive.unchanged,
            "new_attachments": archive.downloaded,
            "errors": archive.errors,
        }
        if state == "deferred":
            status["retry_not_before"] = client.pacer.cooldown_until
        if state == "success":
            (root / "checkpoint.json").unlink(missing_ok=True)
        write_status(root / "status.json", status)
        if options.get("notify_ha") and state in ("success", "partial"):
            notify_home_assistant(status)
    except OSError as exc:
        LOG.error("Could not write backup status: %s", type(exc).__name__)
        state = "failed"
    LOG.info("Backup %s: %d records saved, %d unchanged, %d new attachments, %d errors.",
             state, archive.records, archive.unchanged, archive.downloaded, archive.errors)
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
    pacer = RequestPacer(stop)
    previous = read_status(args.archive / "status.json").get("retry_not_before")
    if type(previous) in (float, int) and math.isfinite(previous):
        pacer.cooldown_until = previous
    if args.once and pacer.cooldown_until > time.time():
        LOG.warning("Backup is still waiting for the previous server cooldown.")
        return 1
    controller = BackupController(pacer)
    server = None
    if not args.once:
        try:
            server = viewer_server(args.archive, backup_controller=controller)
        except OSError:
            LOG.error("Could not start the archive viewer.")
            return 1
        Thread(target=server.run, name="archive-viewer", daemon=True).start()
    try:
        first_delay = 0.0
        resuming = (args.archive / "checkpoint.json").is_file()
        initial_run = not read_status(args.archive / "status.json").get("last_success_at")
        if not args.once and (options["username"] or options["session_cookie"]):
            if resuming:
                first_delay = 5.0
            elif initial_run:
                first_delay = random.uniform(5, INITIAL_STAGGER_SECONDS)
            else:
                first_delay = random.uniform(0, BACKUP_SPREAD_SECONDS)
        next_run = time.time() + first_delay
        if resuming and not args.once:
            LOG.info("이전 백업이 중단된 기록이 있어 잠시 후 중단 지점부터 이어서 백업을 재개합니다.")
        elif initial_run and first_delay > 0:
            LOG.info("첫 설치 백업을 약 %.0f초 후 자동으로 시작합니다.", first_delay)
        elif first_delay > 0:
            start_kst = datetime.fromtimestamp(next_run, timezone(timedelta(hours=9))).strftime("%H:%M")
            LOG.info(
                "Backup startup stagger: %.0f seconds (키즈노트 서버 분산을 위해 약 %d분 후 %s경 백업을 자동 시작합니다).",
                first_delay, max(1, round(first_delay / 60)), start_kst,
            )
        with KidsnoteClient(pacer) as client:
            client.token = normalize_cookie(options["session_cookie"])
            while not stop.is_set():
                target_time = max(next_run, pacer.cooldown_until)
                controller.set_next_run(target_time)
                pacer.wait_until(target_time, wake=None if args.once else controller.wake)
                controller.set_running(True)
                try:
                    state = run_once(client, args.archive, options, stop, progress=controller.set_progress)
                finally:
                    controller.set_running(False)
                if args.once:
                    return 0 if state == "success" else 1
                if state == "deferred":
                    next_run = pacer.cooldown_until
                else:
                    next_run = time.time() + options["interval_hours"] * 3600 + random.uniform(0, BACKUP_SPREAD_SECONDS)
    except Stopping:
        pass
    finally:
        if server is not None:
            server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
