"""Read-only archive viewer served through Home Assistant Ingress."""
from __future__ import annotations

import json
import re
import tempfile
import zipfile
from dataclasses import asdict
from datetime import date, timedelta
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock

from flask import Flask, abort, request, send_file
from waitress import create_server

from archive import write_json
import doc_text
import stats
import supplies

WEB = Path(__file__).with_name("web")
GROUPS = {
    "reports": "children", "albums": "children", "notices": "centers", "menus": "centers",
    "schedules": "centers", "medications": "children", "return_homes": "children", "temperatures": "children",
}
ID = re.compile(r"[0-9]{1,30}")
MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
MENU_ID = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:_[0-9]{1,30})?")
SCHEDULE_ID = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}_(?:[0-9]{1,30}|e[a-f0-9]{12})")
FILENAME = re.compile(r"[a-zA-Z0-9_-]+\.[a-zA-Z0-9]{1,10}")
DEFAULT_TITLE = "키즈노트 보관함"
THEMES = ("auto", "white", "black", "blue", "ivory", "cute", "futuristic")
TABS = (
    "reports", "albums", "notices", "menus", "supplies", "calendar",
    "schedules", "medications", "return_homes", "temperatures", "stats",
)
MAX_CHECKED_SUPPLIES = 500
STAT_PERIODS = {"30": 30, "90": 90, "180": 180, "365": 365, "all": None}
MAX_REVIEWED = 2000
MAX_NOTES = 3000
MAX_MEMO = 1000
DAY = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])")
HOME_FLAGS = {
    "bruise": "멍·상처", "mood": "기분 변화", "sleep": "잠 변화", "meal": "식사 변화",
    "fear": "무서워함·놀람", "avoid": "등원 거부", "sick": "아픔",
}
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
    return supplies.to_date(
        item.get("date_written") or item.get("date_menu") or item.get("date_event")
        or item.get("date_medicated") or item.get("datetime_requested") or item.get("created")
    )


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
    pattern = (
        MENU_ID if kind == "menus"
        else SCHEDULE_ID if kind == "schedules"
        else MONTH if kind == "temperatures"
        else ID
    )
    if not pattern.fullmatch(item):
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


