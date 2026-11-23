"""Download Kidsnote records into a durable, local file archive."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests

from client import BackupError, HttpError, TransportError, checked_https, identifier
from kidsnote_auth import KIDSNOTE_BASE
from pacing import Stopping

LOG = logging.getLogger(__name__)
V1 = "/api/v1"
V12 = "/api/v1_2"
IMAGE_KEYS = ("attached_images", "attached_pictures", "pictures", "images", "attached_image", "picture")
VIDEO_KEYS = ("attached_video", "video", "attached_videos")
FILE_KEYS = ("attached_files", "files", "attachments")
URL_KEYS = ("original", "url", "src", "high", "high_resize", "large_resize")
MENU_PHOTOS = ("morning_img", "morning_snack_img", "lunch_img", "afternoon_snack_img", "dinner_img")
EXTENSION = re.compile(r"\.[a-z0-9]{1,10}", re.ASCII)
REFRESH_SECONDS = 7 * 24 * 60 * 60
CHECKPOINT_TTL_SECONDS = 48 * 60 * 60
OLDER_STOP_STREAK = 10
KST = timezone(timedelta(hours=9))
KIND_LABELS = {
    "reports": "알림장",
    "albums": "앨범",
    "notices": "공지",
    "menus": "식단",
    "schedules": "일정표",
    "medications": "투약의뢰서",
    "return_homes": "귀가동의서",
    "temperatures": "체온기록",
}
ESTIMATE_SECONDS_PER_ITEM = {
    "reports": (6, 20),
    "albums": (10, 40),
    "notices": (2, 6),
    "menus": (2, 5),
    "schedules": (1, 3),
    "medications": (2, 5),
    "return_homes": (2, 4),
    "temperatures": (1, 2),
}


def format_duration(seconds: float) -> str:
    minutes = int(round(max(0.0, seconds) / 60))
    if minutes < 1:
        return "1분 미만"
    if minutes < 60:
        return f"약 {minutes}분"
    hours, rem = divmod(minutes, 60)
    return f"약 {hours}시간 {rem}분" if rem else f"약 {hours}시간"


@contextmanager
def atomic_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=".kidsnote-", suffix=".part", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            yield handle
            handle.close()
            os.replace(temporary, path)
        finally:
            handle.close()
            temporary.unlink(missing_ok=True)


def write_json(path: Path, payload):
    with atomic_file(path) as handle:
        handle.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def first_value(record, keys):
    return next((record[key] for key in keys if record.get(key)), None)


def attachment_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _extract_center_id(obj):
    if not isinstance(obj, dict):
        return None
    value = obj.get("center_id") or obj.get("center")
    if isinstance(value, dict):
        value = value.get("id")
    return identifier(value) if value else None


def _extract_class_id(obj):
    if not isinstance(obj, dict):
        return None
    value = obj.get("belong_to_class") or obj.get("cls")
    if isinstance(value, dict):
        value = value.get("id")
    return identifier(value) if value else None


def enrollments_info(child):
    seen = set()
    enrollments = child.get("enrollment")
    if isinstance(enrollments, dict):
        enrollments = [enrollments]
    elif not isinstance(enrollments, list):
        enrollments = []
    extra = child.get("enrollments")
    if isinstance(extra, list):
        enrollments = [*enrollments, *extra]
    for entry in (*enrollments, child):
        cid = _extract_center_id(entry)
        cls_id = _extract_class_id(entry)
        if cid and (cid, cls_id) not in seen:
            seen.add((cid, cls_id))
            yield cid, cls_id


def center_ids(child):
    seen = set()
    for cid, _ in enrollments_info(child):
        if cid not in seen:
            seen.add(cid)
            yield cid


def month_range(start: date, end: date) -> list[str]:
    year, month = start.year, start.month
    end_ym = (end.year, end.month)
    months = []
    while (year, month) <= end_ym and len(months) < 120:
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year += 1
            month = 1
    return months


def schedule_key(item) -> str:
    day_val = record_date(item)
    day = day_val.isoformat() if day_val else "0000-00-00"
    if item.get("id") is not None:
        return f"{day}_{identifier(item['id'])}"
    raw = f"{day}\n{item.get('title', '')}\n{item.get('content', '')}"
    return f"{day}_e{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"


def menu_key(item):
    try:
        day = date.fromisoformat(str(item.get("date_menu", ""))[:10]).isoformat()
    except ValueError as exc:
        raise BackupError("Kidsnote returned an invalid menu date.") from exc
    return f"{day}_{identifier(item['id'])}" if item.get("id") is not None else day


def record_date(item) -> date | None:
    for key in ("date_written", "date_menu", "date_event", "date_medicated", "datetime_requested", "created"):
        value = item.get(key)
        if value:
            try:
                return date.fromisoformat(str(value)[:10])
            except ValueError:
                continue
    return None


def has_complete_local_record(folder: Path, kind: str) -> bool:
    meta = folder / "meta.json"
    try:
        if not meta.is_file() or (kind != "menus" and not (folder / "note.txt").is_file()):
            return False
        cached = json.loads(meta.read_text(encoding="utf-8"))
        if not isinstance(cached, dict):
            return False
        records_to_check = [cached]
        if kind != "menus":
            if "_comments" not in cached:
                return False
            records_to_check.extend(c for c in (cached["_comments"] or []) if isinstance(c, dict))
        for record in records_to_check:
            assets = record.get("_local_attachments")
            if not isinstance(assets, list):
                return False
            for asset in assets:
                filename = asset["file"]
                target = folder / filename
                if Path(filename).name != filename or not target.is_file() or target.stat().st_size == 0:
                    return False
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return False
    return True


def load_checkpoint(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        updated = data.get("updated_at", 0)
        if type(updated) not in (int, float) or time.time() - updated > CHECKPOINT_TTL_SECONDS:
            return {}
        return {
            "completed_collections": set(
                x for x in data.get("completed_collections", []) if isinstance(x, str)
            ),
            "saved_keys": set(
                x for x in data.get("saved_keys", []) if isinstance(x, str)
            ),
            "center_since": {
                k: v for k, v in (data.get("center_since") or {}).items()
                if isinstance(k, str) and (v is None or isinstance(v, str))
            },
        }
    except (OSError, ValueError):
        return {}


def save_checkpoint(path: Path, state: dict):
    payload = {
        "updated_at": time.time(),
        "completed_collections": sorted(state["completed_collections"]),
        "saved_keys": sorted(state["saved_keys"]),
        "center_since": state["center_since"],
    }
    write_json(path, payload)


def already_backed_up(folder, item):
    modified = item.get("modified")
    comment_count = item.get("num_comments")
    if not isinstance(modified, str) or type(comment_count) is not int:
        return False
    now = time.time()
    meta = folder / "meta.json"
    try:
        changed = datetime.fromisoformat(modified)
        if changed.tzinfo is None:
            changed = changed.replace(tzinfo=timezone.utc)
        if changed.timestamp() > now - REFRESH_SECONDS:
            return False
        if now - meta.stat().st_mtime >= REFRESH_SECONDS or not (folder / "note.txt").is_file():
            return False
        cached = json.loads(meta.read_text(encoding="utf-8"))
        if (cached.get("modified") != modified or cached.get("num_comments") != comment_count
                or "_comments" not in cached):
            return False
        for record in (cached, *(cached["_comments"] or [])):
            assets = record.get("_local_attachments")
            if not isinstance(assets, list):
                return False
            for asset in assets:
                filename = asset["file"]
                target = folder / filename
                if Path(filename).name != filename or not target.is_file() or target.stat().st_size == 0:
                    return False
    except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError):
        return False
    return True


class Archive:
    def __init__(self, client, root: Path, stop, progress=None):
        self.client = client
        self.root = root
        self.stop = stop
        self.progress = progress
        self.records = 0
        self.unchanged = 0
        self.downloaded = 0
        self.errors = 0
        self._phase = ""
        self._phase_seen = 0
        self._phase_total: int | None = None
        self._phase_eta: int | None = None
        self._last_log = time.monotonic()
        self._checkpoint_path = self.root / "checkpoint.json"
        self._checkpoint = {"completed_collections": set(), "saved_keys": set(), "center_since": {}}

    def _flush_checkpoint(self):
        try:
            save_checkpoint(self._checkpoint_path, self._checkpoint)
        except OSError:
            pass

    def _report_progress(self, phase=None):
        if phase is not None:
            self._phase = phase
            self._phase_seen = 0
            self._phase_total = None
            self._phase_eta = None
        if self.progress is not None:
            self.progress(
                self._phase, self.records, self.unchanged, self.downloaded,
                self._phase_seen, self._phase_total, self._phase_eta,
            )

    def check_stop(self):
        if self.stop.is_set():
            raise Stopping

    def media(self, folder, obj, label, extension):
        self.check_stop()
        url = first_value(obj, URL_KEYS) if isinstance(obj, dict) else obj
        if not isinstance(url, str) or not url:
            raise BackupError("Kidsnote returned an attachment without a download URL.")
        url = urljoin(KIDSNOTE_BASE, url)
        parsed = checked_https(url)
        original_name = obj.get("original_file_name") if isinstance(obj, dict) else None
        source_id = obj.get("id") if isinstance(obj, dict) else None
        # A renewed signature should not duplicate a file. Including the original
        # path and attachment ID prevents a reordered attachment from reusing a slot.
        key = f"{parsed.hostname}{parsed.path}\n{source_id}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        suffixes = [Path(str(original_name or "")).suffix.lower(), Path(parsed.path).suffix.lower()]
        suffix = next((value for value in suffixes if EXTENSION.fullmatch(value)), extension)
        filename = f"{label}_{digest}{suffix}"
        target = folder / filename
        if not target.is_file() or target.stat().st_size == 0:
            try:
                with self.client.get(url, media=True) as response:
                    mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if mime == "text/html":
                        raise BackupError("An attachment returned a web page instead of a file.")
                    with atomic_file(target) as handle:
                        for block in response.iter_content(128 * 1024):
                            self.check_stop()
                            handle.write(block)
                        size = handle.tell()
                        if size == 0:
                            raise BackupError("Kidsnote returned an empty attachment.")
                        expected = response.headers.get("Content-Length", "")
                        if (expected.isdecimal() and not response.headers.get("Content-Encoding")
                                and size != int(expected)):
                            raise BackupError("An attachment transfer was incomplete.")
            except HttpError as exc:
                if exc.status in (500, 502, 504):
                    raise
                raise BackupError(f"Attachment server returned HTTP {exc.status}.") from exc
            except requests.RequestException as exc:
                raise BackupError("An attachment transfer was interrupted.") from exc
            self.downloaded += 1
        return {"kind": label, "file": filename, "original_name": original_name}

    def save(self, folder, kind, item):
        self.check_stop()
        if kind == "menus":
            attachments = [
                self.media(folder, item[key], key, ".jpg")
                for key in MENU_PHOTOS if item.get(key)
            ]
        elif kind == "temperatures":
            attachments = []
        else:
            has_comments = kind in ("reports", "albums", "notices")
            item_id = identifier(item.get("id")) if (has_comments or item.get("id") is not None) else None
            if kind == "reports" and item_id is not None:
                detail = self.client.json(f"{KIDSNOTE_BASE}{V12}/reports/{item_id}/")
                item = {**item, **detail}
            comments = []
            if has_comments and item_id is not None and item.get("num_comments") != 0:
                try:
                    comments = list(self.client.items(f"{V1}/{kind}/{item_id}/comments/"))
                except HttpError as exc:
                    # Comments are not exposed for every notice or album.
                    if kind == "reports" or exc.status != 404:
                        raise
                    comments = None
            attachments = self.post_media(folder, item)
            if comments is not None:
                comments = [
                    {**comment, "_local_attachments": self.post_media(
                        folder, comment, f"comment_{identifier(comment['id'])}_"
                    )} if comment.get("id") is not None else comment
                    for comment in comments
                ]
            item = {**item, "_comments": comments}
            body = first_value(item, ("content", "body", "report", "symptom", "method"))
            heading = item.get("title")
            lines = [value for value in (heading, body) if isinstance(value, str) and value]
            with atomic_file(folder / "note.txt") as handle:
                handle.write("\n\n".join(lines).encode("utf-8"))

        write_json(folder / "meta.json", {**item, "_local_attachments": attachments})
        self.records += 1
        self._report_progress()
        now = time.monotonic()
        if self.records % 25 == 0 or now - self._last_log >= 30:
            self._last_log = now
            label = KIND_LABELS.get(self._phase or kind, self._phase or kind)
            if self._phase_total and self._phase_seen and self._phase_seen <= self._phase_total:
                pct = min(100, int(self._phase_seen * 100 / self._phase_total))
                eta_text = f" · 예상 남은 시간 {format_duration(self._phase_eta)}" if self._phase_eta is not None else ""
                LOG.info(
                    "진행 중 [%s]: %d/%d (%d%% · 저장 %d건 · 기존 %d건 · 첨부 %d개%s)",
                    label, self._phase_seen, self._phase_total, pct,
                    self.records, self.unchanged, self.downloaded, eta_text,
                )
            else:
                LOG.info(
                    "진행 중 [%s]: %d건 처리됨 (저장 %d건 · 기존 %d건 · 첨부 %d개 · 오류 %d건)",
                    label, self._phase_seen or self.records,
                    self.records, self.unchanged, self.downloaded, self.errors,
                )
        return item

    def post_media(self, folder, item, prefix=""):
        return [
            self.media(folder, obj, prefix + label, extension)
            for keys, label, extension in (
                (IMAGE_KEYS, "image", ".jpg"), (VIDEO_KEYS, "video", ".mp4"),
                (FILE_KEYS, "file", ".bin"),
            )
            for obj in attachment_list(first_value(item, keys))
        ]

    def failed(self, kind, exc):
        if isinstance(exc, TransportError) or (isinstance(exc, HttpError) and exc.status in (401, 500, 502, 504)):
            raise exc
        self.errors += 1
        message = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
        LOG.warning("%s backup failed: %s", kind, message)

    def collection(
        self, path, folder, kind, *, since: date | None = None,
        discovered_centers: set | None = None, collection_id: str | None = None, **params,
    ):
        self.check_stop()
        label = KIND_LABELS.get(kind, kind)
        if collection_id and collection_id in self._checkpoint["completed_collections"]:
            LOG.info("[%s] 이전 실행에서 이미 완료된 단계이므로 건너뜁니다.", label)
            return None
        self._report_progress(kind)
        earliest: date | None = None
        older_streak = 0
        seen = 0
        saved_count = 0
        unchanged_count = 0
        skipped_old = 0
        errors_before = self.errors
        started = time.monotonic()
        LOG.info("[%s] 백업 시작%s", label, f" ({since.isoformat()} 이후)" if since else "")
        announced_total = False
        try:
            for item in self.client.items(path, **params):
                self.check_stop()
                seen += 1
                self._phase_seen = seen
                if not announced_total:
                    announced_total = True
                    raw_total = getattr(self.client, "last_count", None)
                    if type(raw_total) is int and raw_total > 0:
                        if since is not None:
                            LOG.info("[%s] 기관 전체 %d건 중 %s 이후 기록만 선별하여 백업합니다.",
                                     label, raw_total, since.isoformat())
                        else:
                            self._phase_total = raw_total
                            low_s, high_s = ESTIMATE_SECONDS_PER_ITEM.get(kind, (3, 10))
                            low_str = format_duration(raw_total * low_s)
                            high_str = format_duration(raw_total * high_s)
                            estimate_range = low_str if low_str == high_str else f"{low_str} ~ {high_str}"
                            LOG.info(
                                "[%s] 총 %d건 확인됨 (첫 백업 시 사진·영상 수에 따라 %s 정도 소요될 수 있습니다).",
                                label, raw_total, estimate_range,
                            )
                            self._report_progress()
                if self._phase_total is not None and seen > self._phase_total:
                    self._phase_total = None
                    self._phase_eta = None
                if self._phase_total and seen >= 3:
                    elapsed = time.monotonic() - started
                    remaining_items = max(0, self._phase_total - seen)
                    self._phase_eta = int(round((elapsed / seen) * remaining_items))
                item_date = record_date(item)
                if since is not None and item_date is not None:
                    if item_date < since:
                        older_streak += 1
                        skipped_old += 1
                        if older_streak >= OLDER_STOP_STREAK:
                            LOG.info("Stopping %s pagination after %d consecutive items before %s.",
                                     kind, older_streak, since.isoformat())
                            break
                        continue
                    older_streak = 0
                if item_date is not None:
                    earliest = item_date if earliest is None else min(earliest, item_date)
                if discovered_centers is not None:
                    try:
                        cid = _extract_center_id(item)
                        if cid:
                            discovered_centers.add(cid)
                    except BackupError:
                        pass
                try:
                    key = menu_key(item) if kind == "menus" else identifier(item.get("id"))
                    target = folder / key
                    record_key = f"{collection_id}/{key}" if collection_id else None
                    resumed_skip = (
                        record_key is not None
                        and record_key in self._checkpoint["saved_keys"]
                        and has_complete_local_record(target, kind)
                    )
                    if resumed_skip or (kind != "menus" and already_backed_up(target, item)):
                        self.unchanged += 1
                        unchanged_count += 1
                        self._report_progress()
                        if item_date is None or discovered_centers is not None:
                            try:
                                cached = json.loads((target / "meta.json").read_text(encoding="utf-8"))
                                if item_date is None:
                                    cached_date = record_date(cached)
                                    if cached_date is not None:
                                        earliest = cached_date if earliest is None else min(earliest, cached_date)
                                if discovered_centers is not None:
                                    cid = _extract_center_id(cached)
                                    if cid:
                                        discovered_centers.add(cid)
                            except (OSError, ValueError, BackupError):
                                pass
                        continue
                    saved = self.save(target, kind, item)
                    saved_count += 1
                    if record_key is not None:
                        self._checkpoint["saved_keys"].add(record_key)
                        self._flush_checkpoint()
                    saved_date = record_date(saved)
                    if saved_date is not None:
                        earliest = saved_date if earliest is None else min(earliest, saved_date)
                    if discovered_centers is not None:
                        try:
                            cid = _extract_center_id(saved)
                            if cid:
                                discovered_centers.add(cid)
                        except BackupError:
                            pass
                except (BackupError, OSError) as exc:
                    self.failed(kind, exc)
        except (BackupError, OSError) as exc:
            self.failed(kind, exc)
        if collection_id and self.errors == errors_before:
            self._checkpoint["completed_collections"].add(collection_id)
            self._flush_checkpoint()
        LOG.info(
            "[%s] 백업 완료 (소요 %s · 조회 %d건 · 저장 %d건 · 기존 %d건 · 기준일 이전 스킵 %d건)",
            label, format_duration(time.monotonic() - started), seen, saved_count, unchanged_count, skipped_old,
        )
        return earliest

    def monthly_schedules(self, center_id, class_id, folder, since: date | None, collection_id: str):
        self.check_stop()
        if collection_id in self._checkpoint["completed_collections"]:
            LOG.info("[일정표] 이전 실행에서 이미 완료된 단계이므로 건너뜁니다.")
            return
        self._report_progress("schedules")
        today = datetime.now(KST).date()
        start = since or (today - timedelta(days=31))
        end_year, end_month = today.year, today.month + 2
        if end_month > 12:
            end_year += 1
            end_month -= 12
        months = month_range(start, date(end_year, end_month, 1))
        started = time.monotonic()
        errors_before = self.errors
        saved_count = 0
        unchanged_count = 0
        seen = 0
        LOG.info("[일정표] 백업 시작 (%s ~ %s, %d개월)", months[0], months[-1], len(months))
        for ym in months:
            self.check_stop()
            query = {"month_event": ym, "including_birthday": ""}
            if class_id:
                query["cls"] = class_id
            url = f"{KIDSNOTE_BASE}{V1}/centers/{center_id}/calendar?{urlencode(query)}"
            try:
                payload = self.client.json_any(url)
            except HttpError as exc:
                if exc.status in (403, 404):
                    LOG.info("[일정표] 기관 %s에서 일정표 API를 사용할 수 없습니다 (HTTP %d).", center_id, exc.status)
                    break
                self.failed("schedules", exc)
                continue
            except (BackupError, OSError) as exc:
                self.failed("schedules", exc)
                continue
            entries = payload if isinstance(payload, list) else payload.get("results", [])
            if not isinstance(entries, list):
                continue
            for item in entries:
                if not isinstance(item, dict):
                    continue
                self.check_stop()
                item_date = record_date(item)
                if since is not None and item_date is not None and item_date < since:
                    continue
                seen += 1
                self._phase_seen = seen
                try:
                    key = schedule_key(item)
                    target = folder / key
                    record_key = f"{collection_id}/{key}"
                    if record_key in self._checkpoint["saved_keys"] and has_complete_local_record(target, "schedules"):
                        self.unchanged += 1
                        unchanged_count += 1
                        continue
                    self.save(target, "schedules", item)
                    saved_count += 1
                    self._checkpoint["saved_keys"].add(record_key)
                    self._flush_checkpoint()
                except (BackupError, OSError) as exc:
                    self.failed("schedules", exc)
        if self.errors == errors_before:
            self._checkpoint["completed_collections"].add(collection_id)
            self._flush_checkpoint()
        LOG.info(
            "[일정표] 백업 완료 (소요 %s · 조회 %d건 · 저장 %d건 · 기존 %d건)",
            format_duration(time.monotonic() - started), seen, saved_count, unchanged_count,
        )

    def monthly_temperatures(self, center_id, child_id, folder, since: date | None, collection_id: str):
        self.check_stop()
        if collection_id in self._checkpoint["completed_collections"]:
            LOG.info("[체온기록] 이전 실행에서 이미 완료된 단계이므로 건너뜁니다.")
            return
        self._report_progress("temperatures")
        today = datetime.now(KST).date()
        start = since or today
        months = month_range(start, today)
        started = time.monotonic()
        errors_before = self.errors
        saved_count = 0
        unchanged_count = 0
        seen = 0
        LOG.info("[체온기록] 백업 시작 (%s ~ %s, %d개월)", months[0], months[-1], len(months))
        for ym in months:
            self.check_stop()
            query = {"child_id": child_id, "measured_month": ym}
            url = f"{KIDSNOTE_BASE}{V1}/centers/{center_id}/children/temperatures?{urlencode(query)}"
            try:
                payload = self.client.json_any(url)
            except HttpError as exc:
                if exc.status in (403, 404):
                    LOG.info("[체온기록] 기관 %s에서 체온기록 API를 사용할 수 없습니다 (HTTP %d).", center_id, exc.status)
                    break
                self.failed("temperatures", exc)
                continue
            except (BackupError, OSError) as exc:
                self.failed("temperatures", exc)
                continue
            entries = payload if isinstance(payload, list) else payload.get("results", [])
            if not isinstance(entries, list) or not entries:
                continue
            seen += 1
            self._phase_seen = seen
            try:
                target = folder / ym
                record_key = f"{collection_id}/{ym}"
                if record_key in self._checkpoint["saved_keys"] and has_complete_local_record(target, "temperatures"):
                    self.unchanged += 1
                    unchanged_count += 1
                    continue
                record = {
                    "id": ym,
                    "title": f"{ym} 체온 기록 ({len(entries)}건)",
                    "date_written": f"{ym}-01",
                    "measured_month": ym,
                    "records": entries,
                }
                self.save(target, "temperatures", record)
                saved_count += 1
                self._checkpoint["saved_keys"].add(record_key)
                self._flush_checkpoint()
            except (BackupError, OSError) as exc:
                self.failed("temperatures", exc)
        if self.errors == errors_before:
            self._checkpoint["completed_collections"].add(collection_id)
            self._flush_checkpoint()
        LOG.info(
            "[체온기록] 백업 완료 (소요 %s · 기록 월 %d건 · 저장 %d건 · 기존 %d건)",
            format_duration(time.monotonic() - started), seen, saved_count, unchanged_count,
        )

    def run(self, children, options):
        loaded = load_checkpoint(self._checkpoint_path)
        if loaded:
            self._checkpoint = loaded
            LOG.info(
                "이전 중단 지점을 확인했습니다. 이미 완료된 %d개 단계와 %d건의 기록을 건너뛰고 이어서 백업합니다.",
                len(loaded["completed_collections"]), len(loaded["saved_keys"]),
            )
        center_since: dict[str, date | None] = {}
        center_classes: dict[str, str | None] = {}
        for cid, iso in self._checkpoint["center_since"].items():
            if iso is None:
                center_since[cid] = None
            else:
                try:
                    center_since[cid] = date.fromisoformat(iso)
                except ValueError:
                    center_since[cid] = None
        for child in children:
            self.check_stop()
            child_id = identifier(child.get("id"))
            root = self.root / "children" / child_id
            if isinstance(child.get("name"), str):
                write_json(root / "profile.json", {"name": child["name"]})
            report_centers: set[str] = set()
            earliest_report = self.collection(
                f"{V12}/children/{child_id}/reports/", root / "reports", "reports",
                alternate="reports", tz="Asia/Seoul", child=child_id,
                discovered_centers=report_centers, collection_id=f"child:{child_id}:reports",
            )
            if options["include_albums"]:
                self.collection(
                    f"{V1}/children/{child_id}/albums/", root / "albums", "albums",
                    alternate="albums", collection_id=f"child:{child_id}:albums",
                )
            child_enrollments: list[tuple[str, str | None]] = []
            try:
                child_enrollments = list(enrollments_info(child))
            except (AttributeError, BackupError, TypeError):
                if options["include_notices"] or options["include_menus"] or options.get("include_schedules"):
                    self.failed("centers", BackupError("Kidsnote returned invalid enrollment data."))
            if not child_enrollments and report_centers:
                child_enrollments = [(cid, None) for cid in sorted(report_centers)]
                LOG.info("Child %s: recovered %d center ID(s) from report metadata.", child_id, len(child_enrollments))
            if not child_enrollments and not center_since and (options["include_notices"] or options["include_menus"]):
                LOG.warning(
                    "Child %s: no center ID found (child keys: %s). Notices and menus cannot be fetched.",
                    child_id, ", ".join(sorted(str(k) for k in child.keys())),
                )
            for center_id, class_id in child_enrollments:
                if class_id and not center_classes.get(center_id):
                    center_classes[center_id] = class_id
                previous = center_since.get(center_id)
                if earliest_report is not None:
                    center_since[center_id] = (
                        earliest_report if previous is None else min(previous, earliest_report)
                    )
                else:
                    center_since.setdefault(center_id, None)
                child_since = earliest_report if earliest_report is not None else center_since.get(center_id)
                if options.get("include_medications"):
                    params = {"child": child_id}
                    if class_id:
                        params["cls"] = class_id
                    self.collection(
                        f"{V12}/centers/{center_id}/medications", root / "medications", "medications",
                        alternate="medications", since=child_since,
                        collection_id=f"child:{child_id}:center:{center_id}:medications", **params,
                    )
                if options.get("include_return_homes"):
                    params = {"child": child_id, "tz": "Asia/Seoul"}
                    if class_id:
                        params["cls"] = class_id
                    self.collection(
                        f"{V12}/centers/{center_id}/return-home", root / "return_homes", "return_homes",
                        alternate="return_homes", since=child_since,
                        collection_id=f"child:{child_id}:center:{center_id}:return_homes", **params,
                    )
                if options.get("include_temperatures"):
                    self.monthly_temperatures(
                        center_id, child_id, root / "temperatures", child_since,
                        collection_id=f"child:{child_id}:center:{center_id}:temperatures",
                    )
        self._checkpoint["center_since"] = {
            cid: (d.isoformat() if d is not None else None) for cid, d in center_since.items()
        }
        self._flush_checkpoint()
        LOG.info("Detected %d center(s) for notices/menus/schedules backup.", len(center_since))
        for center_id in sorted(center_since):
            root = self.root / "centers" / center_id
            since = center_since[center_id]
            if since is not None:
                LOG.info("Center %s: backing up center records from %s onward.", center_id, since.isoformat())
            if options["include_notices"]:
                self.collection(
                    f"{V1}/centers/{center_id}/notices/", root / "notices", "notices",
                    alternate="notices", since=since, collection_id=f"center:{center_id}:notices",
                )
            if options["include_menus"]:
                self.collection(
                    f"{V1}/centers/{center_id}/menu/", root / "menus", "menus",
                    alternate="menus", since=since, collection_id=f"center:{center_id}:menus",
                )
            if options.get("include_schedules"):
                self.monthly_schedules(
                    center_id, center_classes.get(center_id), root / "schedules", since,
                    collection_id=f"center:{center_id}:schedules",
                )
