"""Precompute everything the static explorer needs as small JSON files.

web/data/stats.json    ranges per community, community x type, community x type x rooms
web/data/monthly.json  median price per sqm by month, overall and per community
web/data/areas/<slug>.json  recent comparable sales and monthly series for one community
web/data/meta.json     data-as-of date, row counts, validation metrics
web/data/model.json    the LightGBM trees (see export_model)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .export_model import export_model

THIN_CELL = 5
MIN_MONTHLY_ROWS = 30
ROOMS_ORDER = ["Studio", "1 B/R", "2 B/R", "3 B/R", "4 B/R", "5 B/R", "6 B/R", "7 B/R", "PENTHOUSE", "NA"]


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(text).lower())).strip("-")


def _range(series: pd.Series) -> dict:
    q = series.quantile([0.25, 0.5, 0.75])
    return {"n": int(len(series)), "p25": round(float(q.iloc[0])), "median": round(float(q.iloc[1])), "p75": round(float(q.iloc[2]))}


def _mode(series: pd.Series):
    counts = series.dropna().value_counts()
    return None if counts.empty else counts.index[0]


def _defaults(df: pd.DataFrame) -> dict:
    offplan = df["is_offplan"].astype("Float64")
    return {
        "nearest_metro": _mode(df["nearest_metro"]),
        "nearest_mall": _mode(df["nearest_mall"]),
        "nearest_landmark": _mode(df["nearest_landmark"]),
        "is_freehold": bool(df["is_freehold"].astype("Float64").fillna(1).median() >= 0.5),
        "parking_n": int(df["parking_n"].median()),
        "offplan_share": round(float(offplan.mean()), 3) if offplan.notna().any() else None,
        "median_size": round(float(df["actual_area_sqm"].median()), 1),
    }


PROFILE_COLUMNS = ["nearest_metro", "nearest_mall", "nearest_landmark", "parking_n"]
MAX_PROFILES = 6
MIN_PROFILE_ROWS = 10


def cell_profiles(rows: pd.DataFrame, max_profiles: int = MAX_PROFILES) -> list[list]:
    """The most common (metro, mall, landmark, parking, freehold) combinations in a cell
    with their shares. The explorer averages the model over them because a visitor
    knows the community but not which corner of it; within a community the nearest
    metro behaves like a sub-location and can move the estimate by a quarter."""
    key = rows[PROFILE_COLUMNS].astype(object)
    key["is_freehold"] = rows["is_freehold"].astype("Float64").fillna(1).astype(int).to_numpy()
    counts = key.value_counts(dropna=False).head(max_profiles)
    total = float(counts.sum())
    out = []
    for idx, n in counts.items():
        values = [None if (v is None or (isinstance(v, float) and np.isnan(v))) else (int(v) if isinstance(v, (int, np.integer)) else str(v)) for v in idx]
        out.append([*values, round(n / total, 3)])
    return out


def build_profiles(df: pd.DataFrame) -> dict:
    """profiles[subtype]['_all'] for the community x type, and profiles[subtype][rooms]
    for cells with at least MIN_PROFILE_ROWS sales."""
    out: dict = {}
    for subtype, s in df.groupby("property_subtype", observed=True):
        out[str(subtype)] = {"_all": cell_profiles(s)}
        for rooms, r in s.groupby(s["rooms"].fillna("NA"), observed=True):
            if len(r) >= MIN_PROFILE_ROWS:
                out[str(subtype)][str(rooms)] = cell_profiles(r)
    return out


def _rooms_sorted(labels) -> list[str]:
    return sorted(labels, key=lambda r: (ROOMS_ORDER.index(r) if r in ROOMS_ORDER else len(ROOMS_ORDER), str(r)))


def build_stats(df: pd.DataFrame) -> dict:
    areas: dict = {}
    for area, a in df.groupby("area", observed=True):
        entry = {**_range(a["price_per_sqm"]), "subtypes": {}}
        for subtype, s in a.groupby("property_subtype", observed=True):
            sub = {**_range(s["price_per_sqm"]), "median_size": round(float(s["actual_area_sqm"].median()), 1),
                   "defaults": _defaults(s), "rooms": {}}
            for rooms, r in s.groupby(s["rooms"].fillna("NA"), observed=True):
                sub["rooms"][str(rooms)] = {**_range(r["price_per_sqm"]), "median_size": round(float(r["actual_area_sqm"].median()), 1),
                                            "thin": bool(len(r) < THIN_CELL)}
            sub["rooms"] = {k: sub["rooms"][k] for k in _rooms_sorted(sub["rooms"])}
            entry["subtypes"][str(subtype)] = sub
        areas[str(area)] = entry
    return {"n_areas": len(areas), "rooms_order": ROOMS_ORDER, "thin_cell": THIN_CELL, "areas": dict(sorted(areas.items()))}


def _monthly(df: pd.DataFrame) -> dict:
    g = df.groupby("ym")["price_per_sqm"].agg(["median", "size"])
    return {ym: {"median": round(float(row["median"])), "n": int(row["size"])} for ym, row in g.sort_index().iterrows()}


def build_monthly(df: pd.DataFrame) -> dict:
    out = {"all": _monthly(df), "areas": {}}
    for area, a in df.groupby("area", observed=True):
        if len(a) >= MIN_MONTHLY_ROWS:
            out["areas"][str(area)] = _monthly(a)
    return out


def _comps(df: pd.DataFrame, max_comps: int) -> dict:
    out: dict = {}
    for subtype, s in df.groupby("property_subtype", observed=True):
        out[str(subtype)] = {}
        for rooms, r in s.groupby(s["rooms"].fillna("NA"), observed=True):
            latest = r.sort_values("date", ascending=False).head(max_comps)
            out[str(subtype)][str(rooms)] = [
                [row.date.strftime("%Y-%m-%d"), None if pd.isna(row.project) else str(row.project),
                 round(float(row.actual_area_sqm), 1), round(float(row.value_aed)), round(float(row.price_per_sqm)),
                 int(bool(row.is_offplan)) if not pd.isna(row.is_offplan) else None]
                for row in latest.itertuples()
            ]
    return out


def export_web(df: pd.DataFrame, metrics: dict, clean_report: dict, out_dir: Path, max_comps: int = 12) -> dict:
    out_dir = Path(out_dir)
    (out_dir / "areas").mkdir(parents=True, exist_ok=True)
    stats = build_stats(df)
    _write(out_dir / "stats.json", stats)
    _write(out_dir / "monthly.json", build_monthly(df))
    for area, a in df.groupby("area", observed=True):
        _write(out_dir / "areas" / f"{slug(area)}.json",
               {"area": str(area), "monthly": _monthly(a), "comps": _comps(a, max_comps), "profiles": build_profiles(a)})
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_as_of": str(clean_report["date_max"])[:10],
        "date_min": str(clean_report["date_min"])[:10],
        "rows": int(clean_report["clean_rows"]),
        "raw_rows": int(clean_report["raw_rows"]),
        "metrics": {k: metrics[k] for k in ("model", "baseline", "train", "test", "explorer") if k in metrics},
        "clean_steps": clean_report.get("steps", []),
    }
    _write(out_dir / "meta.json", meta)
    return {"areas": stats["n_areas"], "rows": int(len(df))}


def explorer_evaluation(df: pd.DataFrame, models_dir: Path, train_end=None) -> dict:
    """Score the estimate the explorer shows: the model averaged over the sub-location
    profiles of the cell (built from training months only), applied to the test months.
    This is the honest number for a visitor who knows community, type, rooms, size and
    status but not the building."""
    import lightgbm as lgb

    from .features import make_features
    from .model import coverage, metrics, temporal_split

    models_dir = Path(models_dir)
    train, test = temporal_split(df) if train_end is None else temporal_split(df, train_end)
    categories = json.loads((models_dir / "categories.json").read_text(encoding="utf-8"))
    adjust = json.loads((models_dir / "metrics.json").read_text(encoding="utf-8"))["model"]["interval_adjust_log"]
    boosters = {n: lgb.Booster(model_file=str(models_dir / f"{n}.txt")) for n in ("point", "q10", "q90")}

    profiles: dict = {}
    for (area, subtype), s in train.groupby(["area", "property_subtype"], observed=True):
        profiles[(area, subtype, None)] = cell_profiles(s)
        for rooms, r in s.groupby(s["rooms"].fillna("NA"), observed=True):
            if len(r) >= MIN_PROFILE_ROWS:
                profiles[(area, subtype, rooms)] = cell_profiles(r)

    pieces, weights, owners = [], [], []
    for i, row in enumerate(test.itertuples(index=False)):
        rooms = "NA" if pd.isna(row.rooms) else row.rooms
        profs = profiles.get((row.area, row.property_subtype, rooms)) or profiles.get((row.area, row.property_subtype, None))
        if not profs:  # community unseen in training: the model's unknown-category path
            profs = [[None, None, None, 0, 1, 1.0]]
        for metro, mall, landmark, parking, freehold, share in profs:
            pieces.append({"area": row.area, "property_subtype": row.property_subtype, "rooms": row.rooms, "rooms_n": row.rooms_n,
                           "nearest_metro": metro, "nearest_mall": mall, "nearest_landmark": landmark,
                           "actual_area_sqm": row.actual_area_sqm, "is_offplan": row.is_offplan, "is_freehold": bool(freehold),
                           "parking_n": parking, "month_n": row.month_n})
            weights.append(share)
            owners.append(i)
    expanded = pd.DataFrame(pieces)
    expanded["is_offplan"] = expanded["is_offplan"].astype("boolean")
    expanded["is_freehold"] = expanded["is_freehold"].astype("boolean")
    X, _ = make_features(expanded, categories)
    w = np.asarray(weights)
    owners = np.asarray(owners)
    agg = {}
    for name, b in boosters.items():
        log_pred = b.predict(X)
        sums = np.bincount(owners, weights=w * log_pred, minlength=len(test))
        norm = np.bincount(owners, weights=w, minlength=len(test))
        agg[name] = sums / norm
    point = np.exp(agg["point"])
    lo = np.exp(np.minimum(agg["q10"], agg["q90"]) - adjust)
    hi = np.exp(np.maximum(agg["q10"], agg["q90"]) + adjust)
    lo, hi = np.minimum(lo, point), np.maximum(hi, point)
    y = test["price_per_sqm"].to_numpy(dtype=float)
    result = metrics(y, point, test["actual_area_sqm"].to_numpy(dtype=float))
    result["coverage_80"] = coverage(y, lo, hi)
    result["median_interval_width_pct"] = float(np.median((hi - lo) / point * 100))
    result["profiles_per_row"] = float(len(pieces) / len(test))
    (models_dir / "explorer_metrics.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False), encoding="utf-8")


def export_all(processed_dir: Path, models_dir: Path, web_dir: Path, charts_dir: Path) -> None:
    from .charts import make_charts

    df = pd.read_parquet(Path(processed_dir) / "sales_clean.parquet")
    metrics = json.loads((Path(models_dir) / "metrics.json").read_text(encoding="utf-8"))
    metrics["explorer"] = explorer_evaluation(df, models_dir)
    report = json.loads((Path(processed_dir) / "clean_report.json").read_text(encoding="utf-8"))
    summary = export_web(df, metrics, report, Path(web_dir) / "data")
    model_info = export_model(models_dir, Path(web_dir) / "data" / "model.json")
    charts = make_charts(df, models_dir, charts_dir)
    print(f"web data: {summary['areas']} communities, {summary['rows']:,} sales; model.json {model_info['bytes'] / 1e6:.2f} MB "
          f"({model_info['trees']}); charts: {', '.join(p.name for p in charts)}")
