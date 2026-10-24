"""Cleaning rules for DLD sales. Every rule is a CleanStep with a plain-English
description so the README and docs/data.md can print exactly what was dropped.

Bounds were chosen after looking at the 2026 distribution (see docs/data.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Procedures inside the "Sales" group that are straightforward sales of a property.
# Lease-to-own contracts, development registrations (developer-level transfers at
# roughly half the market price per sqm) and land additions are not.
SALE_PROCEDURES = {
    "Sale",
    "Sell",
    "Sell - Pre registration",
    "Delayed Sell",
    "Sale On Payment Plan",
}

RESIDENTIAL_SUBTYPES = ("Flat", "Villa")

# Plausible size in square metres by sub-type (inclusive).
AREA_BOUNDS = {"Flat": (20.0, 1000.0), "Villa": (50.0, 3000.0)}

# Plausible price per square metre in AED (inclusive).
PPSQM_BOUNDS = (3000.0, 120000.0)

PARKING_CAP = 10

SMALL_WORDS = {"of", "and", "the", "al", "bin", "by"}


@dataclass(frozen=True)
class CleanStep:
    name: str
    rule: str
    dropped: int
    remaining: int


def rooms_to_number(text) -> float:
    """'Studio' -> 0, 'N B/R' -> N, anything else (PENTHOUSE, NA, missing) -> NaN."""
    if text is None or (not isinstance(text, str) and pd.isna(text)):
        return np.nan
    text = str(text).strip()
    if text.lower() == "studio":
        return 0.0
    match = re.fullmatch(r"(\d+)\s*B/R", text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else np.nan


def parking_count(text) -> int:
    """Count parking bays from the free-text PARKING field.

    A bare integer is taken as a count ("1", "3"); anything else is a list of bay
    labels separated by commas or runs of spaces ("B1-12, B1-13", "1  0", "EU  0").
    Missing -> 0. Capped at PARKING_CAP because a few rows list a whole floor.
    """
    if text is None or (not isinstance(text, str) and pd.isna(text)):
        return 0
    text = str(text).strip()
    if not text:
        return 0
    if re.fullmatch(r"\d+", text):
        return min(int(text), PARKING_CAP)
    tokens = [t for t in re.split(r",|\s{2,}", text) if t.strip()]
    return min(len(tokens), PARKING_CAP)


def normalise_area(text) -> str | None:
    """Title-case community names so 'BUSINESS BAY' and 'Business Bay' are one key."""
    if text is None or (not isinstance(text, str) and pd.isna(text)):
        return None
    words = str(text).strip().split()
    out = []
    for i, word in enumerate(words):
        lower = word.lower()
        if re.fullmatch(r"[IVX]+", word) or (word.isupper() and any(ch.isdigit() for ch in word)):
            out.append(word)  # roman numerals (Arabian Ranches II) and codes (DMCC-EZ2) stay as they are
        elif i and lower in SMALL_WORDS:
            out.append(lower.capitalize() if lower in {"al", "bin"} else lower)
        else:
            out.append(lower.capitalize())
    return " ".join(out)


def _step(steps: list[CleanStep], name: str, rule: str, before: int, df: pd.DataFrame) -> pd.DataFrame:
    steps.append(CleanStep(name, rule, before - len(df), len(df)))
    return df


def clean_sales(df: pd.DataFrame) -> tuple[pd.DataFrame, list[CleanStep]]:
    """Apply every rule in order and return (clean table, list of steps)."""
    steps: list[CleanStep] = []
    df = df.copy()

    n = len(df)
    df = df[df["group"] == "Sales"]
    _step(steps, "sales_only", "Keep the Sales group only (mortgages and gifts are separate groups).", n, df)

    n = len(df)
    df = df[df["procedure"].isin(SALE_PROCEDURES)]
    _step(steps, "sale_procedures",
          "Keep the four sale procedures (Sale, Sell - Pre registration, Delayed Sell, Sale On Payment Plan). "
          "Drop lease-to-own contracts, development registrations and land additions, which are not market sales.", n, df)

    n = len(df)
    df = df.drop_duplicates()
    _step(steps, "exact_duplicates", "Drop rows that repeat another row exactly (same page served twice).", n, df)

    n = len(df)
    df = df[(df["usage"] == "Residential") & df["property_subtype"].isin(RESIDENTIAL_SUBTYPES)]
    _step(steps, "residential_flat_villa",
          "Keep residential flats and villas only. Drop land, offices, shops, hotel apartments, hotel rooms and whole buildings.", n, df)

    n = len(df)
    df = df.assign(actual_area_sqm=df["actual_area_sqm"].fillna(df["procedure_area_sqm"]))
    df = df[(df["value_aed"].fillna(0) > 0) & (df["actual_area_sqm"].fillna(0) > 0)]
    _step(steps, "missing_value_or_area",
          "Drop rows with a missing or zero price or a missing or zero built-up area "
          "(procedure area stands in when built-up area is missing, as in the bulk file).", n, df)

    n = len(df)
    counts = df["transaction_number"].map(df["transaction_number"].value_counts())
    df = df[counts == 1]
    _step(steps, "bulk_deals",
          "Drop bulk deals: rows that share a transaction number carry the whole portfolio price on every unit.", n, df)

    n = len(df)
    lo = df["property_subtype"].map({k: v[0] for k, v in AREA_BOUNDS.items()})
    hi = df["property_subtype"].map({k: v[1] for k, v in AREA_BOUNDS.items()})
    df = df[(df["actual_area_sqm"] >= lo) & (df["actual_area_sqm"] <= hi)]
    _step(steps, "area_bounds",
          f"Keep flats of {AREA_BOUNDS['Flat'][0]:.0f} to {AREA_BOUNDS['Flat'][1]:.0f} sqm and villas of "
          f"{AREA_BOUNDS['Villa'][0]:.0f} to {AREA_BOUNDS['Villa'][1]:.0f} sqm.", n, df)

    df = df.assign(price_per_sqm=df["value_aed"] / df["actual_area_sqm"])
    n = len(df)
    df = df[(df["price_per_sqm"] >= PPSQM_BOUNDS[0]) & (df["price_per_sqm"] <= PPSQM_BOUNDS[1])]
    _step(steps, "price_per_sqm_bounds",
          f"Keep {PPSQM_BOUNDS[0]:,.0f} to {PPSQM_BOUNDS[1]:,.0f} AED per sqm. Below that are nominal transfers and "
          "data errors; above it are portfolio deals recorded against one unit.", n, df)

    df = df.assign(
        area=df["area"].map(normalise_area).astype("string"),
        ym=df["date"].dt.strftime("%Y-%m"),
        month_n=df["date"].dt.month.astype(int),
        rooms_n=df["rooms"].map(rooms_to_number).astype(float),
        parking_n=df["parking"].map(parking_count).astype(int),
    )
    df = df.sort_values(["date", "transaction_number"], kind="stable").reset_index(drop=True)
    return df, steps
