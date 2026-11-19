"""Read-only archive viewer served through Home Assistant Ingress."""
from __future__ import annotations

import json
import re
import tempfile
import zipfile
from dataclasses import asdict
from datetime import timedelta
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock

from flask import Flask, abort, request, send_file
from waitress import create_server

from archive import write_json
import doc_text
import supplies

WEB = Path(__file__).with_name("web")
GROUPS = {"reports": "children", "albums": "children", "notices": "centers", "menus": "centers"}
ID = re.compile(r"[0-9]{1,30}")
MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
MENU_ID = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:_[0-9]{1,30})?")
FILENAME = re.compile(r"[a-zA-Z0-9_-]+\.[a-zA-Z0-9]{1,10}")
DEFAULT_TITLE = "키즈노트 보관함"
THEMES = ("auto", "white", "black", "blue", "ivory", "cute", "futuristic")
TABS = ("reports", "albums", "notices", "menus", "supplies", "calendar")
MAX_CHECKED_SUPPLIES = 500
STATE_LOCK = Lock()
IMAGE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif"}
VIDEO = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".m4v": "video/mp4"}
LIFE = {
    "meal_status": "식사", "sleep_hour": "수면", "bowel_status": "배변",
    "temperature_status": "체온 상태", "temperature": "체온", "mood_status": "기분",
    "health_status": "건강", "outdoor_activity_status": "야외활동", "bath_status": "목욕",
    "nail_status": "손톱", "activity_rate": "활동", "food": "식사 기록", "sleep": "수면 기록",
    "bowel": "배변 기록", "nursing": "수유 기록",
}
MENU = {"morning": "아침", "morning_snack": "오전 간식", "lunch": "점심",
        "afternoon_snack": "오후 간식", "dinner": "저녁"}
STATUS = {
    "good": "좋음", "average": "보통", "bad": "나쁨", "normal": "정상", "high": "고열",
    "low": "낮음", "slight": "미열", "active": "활발", "calm": "차분", "fixed": "정량",
    "more": "많이", "less": "적게", "none": "안 했어요", "no_sleep": "잠을 안 잤어요",
    "below_1": "1시간 미만", "under_30m": "30분 이내", "30m_to_1": "30분~1시간",
    "1_to_1.5": "1시간~1시간 30분", "1.5_to_2": "1시간 30분~2시간", "over_2": "2시간 이상",
    "sleep_well": "잠을 푹 잤어요", "sleep_hardly": "잠을 설쳤어요", "sleep_late": "늦잠 잤어요",
    "slp_good": "단잠", "slp_normal": "보통", "slp_bad": "부족",
    "ml_free": "자율배식", "ml_one": "1회", "ml_two": "2회", "bw_no": "안 했어요",
    "bw_one": "1회", "bw_two": "2회", "soft": "묽음", "watery": "묽음", "hard": "딱딱함",
    "diarrhea": "설사", "sick": "아픔", "fine": "양호", "bath": "목욕", "shower": "샤워",
    "cut": "자름", "ok": "양호", "trimmed": "정리됨", "needs_trim": "정리 필요",
}
DETAIL_KEYS = {"time_meal": "시간", "name": "내용", "time_start": "시작", "time_end": "종료",
               "time_bowel": "시간", "status": "상태", "time_nursing": "시간", "volume": "양",
               "body_temp_am": "오전", "body_temp_pm": "오후", "am": "오전", "pm": "오후"}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    if not isinstance(value, str):
        return ""
    if not re.search(r"</?(?:p|div|br|li|ul|ol|span|strong|a|img|table)\b", value, re.I):
        return value.strip()
    parser = PlainText()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts).strip()


def body(item):
    return plain(item.get("content") or item.get("body") or item.get("report"))


