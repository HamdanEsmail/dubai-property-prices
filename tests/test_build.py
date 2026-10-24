import json
from pathlib import Path

import pandas as pd

from dubai_prices import build, parse

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_frame():
    page = json.loads((FIXTURES / "page_sales.json").read_text(encoding="utf-8"))
    return parse.frame_from_rows(parse.rows_from_page(page))


def test_build_from_snapshot_only(tmp_path):
    raw, snap, proc = tmp_path / "raw", tmp_path / "snapshot", tmp_path / "processed"
    snap.mkdir()
    _fixture_frame().to_parquet(snap / "dld_sales_2026.parquet", index=False)
    report = build.build_dataset(raw, snap, proc)
    assert report.source == "snapshot"
    assert report.raw_rows == 11 and report.clean_rows == 4
    out = pd.read_parquet(proc / "sales_clean.parquet")
    assert len(out) == 4 and "price_per_sqm" in out.columns
    saved = json.loads((proc / "clean_report.json").read_text(encoding="utf-8"))
    assert saved["clean_rows"] == 4 and saved["steps"][0]["name"] == "sales_only"
    assert saved["date_min"].startswith("2026-01-05") and saved["date_max"].startswith("2026-09-15")
    assert saved["profile"]["procedures_residential_flat_villa"]["Sell"]["rows"] >= 4
    assert saved["profile"]["building_age_zero_share"] < 1
    assert "4 rows" in report.summary()


def test_build_from_raw_pages_writes_snapshot(tmp_path):
    raw, snap, proc = tmp_path / "raw", tmp_path / "snapshot", tmp_path / "processed"
    month = raw / "sales" / "2026-01"
    month.mkdir(parents=True)
    (month / "page-0000.json").write_text((FIXTURES / "page_sales.json").read_text(encoding="utf-8"), encoding="utf-8")
    report = build.build_dataset(raw, snap, proc)
    assert report.source == "raw"
    snapshot = pd.read_parquet(snap / "dld_sales_2026.parquet")
    assert len(snapshot) == 10  # exact duplicate collapsed by the parser
    assert list(snapshot.columns) == parse.COLUMNS
    assert report.clean_rows == 4


def test_build_merges_pulse_csv(tmp_path):
    raw, snap, proc = tmp_path / "raw", tmp_path / "snapshot", tmp_path / "processed"
    snap.mkdir()
    _fixture_frame().to_parquet(snap / "dld_sales_2026.parquet", index=False)
    pulse = raw / "pulse"
    pulse.mkdir(parents=True)
    (pulse / "transactions.csv").write_text((FIXTURES / "pulse_sample.csv").read_text(encoding="utf-8"), encoding="utf-8")
    report = build.build_dataset(raw, snap, proc)
    out = pd.read_parquet(proc / "sales_clean.parquet")
    assert report.raw_rows == 13
    assert (out["source"] == "pulse").sum() == 2  # procedure area stands in for the missing actual area
    assert out["date"].min().year == 2025
