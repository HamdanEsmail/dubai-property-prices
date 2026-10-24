"""Rewrite README.md and docs/data.md from the pipeline's artefacts.

Every number in those two documents is a {{token}} in docs/templates/*.md that this
script replaces from models/metrics.json, data/processed/clean_report.json and the
exported web data, so the text can never drift from what the code produced.

usage (from the repository root, after `uv run dubai-prices all`):
    uv run python scripts/fill_docs.py

Tokens: {{metrics.model.mdape_ppsqm|.1f}}  {{clean.steps.bulk_deals.dropped|,}}  {{table:steps}}
Formats: a Python format spec, or pct / pct1 for a share as a percentage.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dubai_prices.export_model import export_model, predict_exported  # noqa: E402
from dubai_prices.features import make_features  # noqa: E402
from dubai_prices.model import temporal_split  # noqa: E402

KEPT_PROCEDURES = {"Sale", "Sell", "Sell - Pre registration", "Delayed Sell", "Sale On Payment Plan"}
TEMPLATES = {"README.md": ROOT / "README.md", "data.md": ROOT / "docs" / "data.md"}


def load_sources() -> dict:
    metrics = json.loads((ROOT / "models/metrics.json").read_text(encoding="utf-8"))
    clean = json.loads((ROOT / "data/processed/clean_report.json").read_text(encoding="utf-8"))
    stats = json.loads((ROOT / "web/data/stats.json").read_text(encoding="utf-8"))
    explorer_path = ROOT / "models/explorer_metrics.json"
    metrics["explorer"] = json.loads(explorer_path.read_text(encoding="utf-8")) if explorer_path.exists() else {}

    steps = {s["name"]: dict(s) for s in clean["steps"]}
    residential = steps["residential_flat_villa"]["remaining"]
    for s in steps.values():
        s["dropped_share"] = s["dropped"] / residential
    clean["steps"] = steps

    seg = metrics["by_segment"]
    metrics["seg"] = {k: {"model_mdape": v["model"]["mdape_ppsqm"], "baseline_mdape": v["baseline"]["mdape_ppsqm"],
                          "model_mae": v["model"]["mae_ppsqm"], "coverage": v["coverage_80"], "n": v["model"]["n"]}
                      for k, v in seg.items()}
    metrics["calibration"]["adjust_pct"] = math.exp(metrics["model"]["interval_adjust_log"]) - 1

    preds = pd.read_parquet(ROOT / "models/test_predictions.parquet")
    big = preds[preds.abs_pct_error > 50]
    metrics["big_errors_n"] = int(len(big))
    metrics["big_errors_above_share"] = float((big.pred_ppsqm > big.price_per_sqm).mean())
    metrics["above_share"] = float((preds.pred_ppsqm > preds.price_per_sqm).mean())
    metrics["point_outside_share"] = point_outside_raw_band()
    metrics["marina_spread"] = sublocation_spread("Dubai Marina", "Flat", "2 B/R", 120)

    large_path = ROOT / "models/large/metrics.json"
    export = {
        "model_json_mb": (ROOT / "web/data/model.json").stat().st_size / 1e6,
        "snapshot_mb": (ROOT / "data/snapshot/dld_sales_2026.parquet").stat().st_size / 1e6,
        "mortgage_rows": pq.read_metadata(ROOT / "data/snapshot/dld_mortgages_2026.parquet").num_rows,
        "n_areas": stats["n_areas"],
        "trees_point": metrics["rounds"]["point"],
    }
    if large_path.exists():
        large = json.loads(large_path.read_text(encoding="utf-8"))
        metrics["large_mdape"] = large["model"]["mdape_ppsqm"]
        metrics["large_gain"] = metrics["model"]["mdape_ppsqm"] - large["model"]["mdape_ppsqm"]
        metrics["large_trees"] = large["rounds"]["point"]
        if (ROOT / "models/large/point.txt").exists():
            scratch = ROOT / "models/large/model.json"
            export["large_model_mb"] = export_model(ROOT / "models/large", scratch)["bytes"] / 1e6
            scratch.unlink()
    return {"metrics": metrics, "clean": clean, "export": export}


def point_outside_raw_band() -> float:
    """Share of test rows where the point estimate falls outside the calibrated band
    before the rule that widens the band to contain it."""
    df = pd.read_parquet(ROOT / "data/processed/sales_clean.parquet")
    _, test = temporal_split(df)
    categories = json.loads((ROOT / "models/categories.json").read_text(encoding="utf-8"))
    adjust = json.loads((ROOT / "models/metrics.json").read_text(encoding="utf-8"))["model"]["interval_adjust_log"]
    X, _ = make_features(test, categories)
    pred = {n: lgb.Booster(model_file=str(ROOT / f"models/{n}.txt")).predict(X) for n in ("point", "q10", "q90")}
    lo = np.minimum(pred["q10"], pred["q90"]) - adjust
    hi = np.maximum(pred["q10"], pred["q90"]) + adjust
    return float(np.mean((pred["point"] < lo) | (pred["point"] > hi)))


def sublocation_spread(area: str, subtype: str, rooms: str, size: float) -> float:
    """How far apart the point estimates are across a cell's sub-location profiles
    (max over min, minus one) for a ready unit, using the exported browser model."""
    model = json.loads((ROOT / "web/data/model.json").read_text(encoding="utf-8"))
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", area.lower())).strip("-")
    area_file = json.loads((ROOT / "web/data/areas" / f"{slug}.json").read_text(encoding="utf-8"))
    profiles = area_file["profiles"][subtype].get(rooms) or area_file["profiles"][subtype]["_all"]
    rows = [{"area": area, "property_subtype": subtype, "rooms": rooms, "rooms_n": float(rooms.split()[0]) if rooms[0].isdigit() else 0.0,
             "nearest_metro": metro, "nearest_mall": mall, "nearest_landmark": landmark, "actual_area_sqm": size,
             "is_offplan": 0, "is_freehold": freehold, "parking_n": parking, "month_n": model["month_n"]}
            for metro, mall, landmark, parking, freehold, _share in profiles]
    preds = np.exp(predict_exported(model, "point", rows))
    return float(preds.max() / preds.min() - 1)


def fmt(value, spec: str | None) -> str:
    if spec is None:
        return str(value)
    if spec == "pct":
        return f"{value * 100:.0f}%"
    if spec == "pct1":
        return f"{value * 100:.1f}%"
    return format(value, spec)


def table(name: str, src: dict) -> str:
    metrics, clean = src["metrics"], src["clean"]
    if name == "steps":
        rows = ["| # | Rule | Dropped | Remaining |", "|---|---|---:|---:|"]
        for i, s in enumerate(clean["steps"].values(), 1):
            rows.append(f"| {i} | `{s['name']}`: {s['rule']} | {s['dropped']:,} | {s['remaining']:,} |")
    elif name == "procedures":
        rows = ["| Procedure | Rows | Median AED/sqm | Treatment |", "|---|---:|---:|---|"]
        for proc, v in clean["profile"]["procedures_residential_flat_villa"].items():
            rows.append(f"| {proc} | {v['rows']:,} | {v['median_ppsqm']:,} | {'kept' if proc in KEPT_PROCEDURES else 'dropped'} |")
    elif name == "property_types":
        rows = ["| Type / sub-type | Rows |", "|---|---:|"]
        items = clean["profile"]["property_types"]
        shown = {k: v for k, v in items.items() if v >= 100}
        rows += [f"| {k} | {v:,} |" for k, v in shown.items()]
        rows.append(f"| {len(items) - len(shown)} other sub-types | {sum(v for k, v in items.items() if k not in shown):,} |")
    elif name == "segments":
        rows = ["| Segment | Test sales | Baseline MdAPE | Model MdAPE | Model MAE (AED/sqm) | 80% interval coverage |",
                "|---|---:|---:|---:|---:|---:|"]
        for k, v in metrics["seg"].items():
            rows.append(f"| {k} | {v['n']:,} | {v['baseline_mdape']:.1f}% | {v['model_mdape']:.1f}% | {v['model_mae']:,.0f} | {v['coverage'] * 100:.0f}% |")
    elif name == "importance":
        rows = ["| Feature | Share of split gain |", "|---|---:|"]
        rows += [f"| `{i['feature']}` | {i['gain_share'] * 100:.1f}% |" for i in metrics["importance"]]
    elif name == "failures":
        f = pd.read_csv(ROOT / "models/failures.csv")
        rows = ["| Registered | Community | Unit | Registered price | Registered AED/sqm | Model AED/sqm (80% band) | Error |",
                "|---|---|---|---:|---:|---:|---:|"]
        for r in f.itertuples():
            unit = f"{r.rooms} {r.property_subtype.lower()}, {r.actual_area_sqm:,.0f} sqm, {'off-plan' if r.is_offplan else 'ready'}"
            rows.append(f"| {str(r.date)[:10]} | {r.area} | {unit} | {r.value_aed:,.0f} | {r.price_per_sqm:,.0f} | "
                        f"{r.pred_ppsqm:,.0f} ({r.lo_ppsqm:,.0f} to {r.hi_ppsqm:,.0f}) | {r.abs_pct_error:,.0f}% |")
    else:
        raise KeyError(name)
    return "\n".join(rows)


def render(text: str, src: dict) -> str:
    def lookup(path: str):
        node = src
        for part in path.split("."):
            node = node[part]
        return node

    def repl(m):
        body = m.group(1)
        if body.startswith("table:"):
            return table(body[6:], src)
        path, _, spec = body.partition("|")
        return fmt(lookup(path.strip()), spec.strip() or None)

    out = re.sub(r"\{\{(.+?)\}\}", repl, text)
    leftover = re.findall(r"\{\{.+?\}\}", out)
    if leftover:
        raise SystemExit(f"unfilled tokens: {leftover}")
    return out


def main() -> None:
    src = load_sources()
    for name, target in TEMPLATES.items():
        template = ROOT / "docs" / "templates" / name
        target.write_text(render(template.read_text(encoding="utf-8"), src), encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
