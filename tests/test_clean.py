import json
from pathlib import Path

import numpy as np
import pandas as pd

from dubai_prices import clean, parse

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_frame():
    page = json.loads((FIXTURES / "page_sales.json").read_text(encoding="utf-8"))
    return parse.frame_from_pages_rows(page) if hasattr(parse, "frame_from_pages_rows") else parse.frame_from_rows(parse.rows_from_page(page))


def test_bulk_deal_rows_dropped():
    out, steps = clean.clean_sales(fixture_frame())
    assert "11-200-2026" not in set(out["transaction_number"])
    bulk = next(s for s in steps if s.name == "bulk_deals")
    assert bulk.dropped == 2


def test_implausible_area_dropped():
    out, steps = clean.clean_sales(fixture_frame())
    assert "11-105-2026" not in set(out["transaction_number"])  # 1 sqm flat
    assert "11-106-2026" not in set(out["transaction_number"])  # zero value
    assert "11-102-2026" in set(out["transaction_number"])  # 400 sqm villa is fine


def test_non_residential_dropped():
    out, _ = clean.clean_sales(fixture_frame())
    assert "11-103-2026" not in set(out["transaction_number"])  # office
    assert "11-104-2026" not in set(out["transaction_number"])  # land
    assert set(out["property_subtype"]) <= {"Flat", "Villa"}


def test_rooms_and_parking_parsed():
    out, _ = clean.clean_sales(fixture_frame())
    by_tn = out.set_index("transaction_number")
    assert by_tn.loc["11-101-2026", "rooms_n"] == 2
    assert by_tn.loc["102-5-2026", "rooms_n"] == 0  # Studio
    assert by_tn.loc["11-102-2026", "rooms_n"] == 4
    assert by_tn.loc["11-101-2026", "parking_n"] == 1
    assert by_tn.loc["11-102-2026", "parking_n"] == 2  # "B1-12, B1-13"
    assert by_tn.loc["102-5-2026", "parking_n"] == 0  # missing
    assert by_tn.loc["11-900-2026", "parking_n"] == 2  # "1  0" = two bay labels
    assert by_tn.loc["11-101-2026", "price_per_sqm"] == 15000.0
    assert by_tn.loc["11-101-2026", "ym"] == "2026-01" and by_tn.loc["11-900-2026", "month_n"] == 9


def test_rooms_map_edge_cases():
    assert clean.rooms_to_number("Studio") == 0
    assert clean.rooms_to_number("1 B/R") == 1
    assert clean.rooms_to_number("7 B/R") == 7
    assert np.isnan(clean.rooms_to_number("PENTHOUSE"))
    assert np.isnan(clean.rooms_to_number("NA"))
    assert np.isnan(clean.rooms_to_number(None))


def test_parking_count_edge_cases():
    assert clean.parking_count("1") == 1
    assert clean.parking_count("3") == 3
    assert clean.parking_count("B1-12, B1-13") == 2
    assert clean.parking_count("EU  0") == 2
    assert clean.parking_count("G-5,G-6,G-7,G-8,G-9,G-10,G-11,G-12,G-13,G-14,G-15,G-16") == 10  # capped
    assert clean.parking_count(None) == 0
    assert clean.parking_count("") == 0


def test_steps_account_for_every_row():
    df = fixture_frame()
    out, steps = clean.clean_sales(df)
    assert steps[0].remaining + steps[0].dropped == len(df)
    for prev, nxt in zip(steps, steps[1:]):
        assert nxt.dropped + nxt.remaining == prev.remaining
    assert steps[-1].remaining == len(out)
    assert [s.name for s in steps] == [
        "sales_only", "sale_procedures", "exact_duplicates", "residential_flat_villa", "missing_value_or_area",
        "bulk_deals", "area_bounds", "price_per_sqm_bounds",
    ]
    assert all(s.rule for s in steps)  # every step carries a plain-English rule


def test_output_columns_and_order():
    out, _ = clean.clean_sales(fixture_frame())
    for col in ["price_per_sqm", "ym", "month_n", "rooms_n", "parking_n"]:
        assert col in out.columns
    assert out["date"].is_monotonic_increasing
    assert out.index.tolist() == list(range(len(out)))


def test_normalise_area_names():
    assert clean.normalise_area("JUMEIRAH VILLAGE CIRCLE") == "Jumeirah Village Circle"
    assert clean.normalise_area("Al Yelayiss 1") == "Al Yelayiss 1"
    assert clean.normalise_area("Madinat Al Mataar") == "Madinat Al Mataar"
    assert clean.normalise_area("BUSINESS BAY ") == "Business Bay"
    assert clean.normalise_area("Burj Khalifa") == "Burj Khalifa"
    assert clean.normalise_area("palm jumeirah") == "Palm Jumeirah"
    assert clean.normalise_area("ARABIAN RANCHES II") == "Arabian Ranches II"
    assert clean.normalise_area("Arabian Ranches III") == "Arabian Ranches III"
    assert clean.normalise_area("DMCC-EZ2") == "DMCC-EZ2"
    assert clean.normalise_area("INTERNATIONAL CITY PH 1") == "International City Ph 1"


def test_metro_spelling_variants_merged():
    df = fixture_frame()
    df.loc[df["transaction_number"] == "11-102-2026", "nearest_metro"] = "Jumeirah Beach Resdency"
    out, _ = clean.clean_sales(df)
    assert out.set_index("transaction_number").loc["11-102-2026", "nearest_metro"] == "Jumeirah Beach Residency"


def test_non_sale_procedures_dropped():
    df = fixture_frame()
    df.loc[df["transaction_number"] == "11-101-2026", "procedure"] = "Lease to Own Registration"
    out, steps = clean.clean_sales(df)
    assert "11-101-2026" not in set(out["transaction_number"])
    assert next(s for s in steps if s.name == "sale_procedures").dropped == 2  # row 0 and its exact duplicate


def test_procedure_area_fills_missing_actual_area():
    df = fixture_frame()
    df.loc[df["transaction_number"] == "11-102-2026", "actual_area_sqm"] = None
    out, _ = clean.clean_sales(df)
    villa = out.set_index("transaction_number").loc["11-102-2026"]
    assert villa["actual_area_sqm"] == 380.0 and villa["price_per_sqm"] == 6000000 / 380.0
