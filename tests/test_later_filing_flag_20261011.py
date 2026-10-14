"""later_restatement used to flag ANY differing value in any other filing, including OLDER filings.
New rule: only filings with a strictly later public filing date than the original, same tag/unit/
period are 'later_filed_value_difference'. Neither field implies restatement or its absence."""
import datetime as dt
import sys
from pathlib import Path

S = Path(__file__).resolve().parents[1] / "docs/research/real_data_sample_20261011"
sys.path.insert(0, str(S))
import extract  # noqa: E402


def other(accn, filed, val, form="10-K"):
    return dict(accn=accn, filed=filed, val=val, form=form)


def test_2023_filing_is_not_a_later_restatement_of_2025_filing():
    r = extract.classify_other_filings("A-25", "2025-01-13", 100,
                                       [other("A-23", "2023-01-10", 90)])
    assert r["later_filed_value_difference"] == []
    assert r["earlier_or_same_day_value_difference"] == [("A-23", "2023-01-10", 90)]


def test_filing_after_original_with_different_value_is_flagged():
    r = extract.classify_other_filings("A-23", "2023-01-10", 90, [other("A-25", "2025-01-13", 100)])
    assert r["later_filed_value_difference"] == [("A-25", "2025-01-13", 100)]


def test_later_filing_same_value_not_flagged():
    r = extract.classify_other_filings("A-23", "2023-01-10", 90, [other("A-25", "2025-01-13", 90)])
    assert r["later_filed_value_difference"] == [] and r["earlier_or_same_day_value_difference"] == []


def test_same_day_different_accession_is_unordered_not_later():
    r = extract.classify_other_filings("A-1", "2025-01-13", 1, [other("A-2", "2025-01-13", 2)])
    assert r["later_filed_value_difference"] == []
    assert r["earlier_or_same_day_value_difference"] == [("A-2", "2025-01-13", 2)]


def test_input_order_does_not_change_result():
    o = [other("A-27", "2027-01-01", 5), other("A-25", "2025-01-01", 6), other("A-23", "2023-01-01", 7)]
    a = extract.classify_other_filings("A-24", "2024-01-01", 1, o)
    b = extract.classify_other_filings("A-24", "2024-01-01", 1, list(reversed(o)))
    assert a == b
    assert [x[0] for x in a["later_filed_value_difference"]] == ["A-25", "A-27"]


def test_committed_csv_has_new_columns_and_no_old_misleading_name():
    import csv
    cols = next(csv.reader(open(S / "data_points.csv")))
    assert "later_restatement" not in cols
    assert "later_filed_value_difference" in cols and "earlier_or_same_day_value_difference" in cols


def test_committed_csv_old_filing_not_flagged_later():
    import csv
    for r in csv.DictReader(open(S / "data_points.csv")):
        for item in filter(None, r["later_filed_value_difference"].split(";")):
            if item == "none":
                continue
            accn, filed, _ = item.split(":")[0], item.split(":")[1], None
            assert filed > r["filing_date"]


def test_reports_do_not_infer_no_restatement():
    txt = (S / "REPORT.md").read_text().lower()
    assert "no restated values" not in txt
    assert "later_restatement" not in txt or "renamed" in txt