def display(value, key=""):
    if isinstance(value, bool):
        return "했어요" if value else "안 했어요"
    if isinstance(value, list):
        return "\n".join(rendered for entry in value if (rendered := display(entry, key)))
    if isinstance(value, dict):
        return " · ".join(
            f"{DETAIL_KEYS.get(field, field)} {display(entry, key)}"
            for field, entry in value.items()
            if field != "id" and entry not in ("", None, [], {})
        )
    if value is None:
        return ""
    if key == "temperature":
        return f"{value} °C"
    if key == "activity_rate":
        return {"10": "적극적", "20": "보통", "30": "소극적"}.get(str(value), str(value))
    return STATUS.get(str(value), str(value))


def author(item):
    value = item.get("author")
    return value if isinstance(value, dict) else {}


def comments(item):
    return [comment for comment in (item.get("_comments") or []) if isinstance(comment, dict)]


def date_of(item):
    return supplies.to_date(item.get("date_written") or item.get("date_menu") or item.get("created"))


def safe_path(path):
    return path.resolve() == path and path.is_file()


def read_json(path):
    try:
        if safe_path(path):
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
    except (OSError, ValueError):
        pass
    return {}


def meta_path(root, key):
    parts = key.split("/")
    if len(parts) != 3:
        return None
    kind, owner, item = parts
    if kind not in GROUPS or not ID.fullmatch(owner):
        return None
    if not (MENU_ID if kind == "menus" else ID).fullmatch(item):
        return None
    return root / GROUPS[kind] / owner / kind / item / "meta.json"


def attachments(item, folder):
    for asset in item.get("_local_attachments") or []:
        filename = asset.get("file", "") if isinstance(asset, dict) else ""
        if not isinstance(filename, str) or not FILENAME.fullmatch(filename):
            continue
        path = folder / filename
        if safe_path(path):
            yield path, plain(asset.get("original_name")) or filename


def asset_summary(path, name):
    suffix = path.suffix.lower()
    kind = "image" if suffix in IMAGE else "video" if suffix in VIDEO else "file"
    return {"file": path.name, "name": name, "type": kind}


@lru_cache(maxsize=24)
def document_text(path, mtime, size):
    if size > doc_text.MAX_INPUT_BYTES:
        return ""
    try:
        with path.open("rb") as handle:
            return doc_text.extract_text(handle.read(doc_text.MAX_INPUT_BYTES + 1), path.name)
    except OSError:
        return ""


def document(path):
    if doc_text.can_extract(path.name):
        try:
            stat = path.stat()
            return document_text(path, stat.st_mtime_ns, stat.st_size)
        except OSError:
            pass
    return ""


def records(root, kind):
    for path in (root / GROUPS[kind]).glob(f"*/{kind}/*/meta.json"):
        key = f"{kind}/{path.parents[2].name}/{path.parent.name}"
        if meta_path(root, key) == path:
            yield key, path


@lru_cache(maxsize=4096)
def summary(path, mtime, size):
    item = read_json(path)
    if not item:
        return None
    text = body(item)
    title = plain(item.get("title"))
    day = date_of(item)
    cover = next((p.name for p, _ in attachments(item, path.parent) if p.suffix.lower() in IMAGE), "")
    has_video = any(
        p.suffix.lower() in VIDEO
        for entry in (item, *comments(item))
        for p, _ in attachments(entry, path.parent)
    )
    menu = "\n".join(plain(item.get(key)) for key in MENU)
    doc_texts = [
        extracted
        for entry in (item, *comments(item))
        for p, _ in attachments(entry, path.parent)
        if (extracted := document(p))
    ]
    return {
        "title": title or (text.splitlines() or [""])[0][:66],
        "date": day.isoformat() if day else "",
        "excerpt": " ".join((text or menu).split())[:130],
        "cover": cover,
        "has_video": has_video,
        "search": "\n".join([title, text, menu] + [body(comment) for comment in comments(item)] + doc_texts).casefold(),
    }


