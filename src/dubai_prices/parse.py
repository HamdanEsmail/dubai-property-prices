"""Turn cached API pages (or a Dubai Pulse bulk CSV) into one tidy, typed table.

Only the English columns are kept. The numeric id columns in the public API are
all zero, so names (area, project, metro...) are the keys.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

COLUMNS = [
    "transaction_number",
    "date",
    "group",
    "procedure",
    "is_offplan",
    "is_freehold",
    "usage",
    "area",
    "property_type",
    "property_subtype",
    "rooms",
    "parking",
    "building_age",
    "project",
    "master_project",
    "nearest_metro",
    "nearest_mall",
    "nearest_landmark",
    "value_aed",
    "procedure_area_sqm",
    "actual_area_sqm",
    "source",
]

# API field -> tidy column (order follows COLUMNS)
API_FIELDS = {
    "TRANSACTION_NUMBER": "transaction_number",
    "INSTANCE_DATE": "date",
    "GROUP_EN": "group",
    "PROCEDURE_EN": "procedure",
    "IS_OFFPLAN": "is_offplan",
    "IS_FREE_HOLD": "is_freehold",
    "USAGE_EN": "usage",
    "AREA_EN": "area",
    "PROP_TYPE_EN": "property_type",
    "PROP_SB_TYPE_EN": "property_subtype",
    "ROOMS_EN": "rooms",
    "PARKING": "parking",
    "BUILDING_AGE": "building_age",
    "PROJECT_EN": "project",
    "MASTER_PROJECT_EN": "master_project",
    "NEAREST_METRO_EN": "nearest_metro",
    "NEAREST_MALL_EN": "nearest_mall",
    "NEAREST_LANDMARK_EN": "nearest_landmark",
    "TRANS_VALUE": "value_aed",
    "PROCEDURE_AREA": "procedure_area_sqm",
    "ACTUAL_AREA": "actual_area_sqm",
}

# Dubai Pulse / data.dubai "dld_transactions" bulk file -> tidy column
PULSE_FIELDS = {
    "transaction_id": "transaction_number",
    "instance_date": "date",
    "trans_group_en": "group",
    "procedure_name_en": "procedure",
    "property_usage_en": "usage",
    "area_name_en": "area",
    "property_type_en": "property_type",
    "property_sub_type_en": "property_subtype",
    "rooms_en": "rooms",
    "project_name_en": "project",
    "master_project_en": "master_project",
    "nearest_metro_en": "nearest_metro",
    "nearest_mall_en": "nearest_mall",
    "nearest_landmark_en": "nearest_landmark",
    "actual_worth": "value_aed",
    "procedure_area": "procedure_area_sqm",
}

STRING_COLUMNS = [
    "transaction_number", "group", "procedure", "usage", "area", "property_type", "property_subtype",
    "rooms", "parking", "project", "master_project", "nearest_metro", "nearest_mall", "nearest_landmark", "source",
]
FLOAT_COLUMNS = ["building_age", "value_aed", "procedure_area_sqm", "actual_area_sqm"]
BOOL_COLUMNS = ["is_offplan", "is_freehold"]


def _clean_text(value) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def rows_from_page(page: dict) -> list[dict]:
    """Tidy dicts (English columns only, in COLUMNS order) for every row of one API page."""
    result = (page.get("response") or {}).get("result") or []
    rows = []
    for raw in result:
        row = {col: raw.get(field) for field, col in API_FIELDS.items()}
        row["source"] = "dld-api"
        rows.append({col: row[col] for col in COLUMNS})
    return rows


def frame_from_rows(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    return _type_frame(df)


def _type_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for col in COLUMNS:
        series = df[col] if col in df else pd.Series([None] * len(df), index=df.index, dtype=object)
        if col == "date":
            out[col] = pd.to_datetime(series, errors="coerce")
        elif col in BOOL_COLUMNS:
            numeric = pd.to_numeric(series, errors="coerce")
            out[col] = (numeric == 1).astype("boolean").where(numeric.notna(), pd.NA)
        elif col in FLOAT_COLUMNS:
            out[col] = pd.to_numeric(series, errors="coerce").astype("float64")
        else:
            out[col] = series.map(_clean_text).astype("string")
    return out[COLUMNS]


def frame_from_pages(paths: Iterable[Path]) -> pd.DataFrame:
    """Concatenate cached pages and drop rows that are exact repeats (same row on two pages)."""
    rows: list[dict] = []
    for path in paths:
        rows.extend(rows_from_page(json.loads(Path(path).read_text(encoding="utf-8"))))
    df = frame_from_rows(rows)
    return df.drop_duplicates().reset_index(drop=True)


def frame_from_pulse_csv(path: Path) -> pd.DataFrame:
    """Map the data.dubai bulk CSV (previous years) onto the same tidy columns.

    The bulk file has no actual area, building age or freehold flag; those stay
    missing so the cleaner treats them the same way as missing API values.
    """
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    raw.columns = [c.strip().lower() for c in raw.columns]
    df = pd.DataFrame(index=raw.index)
    for src, col in PULSE_FIELDS.items():
        df[col] = raw[src].replace("", None) if src in raw else None
    df["date"] = pd.to_datetime(raw["instance_date"], format="%d-%m-%Y", errors="coerce")
    reg = raw.get("reg_type_en", pd.Series([""] * len(raw)))
    df["is_offplan"] = reg.str.contains("Off-Plan", case=False, na=False).astype(int).where(reg != "", None)
    df["is_freehold"] = None
    df["parking"] = raw.get("has_parking", pd.Series([""] * len(raw))).map(lambda v: "1" if str(v).strip() == "1" else None)
    df["building_age"] = None
    df["actual_area_sqm"] = None
    df["source"] = "pulse"
    return _type_frame(df)
