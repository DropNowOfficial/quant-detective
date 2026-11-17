"""Consistency tests for the committed real-data sample (docs/research/real_data_sample_20261011).

These check internal consistency of the committed CSV and results; they say
nothing about investment validity.
"""
import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

S = Path(__file__).resolve().parents[1] / "docs/research/real_data_sample_20261011"
ET = ZoneInfo("America/New_York")


def rows():
    return list(csv.DictReader(open(S / "data_points.csv")))


def test_acceptance_columns_present():
    cols = rows()[0].keys()
    for c in ("acceptance_utc", "acceptance_et", "acceptance_index_et_naive",
              "acceptance_raw_submissions_field", "acceptance_check"):
        assert c in cols


def test_acceptance_utc_is_index_accepted_time_converted_from_eastern():
    for r in rows():
        naive = dt.datetime.strptime(r["acceptance_index_et_naive"], "%Y-%m-%d %H:%M:%S")
        want = naive.replace(tzinfo=ET).astimezone(dt.timezone.utc)
        got = dt.datetime.fromisoformat(r["acceptance_utc"].replace("Z", "+00:00"))
        assert got == want, (r["accession"], got, want)
        et = dt.datetime.fromisoformat(r["acceptance_et"])
        assert et == want and et.utcoffset() in (dt.timedelta(hours=-4), dt.timedelta(hours=-5))
        assert et.replace(tzinfo=None) == naive


def test_every_row_records_whether_raw_field_matches_index():
    assert {r["acceptance_check"] for r in rows()} <= {"raw_field_matches_index", "RAW_FIELD_MISMATCH_index_used"}
