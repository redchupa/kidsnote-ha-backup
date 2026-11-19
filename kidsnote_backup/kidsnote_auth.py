"""Kidsnote login, adapted from redchupa/kidsnote-backup under the MIT license."""

from __future__ import annotations

import http.client
import json
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from pacing import RequestPacer, TRANSIENT_DELAYS

KIDSNOTE_BASE = "https://www.kidsnote.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20  # seconds, per attempt
# Waits before retrying a login that collided with another login for the
# same account (HTTP 409 "Already exists."), e.g. two children's jobs.
LOGIN_BUSY_DELAYS = (5, 10, 20)


# The web login form cleans what the parent types before sending it (login
# page JS, 2026-09-14): id -> lower-case, first 32 chars, trimmed; password
# -> every whitespace and Korean character removed. Doing the same makes a
# secret behave exactly like typing it on kidsnote.com, and drops the stray
# newlines / BOMs that copy-pasted secrets often carry.
_BLANK = re.compile(r"[\s﻿]")  # JS \s matches U+FEFF (BOM); Python's \s doesn't
_TRIM = re.compile(r"^[\s﻿]+|[\s﻿]+$")
_KOREAN = re.compile(r"[ㄱ-ㅎ|ㅏ-ㅣ|가-힣]")  # the form's exact class, literal '|' included
_SESSIONID = re.compile(r"[A-Za-z0-9]+")


class AuthError(RuntimeError):
    """Login failed with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def normalize_username(value: str | None) -> str:
    return _TRIM.sub("", _TRIM.sub("", value or "").lower()[:32])


def normalize_password(value: str | None) -> str:
    return _KOREAN.sub("", _BLANK.sub("", value or ""))


def normalize_cookie(value: str | None) -> str:
    """Accept the bare value or a pasted 'sessionid=<value>; ...'; '' if it can't be a sessionid."""
    value = _BLANK.sub("", value or "").strip("\"'").split(";", 1)[0]
    if value.lower().startswith("sessionid="):
        value = value[len("sessionid="):].strip("\"'")
    return value if _SESSIONID.fullmatch(value) else ""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener(user_agent: str, jar: CookieJar | None = None) -> urllib.request.OpenerDirector:
    handlers = [urllib.request.HTTPCookieProcessor(jar), NoRedirects(), urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [
        ("User-Agent", user_agent),
        ("Accept", "application/json, text/plain, */*"),
        ("Accept-Language", "ko"),
    ]
    return opener


def _request(
    opener: urllib.request.OpenerDirector,
    pacer: RequestPacer,
    method: str,
    path: str,
    *,
    body: dict | None = None,
) -> tuple[int, bytes]:
    """Send a paced request, retrying transient failures. Returns (status, body)."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    for delay in (*TRANSIENT_DELAYS, None):
        req = urllib.request.Request(KIDSNOTE_BASE + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
            req.add_header("Origin", KIDSNOTE_BASE)
            req.add_header("Referer", f"{KIDSNOTE_BASE}/kr/login")
        pacer.wait()
        try:
            with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                retry_after = e.headers.get("Retry-After") if e.headers is not None else None
                pacer.check_response(status, retry_after)
                raw = e.read()
            except (OSError, http.client.HTTPException):
                raw = b""
            finally:
                e.close()
        except (OSError, http.client.HTTPException) as e:  # URLError, timeout, DNS, dropped connection
            if delay is None:
                raise AuthError("network", f"kidsnote.com unreachable: {e}") from e
            pacer.pause(delay + random.uniform(0, 3))
            continue
        if status in (500, 502, 504) and delay is not None:
            pacer.pause(delay + random.uniform(0, 3))
            continue
        return status, raw
    raise AssertionError("unreachable")


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _status_error(status: int, where: str) -> AuthError:
    if status == 429:
        return AuthError("rate_limited", f"HTTP 429 on {where}")
    if status >= 500:
        return AuthError("server_error", f"HTTP {status} on {where}")
    return AuthError("unexpected", f"HTTP {status} on {where}")


def login(
    username: str, password: str, user_agent: str = DEFAULT_USER_AGENT, *, pacer: RequestPacer | None = None,
) -> str:
    """Log in with id/password and return the new ``sessionid`` value."""
    username, password = normalize_username(username), normalize_password(password)
    if not username or not password:
        raise AuthError("missing", "KIDSNOTE_USERNAME / KIDSNOTE_PASSWORD is empty")
    jar = CookieJar()
    opener = _opener(user_agent, jar)
    pacer = pacer if pacer is not None else RequestPacer()

    quoted = urllib.parse.quote(username, safe="")
    status, raw = _request(opener, pacer, "GET", f"/api/v1/second-factors/{quoted}/")
    if status >= 500:
        raise _status_error(status, "two-step verification check")
    if status == 200:
        info = _json(raw)
        if info.get("is_enabled") and info.get("second_factors"):
            raise AuthError("2fa_enabled", "2-step verification is enabled on this account")
    # Any other status is not fatal: the SPA only uses this call to decide
    # whether to show the 2FA form, and the login call below reports errors.

    # kidsnote answers 409 "Already exists." while another login for the same
    # account is in flight (several children's jobs start together). A retry a
    # few seconds later succeeds, and both sessions stay valid (checked live).
    for attempt in range(len(LOGIN_BUSY_DELAYS) + 1):
        status, raw = _request(
            opener, pacer, "POST", "/api/web/login/",
            body={"username": username, "password": password, "remember_me": True},
        )
        if status != 409 or attempt == len(LOGIN_BUSY_DELAYS):
            break
        pacer.pause(LOGIN_BUSY_DELAYS[attempt] + random.uniform(0, 3))
    if status == 409:
        raise AuthError("login_busy", "HTTP 409 on /api/web/login/: another login for this account is in progress")
    if status != 200:
        if status == 429 or status >= 500:
            raise _status_error(status, "/api/web/login/")
        if _json(raw).get("err_code") == "blocked":
            raise AuthError("blocked", f"HTTP {status} on /api/web/login/: account blocked")
        if status in (400, 401, 403, 404):
            raise AuthError("invalid_credentials", f"HTTP {status} on /api/web/login/")
        raise _status_error(status, "/api/web/login/")

    sessionid = next((c.value for c in jar if c.name == "sessionid"), None) or ""
    if not _SESSIONID.fullmatch(sessionid):
        raise AuthError("unexpected", "login succeeded but kidsnote sent no usable sessionid cookie")
    return sessionid
