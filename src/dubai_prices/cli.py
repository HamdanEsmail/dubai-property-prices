"""Command line entry point: uv run dubai-prices <command>."""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
SNAPSHOT_DIR = ROOT / "data" / "snapshot"
PROCESSED_DIR = ROOT / "data" / "processed"
MODELS_DIR = ROOT / "models"
WEB_DIR = ROOT / "web"
CHARTS_DIR = ROOT / "docs" / "charts"


def _parse_date(text: str) -> date:
    return date.fromisoformat(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dubai-prices", description="Dubai property prices pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="download transaction pages from the DLD open-data API into data/raw")
    p_fetch.add_argument("--start", type=_parse_date, default=date(date.today().year, 1, 1))
    p_fetch.add_argument("--end", type=_parse_date, default=date.today())
    p_fetch.add_argument("--groups", default="sales,mortgages", help="comma list of sales,mortgages,gifts")
    p_fetch.add_argument("--take", type=int, default=2000)
    p_fetch.add_argument("--pause", type=float, default=1.0)

    sub.add_parser("build", help="parse raw pages (or the committed snapshot) and clean into data/processed")
    p_train = sub.add_parser("train", help="fit baseline and LightGBM models with temporal validation into models/")
    p_train.add_argument("--params", choices=["default", "large"], default="default",
                         help="'large' is the 63-leaf comparison model quoted in the README (not shipped)")
    p_train.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    sub.add_parser("export", help="write web/data JSON, the browser model and docs/charts")
    sub.add_parser("all", help="build, train and export in one go")
    return parser


def cmd_fetch(args) -> int:
    from . import api, fetch

    client = api.DLDClient(pause_seconds=args.pause)
    groups = {"sales": api.GROUP_SALES, "mortgages": api.GROUP_MORTGAGE, "gifts": api.GROUP_GIFTS}
    for name in args.groups.split(","):
        name = name.strip()
        result = fetch.fetch_window(client, groups[name], args.start, args.end, RAW_DIR, take=args.take)
        print(f"{name}: {result.rows} rows in {result.pages} pages (API total {result.total})")
    return 0


def cmd_build(args) -> int:
    from .build import build_dataset

    report = build_dataset(RAW_DIR, SNAPSHOT_DIR, PROCESSED_DIR)
    print(report.summary())
    return 0


def cmd_train(args) -> int:
    from .model import DEFAULT_PARAMS, LARGE_PARAMS, train_and_evaluate

    params = LARGE_PARAMS if getattr(args, "params", "default") == "large" else DEFAULT_PARAMS
    report = train_and_evaluate(PROCESSED_DIR / "sales_clean.parquet", getattr(args, "models_dir", MODELS_DIR), params=params)
    print(report["summary"])
    return 0


def cmd_export(args) -> int:
    from .export_web import export_all

    export_all(PROCESSED_DIR, MODELS_DIR, WEB_DIR, CHARTS_DIR)
    return 0


def cmd_all(args) -> int:
    return cmd_build(args) or cmd_train(args) or cmd_export(args)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    handler = {"fetch": cmd_fetch, "build": cmd_build, "train": cmd_train, "export": cmd_export, "all": cmd_all}
    return handler[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
