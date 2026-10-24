import json
from pathlib import Path

import pandas as pd

from dubai_prices import parse

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = json.loads((FIXTURES / "page_sales.json").read_text(encoding="utf-8"))


def test_rows_from_page_keeps_english_columns_only():
    rows = parse.rows_from_page(PAGE)
    assert len(rows) == 11
    assert list(rows[0].keys()) == parse.COLUMNS
    assert not any(k.endswith("_AR") or k.isupper() for k in rows[0])
    assert rows[0]["transaction_number"] == "11-101-2026"
    assert rows[0]["source"] == "dld-api"


def test_frame_types():
    df = parse.frame_from_rows(parse.rows_from_page(PAGE))
    assert list(df.columns) == parse.COLUMNS
    assert str(df["date"].dtype).startswith("datetime64")
    assert df["date"].iloc[0] == pd.Timestamp("2026-01-05 10:00:00")
    assert df["is_offplan"].dtype == "boolean" and df["is_freehold"].dtype == "boolean"
    assert bool(df["is_offplan"].iloc[2]) is True and bool(df["is_offplan"].iloc[0]) is False
    assert df["value_aed"].dtype == "float64" and df["value_aed"].iloc[0] == 1500000.0
    assert df["actual_area_sqm"].iloc[1] == 400.0 and df["procedure_area_sqm"].iloc[1] == 380.0
    assert df["building_age"].iloc[1] == 12.0
    assert pd.isna(df["nearest_metro"].iloc[0])
    assert pd.isna(df["parking"].iloc[2])
    assert df["parking"].iloc[1] == "B1-12, B1-13"


def test_duplicate_rows_collapse(tmp_path):
    (tmp_path / "page-0000.json").write_text(json.dumps(PAGE), encoding="utf-8")
    second = {**PAGE, "response": {"result": [dict(r, RN=99) for r in PAGE["response"]["result"][:3]]}}
    (tmp_path / "page-0001.json").write_text(json.dumps(second), encoding="utf-8")
    df = parse.frame_from_pages(sorted(tmp_path.glob("page-*.json")))
    # 11 fixture rows contain one exact duplicate (row 11 == row 1); page 2 repeats three rows
    assert len(df) == 10
    assert df["transaction_number"].tolist()[:3] == ["11-101-2026", "11-102-2026", "102-5-2026"]


def test_pulse_csv_maps_columns():
    df = parse.frame_from_pulse_csv(FIXTURES / "pulse_sample.csv")
    assert list(df.columns) == parse.COLUMNS
    assert len(df) == 2
    assert df["date"].iloc[0] == pd.Timestamp("2025-01-05")
    assert df["date"].iloc[1] == pd.Timestamp("2025-03-20")
    assert bool(df["is_offplan"].iloc[0]) is False and bool(df["is_offplan"].iloc[1]) is True
    assert df["is_freehold"].isna().all()  # not published in the bulk file
    assert df["area"].iloc[0] == "Marsa Dubai" and df["property_subtype"].iloc[1] == "Flat"
    assert df["rooms"].iloc[1] == "Studio"
    assert df["value_aed"].iloc[0] == 1200000.0
    assert df["procedure_area_sqm"].iloc[0] == 75.5
    assert df["actual_area_sqm"].isna().all()  # the bulk file only has procedure area
    assert df["parking"].iloc[0] == "1" and pd.isna(df["parking"].iloc[1])
    assert (df["source"] == "pulse").all()
    assert (df["group"] == "Sales").all()
