"""Crash-conservative GitHub-backed Slack delivery records.

GitHub is the durable authority. Initialization is explicit, source snapshots
are immutable, and Slack transport outcomes require conservative evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import argparse
import hashlib
import http.client
import json
import math
import os
import re
import signal
import sys
import time
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.error import URLError
from uuid import uuid4
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 1
OVERLAP_SECONDS = 60
MAX_ATTEMPTS = 3
SHARD_RECORDS = 64
SHARD_BYTES = 48 * 1024
BUDGET_SECONDS = 30
MIN_SEND_INTERVAL = 1
RECORD_BYTES = 40 * 1024
MAX_VISIBLE_TEXT = 36_000
MAX_BLOCKS = 20
BLOCK_TEXT_CHARS = 3000
# Shared transport serves 100 near-48 KiB GitHub shards, including outer JSON
# Unicode escaping and comment envelopes, while bounding any untrusted response.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
REPO = "DropNowOfficial/quant-detective"
CHANNEL_ID = "C0C7HLTGQ0N"
API = "https://api.github.com"
MANIFEST_MARKER = "<!-- qd-slack-ledger:v1 -->"
UNINITIALIZED_MARKER = "<!-- qd-slack-ledger:uninitialized:v1 -->"
HISTORY_PREFIX = "历史结果／延迟补发，仅供回顾"
STATUSES = frozenset({"pending", "attempting", "delivered", "retry_wait", "unknown",
                      "needs_attention", "duplicate_source"})
UTC = timezone.utc


class LedgerUnavailable(RuntimeError):
    """The ledger cannot be completely read or trusted; stop dependent sends."""


class SourceMissing(LedgerUnavailable):
    """The GitHub resource explicitly returned 404."""


class LedgerUncertain(LedgerUnavailable):
    """A mutation could not be verified; never infer that it did not commit."""


class BudgetExpired(RuntimeError):
    pass


class PayloadTooLarge(ValueError):
    """The complete source cannot fit the single-message admission policy."""


class TransportFailure(RuntimeError):
    def __init__(self, code: str, proven_unwritten: bool = False):
        self.code = code
        self.proven_unwritten = proven_unwritten
        # Transport diagnostics are codes, never raw exception text or URLs.
        super().__init__("HTTP transport failure")


@dataclass(frozen=True)
class Config:
    repo: str
    ledger_issue: int
    producer_ids: frozenset[int]
    channel_id: str

    def __post_init__(self):
        if (self.repo != REPO or self.channel_id != CHANNEL_ID
                or type(self.ledger_issue) is not int or self.ledger_issue <= 0
                or not isinstance(self.producer_ids, frozenset) or not self.producer_ids
                or any(type(i) is not int or i <= 0 for i in self.producer_ids)):
            raise ValueError("Invalid fixed Slack ledger configuration")


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class HttpClient(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str],
                body: bytes | None, timeout: float) -> HttpResponse: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class Budget(Protocol):
    def remaining(self) -> float: ...
    def require(self, seconds: float = 0) -> None: ...


@dataclass(frozen=True)
class SourceEvent:
    repo: str
    comment_id: int
    issue_number: int
    event_key: str
    symbol: str
    state: str
    created_at: datetime
    updated_at: datetime
    generated_at_et: str | None
    run_id: str | None
    html_url: str
    body: str | None
    body_sha256: str


@dataclass(frozen=True)
class Entry:
    source: SourceEvent
    status: str
    attempts: int
    next_attempt_at: datetime | None
    attempt_id: str | None
    attempted_at: datetime | None
    delivered_at: datetime | None
    reason: str | None
    canonical_source_id: int | None


@dataclass(frozen=True)
class Manifest:
    version: int
    repo: str
    channel_id: str
    enabled_at: datetime
    baseline_ids: frozenset[int]
    cursor: datetime
    shard_count: int
    next_send_at: datetime | None


@dataclass(frozen=True)
class Ledger:
    manifest: Manifest
    entries: dict[int, Entry]


@dataclass(frozen=True)
class Summary:
    """delivered counts new confirmations this run; waiting states are current."""
    delivered: int
    pending: int
    retry_wait: int
    unknown: int
    needs_attention: int
    duplicate_source: int
    rejected: int
    paused_reason: str | None


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _encode(value):
    if isinstance(value, datetime):
        _utc(value)
        return value.isoformat()
    if isinstance(value, frozenset):
        return sorted(value)
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    return value


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise LedgerUnavailable("Ledger timestamp is not aware UTC")
    return value


def _time(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise LedgerUnavailable("Invalid ledger timestamp")
    try:
        return _utc(datetime.fromisoformat(value))
    except (ValueError, TypeError):
        raise LedgerUnavailable("Invalid ledger timestamp") from None


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise LedgerUnavailable("Invalid ledger integer")
    return value


def _shape(value, cls):
    if not isinstance(value, dict) or set(value) != {f.name for f in fields(cls)}:
        raise LedgerUnavailable("Invalid ledger schema")


def _document(body: str, marker: str) -> dict:
    if not isinstance(body, str) or not body.startswith(marker + "\n"):
        raise LedgerUnavailable("Missing or unknown ledger marker")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise LedgerUnavailable("Duplicate ledger JSON key")
            result[key] = value
        return result
    try:
        result = json.loads(body[len(marker) + 1:], object_pairs_hook=unique)
    except (ValueError, TypeError):
        raise LedgerUnavailable("Malformed ledger JSON") from None
    if not isinstance(result, dict):
        raise LedgerUnavailable("Invalid ledger document")
    return result


def _envelope(document, keys):
    if set(document) != keys or not isinstance(document.get("operation_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", document["operation_id"]):
        raise LedgerUnavailable("Invalid ledger operation")
    _integer(document["revision"], 1)


def _manifest(value, config):
    _shape(value, Manifest)
    if type(value["version"]) is not int or value["version"] != SCHEMA_VERSION or value["repo"] != config.repo or value["channel_id"] != config.channel_id:
        raise LedgerUnavailable("Ledger version or target mismatch")
    ids = value["baseline_ids"]
    if not isinstance(ids, list) or any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids)):
        raise LedgerUnavailable("Invalid baseline IDs")
    result = Manifest(**(value | {"enabled_at": _time(value["enabled_at"]),
                                 "cursor": _time(value["cursor"]), "baseline_ids": frozenset(ids),
                                 "next_send_at": _time(value["next_send_at"], optional=True)}))
    _integer(result.shard_count)
    if result.cursor < result.enabled_at:
        raise LedgerUnavailable("Ledger cursor predates baseline")
    return result


def _entry(value, config):
    _shape(value, Entry)
    source = value["source"]
    _shape(source, SourceEvent)
    _integer(source["comment_id"], 1)
    _integer(source["issue_number"], 1)
    if source["repo"] != config.repo:
        raise LedgerUnavailable("Source repository mismatch")
    for key in ("event_key", "symbol", "state", "html_url", "body_sha256"):
        if not isinstance(source[key], str) or not source[key]:
            raise LedgerUnavailable("Invalid source identity")
    for key in ("generated_at_et", "run_id"):
        if source[key] is not None and not isinstance(source[key], str):
            raise LedgerUnavailable("Invalid source metadata")
    if source["html_url"] != f"https://github.com/{config.repo}/issues/{source['issue_number']}#issuecomment-{source['comment_id']}":
        raise LedgerUnavailable("Invalid source link")
    if not re.fullmatch(r"[0-9a-f]{64}", source["body_sha256"]):
        raise LedgerUnavailable("Invalid source digest")
    body = source["body"]
    if body is None:
        if value["status"] != "needs_attention" or not isinstance(value["reason"], str) or not value["reason"].startswith("oversized:"):
            raise LedgerUnavailable("Missing source snapshot")
    elif not isinstance(body, str) or hashlib.sha256(body.encode()).hexdigest() != source["body_sha256"]:
        raise LedgerUnavailable("Source snapshot hash mismatch")
    source_obj = SourceEvent(**(source | {"created_at": _time(source["created_at"]), "updated_at": _time(source["updated_at"])}))
    if source_obj.updated_at < source_obj.created_at:
        raise LedgerUnavailable("Invalid source timestamps")
    if not isinstance(value["status"], str) or value["status"] not in STATUSES or _integer(value["attempts"]) > MAX_ATTEMPTS:
        raise LedgerUnavailable("Invalid delivery state")
    for key, maximum in (("reason", 256), ("attempt_id", 128)):
        if value[key] is not None and (not isinstance(value[key], str) or len(value[key]) > maximum):
            raise LedgerUnavailable("Invalid bounded delivery field")
    if value["canonical_source_id"] is not None:
        _integer(value["canonical_source_id"], 1)
    return Entry(**(value | {"source": source_obj, **{key: _time(value[key], optional=True)
                  for key in ("next_attempt_at", "attempted_at", "delivered_at")}}))


def _scan_time(source: SourceEvent) -> datetime | None:
    raw = source.generated_at_et
    if not isinstance(raw, str):
        return None
    try:
        if len(raw.encode()) > SOURCE_SCAN_TIME_BYTES:
            return None
        scanned = datetime.fromisoformat(raw)
        return scanned if scanned.tzinfo is not None and scanned.utcoffset() is not None else None
    except (ValueError, UnicodeError, OverflowError):
        return None


def source_metadata_text(source: SourceEvent, *, historical: bool = False) -> str:
    """Exact shared plain-text framing for admission and Slack formatting.

    Admission includes the history prefix so a valid first send remains valid on
    retry. Snapshot text is excluded; formatters must reuse this framing.
    """
    lines = [HISTORY_PREFIX] if historical else []
    lines += [f"{source.state} | {source.symbol}",
              f"原扫描时间：{source.generated_at_et if _scan_time(source) is not None else '原扫描时间未知'}",
              f"GitHub 发布时间：{source.created_at.isoformat()}",
              f"GitHub 评论 ID：{source.comment_id}", f"原文：{source.html_url}",
              "观察用途，不创建或起草订单"]
    return "\n".join(lines)


def _presentation_too_large(entry: Entry) -> bool:
    """Reserve retry framing, using exactly the durable admission limits."""
    body = entry.source.body
    if body is None:
        return True
    metadata = source_metadata_text(entry.source, historical=True)
    return (len(_json(_encode(asdict(entry))).encode()) > RECORD_BYTES
            or len(metadata) + len(body) > MAX_VISIBLE_TEXT
            or len(metadata) > BLOCK_TEXT_CHARS
            or 1 + (len(body) + BLOCK_TEXT_CHARS - 1) // BLOCK_TEXT_CHARS > MAX_BLOCKS)


def _admit(entry, config):
    value = _encode(asdict(entry))
    checked = _entry(value, config)
    if checked.source.body is None:
        return checked
    if _presentation_too_large(checked):
        # Only newly admitted sources lose their inline body; existing accepted
        # snapshots are immutable and retained through every state transition.
        reason = "oversized: complete snapshot exceeds ledger or Slack presentation limit"
        if checked.reason:
            reason += "; " + checked.reason[:256 - len(reason) - 2]
        return replace(checked, source=replace(checked.source, body=None),
                       status="needs_attention", reason=reason)
    return checked


def _header(headers, name):
    return next((value for key, value in headers.items() if key.lower() == name.lower()), None)


def _server_date(response):
    try:
        value = parsedate_to_datetime(_header(response.headers, "Date") or "")
        return _utc(value)
    except (ValueError, TypeError, IndexError):
        raise LedgerUnavailable("Missing trusted GitHub response Date") from None


class GitHubClient:
    def __init__(self, config: Config, token: str, http: HttpClient, budget: Budget):
        self.config = config
        self._token = token
        self.http = http
        self.budget = budget
        self.server_time: datetime | None = None
        self.repo_comments_started_at: datetime | None = None

    def _request(self, method, path, document=None):
        prefix = f"/repos/{self.config.repo}/issues"
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or not (parsed.path == prefix or parsed.path.startswith(prefix + "/")):
            raise LedgerUnavailable("GitHub request escaped configured repository")
        self.budget.require()
        timeout = min(5.0, self.budget.remaining())
        if timeout <= 0:
            raise BudgetExpired()
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self._token}",
                   "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                   "User-Agent": "quant-detective-slack-ledger"}
        try:
            response = self.http.request(method, API + path, headers=headers,
                                         body=None if document is None else _json(document).encode(), timeout=timeout)
        except BudgetExpired:
            raise
        except Exception:
            raise LedgerUnavailable("GitHub transport unavailable") from None
        if response.status == 404:
            raise SourceMissing("GitHub resource is missing")
        if not 200 <= response.status < 300:
            raise LedgerUnavailable("GitHub response was unsuccessful")
        try:
            value = json.loads(response.body)
        except (ValueError, UnicodeError):
            raise LedgerUnavailable("GitHub response was malformed") from None
        try:
            self.server_time = _server_date(response)
        except LedgerUnavailable:
            self.server_time = None
        return value, response

    def get_issue(self, number: int) -> tuple[dict, HttpResponse]:
        _integer(number, 1)
        value, response = self._request("GET", f"/repos/{self.config.repo}/issues/{number}")
        if not isinstance(value, dict) or value.get("number") != number:
            raise LedgerUnavailable("Invalid GitHub issue response")
        return value, response

    def get_comment(self, comment_id: int) -> dict:
        _integer(comment_id, 1)
        value, _ = self._request("GET", f"/repos/{self.config.repo}/issues/comments/{comment_id}")
        if not isinstance(value, dict) or value.get("id") != comment_id:
            raise LedgerUnavailable("Invalid GitHub comment response")
        return value

    def _pages(self, path, query, *, repo_comments=False):
        items, seen = [], set()
        page = 1
        next_page_advertised = False
        while True:
            value, response = self._request("GET", path + "?" + urlencode(query | {"per_page": 100, "page": page}))
            if repo_comments and page == 1:
                self.repo_comments_started_at = _server_date(response)
            if not isinstance(value, list) or len(value) > 100 or (next_page_advertised and not value):
                raise LedgerUnavailable("Invalid GitHub pagination")
            for item in value:
                if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0 or item["id"] in seen:
                    raise LedgerUnavailable("Duplicate or invalid GitHub page record")
                seen.add(item["id"])
                items.append(item)
            link = _header(response.headers, "Link") or ""
            next_links = re.findall(r'<([^>]+)>;\s*rel="next"', link)
            if next_links:
                if len(next_links) != 1:
                    raise LedgerUnavailable("Ambiguous GitHub next page")
                target = urlsplit(next_links[0])
                expected = {k: [str(v)] for k, v in (query | {"per_page": 100, "page": page + 1}).items()}
                if target.scheme != "https" or target.netloc != "api.github.com" or target.path != path or target.fragment or parse_qs(target.query) != expected or not value:
                    raise LedgerUnavailable("Invalid GitHub next page")
            elif len(value) < 100:
                return items
            next_page_advertised = bool(next_links)
            page += 1

    def list_issue_comments(self, number: int) -> list[dict]:
        _integer(number, 1)
        return self._pages(f"/repos/{self.config.repo}/issues/{number}/comments", {})

    def list_repo_comments(self, since: datetime) -> list[dict]:
        self.repo_comments_started_at = None
        return self._pages(f"/repos/{self.config.repo}/issues/comments",
                           {"since": _utc(since).isoformat(), "sort": "created", "direction": "asc"}, repo_comments=True)

    def _trusted(self, item):
        author = item.get("user", {}).get("id") if isinstance(item.get("user"), dict) else None
        if type(author) is not int or author not in self.config.producer_ids:
            raise LedgerUnavailable("Untrusted ledger writer")

    def write_verified(self, method: str, path: str, document: dict, *, operation_id: str) -> dict:
        """Attempt once, then read complete authoritative content before success."""
        root = f"/repos/{self.config.repo}/issues"
        post_path = f"{root}/{self.config.ledger_issue}/comments"
        issue_path = f"{root}/{self.config.ledger_issue}"
        comment_match = re.fullmatch(re.escape(root) + r"/comments/([1-9][0-9]*)", path)
        if not ((method == "POST" and path == post_path) or (method == "PATCH" and (path == issue_path or comment_match))):
            raise LedgerUnavailable("Unsupported ledger write target")
        if set(document) != {"body"} or not isinstance(document["body"], str):
            raise LedgerUnavailable("Invalid ledger write")
        try:
            embedded = json.loads(document["body"].split("\n", 1)[1])
            if embedded["operation_id"] != operation_id:
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise LedgerUnavailable("Ledger operation ID mismatch") from None
        try:
            self._request(method, path, document)
        except BudgetExpired:
            raise
        except LedgerUnavailable:
            # No second POST: a lost response says nothing about commit outcome.
            pass
        try:
            if method == "POST":
                matches = []
                for item in self.list_issue_comments(self.config.ledger_issue):
                    raw = item.get("body", "")
                    if not isinstance(raw, str) or not raw.startswith("<!-- qd-slack-shard:"):
                        continue
                    try:
                        candidate = json.loads(raw.split("\n", 1)[1])
                    except (ValueError, IndexError):
                        continue
                    if isinstance(candidate, dict) and candidate.get("operation_id") == operation_id:
                        matches.append(item)
                if len(matches) != 1:
                    raise LedgerUnavailable("Write not uniquely visible")
                result = matches[0]
            elif path == issue_path:
                result, _ = self.get_issue(self.config.ledger_issue)
            else:
                result = self.get_comment(int(comment_match[1]))
            self._trusted(result)
            if path != issue_path and result.get("issue_url") != API + issue_path:
                raise LedgerUnavailable("Write readback is in the wrong issue")
            if result.get("body") != document["body"]:
                raise LedgerUnavailable("Write readback differs from complete target")
            return result
        except BudgetExpired:
            raise
        except LedgerUnavailable:
            raise LedgerUncertain("Ledger write could not be verified") from None


class LedgerStore:
    def __init__(self, github: GitHubClient, config: Config):
        if github.config != config:
            raise ValueError("Ledger configuration mismatch")
        self.github = github
        self.config = config
        self._shards = {}
        self._manifest_revision = 0
        self._run_active = False
        self._snapshot = None
        self._manifest_raw = None
        self._shard_bodies = {}

    @contextmanager
    def verified_run(self):
        """One serialized writer: full read, guarded writes, exact readbacks.

        The manifest and touched shard are reread before every mutation. A
        revision OR content change aborts rather than merging external writes.
        Untouched shards need not be repeatedly paginated inside this run;
        the workflow's single-writer contract remains mandatory. No cache is
        reused across runs or exposed to ordinary callers after exit.
        """
        if self._run_active:
            raise LedgerUnavailable("Nested ledger run")
        self._run_active = True
        self._snapshot = None
        try:
            yield
        finally:
            self._run_active = False
            self._snapshot = None

    def refresh(self):
        self._snapshot = None
        return self.load()

    def _guard_manifest(self):
        if self._run_active:
            issue, _ = self.github.get_issue(self.config.ledger_issue)
            self.github._trusted(issue)
            if "pull_request" in issue or issue.get("body") != self._manifest_raw:
                raise LedgerUnavailable("Ledger manifest changed during serialized run")

    def _guard_shard(self, number):
        if self._run_active:
            comment = self.github.get_comment(self._shards[number][0])
            self.github._trusted(comment)
            if (comment.get("issue_url") != f"{API}/repos/{self.config.repo}/issues/{self.config.ledger_issue}"
                    or comment.get("body") != self._shard_bodies[number]):
                raise LedgerUnavailable("Ledger shard changed during serialized run")

    def _manifest_body(self, manifest, revision, operation_id):
        return MANIFEST_MARKER + "\n" + _json({"operation_id": operation_id, "revision": revision,
                                                "manifest": _encode(asdict(manifest))})

    def _shard_body(self, number, records, revision, operation_id):
        return f"<!-- qd-slack-shard:v1:{number} -->\n" + _json({"version": SCHEMA_VERSION,
                 "operation_id": operation_id, "revision": revision, "records": records})

    def _write_manifest(self, manifest):
        self._guard_manifest()
        operation_id = uuid4().hex
        body = self._manifest_body(manifest, self._manifest_revision + 1, operation_id)
        self.github.write_verified("PATCH", f"/repos/{self.config.repo}/issues/{self.config.ledger_issue}",
                                   {"body": body}, operation_id=operation_id)
        self._manifest_revision += 1
        self._manifest_raw = body
        if self._snapshot is not None:
            self._snapshot = Ledger(manifest, self._snapshot.entries)

    def load(self) -> Ledger:
        if self._run_active and self._snapshot is not None:
            return self._snapshot
        issue, _ = self.github.get_issue(self.config.ledger_issue)
        self.github._trusted(issue)
        if "pull_request" in issue:
            raise LedgerUnavailable("Ledger target is not an issue")
        document = _document(issue.get("body"), MANIFEST_MARKER)
        _envelope(document, {"operation_id", "revision", "manifest"})
        manifest = _manifest(document["manifest"], self.config)
        shards, entries, bodies = {}, {}, {}
        for comment in self.github.list_issue_comments(self.config.ledger_issue):
            body = comment.get("body")
            if not isinstance(body, str) or "qd-slack-shard:" not in body:
                continue
            self.github._trusted(comment)
            if comment.get("issue_url") != f"{API}/repos/{self.config.repo}/issues/{self.config.ledger_issue}":
                raise LedgerUnavailable("Shard is in the wrong issue")
            match = re.match(r"<!-- qd-slack-shard:v1:(0|[1-9][0-9]*) -->\n", body)
            if not match or len(body.encode()) > SHARD_BYTES:
                raise LedgerUnavailable("Invalid shard marker or byte limit")
            number = int(match[1])
            if number in shards:
                raise LedgerUnavailable("Duplicate shard sequence")
            shard = _document(body, match[0].rstrip("\n"))
            _envelope(shard, {"version", "operation_id", "revision", "records"})
            if type(shard["version"]) is not int or shard["version"] != SCHEMA_VERSION or not isinstance(shard["records"], list) or not 1 <= len(shard["records"]) <= SHARD_RECORDS:
                raise LedgerUnavailable("Invalid shard version or records")
            for record in shard["records"]:
                entry = _entry(record, self.config)
                if entry.source.body is not None:
                    initial = replace(entry, status="pending", attempts=0, next_attempt_at=None,
                                      attempt_id=None, attempted_at=None, delivered_at=None,
                                      reason=None, canonical_source_id=None)
                    if _admit(initial, self.config).source.body is None:
                        raise LedgerUnavailable("Oversized snapshot bypassed ledger admission")
                if entry.source.comment_id in entries:
                    raise LedgerUnavailable("Duplicate ledger source")
                entries[entry.source.comment_id] = entry
            shards[number] = (comment["id"], shard)
            bodies[number] = body
        if set(shards) != set(range(len(shards))) or len(shards) < manifest.shard_count:
            raise LedgerUnavailable("Missing ledger shard")
        # Only the immediately next shard can be left by a serialized writer.
        if len(shards) > manifest.shard_count + 1:
            raise LedgerUnavailable("Conflicting orphan ledger shards")
        self._shards = shards
        self._shard_bodies = bodies
        self._manifest_raw = issue["body"]
        self._manifest_revision = document["revision"]
        if len(shards) > manifest.shard_count:
            manifest = replace(manifest, shard_count=len(shards))
            self._write_manifest(manifest)
        result = Ledger(manifest, entries)
        if self._run_active:
            self._snapshot = result
        return result

    def initialize(self) -> Ledger:
        issue, response = self.github.get_issue(self.config.ledger_issue)
        self.github._trusted(issue)
        if issue.get("body") != UNINITIALIZED_MARKER:
            return self.load()
        if "pull_request" in issue:
            raise LedgerUnavailable("Ledger target is not an issue")
        cut = _server_date(response)
        comments = self.github.list_repo_comments(cut - timedelta(seconds=1))
        baseline = frozenset(c["id"] for c in comments if _time(c.get("created_at")).replace(microsecond=0) == cut)
        # Existing marked shards cannot be silently adopted as a new baseline.
        existing = self.github.list_issue_comments(self.config.ledger_issue)
        if any("qd-slack-shard:" in str(c.get("body", "")) for c in existing):
            raise LedgerUnavailable("Uninitialized ledger already contains shards")
        manifest = Manifest(SCHEMA_VERSION, self.config.repo, self.config.channel_id,
                            cut, baseline, cut, 0, None)
        self._manifest_revision = 0
        self._write_manifest(manifest)
        return self.load()

    def update_manifest(self, manifest: Manifest) -> None:
        ledger = self.load()
        checked = _manifest(_encode(asdict(manifest)), self.config)
        old = ledger.manifest
        if (checked.enabled_at != old.enabled_at or checked.baseline_ids != old.baseline_ids
                or checked.shard_count != old.shard_count or checked.cursor < old.cursor):
            raise LedgerUnavailable("Cannot reset ledger identity, baseline, shards or cursor")
        if checked != old:
            self._write_manifest(checked)

    def _fits(self, number, records, revision):
        if len(records) > SHARD_RECORDS:
            return False
        # Reserve each state field's maximum UTF-8 size. Packing must remain
        # safe when every record later receives its longest permissible reason.
        reserved = []
        for record in records:
            reserved.append(record | {"status": "duplicate_source", "attempts": MAX_ATTEMPTS,
                "reason": "\x00" * 256, "attempt_id": "\x00" * 128,
                "next_attempt_at": "9999-12-31T23:59:59.999999+00:00",
                "attempted_at": "9999-12-31T23:59:59.999999+00:00",
                "delivered_at": "9999-12-31T23:59:59.999999+00:00",
                "canonical_source_id": 9223372036854775807})
        body = self._shard_body(number, reserved, max(revision, 9223372036854775807), "a" * 128)
        return len(body.encode()) <= SHARD_BYTES

    def put(self, entry: Entry) -> None:
        ledger = self.load()
        existing = ledger.entries.get(entry.source.comment_id)
        if existing is None:
            checked = _admit(entry, self.config)
        else:
            checked = _entry(_encode(asdict(entry)), self.config)
            if checked.source != existing.source:
                raise LedgerUnavailable("Accepted source snapshots are immutable")
        if existing == checked:
            return
        record = _encode(asdict(checked))
        selected = None
        for number, (comment_id, shard) in self._shards.items():
            records = list(shard["records"])
            index = next((i for i, r in enumerate(records) if r["source"]["comment_id"] == checked.source.comment_id), None)
            if index is not None:
                records[index] = record
                selected = (number, comment_id, records, shard["revision"] + 1)
                break
            if existing is None and selected is None and self._fits(number, records + [record], shard["revision"] + 1):
                selected = (number, comment_id, records + [record], shard["revision"] + 1)
        if selected is None:
            number = ledger.manifest.shard_count
            if not self._fits(number, [record], 1):
                raise LedgerUnavailable("Source identity cannot fit a durable shard")
            selected = (number, None, [record], 1)
        number, comment_id, records, revision = selected
        operation_id = uuid4().hex
        body = self._shard_body(number, records, revision, operation_id)
        if len(body.encode()) > SHARD_BYTES:
            raise LedgerUnavailable("Updated shard exceeds byte limit")
        root = f"/repos/{self.config.repo}/issues"
        method = "POST" if comment_id is None else "PATCH"
        path = f"{root}/{self.config.ledger_issue}/comments" if comment_id is None else f"{root}/comments/{comment_id}"
        self._guard_manifest()
        if comment_id is not None:
            self._guard_shard(number)
        elif self._run_active:
            # New shard sequence must not already have appeared. This uncommon
            # path retains a complete authoritative check before POST.
            before = self.refresh()
            if before != ledger:
                raise LedgerUnavailable("Ledger changed before shard creation")
        visible = self.github.write_verified(method, path, {"body": body}, operation_id=operation_id)
        self._shards[number] = (visible["id"], _document(body, f"<!-- qd-slack-shard:v1:{number} -->"))
        self._shard_bodies[number] = body
        if self._snapshot is not None:
            self._snapshot.entries[checked.source.comment_id] = checked
        if comment_id is None:
            self._write_manifest(replace(ledger.manifest, shard_count=ledger.manifest.shard_count + 1))


SOURCE_STATES = frozenset({"ENTRY_ARMED", "ENTRY_CONFIRMED", "LEADER_WATCH", "LEADER_HOT_NO_CHASE"})
# Bound extracted optional fields, never the original body. Excessive values
# stay unknown so even a body-less oversized rejection fits a durable shard.
SOURCE_RUN_ID_BYTES = 256
SOURCE_SCAN_TIME_BYTES = 128
ET = ZoneInfo("America/New_York")


def _source_issue_number(comment: dict, config: Config) -> int | None:
    """Cheap trusted-source gate before fetching an Issue, never a URL to follow."""
    if not isinstance(comment, dict):
        return None
    user, body = comment.get("user"), comment.get("body")
    author = user.get("id") if isinstance(user, dict) else None
    if (type(author) is not int or author not in config.producer_ids
            or not isinstance(body, str) or not body.startswith("<!-- qd-event:")):
        return None
    issue_url = comment.get("issue_url")
    if not isinstance(issue_url, str):
        return None
    match = re.fullmatch(re.escape(f"{API}/repos/{config.repo}/issues/") + r"([1-9][0-9]*)", issue_url)
    try:
        return int(match[1]) if match else None
    except ValueError:
        return None


def _source_digest(body) -> str | None:
    if not isinstance(body, str):
        return None
    try:
        return hashlib.sha256(body.encode()).hexdigest()
    except UnicodeError:
        return None


def parse_source(comment: dict, issue: dict, config: Config) -> SourceEvent | None:
    """Accept only exact persisted producer identities; all body content is data."""
    number = _source_issue_number(comment, config)
    if number is None or not isinstance(issue, dict) or "pull_request" in issue:
        return None
    if (type(issue.get("number")) is not int or issue["number"] != number
            or issue.get("html_url") != f"https://github.com/{config.repo}/issues/{number}"):
        return None
    comment_id, body = comment.get("id"), comment["body"]
    if (type(comment_id) is not int or comment_id <= 0
            or comment.get("html_url") != f"https://github.com/{config.repo}/issues/{number}#issuecomment-{comment_id}"):
        return None
    lines = body.split("\n")
    marker = re.fullmatch(r"<!-- qd-event:([^\r\n]+) -->", lines[0])
    if not marker or len(re.findall(r"<!--\s*qd-event\b", body)) != 1:
        return None
    parts = marker[1].split("|")
    if len(parts) != 3:
        return None
    day, symbol, state = parts
    if (not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day)
            or not re.fullmatch(r"[A-Z][A-Z0-9.^=-]{0,31}", symbol)
            or state not in SOURCE_STATES or issue.get("title") != f"Market Watch | {day} ET"):
        return None
    try:
        datetime.fromisoformat(day)
        created, updated = _time(comment.get("created_at")), _time(comment.get("updated_at"))
    except (ValueError, LedgerUnavailable):
        return None
    if updated < created:
        return None
    generated, run_id, heading = None, None, 1
    if len(lines) > 1 and lines[1].startswith("<!-- qd-source:"):
        heading = 2
        metadata = re.fullmatch(r"<!-- qd-source:(.*) -->", lines[1])
        try:
            data = json.loads(metadata[1]) if metadata else None
        except (ValueError, TypeError, RecursionError):
            data = None
        if isinstance(data, dict) and type(data.get("schema")) is int and data["schema"] == 1:
            raw_run_id = data.get("run_id")
            if _source_digest(raw_run_id) is not None and len(raw_run_id.encode()) <= SOURCE_RUN_ID_BYTES:
                run_id = raw_run_id
            raw = data.get("generated_at_et")
            if _source_digest(raw) is not None:
                try:
                    scanned = datetime.fromisoformat(raw)
                    if scanned.tzinfo is not None and scanned.utcoffset() is not None:
                        if scanned.astimezone(ET).date().isoformat() != day:
                            return None
                        # Date consistency still applies to parseable excessive
                        # timestamps, but their extracted representation is unknown.
                        if len(raw.encode()) <= SOURCE_SCAN_TIME_BYTES:
                            generated = raw
                except (ValueError, OverflowError):
                    pass
    if len(lines) <= heading or lines[heading] != f"### {symbol} — {state}":
        return None
    digest = _source_digest(body)
    if digest is None:
        return None
    return SourceEvent(config.repo, comment_id, number, marker[1], symbol, state,
                       created, updated, generated, run_id, comment["html_url"], body, digest)


def after_baseline(source: SourceEvent, manifest: Manifest) -> bool:
    """The server-time cut includes new IDs in the same second, never old IDs."""
    return source.created_at >= manifest.enabled_at and source.comment_id not in manifest.baseline_ids


def _source_order(entry: Entry):
    return entry.source.created_at, entry.source.comment_id


def _was_attempted(entry: Entry) -> bool:
    return (entry.attempts > 0 or entry.attempted_at is not None or entry.attempt_id is not None
            or entry.delivered_at is not None or entry.status in {"attempting", "delivered", "retry_wait", "unknown"})


def discover(store: LedgerStore, github: GitHubClient, clock: Clock, budget: Budget) -> int:
    """Durably enqueue a complete traversal before advancing its server-time cut.

    Local wall time is deliberately unused. The first page's GitHub Date leaves
    comments arriving during this read visible to the next overlapping traversal.
    Serialized workflow execution is required, as it is for LedgerStore writes.
    """
    if github.config != store.config:
        raise LedgerUnavailable("Discovery configuration mismatch")
    budget.require()
    ledger = store.load()
    original_source_ids = frozenset(ledger.entries)
    since = max(ledger.manifest.enabled_at - timedelta(seconds=1),
                ledger.manifest.cursor - timedelta(seconds=OVERLAP_SECONDS))
    budget.require()
    comments = github.list_repo_comments(since)
    started_at = _utc(github.repo_comments_started_at)
    if started_at < ledger.manifest.cursor:
        raise LedgerUnavailable("GitHub discovery time predates durable cursor")
    entries, issues = dict(ledger.entries), {}
    for comment in comments:
        budget.require()
        old = ledger.entries.get(comment["id"])
        if old is not None:
            digest = _source_digest(comment.get("body"))
            if digest != old.source.body_sha256 and old.status != "delivered":
                reason = "accepted source body changed; original snapshot retained"
                if old.source.body is None:
                    reason = "oversized: " + reason
                entries[comment["id"]] = replace(old, status="needs_attention", reason=reason)
            continue
        number = _source_issue_number(comment, store.config)
        if number is None:
            continue
        if number not in issues:
            issues[number], _ = github.get_issue(number)
        source = parse_source(comment, issues[number], store.config)
        if source is not None and after_baseline(source, ledger.manifest):
            candidate = Entry(source, "pending", 0, None, None, None, None, None, None)
            entries[source.comment_id] = _admit(candidate, store.config)

    # Decide roots over the entire traversal plus durable history, never per page.
    groups = {}
    for entry in sorted(entries.values(), key=_source_order):
        groups.setdefault(entry.source.event_key, []).append(entry)
    roots = set()
    for group in groups.values():
        attempted = [entry for entry in group if _was_attempted(entry)]
        if len(attempted) > 1:
            raise LedgerUnavailable("Conflicting attempted sources for one event")
        canonical = attempted[0] if attempted else group[0]
        root_id = canonical.source.comment_id
        roots.add(root_id)
        for entry in group:
            source_id = entry.source.comment_id
            if source_id == root_id:
                if entry.status == "duplicate_source":
                    entries[source_id] = replace(entry, status="pending", reason=None, canonical_source_id=None)
                continue
            reason = "duplicate event_key; canonical source retained"
            if attempted and _source_order(entry) < _source_order(canonical):
                reason = "earlier duplicate discovered after canonical attempt; verify source identity"
            if entry.status == "needs_attention":
                attention_reason = entry.reason
                if attempted and _source_order(entry) < _source_order(canonical):
                    if reason not in (attention_reason or ""):
                        # Bound the mutable diagnostic, never the source snapshot.
                        prior = (attention_reason or "")[:256 - len(reason) - 2]
                        attention_reason = (prior + "; " + reason).lstrip("; ")
                entries[source_id] = replace(entry, canonical_source_id=root_id, reason=attention_reason)
            else:
                entries[source_id] = replace(entry, status="duplicate_source", reason=reason, canonical_source_id=root_id)

    # Retire previous unattempted roots before admitting replacements. A crash
    # may temporarily leave no root, but can never leave two sendable roots.
    changes = [entry for source_id, entry in entries.items() if entry != ledger.entries.get(source_id)]
    changes.sort(key=lambda entry: (entry.source.comment_id in roots, _source_order(entry)))
    for entry in changes:
        budget.require()
        store.put(entry)
    budget.require()
    durable = store.load()
    budget.require()
    store.update_manifest(replace(durable.manifest, cursor=started_at))
    return len(roots - original_source_ids)


@dataclass(frozen=True)
class SendResult:
    kind: str
    status: int | None
    retry_after: int | None
    reason: str


def format_payload(entry: Entry, now: datetime) -> dict:
    """One safe message containing the complete immutable source snapshot.

    Call before increasing attempts: any previous attempt is historical. The
    top-level fallback contains only validated metadata, never source prose.
    """
    source = entry.source
    if source.body is None:
        raise PayloadTooLarge("Complete source snapshot unavailable")
    try:
        day, symbol, state = source.event_key.split("|")
        digest = _source_digest(source.body)
        valid = (source.repo == REPO and symbol == source.symbol and state == source.state
                 and re.fullmatch(r"[A-Z][A-Z0-9.^=-]{0,31}", symbol)
                 and state in SOURCE_STATES and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day)
                 and type(source.comment_id) is int and source.comment_id > 0
                 and type(source.issue_number) is int and source.issue_number > 0
                 and source.html_url == f"https://github.com/{REPO}/issues/{source.issue_number}#issuecomment-{source.comment_id}"
                 and digest is not None and digest == source.body_sha256)
        event_day = datetime.fromisoformat(day).date()
        _utc(source.created_at)
        _utc(now)
        if not valid:
            raise ValueError
    except (ValueError, TypeError, AttributeError, LedgerUnavailable):
        raise ValueError("Invalid Slack source identity or timestamp") from None
    # Mutable attempt diagnostics do not invalidate an already admitted source.
    initial = replace(entry, status="pending", attempts=0, next_attempt_at=None,
                      attempt_id=None, attempted_at=None, delivered_at=None,
                      reason=None, canonical_source_id=None)
    if _presentation_too_large(initial):
        raise PayloadTooLarge("Complete source exceeds admission limits")
    scanned = _scan_time(source)
    historical = (entry.attempts >= 1 or event_day < now.astimezone(ET).date()
                  or (scanned is not None and (now - scanned).total_seconds() > 600))
    metadata = source_metadata_text(source, historical=historical)
    texts = [metadata] + [source.body[i:i + BLOCK_TEXT_CHARS]
                         for i in range(0, len(source.body), BLOCK_TEXT_CHARS)]
    return {"text": metadata, "mrkdwn": False, "link_names": False,
            "unfurl_links": False, "unfurl_media": False,
            "blocks": [{"type": "section", "text": {"type": "plain_text", "text": text, "emoji": False}}
                       for text in texts]}


def send_webhook(url: str, payload: dict, *, http: HttpClient, budget: Budget) -> SendResult:
    """Attempt exactly once; unknown delivery is never permission to resend."""
    if not _valid_webhook_url(url):
        return SendResult("permanent", None, None, "invalid_webhook_url")
    try:
        body = _json(payload).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return SendResult("permanent", None, None, "invalid_payload")
    budget.require()
    timeout = min(5.0, budget.remaining())
    if timeout <= 0:
        raise BudgetExpired()
    try:
        response = http.request("POST", url, headers={"Content-Type": "application/json"},
                                body=body, timeout=timeout)
    except BudgetExpired:
        raise
    except TransportFailure as error:
        if error.proven_unwritten:
            return SendResult("retryable", None, None, "request_proven_unwritten")
        return SendResult("unknown", None, None, "delivery_unconfirmed")
    except Exception:
        return SendResult("unknown", None, None, "delivery_unconfirmed")
    status = response.status
    if status == 200 and response.body.strip() == b"ok":
        return SendResult("accepted", status, None, "accepted")
    if status == 429:
        values = [value for key, value in response.headers.items() if key.lower() == "retry-after"]
        raw = values[0] if len(values) == 1 else None
        if isinstance(raw, str) and re.fullmatch(r"[0-9]+", raw.strip()):
            try:
                return SendResult("retryable", status, int(raw.strip()), "rate_limited")
            except ValueError:
                pass
        return SendResult("retryable", status, 300, "rate_limited_retry_after_invalid_or_missing")
    if 400 <= status < 500:
        return SendResult("permanent", status, None, "webhook_rejected")
    return SendResult("unknown", status, None, "unexpected_webhook_response")


def _valid_webhook_url(url) -> bool:
    return isinstance(url, str) and re.fullmatch(
        r"https://hooks\.slack\.com/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", url) is not None


# Restrict framing to the response forms these two JSON/text services use.
# The standard library still parses status/headers and all non-chunked bodies.
_HTTP_TOKEN = rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_HTTP_QUOTED = rb'"(?:[\t \x21\x23-\x5b\x5d-\x7e\x80-\xff]|\\[\t \x21-\x7e\x80-\xff])*"'
_CHUNK_LINE = re.compile(rb"([0-9A-Fa-f]+)(?:[ \t]*;[ \t]*" + _HTTP_TOKEN
                         + rb"(?:[ \t]*=[ \t]*(?:" + _HTTP_TOKEN + rb"|" + _HTTP_QUOTED + rb"))?)*\r\n")


def _response_is_chunked(headers) -> bool:
    lengths = [value for key, value in headers if key.lower() == "content-length"]
    encodings = [value for key, value in headers if key.lower() == "transfer-encoding"]
    if len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings):
        raise TransportFailure("invalid_response_framing")
    if lengths:
        value = lengths[0].strip(" \t")
        if not re.fullmatch(r"[0-9]+", value):
            raise TransportFailure("invalid_response_framing")
        try:
            length = int(value)
        except ValueError:
            raise TransportFailure("invalid_response_framing") from None
        if length > MAX_RESPONSE_BYTES:
            raise TransportFailure("response_too_large")
    if encodings and encodings[0].strip(" \t").lower() != "chunked":
        raise TransportFailure("invalid_response_framing")
    return bool(encodings)


def _read_strict_chunks(response) -> bytes:
    """Validate the delimiters that HTTPResponse's tolerant decoder discards."""
    body = bytearray()
    while True:
        line = response.fp.readline(65537)
        match = _CHUNK_LINE.fullmatch(line) if len(line) <= 65536 else None
        if match is None:
            raise TransportFailure("invalid_response_framing")
        size = int(match[1], 16)
        if size == 0:
            # Trailers are ignored as metadata, but must be bounded fields and
            # end with an actual CRLF. EOF is not a valid trailer terminator.
            trailer_bytes = 0
            for _ in range(101):
                line = response.fp.readline(65537)
                trailer_bytes += len(line)
                if trailer_bytes > 65536:
                    break
                if line == b"\r\n":
                    return bytes(body)
                field = re.fullmatch(_HTTP_TOKEN + rb":[\t \x21-\x7e\x80-\xff]*\r\n", line)
                if field is None or line.split(b":", 1)[0].lower() in {b"content-length", b"transfer-encoding"}:
                    break
            raise TransportFailure("invalid_response_framing")
        if size > MAX_RESPONSE_BYTES - len(body):
            raise TransportFailure("response_too_large")
        chunk = response.fp.read(size)
        if len(chunk) != size or response.fp.read(2) != b"\r\n":
            raise TransportFailure("invalid_response_framing")
        body.extend(chunk)


