"""Read-only Client Portal Gateway adapter for live market observations.

Only loopback HTTPS is allowed. This module intentionally exposes authentication,
contract discovery, keepalive and market-data snapshot reads only. It has no
order endpoints.
"""
from __future__ import annotations

import json
import math
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


ALLOWED_PATHS = {
    "/iserver/auth/status": {"GET"},
    "/tickle": {"POST"},
    "/iserver/accounts": {"GET"},
    "/iserver/secdef/search": {"GET"},
    "/iserver/marketdata/snapshot": {"GET"},
}


class CPGError(RuntimeError):
    pass


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    while text and text[0].isalpha():
        text = text[1:].lstrip()
    scale = 1.0
    if text and text[-1:].upper() in {"K", "M", "B"}:
        scale = {"K": 1e3, "M": 1e6, "B": 1e9}[text[-1].upper()]
        text = text[:-1]
    try:
        out = float(text) * scale
    except ValueError:
        return None
    return out if math.isfinite(out) else None


class ClientPortalGateway:
    def __init__(self, base_url="https://127.0.0.1:5000/v1/api", timeout=8, opener=None, sleeper=time.sleep):
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("IBKR Client Portal Gateway URL must be loopback HTTPS")
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._open = opener or urlopen
        self._sleep = sleeper
        self._ssl = ssl._create_unverified_context()
        self._accounts_ready = False
        self._snapshot_warmed = set()

    def _request(self, method, path, params=None):
        if method not in ALLOWED_PATHS.get(path, set()):
            raise ValueError("IBKR adapter blocks non-read-only/non-keepalive endpoint")
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        req = Request(url, method=method, headers={
            "Accept": "application/json",
            "User-Agent": "quant-detective-read-only/0.2",
        })
        data = b"" if method == "POST" else None
        if data is not None:
            req.data = data
        try:
            with self._open(req, timeout=self.timeout, context=self._ssl) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise CPGError(f"IBKR HTTP {exc.code}: {detail}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise CPGError(f"IBKR gateway unavailable: {type(exc).__name__}: {exc}") from exc
        if status != 200:
            raise CPGError(f"IBKR HTTP {status}")
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CPGError("IBKR returned non-JSON data") from exc

    def auth_status(self):
        data = self._request("GET", "/iserver/auth/status")
        return {
            "authenticated": bool(data.get("authenticated")),
            "connected": bool(data.get("connected")),
            "competing": bool(data.get("competing")),
            "message": data.get("message") or "",
        }

    def tickle(self):
        return self._request("POST", "/tickle")

    def ensure_accounts(self):
        data = self._request("GET", "/iserver/accounts")
        accounts = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(accounts, list) or not accounts:
            raise CPGError("IBKR brokerage accounts unavailable; gateway session may not be authenticated")
        self._accounts_ready = True
        return accounts

    def resolve_symbol(self, symbol):
        rows = self._request("GET", "/iserver/secdef/search", {"symbol": symbol})
        if not isinstance(rows, list):
            raise CPGError(f"{symbol}: IBKR contract search returned invalid payload")
        exact = []
        for row in rows:
            if not isinstance(row, dict) or str(row.get("symbol", "")).upper() != symbol.upper():
                continue
            sections = row.get("sections") or []
            if any(isinstance(s, dict) and s.get("secType") == "STK" for s in sections):
                exact.append(row)
        if not exact:
            raise CPGError(f"{symbol}: no exact STK contract found")
        preferred = [r for r in exact if str(r.get("description", "")).upper() in {
            "NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"
        }]
        row = (preferred or exact)[0]
        try:
            conid = int(row["conid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CPGError(f"{symbol}: invalid conid") from exc
        return {"symbol": symbol.upper(), "conid": conid, "exchange": row.get("description")}

    def resolve_symbols(self, symbols, pause_seconds=0.12):
        out, errors = {}, {}
        for i, symbol in enumerate(symbols):
            try:
                out[symbol] = self.resolve_symbol(symbol)
            except Exception as exc:
                errors[symbol] = f"{type(exc).__name__}: {str(exc)[:200]}"
            if i + 1 < len(symbols) and pause_seconds:
                self._sleep(pause_seconds)
        return out, errors

    def snapshots(self, contracts, fields=("31", "82", "83", "84", "86", "7762")):
        if not contracts:
            return {}
        if not self._accounts_ready:
            self.ensure_accounts()
        conids = ",".join(str(v["conid"]) for v in contracts.values())
        params = {"conids": conids, "fields": ",".join(fields)}
        key = tuple(sorted(int(v["conid"]) for v in contracts.values()))
        rows = self._request("GET", "/iserver/marketdata/snapshot", params)
        if key not in self._snapshot_warmed:
            self._snapshot_warmed.add(key)
            self._sleep(0.55)
            rows = self._request("GET", "/iserver/marketdata/snapshot", params)
        by_conid = {int(v["conid"]): symbol for symbol, v in contracts.items()}
        out = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                conid = int(row.get("conid"))
            except (TypeError, ValueError):
                continue
            symbol = by_conid.get(conid)
            if not symbol:
                continue
            out[symbol] = {
                "symbol": symbol,
                "conid": conid,
                "last": _number(row.get("31")),
                "change": _number(row.get("82")),
                "change_pct": _number(row.get("83")),
                "bid": _number(row.get("84")),
                "ask": _number(row.get("86")),
                "volume": _number(row.get("7762")) or _number(row.get("87")),
                "updated_ms": _number(row.get("_updated")),
                "market_data_availability": row.get("6509"),
            }
        return out
