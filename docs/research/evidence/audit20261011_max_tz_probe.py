"""Probe: same instant, different zone spelling -> MAX (run against the code at HEAD)."""
import sys; sys.path.insert(0, "tests"); sys.path.insert(0, ".")
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import inspect
from factor_candidates import max_lottery
import test_factor_audit_20261011 as t
inst = datetime(2026, 3, 1, 0, 30, tzinfo=timezone.utc)
ny = inst.astimezone(ZoneInfo("America/New_York"))
kw = {"calendar_tz": "America/New_York"} if "calendar_tz" in inspect.signature(max_lottery).parameters else {}
for name, x in (("UTC", inst), ("America/New_York", ny)):
    v = max_lottery(t.rows(t.PEAKS), "A", x, t.CAL, **kw)
    print(f"as_of={x.isoformat():32s} calendar_tz_param={'yes' if kw else 'no (implicit: as_of.date() in input zone)'} MAX={v}")
