"""Download Kidsnote records into a durable, local file archive."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests

from client import BackupError, HttpError, checked_https, identifier
from kidsnote_auth import KIDSNOTE_BASE

LOG = logging.getLogger(__name__)
V1 = "/api/v1"
V12 = "/api/v1_2"
IMAGE_KEYS = ("attached_images", "attached_pictures", "pictures", "images")
VIDEO_KEYS = ("attached_video", "video", "attached_videos")
FILE_KEYS = ("attached_files", "files", "attachments")
URL_KEYS = ("original", "url", "src", "high", "high_resize", "large_resize")
MENU_PHOTOS = ("morning_img", "morning_snack_img", "lunch_img", "afternoon_snack_img", "dinner_img")
EXTENSION = re.compile(r"\.[a-z0-9]{1,10}", re.ASCII)


class Stopping(Exception):
    """The supervisor has requested shutdown."""


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


def center_ids(child):
    for enrollment in child.get("enrollment") or []:
        value = enrollment.get("center_id") or enrollment.get("center")
        if isinstance(value, dict):
            value = value.get("id")
        if value:
            yield identifier(value)


def menu_key(item):
    try:
        day = date.fromisoformat(str(item.get("date_menu", ""))[:10]).isoformat()
    except ValueError as exc:
        raise BackupError("Kidsnote returned an invalid menu date.") from exc
    return f"{day}_{identifier(item['id'])}" if item.get("id") is not None else day


class Archive:
    def __init__(self, client, root: Path, stop):
        self.client = client
        self.root = root
        self.stop = stop
        self.records = 0
        self.downloaded = 0
        self.errors = 0

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
                if exc.status == 429:
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
        else:
            item_id = identifier(item.get("id"))
            if kind == "reports":
                detail = self.client.json(f"{KIDSNOTE_BASE}{V12}/reports/{item_id}/")
                item = {**item, **detail}
            try:
                comments = list(self.client.items(f"{V1}/{kind}/{item_id}/comments/"))
            except HttpError as exc:
                # Comments are not exposed for every notice or album.
                if kind == "reports" or exc.status != 404:
                    raise
                comments = None
            item = {**item, "_comments": comments}
            attachments = []
            for keys, label, ext in (
                (IMAGE_KEYS, "image", ".jpg"), (VIDEO_KEYS, "video", ".mp4"),
                (FILE_KEYS, "file", ".bin"),
            ):
                for obj in attachment_list(first_value(item, keys)):
                    attachments.append(self.media(folder, obj, label, ext))
            body = first_value(item, ("content", "body", "report"))
            heading = item.get("title")
            lines = [value for value in (heading, body) if isinstance(value, str) and value]
            with atomic_file(folder / "note.txt") as handle:
                handle.write("\n\n".join(lines).encode("utf-8"))

        write_json(folder / "meta.json", {**item, "_local_attachments": attachments})
        self.records += 1
        if self.records % 100 == 0:
            LOG.info("Progress: %d records saved, %d new attachments.", self.records, self.downloaded)

    def failed(self, kind, exc):
        if isinstance(exc, HttpError) and exc.status in (401, 429):
            raise exc
        self.errors += 1
        message = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
        LOG.warning("%s backup failed: %s", kind, message)

    def collection(self, path, folder, kind, **params):
        self.check_stop()
        try:
            for item in self.client.items(path, **params):
                self.check_stop()
                try:
                    key = menu_key(item) if kind == "menus" else identifier(item.get("id"))
                    self.save(folder / key, kind, item)
                except (BackupError, OSError) as exc:
                    self.failed(kind, exc)
        except (BackupError, OSError) as exc:
            self.failed(kind, exc)

    def run(self, children, options):
        centers = set()
        for child in children:
            child_id = identifier(child.get("id"))
            root = self.root / "children" / child_id
            self.collection(
                f"{V12}/children/{child_id}/reports/", root / "reports", "reports",
                alternate="reports", tz="Asia/Seoul", child=child_id,
            )
            if options["include_albums"]:
                self.collection(f"{V1}/children/{child_id}/albums/", root / "albums", "albums")
            if options["include_notices"] or options["include_menus"]:
                try:
                    centers.update(center_ids(child))
                except (AttributeError, BackupError, TypeError):
                    self.failed("centers", BackupError("Kidsnote returned invalid enrollment data."))
        for center_id in sorted(centers):
            root = self.root / "centers" / center_id
            if options["include_notices"]:
                self.collection(f"{V1}/centers/{center_id}/notices/", root / "notices", "notices")
            if options["include_menus"]:
                self.collection(f"{V1}/centers/{center_id}/menu/", root / "menus", "menus")
