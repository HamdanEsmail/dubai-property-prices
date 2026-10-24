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
        _write(out_dir / "areas" / f"{slug(area)}.json", {"area": str(area), "monthly": _monthly(a), "comps": _comps(a, max_comps)})
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_as_of": str(clean_report["date_max"])[:10],
        "date_min": str(clean_report["date_min"])[:10],
        "rows": int(clean_report["clean_rows"]),
        "raw_rows": int(clean_report["raw_rows"]),
        "metrics": {k: metrics[k] for k in ("model", "baseline", "train", "test") if k in metrics},
        "clean_steps": clean_report.get("steps", []),
    }
    _write(out_dir / "meta.json", meta)
    return {"areas": stats["n_areas"], "rows": int(len(df))}


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False), encoding="utf-8")


def export_all(processed_dir: Path, models_dir: Path, web_dir: Path, charts_dir: Path) -> None:
    from .charts import make_charts

    df = pd.read_parquet(Path(processed_dir) / "sales_clean.parquet")
    metrics = json.loads((Path(models_dir) / "metrics.json").read_text(encoding="utf-8"))
    report = json.loads((Path(processed_dir) / "clean_report.json").read_text(encoding="utf-8"))
    summary = export_web(df, metrics, report, Path(web_dir) / "data")
    model_info = export_model(models_dir, Path(web_dir) / "data" / "model.json")
    charts = make_charts(df, models_dir, charts_dir)
    print(f"web data: {summary['areas']} communities, {summary['rows']:,} sales; model.json {model_info['bytes'] / 1e6:.2f} MB "
          f"({model_info['trees']}); charts: {', '.join(p.name for p in charts)}")
