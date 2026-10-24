"""Feature table for the models. Pure function of the clean table so Python and the
browser build identical inputs.

Building age is not a feature: the public API publishes 0 for every 2026 row.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CATEGORICAL = ["area", "property_subtype", "rooms", "nearest_metro", "nearest_mall", "nearest_landmark"]
NUMERIC = ["log_area", "rooms_n", "is_offplan", "is_freehold", "parking_n", "month_n"]
FEATURES = CATEGORICAL + NUMERIC

UNKNOWN = "__unknown__"  # label never seen in training
MISSING = "__missing__"  # label absent in the row (e.g. no nearest metro)


def _labels(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna(MISSING).astype(object)


def make_features(df: pd.DataFrame, categories: dict[str, list[str]] | None = None) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Return (X, categories). Pass the training `categories` back in at prediction time
    so unseen labels become UNKNOWN and category codes stay identical."""
    X = pd.DataFrame(index=df.index)
    cats: dict[str, list[str]] = {}
    for col in CATEGORICAL:
        values = _labels(df[col])
        if categories is None:
            levels = sorted(set(values) | {UNKNOWN, MISSING})
        else:
            levels = list(categories[col])
        values = values.where(values.isin(set(levels)), UNKNOWN)
        X[col] = pd.Categorical(values, categories=levels)
        cats[col] = levels
    X["log_area"] = np.log(df["actual_area_sqm"].astype(float))
    X["rooms_n"] = df["rooms_n"].astype(float)
    X["is_offplan"] = df["is_offplan"].astype("Float64").astype(float)
    X["is_freehold"] = df["is_freehold"].astype("Float64").astype(float)
    X["parking_n"] = df["parking_n"].astype(float)
    X["month_n"] = df["month_n"].astype(float)
    return X[FEATURES], cats