@lru_cache(maxsize=512)
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
    doc_files = tuple(
        p for entry in (item, *comments(item))
        for p, _ in attachments(entry, path.parent)
        if doc_text.can_extract(p.name)
    )
    menu = "\n".join(plain(item.get(key)) for key in MENU)
    extra_parts = []
    if item.get("symptom"):
        extra_parts.append(plain(item.get("symptom")))
    if item.get("method") or item.get("guardian_name"):
        extra_parts.append(" · ".join(p for p in (plain(item.get("method")), plain(item.get("guardian_name"))) if p))
    if item.get("address_summary"):
        extra_parts.append(plain(item.get("address_summary")))
    extra = " ".join(extra_parts)
    fallback_title = (text.splitlines() or [""])[0][:66] or extra[:66]
    return {
        "title": title or fallback_title,
        "date": day.isoformat() if day else "",
        "excerpt": " ".join((text or menu or extra).split())[:130],
        "cover": cover,
        "has_video": has_video,
        "doc_files": doc_files,
        "search": "\n".join([title, text, menu, extra] + [body(comment) for comment in comments(item)]).casefold(),
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
    with STATE_LOCK:
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


def read_reviewed(root):
    items = read_json(root / "reviewed_reports.json").get("reviewed")
    if not isinstance(items, list):
        return []
    return [entry for entry in items if isinstance(entry, str) and 0 < len(entry) <= 100]


def update_reviewed(root, key, reviewed):
    with STATE_LOCK:
        items = [entry for entry in read_reviewed(root) if entry != key]
        if reviewed:
            items = (items + [key])[-MAX_REVIEWED:]
        write_json(root / "reviewed_reports.json", {"reviewed": items})
    return items


def read_notes(root):
    raw = read_json(root / "home_notes.json").get("notes")
    notes = {}
    if not isinstance(raw, dict):
        return notes
    for owner, days in raw.items():
        if not (isinstance(owner, str) and ID.fullmatch(owner) and isinstance(days, dict)):
            continue
        for day, note in days.items():
            if not (isinstance(day, str) and DAY.fullmatch(day) and isinstance(note, dict)):
                continue
            flags = [flag for flag in note.get("flags") or [] if flag in HOME_FLAGS]
            memo = note.get("memo") if isinstance(note.get("memo"), str) else ""
            if flags or memo:
                notes.setdefault(owner, {})[day] = {"flags": flags, "memo": memo[:MAX_MEMO]}
    return notes


def note_view(day, note):
    return {"date": day, "flags": note["flags"], "labels": [HOME_FLAGS[flag] for flag in note["flags"]],
            "memo": note["memo"]}


def save_note(root, payload):
    if not isinstance(payload, dict):
        abort(400)
    owner, day = payload.get("owner"), payload.get("date")
    flags, memo = payload.get("flags", []), payload.get("memo", "")
    if (not isinstance(owner, str) or not ID.fullmatch(owner) or not isinstance(day, str)
            or not DAY.fullmatch(day) or not isinstance(flags, list) or not isinstance(memo, str)
            or any(flag not in HOME_FLAGS for flag in flags) or len(memo) > MAX_MEMO):
        abort(400)
    try:
        date.fromisoformat(day)
    except ValueError:
        abort(400)
    memo = memo.strip()
    with STATE_LOCK:
        notes = read_notes(root)
        days = notes.setdefault(owner, {})
        if flags or memo:
            days[day] = {"flags": sorted(set(flags), key=list(HOME_FLAGS).index), "memo": memo}
        else:
            days.pop(day, None)
        if sum(len(entries) for entries in notes.values()) > MAX_NOTES:
            abort(413)
        write_json(root / "home_notes.json", {"notes": notes})
    return notes.get(owner, {})


def photo_days(root, owner):
    days = set()
    for kind in ("albums", "reports"):
        for key, path in records(root, kind):
            if key.split("/")[1] != owner:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            record = summary(path, stat.st_mtime_ns, stat.st_size)
            if record and record["cover"] and record["date"]:
                days.add(date.fromisoformat(record["date"]))
    return days


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


def item_fields(kind, data):
    if kind in ("menus", "reports"):
        values = MENU if kind == "menus" else LIFE
        return [{"label": label, "value": display(data[field], field)}
                for field, label in values.items() if data.get(field) not in (None, "", [], {})]
    fields = []
    if kind == "schedules":
        for key, label in (("class_name", "반"), ("address_summary", "장소"), ("author_name", "작성자")):
            if data.get(key):
                fields.append({"label": label, "value": plain(str(data[key]))})
    elif kind == "medications":
        if data.get("class_name"):
            fields.append({"label": "반", "value": plain(str(data["class_name"]))})
        if data.get("symptom"):
            fields.append({"label": "증상", "value": plain(str(data["symptom"]))})
        med_items = data.get("items") or []
        if isinstance(med_items, list) and med_items:
            pres_map = {"room": "실온", "refrigerated": "냉장", "cold": "냉장"}
            lines = []
            for entry in med_items:
                if not isinstance(entry, dict):
                    continue
                parts = []
                if entry.get("medicine_type"):
                    parts.append(str(entry["medicine_type"]))
                if entry.get("dosage"):
                    parts.append(f"용량 {entry['dosage']}")
                if entry.get("frequency"):
                    parts.append(f"{entry['frequency']}회")
                if entry.get("medication_time"):
                    parts.append(f"시간 {entry['medication_time']}")
                if entry.get("preservation"):
                    parts.append(f"보관 {pres_map.get(str(entry['preservation']), entry['preservation'])}")
                if entry.get("special_note"):
                    parts.append(f"특이사항 {entry['special_note']}")
                if parts:
                    lines.append(" · ".join(parts))
            if lines:
                fields.append({"label": "투약 내용", "value": "\n".join(lines)})
        report = data.get("report")
        if isinstance(report, dict):
            rep_parts = []
            if report.get("reporter_name"):
                rep_parts.append(f"담당 {report['reporter_name']}")
            if report.get("special_note"):
                rep_parts.append(str(report["special_note"]))
            if rep_parts:
                fields.append({"label": "투약 완료 보고", "value": " · ".join(rep_parts)})
    elif kind == "return_homes":
        for key, label in (
            ("class_name", "반"),
            ("datetime_requested", "귀가 요청 일시"),
            ("method", "귀가 방법"),
            ("guardian_name", "보호자/인계자"),
        ):
            if data.get(key):
                fields.append({"label": label, "value": plain(str(data[key]))})
        if "is_confirmed" in data:
            fields.append({"label": "원 확인", "value": "확인 완료" if data.get("is_confirmed") else "미확인"})
        if data.get("date_confirmed"):
            fields.append({"label": "확인 시각", "value": plain(str(data["date_confirmed"]))})
    elif kind == "temperatures":
        for idx, entry in enumerate(data.get("records") or [], 1):
            if isinstance(entry, dict):
                rendered = display(entry)
                if rendered:
                    fields.append({"label": f"기록 {idx}", "value": rendered})
    return fields


def create_app(root, proxy="172.30.32.2", backup_controller=None):
    root = Path(root).resolve()
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

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
            if month and not record["date"].startswith(month):
                continue
            if query and not all(word in record["search"] for word in query):
                if not record["doc_files"]:
                    continue
                combined = record["search"] + "\n" + "\n".join(document(p) for p in record["doc_files"]).casefold()
                if not all(word in combined for word in query):
                    continue
            items.append({"key": key, **{
                field: value for field, value in record.items() if field not in ("search", "doc_files")
            }})
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
        fields = item_fields(kind, data)
        home_note = None
        if kind == "reports" and day:
            note = read_notes(root).get(key.split("/")[1], {}).get(day.isoformat())
            home_note = note_view(day.isoformat(), note) if note else None
        return {
            "home_note": home_note,
            "title": plain(data.get("title")),
            "body": body(data),
            "date": day.isoformat() if day else "",
            "author": plain(author(data).get("name") or data.get("author_name")),
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
        today_iso = today.isoformat()
        month = request.args.get("month") or today.strftime("%Y-%m")
        owner = request.args.get("owner", "")
        if not MONTH.fullmatch(month) or (owner and not ID.fullmatch(owner)):
            abort(400)
        month_start = f"{month}-01"
        month_end = f"{month}-31"
        events = []
        if month_end >= today_iso:
            board_data = serialize_board(root)
            events = [
                entry for entry in board_data["events"]
                if entry["start"] <= month_end and entry["end"] >= month_start
                and (
                    not owner
                    or not entry.get("page_id", "").startswith("reports/")
                    or entry.get("page_id", "").split("/")[1:2] == [owner]
                )
            ]
        child_names = {}
        child_ids = set()
        for kind in ("reports", "albums"):
            for key, _ in records(root, kind):
                child_ids.add(key.split("/")[1])
        for child_id in sorted(child_ids):
            child_names[child_id] = plain(read_json(root / "children" / child_id / "profile.json").get("name"))
        items = []
        for kind in ("reports", "albums", "notices", "menus", "schedules", "medications", "return_homes"):
            for key, path in records(root, kind):
                group_id = key.split("/")[1]
                is_child_kind = GROUPS[kind] == "children"
                if owner and is_child_kind and group_id != owner:
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
                    "owner_name": child_names.get(group_id, "") if is_child_kind else "",
                })
        items.sort(key=lambda entry: (entry["date"], entry["kind"], entry["key"]), reverse=True)
        return {
            "today": today_iso,
            "month": month,
            "owners": [{"id": cid, "name": child_names[cid]} for cid in sorted(child_ids)],
            "events": events,
            "records": items,
        }

    @app.get("/api/stats")
    def stats_view():
        owner = request.args.get("owner", "")
        period = request.args.get("days", "90")
        if (owner and not ID.fullmatch(owner)) or period not in STAT_PERIODS:
            abort(400)
        by_owner = {}
        for key, path in records(root, "reports"):
            by_owner.setdefault(key.split("/")[1], []).append((key, path))
        owners = [{"id": cid, "name": plain(read_json(root / "children" / cid / "profile.json").get("name"))}
                  for cid in sorted(by_owner)]
        if not owner and owners:
            owner = owners[0]["id"]
        name = next((entry["name"] for entry in owners if entry["id"] == owner), "")
        reports = []
        for key, path in by_owner.get(owner, []):
            item = read_json(path)
            if item:
                reports.append((key, item, body(item), date_of(item)))
        result = stats.analyze(reports, child_name=name, today=supplies.today_kst(), days=STAT_PERIODS[period],
                               photo_days=photo_days(root, owner) if owner else ())
        notes = read_notes(root).get(owner, {})
        return {"owners": owners, "owner": owner, "days": period, **result,
                "reviewed": read_reviewed(root),
                "home_flags": [{"id": flag, "label": label} for flag, label in HOME_FLAGS.items()],
                "notes": [note_view(day, note) for day, note in sorted(notes.items(), reverse=True)[:60]]}

    @app.post("/api/stats/review")
    def stats_review():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        key, reviewed = payload.get("key"), payload.get("reviewed")
        if not isinstance(key, str) or not key.startswith("reports/") or meta_path(root, key) is None                 or type(reviewed) is not bool:
            abort(400)
        return {"reviewed": update_reviewed(root, key, reviewed)}

    @app.post("/api/home-notes")
    def home_notes():
        payload = request.get_json(silent=True)
        notes = save_note(root, payload)
        return {"notes": [note_view(day, note) for day, note in sorted(notes.items(), reverse=True)[:60]]}

    @app.get("/api/backup/status")
    def backup_status():
        status = read_json(root / "status.json")
        if backup_controller is None:
            return {"status": status, "running": False, "cooldown_until": 0.0, "next_run": 0.0, "can_trigger": False}
        return {"status": status, **backup_controller.snapshot(), "can_trigger": True}

    @app.post("/api/backup/trigger")
    def backup_trigger():
        if not request.is_json:
            abort(400)
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
                    suffix = attachment.suffix.lower()
                    mime = IMAGE.get(suffix) or VIDEO.get(suffix)
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
        try:
            used = set()
            with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_STORED) as bundle:
                for attachment, label in files:
                    arcname = safe_zip_name(label, attachment.name, used)
                    bundle.write(attachment, arcname=arcname)
            spool.seek(0)
        except Exception:
            spool.close()
            raise
        day = date_of(data)
        prefix = day.isoformat() if day else entry
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", plain(data.get("title"))).strip("_")[:30]
        zip_name = f"{prefix}_{title}.zip" if title else f"{prefix}_{kind}.zip"
        return send_file(spool, mimetype="application/zip", as_attachment=True, download_name=zip_name)

    return app


def viewer_server(root, backup_controller=None):
    return create_server(create_app(root, backup_controller=backup_controller), host="0.0.0.0", port=8099, threads=8)