def make_board(root):
    today = supplies.today_kst()
    oldest = today - timedelta(days=supplies.EVENT_LOOKBACK_DAYS)
    posts = []
    for kind, label in (("reports", "알림장"), ("notices", "공지")):
        for key, path in records(root, kind):
            item = read_json(path)
            day = date_of(item)
            if day is None or day < oldest or day > today:
                continue
            item_id = int(path.parent.name)
            title = plain(item.get("title"))
            for entry, entry_label, entry_date in [
                (item, label, day),
                *((comment, "선생님 댓글", date_of(comment) or day) for comment in comments(item)),
            ]:
                if str(author(entry).get("type", "")).lower() not in supplies.TEACHER_TYPES:
                    continue
                text = body(entry)
                if entry is item and kind == "notices" and title:
                    text = f"{title}\n{text}"
                posts.append(supplies.Post(entry_label, item_id, entry_date, text, title, key))
                for attachment, name in attachments(entry, path.parent):
                    if extracted := document(attachment):
                        posts.append(supplies.Post(f"첨부 · {name}", item_id, entry_date, extracted, title, key))
    return asdict(supplies.build_board(posts, today=today))


def serialize_asset(path, name):
    return {**asset_summary(path, name), "text": document(path)}


def serialize_board(root):
    data = make_board(root)
    data["today"] = data["today"].isoformat()
    for entry in data["events"] + data["requests"]:
        for key in ("start", "end", "posted"):
            if key in entry:
                entry[key] = entry[key].isoformat()
    return data


def read_settings(root):
    raw = read_json(root / "ui_settings.json")
    title = raw.get("title")
    title = " ".join(title.split())[:40] if isinstance(title, str) and title.strip() else DEFAULT_TITLE
    theme = raw.get("theme") if raw.get("theme") in THEMES else "auto"
    default_tab = raw.get("default_tab") if raw.get("default_tab") in TABS else "reports"
    return {"title": title, "theme": theme, "default_tab": default_tab}


def save_settings(root, payload):
    if not isinstance(payload, dict):
        abort(400)
    current = read_settings(root)
    if "title" in payload:
        title = payload["title"]
        if not isinstance(title, str):
            abort(400)
        cleaned = " ".join(title.split())[:40]
        current["title"] = cleaned or DEFAULT_TITLE
    if "theme" in payload:
        if payload["theme"] not in THEMES:
            abort(400)
        current["theme"] = payload["theme"]
    if "default_tab" in payload:
        if payload["default_tab"] not in TABS:
            abort(400)
        current["default_tab"] = payload["default_tab"]
    with STATE_LOCK:
        write_json(root / "ui_settings.json", current)
    return current


def read_checked_supplies(root):
    raw = read_json(root / "checked_supplies.json")
    items = raw.get("checked")
    if not isinstance(items, list):
        return []
    return [entry for entry in items if isinstance(entry, str) and 0 < len(entry) <= 300]


def update_checked_supplies(root, item_id, checked):
    with STATE_LOCK:
        items = read_checked_supplies(root)
        if checked:
            if item_id in items:
                items.remove(item_id)
            items.append(item_id)
            items = items[-MAX_CHECKED_SUPPLIES:]
        else:
            items = [entry for entry in items if entry != item_id]
        write_json(root / "checked_supplies.json", {"checked": items})
    return items