class StdlibHttpClient:
    """HTTPS only, no redirects/retries, with a distinct pre-write connection.

    A connection-phase OSError can establish that no HTTP request was written.
    Timeout and generic URL errors remain unknown even there. After request()
    begins, no exception establishes non-delivery. All diagnostics are fixed
    codes, never exception text, credential URLs, headers or response bodies.
    """
    def request(self, method: str, url: str, *, headers: dict[str, str],
                body: bytes | None, timeout: float) -> HttpResponse:
        try:
            parsed = urlsplit(url)
            if (not isinstance(url, str) or re.search(r"[\x00-\x20\x7f]", url)
                    or parsed.scheme != "https" or parsed.fragment
                    or parsed.netloc not in {"api.github.com", "hooks.slack.com"}
                    or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0
                    or (parsed.netloc == "hooks.slack.com" and (not _valid_webhook_url(url)
                        or any(key.lower() == "authorization" for key in headers)))):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise TransportFailure("invalid_request", True) from None
        connection = response = None
        phase = "connect"
        try:
            connection = http.client.HTTPSConnection(parsed.netloc, timeout=min(5.0, timeout))
            connection.connect()
            phase = "request"
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            raw_headers = response.getheaders()
            chunked = _response_is_chunked(raw_headers)
            response_body = _read_strict_chunks(response) if chunked else response.read(MAX_RESPONSE_BYTES + 1)
            incomplete = not chunked and response.length not in (None, 0)
            response_headers = {}
            for key, value in raw_headers:
                key = key.lower()
                response_headers[key] = response_headers[key] + ", " + value if key in response_headers else value
            result = HttpResponse(response.status, response_headers, response_body)
        except BudgetExpired:
            raise
        except Exception as error:
            unwritten = (phase == "connect" and isinstance(error, OSError)
                         and not isinstance(error, (TimeoutError, URLError)))
            code = "transport_timeout" if isinstance(error, TimeoutError) else "transport_failure"
            if isinstance(error, TransportFailure) and error.code in {"invalid_response_framing", "response_too_large"}:
                code = error.code
            raise TransportFailure(code, unwritten) from None
        finally:
            # HTTPConnection can relinquish close-delimited responses. Close
            # both explicitly even after bounded reads or failed body parsing.
            for resource in (response, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except BudgetExpired:
                        raise
                    except Exception:
                        pass
        if len(result.body) > MAX_RESPONSE_BYTES:
            raise TransportFailure("response_too_large")
        if incomplete:
            raise TransportFailure("incomplete_response")
        return result


def prepare(config: Config, *, store: LedgerStore, initialize: bool) -> bool:
    """Only explicit initialization may establish the durable enablement cut."""
    if store.config != config:
        return False
    try:
        store.initialize() if initialize else store.load()
        return True
    except LedgerUnavailable:
        return False


def _later(now: datetime, seconds: int | float) -> datetime:
    # A valid Retry-After can be an arbitrarily large integer. Saturation is a
    # conservative hold, never an overflow, wraparound, or immediate retry.
    ceiling = datetime.max.replace(tzinfo=UTC)
    if seconds >= (ceiling - now).total_seconds():
        return ceiling
    return now + timedelta(seconds=seconds)


def _summary(ledger: Ledger | None, delivered=0, paused=None) -> Summary:
    entries = list(ledger.entries.values()) if ledger is not None else []
    counts = {status: sum(entry.status == status for entry in entries) for status in STATUSES}
    return Summary(delivered, counts["pending"], counts["retry_wait"], counts["unknown"],
                   counts["needs_attention"], counts["duplicate_source"],
                   sum(entry.status == "needs_attention" and (entry.reason or "").startswith(
                       ("oversized:", "payload_rejected", "webhook_rejected", "invalid_webhook_url"))
                       for entry in entries), paused)


def _forward(config: Config, *, store: LedgerStore, github: GitHubClient,
            webhook_url: str, http: HttpClient, clock: Clock, budget: Budget) -> Summary:
    """Conservative durable sender. Only verified attempting permits one POST.

    Existing due work has priority over discovery. No result re-runs market
    analysis. Every unconfirmed attempt is quarantined rather than replayed.
    """
    ledger = None
    delivered = 0
    try:
        if store.config != config or github.config != config:
            raise LedgerUnavailable("Forward configuration mismatch")
        if not _valid_webhook_url(webhook_url):
            return _summary(None, paused="invalid_configuration")
        budget.require()
        ledger = store.load()

        def save(entry):
            store.put(entry)
            ledger.entries[entry.source.comment_id] = entry

        def manifest(value):
            nonlocal ledger
            store.update_manifest(value)
            ledger = Ledger(value, ledger.entries)

        for entry in list(ledger.entries.values()):
            if entry.status == "attempting":
                save(replace(entry, status="unknown", reason="interrupted_attempt_unconfirmed"))

        considered = set()

        def queue():
            nonlocal delivered, ledger
            candidates = sorted((entry for entry in ledger.entries.values()
                                 if entry.status in {"pending", "retry_wait"}),
                                key=lambda entry: (entry.next_attempt_at or entry.source.created_at,
                                                   entry.source.created_at, entry.source.comment_id))
            for entry in candidates:
                if entry.source.comment_id in considered:
                    continue
                budget.require(1)
                now = clock.now()
                if entry.next_attempt_at is not None and entry.next_attempt_at > now:
                    continue
                gate = ledger.manifest.next_send_at
                if gate is not None and gate > now:
                    delay = (gate - now).total_seconds()
                    rate_limited = any(e.status == "retry_wait" and (e.reason or "").startswith("rate_limited")
                                       and e.next_attempt_at == gate for e in ledger.entries.values())
                    if delay > MIN_SEND_INTERVAL or rate_limited:
                        return False
                    budget.require(delay + 1)
                    clock.sleep(delay)
                considered.add(entry.source.comment_id)
                if entry.attempts >= MAX_ATTEMPTS:
                    save(replace(entry, status="needs_attention", reason="attempts_exhausted"))
                    continue
                try:
                    payload = format_payload(entry, clock.now())
                except (ValueError, PayloadTooLarge):
                    save(replace(entry, status="needs_attention", reason="payload_rejected"))
                    continue
                try:
                    comment = github.get_comment(entry.source.comment_id)
                    issue, _ = github.get_issue(entry.source.issue_number)
                except SourceMissing:
                    save(replace(entry, status="needs_attention", reason="source_missing"))
                    continue
                except LedgerUnavailable:
                    continue
                checked = parse_source(comment, issue, config)
                if checked is None or checked != entry.source:
                    save(replace(entry, status="needs_attention", reason="source_changed"))
                    continue
                budget.require(1)
                attempted = replace(entry, status="attempting", attempts=entry.attempts + 1,
                                    attempt_id=uuid4().hex, attempted_at=clock.now(),
                                    next_attempt_at=None, reason=None)
                save(attempted)
                # Until the outcome is persisted, reserve through this run's
                # deadline + 1 second: a crash anywhere in the POST/read window
                # cannot let the next runner violate the channel interval.
                manifest(replace(ledger.manifest, next_send_at=_later(
                    clock.now(), budget.remaining() + MIN_SEND_INTERVAL)))
                budget.require(1)
                result = send_webhook(webhook_url, payload, http=http, budget=budget)
                # Connecting can take longer than the channel interval. Hold
                # from observed completion, which is after any actual write,
                # so a faster next connection cannot collapse POST spacing.
                next_send = _later(clock.now(), MIN_SEND_INTERVAL)
                if result.kind == "accepted":
                    final = replace(attempted, status="delivered", delivered_at=clock.now(), reason=None)
                elif result.kind == "retryable":
                    delay = max(MIN_SEND_INTERVAL, result.retry_after or 0) if result.status == 429 else (
                        60 if attempted.attempts == 1 else 300)
                    retry_at = _later(clock.now(), delay)
                    if result.status == 429:
                        next_send = retry_at
                        # Preserve the long channel hold before any other write.
                        manifest(replace(ledger.manifest, next_send_at=retry_at))
                    final = replace(attempted, status="needs_attention" if attempted.attempts >= MAX_ATTEMPTS
                                    else "retry_wait", next_attempt_at=None if attempted.attempts >= MAX_ATTEMPTS
                                    else retry_at, reason=result.reason)
                else:
                    final = replace(attempted, status="needs_attention" if result.kind == "permanent"
                                    else "unknown", reason=result.reason)
                try:
                    save(final)
                except LedgerUncertain:
                    if result.kind != "accepted":
                        raise
                    ledger = store.refresh()
                    visible = ledger.entries[entry.source.comment_id]
                    if visible == final:
                        pass
                    elif visible == attempted:
                        save(replace(visible, status="unknown", reason="accepted_receipt_unconfirmed"))
                        raise LedgerUnavailable("Delivery receipt was not durable") from None
                    else:
                        raise LedgerUnavailable("Unexpected delivery receipt state") from None
                if result.kind == "accepted":
                    delivered += 1
                manifest(replace(ledger.manifest, next_send_at=next_send))
                if result.status == 429:
                    return False
            return True

        if not queue():
            return _summary(ledger, delivered)
        try:
            discover(store, github, clock, budget)
        except LedgerUncertain:
            raise
        except LedgerUnavailable:
            return _summary(ledger, delivered, "discovery_unavailable")
        ledger = store.load()
        queue()
        return _summary(ledger, delivered)
    except BudgetExpired:
        return _summary(ledger, delivered, "budget_exhausted")
    except LedgerUnavailable:
        return _summary(ledger, delivered, "ledger_unavailable")


def forward(config: Config, *, store: LedgerStore, github: GitHubClient,
            webhook_url: str, http: HttpClient, clock: Clock, budget: Budget) -> Summary:
    with store.verified_run():
        return _forward(config, store=store, github=github, webhook_url=webhook_url,
                        http=http, clock=clock, budget=budget)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class MonotonicBudget:
    def __init__(self, clock: Clock, seconds: float = BUDGET_SECONDS):
        if not math.isfinite(seconds) or not 0 <= seconds <= BUDGET_SECONDS:
            raise ValueError("Invalid activity budget")
        self.clock = clock
        self.deadline = clock.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self.deadline - self.clock.monotonic())

    def require(self, seconds: float = 0) -> None:
        if self.remaining() <= 0 or self.remaining() < seconds:
            raise BudgetExpired()


