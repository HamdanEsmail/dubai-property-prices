"""Parse the raw cache (or the committed snapshot) and clean it into data/processed."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .clean import CleanStep, clean_sales
from .fetch import page_paths
from .parse import frame_from_pages, frame_from_pulse_csv

log = logging.getLogger(__name__)

SALES_SNAPSHOT = "dld_sales_2026.parquet"
MORTGAGE_SNAPSHOT = "dld_mortgages_2026.parquet"


@dataclass
class BuildReport:
    source: str
    raw_rows: int
    clean_rows: int
    steps: list[CleanStep]
    date_min: str
    date_max: str

    def summary(self) -> str:
        lines = [f"source: {self.source}; {self.raw_rows} raw rows -> {self.clean_rows} rows after cleaning "
                 f"({self.date_min[:10]} to {self.date_max[:10]})"]
        for s in self.steps:
            lines.append(f"  {s.name:<24} dropped {s.dropped:>7,}  remaining {s.remaining:>8,}")
        return "\n".join(lines)


def profile_raw(raw: pd.DataFrame) -> dict:
    """Facts about the raw rows that the documentation quotes (docs/data.md)."""
    res = raw[(raw["usage"] == "Residential") & raw["property_subtype"].isin(["Flat", "Villa"])
              & (raw["value_aed"].fillna(0) > 0) & (raw["actual_area_sqm"].fillna(0) > 0)]
    ppsqm = res["value_aed"] / res["actual_area_sqm"]
    procedures = {}
    for name, grp in ppsqm.groupby(res["procedure"], observed=True):
        procedures[str(name)] = {"rows": int(len(grp)), "median_ppsqm": round(float(grp.median()))}
    procedures = dict(sorted(procedures.items(), key=lambda kv: -kv[1]["rows"]))
    types = raw.groupby(["property_type", "property_subtype"], observed=True, dropna=False).size()
    return {
        "rows": int(len(raw)),
        "procedures_residential_flat_villa": procedures,
        "property_types": {f"{t} / {s}": int(n) for (t, s), n in types.sort_values(ascending=False).items()},
        "missing_share": {c: round(float(raw[c].isna().mean()), 3) for c in
                          ["rooms", "parking", "project", "master_project", "nearest_metro", "nearest_mall", "nearest_landmark", "is_freehold"]},
        "building_age_zero_share": round(float((raw["building_age"].fillna(0) == 0).mean()), 4),
        "offplan_share": round(float(raw["is_offplan"].astype("Float64").mean()), 3),
        "distinct": {c: int(raw[c].nunique()) for c in ["area", "project", "nearest_metro", "nearest_mall", "nearest_landmark"]},
        "area_case_variants": int((raw.groupby(raw["area"].str.upper())["area"].nunique() > 1).sum()),
        "procedure_area_equals_actual_share": round(float((raw["procedure_area_sqm"].round(2) == raw["actual_area_sqm"].round(2)).mean()), 3),
    }


def _snapshot_group(raw_dir: Path, snapshot_dir: Path, group: str, filename: str) -> tuple[pd.DataFrame | None, str]:
    """Parse raw pages into the snapshot when they exist, else read the snapshot."""
    paths = page_paths(raw_dir, group) if (raw_dir / group).exists() else []
    snapshot = snapshot_dir / filename
    if paths:
        log.info("parsing %d raw %s pages", len(paths), group)
        df = frame_from_pages(paths)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(snapshot, index=False, compression="zstd")
        log.info("wrote %s (%d rows, %.1f MB)", snapshot, len(df), snapshot.stat().st_size / 1e6)
        return df, "raw"
    if snapshot.exists():
        return pd.read_parquet(snapshot), "snapshot"
    return None, "missing"


def build_dataset(raw_dir: Path, snapshot_dir: Path, processed_dir: Path) -> BuildReport:
    sales, source = _snapshot_group(raw_dir, snapshot_dir, "sales", SALES_SNAPSHOT)
    if sales is None:
        raise FileNotFoundError(f"no raw pages under {raw_dir / 'sales'} and no snapshot at {snapshot_dir / SALES_SNAPSHOT}")
    _snapshot_group(raw_dir, snapshot_dir, "mortgages", MORTGAGE_SNAPSHOT)

    frames = [sales]
    pulse_dir = raw_dir / "pulse"
    for csv in sorted(pulse_dir.glob("*.csv")) if pulse_dir.exists() else []:
        extra = frame_from_pulse_csv(csv)
        log.info("merged %d rows from bulk file %s", len(extra), csv.name)
        frames.append(extra)
    raw = pd.concat(frames, ignore_index=True) if len(frames) > 1 else sales

    clean, steps = clean_sales(raw)
    profile = profile_raw(raw)
    processed_dir.mkdir(parents=True, exist_ok=True)
    clean.to_parquet(processed_dir / "sales_clean.parquet", index=False, compression="zstd")
    report = BuildReport(
        source=source,
        raw_rows=len(raw),
        clean_rows=len(clean),
        steps=steps,
        date_min=str(clean["date"].min()),
        date_max=str(clean["date"].max()),
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "raw_rows": report.raw_rows,
        "clean_rows": report.clean_rows,
        "date_min": report.date_min,
        "date_max": report.date_max,
        "steps": [s.__dict__ for s in steps],
        "profile": profile,
    }
    (processed_dir / "clean_report.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return report