def safe_zip_name(label, fallback, used):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(label or fallback).name).strip(" .") or fallback
    stem, suffix = Path(cleaned).stem or "file", Path(cleaned).suffix
    candidate = cleaned
    index = 2
    while candidate.casefold() in used:
        candidate = f"{stem}_{index}{suffix}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def create_app(root, proxy="172.30.32.2", backup_controller=None):
    root = Path(root).resolve()
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False

    @app.before_request
    def ingress_only():
        groups = request.headers.get("X-Remote-User-Groups", "").split(",")
        if request.remote_addr != proxy or "system-admin" not in groups:
            abort(403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "media-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'; form-action 'none'"
        )
        return response

    @app.get("/")
    def index():
        return send_file(WEB / "index.html")

    @app.get("/assets/<name>")
    def assets(name):
        if name not in {"viewer.css", "viewer.js"}:
            abort(404)
        return send_file(WEB / name)

    @app.get("/api/settings")
    def get_settings():
        return read_settings(root)

    @app.post("/api/settings")
    def post_settings():
        payload = request.get_json(silent=True)
        return save_settings(root, payload)

    @app.get("/api/catalog")
    def catalog():
        kind = request.args.get("kind", "reports")
        owner = request.args.get("owner", "")
        query = request.args.get("q", "")[:200].casefold().split()
        month = request.args.get("month", "")
        only_video = request.args.get("video", "").lower() in ("1", "true")
        if kind not in GROUPS or (owner and not ID.fullmatch(owner)):
            abort(400)
        page = max(1, request.args.get("page", 1, type=int))
        items, owners, months = [], set(), set()
        for key, path in records(root, kind):
            try:
                stat = path.stat()
            except OSError:
                continue
            record = summary(path, stat.st_mtime_ns, stat.st_size)
            if not record:
                continue
            group_id = key.split("/")[1]
            owners.add(group_id)
            if owner and owner != group_id:
                continue
            if record["date"]:
                months.add(record["date"][:7])
            if only_video and not record["has_video"]:
                continue
            if (month and not record["date"].startswith(month)) or not all(word in record["search"] for word in query):
                continue
            items.append({"key": key, **{field: value for field, value in record.items() if field != "search"}})
        items.sort(key=lambda item: (item["date"], item["key"]), reverse=True)
        filters = [
            {"id": value, "name": plain(read_json(root / GROUPS[kind] / value / "profile.json").get("name"))}
            for value in sorted(owners)
        ]
        status = read_json(root / "status.json")
        return {"items": items[(page - 1) * 12:page * 12], "total": len(items), "owners": filters,
                "months": sorted(months, reverse=True), "status": status}

    @app.get("/api/item")
    def item():
        key = request.args.get("key", "")
        path = meta_path(root, key)
        data = read_json(path) if path else {}
        if not data:
            abort(404)
        day = date_of(data)
        kind = key.split("/")[0]
        values = MENU if kind == "menus" else LIFE if kind == "reports" else {}
        fields = [{"label": label, "value": display(data[field], field)}
                  for field, label in values.items() if data.get(field) not in (None, "", [], {})]
        return {
            "title": plain(data.get("title")),
            "body": body(data),
            "date": day.isoformat() if day else "",
            "author": plain(author(data).get("name")),
            "fields": fields,
            "attachments": [serialize_asset(p, n) for p, n in attachments(data, path.parent)],
            "comments": [{
                "body": body(comment),
                "name": plain(author(comment).get("name") or comment.get("author_name")),
                "date": date.isoformat() if (date := date_of(comment)) else "",
                "attachments": [serialize_asset(p, n) for p, n in attachments(comment, path.parent)],
            } for comment in comments(data)],
        }

    @app.get("/api/board")
    def board():
        data = serialize_board(root)
        data["checked"] = read_checked_supplies(root)
        return data

    @app.post("/api/supplies/check")
    def check_supply():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        item_id = payload.get("id")
        checked = payload.get("checked")
        if not isinstance(item_id, str) or not (0 < len(item_id) <= 300) or type(checked) is not bool:
            abort(400)
        return {"checked": update_checked_supplies(root, item_id, checked)}

    @app.get("/api/calendar")
    def calendar_month():
        today = supplies.today_kst()
        month = request.args.get("month") or today.strftime("%Y-%m")
        owner = request.args.get("owner", "")
        if not MONTH.fullmatch(month) or (owner and not ID.fullmatch(owner)):
            abort(400)
        board_data = serialize_board(root)
        month_start = f"{month}-01"
        month_end = f"{month}-31"
        events = [
            entry for entry in board_data["events"]
            if entry["start"] <= month_end and entry["end"] >= month_start
        ]
        child_names = {}
        child_ids = set()
        for kind in ("reports", "albums"):
            for key, _ in records(root, kind):
                child_ids.add(key.split("/")[1])
        for child_id in sorted(child_ids):
            child_names[child_id] = plain(read_json(root / "children" / child_id / "profile.json").get("name"))
        items = []
        for kind in ("reports", "albums", "notices", "menus"):
            for key, path in records(root, kind):
                group_id = key.split("/")[1]
                if owner and kind in ("reports", "albums") and group_id != owner:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                record = summary(path, stat.st_mtime_ns, stat.st_size)
                if not record or not record["date"].startswith(month):
                    continue
                items.append({
                    "key": key,
                    "kind": kind,
                    "date": record["date"],
                    "title": record["title"],
                    "excerpt": record["excerpt"],
                    "cover": record["cover"],
                    "owner": group_id,
                    "owner_name": child_names.get(group_id, "") if kind in ("reports", "albums") else "",
                })
        items.sort(key=lambda entry: (entry["date"], entry["kind"], entry["key"]), reverse=True)
        return {
            "today": board_data["today"],
            "month": month,
            "owners": [{"id": cid, "name": child_names[cid]} for cid in sorted(child_ids)],
            "events": events,
            "records": items,
        }

    @app.get("/api/backup/status")
    def backup_status():
        status = read_json(root / "status.json")
        if backup_controller is None:
            return {"status": status, "running": False, "cooldown_until": 0.0, "next_run": 0.0, "can_trigger": False}
        return {"status": status, **backup_controller.snapshot(), "can_trigger": True}

    @app.post("/api/backup/trigger")
    def backup_trigger():
        if backup_controller is None:
            return {"accepted": False, "message": "수동 백업이 비활성화되어 있습니다."}, 503
        code, payload = backup_controller.trigger()
        return payload, code

    @app.get("/media/<kind>/<owner>/<entry>/<name>")
    def media(kind, owner, entry, name):
        path = meta_path(root, f"{kind}/{owner}/{entry}")
        data = read_json(path) if path else {}
        if not data or not FILENAME.fullmatch(name):
            abort(404)
        for item in (data, *comments(data)):
            for attachment, label in attachments(item, path.parent):
                if attachment.name == name:
                    mime = IMAGE.get(attachment.suffix) or VIDEO.get(attachment.suffix)
                    return send_file(attachment, mimetype=mime or "application/octet-stream",
                                     as_attachment="download" in request.args or not mime,
                                     download_name=label, conditional=True)
        abort(404)

    @app.get("/media-zip/<kind>/<owner>/<entry>")
    def media_zip(kind, owner, entry):
        path = meta_path(root, f"{kind}/{owner}/{entry}")
        data = read_json(path) if path else {}
        if not data:
            abort(404)
        files = [
            (attachment, label)
            for record in (data, *comments(data))
            for attachment, label in attachments(record, path.parent)
        ]
        if not files:
            abort(404)
        spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
        used = set()
        with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_STORED) as bundle:
            for attachment, label in files:
                arcname = safe_zip_name(label, attachment.name, used)
                bundle.write(attachment, arcname=arcname)
        spool.seek(0)
        day = date_of(data)
        prefix = day.isoformat() if day else entry
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", plain(data.get("title"))).strip("_")[:30]
        zip_name = f"{prefix}_{title}.zip" if title else f"{prefix}_{kind}.zip"
        return send_file(spool, mimetype="application/zip", as_attachment=True, download_name=zip_name)

    return app


def viewer_server(root, backup_controller=None):
    return create_server(create_app(root, backup_controller=backup_controller), host="0.0.0.0", port=8099, threads=8)