@contextmanager
def _hard_deadline(budget: Budget):
    """Linux wall deadline also interrupts blocking DNS/connect/read calls."""
    budget.require()
    if not sys.platform.startswith("linux"):
        # Non-production platforms retain cooperative request deadlines.
        yield
        return
    old_handler = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError("Existing process timer")
    def expired(signum, frame):
        raise BudgetExpired()
    signal.signal(signal.SIGALRM, expired)
    try:
        remaining = budget.remaining()
        if remaining <= 0:
            raise BudgetExpired()
        signal.setitimer(signal.ITIMER_REAL, remaining)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes arbitrary user input to stderr.
        raise ValueError("Invalid command arguments")


def _cli_report(data: dict, *, preparing: bool) -> None:
    """Only fixed labels/codes and counts, never credentials or raw failures."""
    print(_json(data))
    output = os.environ.get("GITHUB_OUTPUT")
    if preparing and output:
        exported_budget = math.floor(data["remaining_budget"] * 1_000_000) / 1_000_000
        with open(output, "a", encoding="utf-8") as file:
            file.write(f"ledger_ready={str(data['ledger_ready']).lower()}\n"
                       f"remaining_budget={exported_budget:.6f}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["## GitHub → Slack forwarding", f"Paused: {data.get('paused_reason') or 'no'}"]
        if preparing:
            lines += [f"Ledger ready: {data['ledger_ready']}",
                      f"Remaining Slack activity budget: {data['remaining_budget']:.6f} seconds"]
        else:
            lines += [f"Newly confirmed delivered this run: {data['delivered']}",
                      "Current durable queue (last verified snapshot): " + ", ".join(
                          f"{key}={data[key]}" for key in
                          ("pending", "retry_wait", "unknown", "needs_attention", "duplicate_source")),
                      f"Rejected (subset of needs_attention): {data['rejected']}"]
        with open(summary, "a", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Default-off CLI. Credentials are read only after fixed trust guards."""
    preparing = bool(argv and argv[0] == "prepare") if argv is not None else len(sys.argv) > 1 and sys.argv[1] == "prepare"
    data = asdict(_summary(None))
    remaining = 0.0
    ready = False
    code = 0
    try:
        parser = _SafeParser(description="Forward persisted GitHub observations to fixed Slack destination")
        commands = parser.add_subparsers(dest="command", required=True)
        prepare_parser = commands.add_parser("prepare")
        prepare_parser.add_argument("--initialize", action="store_true")
        forward_parser = commands.add_parser("forward")
        forward_parser.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS)
        args = parser.parse_args(argv)
        preparing = args.command == "prepare"
        if os.environ.get("QD_SLACK_ENABLED") != "true":
            data["paused_reason"] = "disabled"
        elif (os.environ.get("GITHUB_REPOSITORY") != REPO
              or os.environ.get("GITHUB_REF") != "refs/heads/main"
              or os.environ.get("GITHUB_EVENT_NAME") not in {"push", "schedule", "workflow_dispatch"}):
            data["paused_reason"] = "untrusted_context"
        else:
            if preparing and args.initialize and os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
                raise ValueError("Initialization requires explicit trusted dispatch")
            clock = SystemClock()
            budget = MonotonicBudget(clock, BUDGET_SECONDS if preparing else args.budget_seconds)
            issue = os.environ.get("QD_SLACK_LEDGER_ISSUE", "")
            producers = os.environ.get("QD_SLACK_PRODUCER_IDS", "")
            if not re.fullmatch(r"[1-9][0-9]*", issue) or not re.fullmatch(r"[1-9][0-9]*(?:,[1-9][0-9]*)*", producers):
                raise ValueError("Invalid ledger configuration")
            config = Config(REPO, int(issue), frozenset(int(i) for i in producers.split(",")), CHANNEL_ID)
            token = os.environ.get("GITHUB_TOKEN")
            webhook = None if preparing else os.environ.get("QD_SLACK_WEBHOOK_URL")
            if not token or (not preparing and not _valid_webhook_url(webhook)):
                raise ValueError("Missing forwarding credentials")
            http = StdlibHttpClient()
            github = GitHubClient(config, token, http, budget)
            store = LedgerStore(github, config)
            try:
                with _hard_deadline(budget):
                    if preparing:
                        ready = prepare(config, store=store, initialize=args.initialize)
                        data["paused_reason"] = None if ready else "ledger_unavailable"
                    else:
                        data = asdict(forward(config, store=store, github=github, webhook_url=webhook,
                                              http=http, clock=clock, budget=budget))
            finally:
                remaining = budget.remaining()
    except BudgetExpired:
        data["paused_reason"] = "budget_exhausted"
    except (ValueError, TypeError):
        data["paused_reason"] = "invalid_configuration"
    except Exception:
        data["paused_reason"] = "internal_error"
        code = 1
    if preparing:
        data = {"ledger_ready": ready, "remaining_budget": remaining,
                "paused_reason": data["paused_reason"]}
    else:
        data["count_scope"] = {"delivered": "newly_confirmed_this_run",
                               "queue": "last_verified_durable_snapshot",
                               "rejected": "current_subset_of_needs_attention"}
    try:
        _cli_report(data, preparing=preparing)
    except (OSError, ValueError):
        # Reporting failures must not expose raw file paths or exception text.
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
