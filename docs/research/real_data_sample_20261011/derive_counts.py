#!/usr/bin/env python3
"""Derive row / used-input counts from data_points.csv + used_inputs.json (written by recompute.py).

  python derive_counts.py --print          # JSON to stdout
  python derive_counts.py --write          # (re)write counts.json
Documented counts must equal these (tests/test_sample_counts_20261011.py)."""
import csv, json, pathlib, sys, collections
HERE = pathlib.Path(__file__).resolve().parent


def derive(data=HERE / "data_points.csv", used=HERE / "used_inputs.json"):
    rows = list(csv.DictReader(open(data)))
    used_keys = {tuple(k) for k in json.load(open(used))}
    key = lambda r: (r["ticker"], r["accession"], r["tag"], r["end"])
    row_keys = [key(r) for r in rows]
    assert len(set(row_keys)) == len(row_keys), "duplicate (ticker, accession, tag, end) rows"
    missing = used_keys - set(row_keys)
    assert not missing, f"used inputs without a data_points row: {sorted(missing)[:3]}"
    u = [r for r in rows if key(r) in used_keys]
    rv = lambda rs: sum(1 for r in rs if r["rfile_matches"] == "True")
    return dict(
        rows=len(rows), used_inputs=len(u), context_extra_rows=len(rows) - len(u),
        rows_per_ticker=dict(sorted(collections.Counter(r["ticker"] for r in rows).items())),
        used_per_ticker=dict(sorted(collections.Counter(r["ticker"] for r in u).items())),
        filings=len({r["accession"] for r in rows}),
        used_with_rfile_value=sum(1 for r in u if r["rfile_value"] != ""), used_rfile_matches=rv(u),
        used_rfile_mismatches=sum(1 for r in u if r["rfile_matches"] == "False"),
        context_rows_with_rfile_mismatch=sum(1 for r in rows if key(r) not in used_keys and r["rfile_matches"] == "False"),
        all_rows_rfile_matches=rv(rows), all_rows_rfile_mismatches=sum(1 for r in rows if r["rfile_matches"] == "False"),
        rows_acceptance_raw_field_mismatch=sum(1 for r in rows if r["acceptance_check"] != "raw_field_matches_index"),
        rows_with_later_filed_value_difference=sum(1 for r in rows if r["later_filed_value_difference"] != "none"),
        rows_with_earlier_or_same_day_value_difference=sum(1 for r in rows if r["earlier_or_same_day_value_difference"] != "none"),
        used_rows_with_later_filed_value_difference=sum(1 for r in u if r["later_filed_value_difference"] != "none"),
    )


if __name__ == "__main__":
    d = derive()
    if "--write" in sys.argv:
        (HERE / "counts.json").write_text(json.dumps(d, indent=1) + "\n")
    print(json.dumps(d, indent=1))
