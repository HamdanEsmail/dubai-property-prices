"""Matplotlib charts for the README (docs/charts/*.png).

One hue per entity, fixed everywhere: flats and the model are blue, villas and the
baseline are orange. Thin marks, recessive grid, direct labels where they help.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
CHART_NAMES = [
    "monthly_price_per_sqm.png",
    "communities_top.png",
    "price_per_sqm_distribution.png",
    "actual_vs_predicted.png",
    "feature_importance.png",
    "interval_coverage.png",
    "error_by_segment.png",
]

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.titleweight": "bold", "axes.titlesize": 12, "axes.titlelocation": "left",
    "font.size": 10, "legend.frameon": False,
})


def _fig(w=8.0, h=4.5):
    fig, ax = plt.subplots(figsize=(w, h), dpi=150)
    ax.tick_params(length=0)
    return fig, ax


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _thousands(ax, axis="y"):
    fmt = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def _month_labels(yms) -> list[str]:
    return [pd.Timestamp(ym + "-01").strftime("%b") for ym in yms]


def chart_monthly(df: pd.DataFrame, path: Path) -> Path:
    fig, ax = _fig()
    series = {st: df[df["property_subtype"] == st].groupby("ym")["price_per_sqm"].median() for st in ("Flat", "Villa")}
    months = sorted(set().union(*(s.index for s in series.values())))
    ends = {st: float(s.values[-1]) for st, s in series.items()}
    span = max(max(s.max() for s in series.values()) - min(s.min() for s in series.values()), 1)
    # keep the two end labels apart when the lines finish close together
    offsets = {st: 0.0 for st in series}
    if abs(ends["Flat"] - ends["Villa"]) < span * 0.12:
        hi_st = max(ends, key=ends.get)
        offsets[hi_st], offsets[min(ends, key=ends.get)] = 7, -7
    for subtype, color in [("Flat", BLUE), ("Villa", ORANGE)]:
        s = series[subtype]
        xs = [months.index(m) for m in s.index]
        ax.plot(xs, s.values, color=color, linewidth=2, marker="o", markersize=4, label=f"{subtype}s")
        ax.annotate(f"{subtype}s  {ends[subtype]:,.0f}", (xs[-1], ends[subtype]), xytext=(7, offsets[subtype]),
                    textcoords="offset points", va="center", color=INK, fontsize=9)
    ax.set_xticks(range(len(months)), _month_labels(months))
    ax.set_title("Median price per square metre by month, 2026 (AED)")
    lo = min(s.min() for s in series.values())
    hi = max(s.max() for s in series.values())
    ax.set_ylim(lo - span * 0.6, hi + span * 0.5)
    _thousands(ax)
    ax.legend(loc="lower left")
    ax.margins(x=0.14)
    return _save(fig, path)


def chart_communities(df: pd.DataFrame, path: Path, top: int = 15) -> Path:
    g = df.groupby("area")["price_per_sqm"].agg(["median", "size"])
    g = g.sort_values("size", ascending=False).head(top).sort_values("median")
    fig, ax = _fig(8.0, 5.5)
    ax.set_axisbelow(True)
    bars = ax.barh(g.index, g["median"], color=BLUE, height=0.62)
    for bar, (median, n) in zip(bars, g[["median", "size"]].itertuples(index=False)):
        ax.text(bar.get_width() + 150, bar.get_y() + bar.get_height() / 2, f"{median:,.0f}   (n={n:,})",
                va="center", fontsize=8.5, color=INK2)
    ax.set_title(f"Median AED per sqm, {top} communities with the most sales")
    ax.set_xlim(0, g["median"].max() * 1.3)
    _thousands(ax, "x")
    ax.grid(axis="y", visible=False)
    return _save(fig, path)


def chart_distribution(df: pd.DataFrame, path: Path) -> Path:
    fig, ax = _fig()
    bins = np.logspace(np.log10(3000), np.log10(120000), 60)
    for subtype, color in [("Flat", BLUE), ("Villa", ORANGE)]:
        s = df.loc[df["property_subtype"] == subtype, "price_per_sqm"]
        ax.hist(s, bins=bins, color=color, alpha=0.6, label=f"{subtype}s (median {s.median():,.0f})", edgecolor=SURFACE, linewidth=0.5)
    ax.set_xscale("log")
    ax.set_title("Distribution of price per square metre after cleaning (AED, log scale)")
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_xticks([3000, 5000, 10000, 20000, 50000, 100000])
    ax.set_ylabel("sales")
    ax.legend()
    return _save(fig, path)


def chart_actual_vs_predicted(preds: pd.DataFrame, metrics: dict, path: Path) -> Path:
    sample = preds.sample(min(4000, len(preds)), random_state=0)
    fig, ax = _fig(6.0, 6.0)
    ax.scatter(sample["pred_ppsqm"], sample["price_per_sqm"], s=6, color=BLUE, alpha=0.35, linewidths=0)
    lo, hi = 3000, 120000
    ax.plot([lo, hi], [lo, hi], color=INK2, linewidth=1, linestyle="--")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_xlabel("predicted AED per sqm")
    ax.set_ylabel("actual AED per sqm")
    m = metrics["model"]
    ax.set_title("Test months: actual vs predicted price per sqm")
    label = f"median abs. error {m['mdape_ppsqm']:.1f}%" + "\n" + f"MAE {m['mae_ppsqm']:,.0f} AED per sqm"
    ax.text(0.03, 0.95, label, transform=ax.transAxes, va="top", fontsize=9.5, color=INK2)
    return _save(fig, path)


def chart_importance(metrics: dict, path: Path) -> Path:
    imp = pd.DataFrame(metrics["importance"]).sort_values("gain_share")
    fig, ax = _fig(8.0, 4.5)
    ax.set_axisbelow(True)
    bars = ax.barh(imp["feature"], imp["gain_share"] * 100, color=BLUE, height=0.62)
    for bar, v in zip(bars, imp["gain_share"]):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2, f"{v * 100:.1f}%", va="center", fontsize=8.5, color=INK2)
    ax.set_title("Share of the model's split gain by feature")
    ax.set_xlim(0, imp["gain_share"].max() * 115)
    ax.grid(axis="y", visible=False)
    return _save(fig, path)


def chart_coverage(preds: pd.DataFrame, metrics: dict, path: Path) -> Path:
    deciles = pd.qcut(preds["pred_ppsqm"], 10, labels=False, duplicates="drop")
    covered = (preds["price_per_sqm"] >= preds["lo_ppsqm"]) & (preds["price_per_sqm"] <= preds["hi_ppsqm"])
    g = covered.groupby(deciles).mean() * 100
    edges = preds.groupby(deciles)["pred_ppsqm"].min()
    labels = [f"{e / 1000:.0f}k" for e in edges]
    fig, ax = _fig()
    ax.set_axisbelow(True)
    ax.bar(range(len(g)), g.values, color=BLUE, width=0.7)
    ax.axhline(80, color=ORANGE, linewidth=1.5, linestyle="--")
    ax.text(len(g) - 0.5, 81.5, "80% target", color=ORANGE, ha="right", fontsize=9)
    ax.set_xticks(range(len(g)), labels)
    ax.set_xlabel("predicted AED per sqm (decile lower edge)")
    ax.set_ylabel("share of sales inside the interval, %")
    ax.set_ylim(0, 100)
    ax.set_title(f"Share of test sales inside the 80% interval, by predicted price ({metrics['model']['coverage_80'] * 100:.0f}% overall)")
    ax.grid(axis="x", visible=False)
    return _save(fig, path)


def chart_segments(metrics: dict, path: Path) -> Path:
    segs = list(metrics["by_segment"].keys())
    base = [metrics["by_segment"][s]["baseline"]["mdape_ppsqm"] for s in segs]
    mod = [metrics["by_segment"][s]["model"]["mdape_ppsqm"] for s in segs]
    x = np.arange(len(segs))
    fig, ax = _fig()
    ax.set_axisbelow(True)
    ax.bar(x - 0.18, base, width=0.34, color=ORANGE, label="baseline (median by community, type, rooms)")
    ax.bar(x + 0.18, mod, width=0.34, color=BLUE, label="gradient boosting model")
    for xi, b, m in zip(x, base, mod):
        ax.text(xi - 0.18, b + 0.3, f"{b:.1f}", ha="center", fontsize=8.5, color=INK2)
        ax.text(xi + 0.18, m + 0.3, f"{m:.1f}", ha="center", fontsize=8.5, color=INK2)
    ax.set_xticks(x, segs)
    ax.set_ylabel("median absolute % error")
    ax.set_title("Error on the test months by segment")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left")
    ax.set_ylim(0, max(base + mod) * 1.25)
    return _save(fig, path)


def make_charts(df: pd.DataFrame, models_dir: Path, out_dir: Path) -> list[Path]:
    models_dir, out_dir = Path(models_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = json.loads((models_dir / "metrics.json").read_text(encoding="utf-8"))
    preds = pd.read_parquet(models_dir / "test_predictions.parquet")
    return [
        chart_monthly(df, out_dir / CHART_NAMES[0]),
        chart_communities(df, out_dir / CHART_NAMES[1]),
        chart_distribution(df, out_dir / CHART_NAMES[2]),
        chart_actual_vs_predicted(preds, metrics, out_dir / CHART_NAMES[3]),
        chart_importance(metrics, out_dir / CHART_NAMES[4]),
        chart_coverage(preds, metrics, out_dir / CHART_NAMES[5]),
        chart_segments(metrics, out_dir / CHART_NAMES[6]),
    ]
