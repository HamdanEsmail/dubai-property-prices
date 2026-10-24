# Dubai Property Prices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A public, reproducible repo that pulls 2026 Dubai Land Department (DLD) sales, cleans them into one tidy table, fits a price-per-square-metre model with honest temporal validation, and ships a static explorer on GitHub Pages.

**Architecture:** A small Python package (`src/dubai_prices/`) with one module per stage: API client -> raw page cache -> parser -> cleaner -> features -> model -> charts -> web export. Every stage is a pure function over DataFrames so it can be tested on fixture pages without the network. The explorer is vanilla HTML/JS reading precomputed JSON plus a compact tree dump of the LightGBM models evaluated in the browser.

**Tech Stack:** Python 3.12, uv, pandas 3, pyarrow, LightGBM 4, scikit-learn (metrics only), matplotlib, pytest; vanilla JS for `web/`; GitHub Actions for Pages.

**Spec:** The task brief from the parent session (brainstorming already done there). Key facts verified live on 2026-10-24 are recorded in `docs/data.md` once written.

## Global Constraints

- Python `>=3.12`, dependencies pinned by `uv.lock`; `uv run` is the only entry point.
- Polite client: one request at a time, 1.0 s pause between pages, 4 retries with exponential backoff, raw pages cached on disk and never re-fetched.
- Committed data snapshot must stay well under 50 MB (target < 15 MB).
- Temporal validation only: train = January to August 2026, test = 1 September to 24 October 2026.
- README: plain English, no hype, no AI-attribution notes, no LinkedIn links. Code MIT; data credited to Dubai Land Department under the Dubai Open Data Licence.
- Explorer: zero servers, works on a phone, shows "data as of" date and a one-line method note.

## Review Focus

1. A community/type/rooms combination with very few sales (n < 5): the explorer must say so and show the wider community range rather than a confident number. Test in Task 9 (`test_stats_marks_thin_cells`).
2. Bulk portfolio deals where one transaction number covers many units and the value is the whole deal: the cleaner must drop them, not treat each unit as a sale at the portfolio price. Test in Task 5 (`test_bulk_deal_rows_dropped`).
3. A page fetched twice because the API total changed mid-run: parser must de-duplicate on the full row, and fetch must verify page count against TOTAL. Test in Task 4 (`test_duplicate_rows_collapse`).
4. Sizes outside the plausible range (0, 1, or 90,000 sqm) that would produce absurd price per sqm: cleaner drops them with documented bounds. Test in Task 5 (`test_implausible_area_dropped`).
5. Unknown category at prediction time (a community the model never saw, or the browser sending a label with different spacing): Python and JS predictors must map it to "unknown" and still return a number. Test in Task 7 (`test_unknown_category_predicts`).

---

### Task 1: Project skeleton and CLI stub

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `LICENSE`, `README.md` (stub), `src/dubai_prices/__init__.py`, `src/dubai_prices/cli.py`, `Makefile`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `dubai_prices.cli.main(argv: list[str] | None = None) -> int` dispatching subcommands `fetch`, `build`, `train`, `export`, `all`.

- [ ] Step 1: Write `tests/test_cli.py::test_cli_lists_subcommands` asserting `main(["--help"])` raises `SystemExit(0)` and the help text names all five subcommands.
- [ ] Step 2: Run `uv run pytest tests/test_cli.py -v`; expect FAIL.
- [ ] Step 3: Implement argparse-based `main` in `cli.py`; subcommands call into later modules (import lazily).
- [ ] Step 4: Run tests; expect PASS.

### Task 2: API client

**Files:**
- Create: `src/dubai_prices/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `class DLDClient(session=None, pause_seconds=1.0, max_retries=4, sleep=time.sleep)` with `fetch_page(group_id: str, from_date: date, to_date: date, take: int, skip: int) -> dict` (the parsed JSON) and module function `build_body(group_id, from_date, to_date, take, skip) -> dict` (pure). Constants `GROUP_SALES="1"`, `GROUP_MORTGAGE="2"`, `GROUP_GIFTS="3"`, `GROUP_NAMES`, `URL`, `HEADERS`. `class DLDError(Exception)`.

- [ ] Step 1: Tests: `test_build_body_formats_dates_mmddyyyy` (date(2026,1,5) -> "01/05/2026", all values strings, sort `TRANSACTION_NUMBER_ASC`); `test_fetch_page_retries_on_5xx_then_succeeds` using a fake session whose `post` fails twice with status 503 then returns a fixture; `test_fetch_page_raises_after_max_retries`; `test_fetch_page_rejects_bad_response_code`.
- [ ] Step 2: Run; expect FAIL.
- [ ] Step 3: Implement with `requests.Session`, injectable `sleep` for tests, backoff `2**attempt` seconds, raise `DLDError` on non-200 `responseCode`.
- [ ] Step 4: Run; expect PASS.

### Task 3: Raw page cache and fetch loop

**Files:**
- Create: `src/dubai_prices/fetch.py`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `DLDClient.fetch_page`.
- Produces: `fetch_range(client, group_id: str, start: date, end: date, raw_dir: Path, take: int = 2000) -> list[MonthResult]`; `MonthResult(month: str, total: int, pages: int, rows: int)`; files at `raw_dir/{group_name}/{YYYY-MM}/page-{n:04d}.json`; a `_done.json` marker per month with `{"total":..., "pages":..., "rows":..., "fetched_at":...}`; `month_ranges(start, end) -> list[tuple[date, date]]`; `page_paths(raw_dir, group_name) -> list[Path]`.

- [ ] Step 1: Tests: `test_month_ranges_splits_inclusive` (2026-01-01..2026-10-24 -> 10 ranges, last ends 2026-10-24); `test_fetch_range_writes_pages_and_marker` with a fake client returning TOTAL=5 over take=2; `test_fetch_range_skips_completed_month` (marker present -> client never called); `test_fetch_range_records_total_mismatch` (rows != total recorded in marker).
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS.

### Task 4: Parser (API pages and Pulse CSV -> typed frame)

**Files:**
- Create: `src/dubai_prices/parse.py`, `tests/fixtures/page_sales.json` (hand-built rows covering flat, villa, land, office, duplicate, bulk), `tests/fixtures/pulse_sample.csv`
- Test: `tests/test_parse.py`

**Interfaces:**
- Produces: `COLUMNS` (ordered list of tidy column names), `rows_from_page(page: dict) -> list[dict]`, `frame_from_rows(rows) -> pd.DataFrame`, `frame_from_pages(paths: Iterable[Path]) -> pd.DataFrame`, `frame_from_pulse_csv(path: Path) -> pd.DataFrame` with the same `COLUMNS`. Tidy columns: `transaction_number:str, date:datetime64, group:str, procedure:str, is_offplan:bool, is_freehold:bool, usage:str, area:str, property_type:str, property_subtype:str, rooms:str, parking:str, building_age:float, project:str, master_project:str, nearest_metro:str, nearest_mall:str, nearest_landmark:str, value_aed:float, procedure_area_sqm:float, actual_area_sqm:float, source:str`.

- [ ] Step 1: Tests: `test_rows_from_page_keeps_english_columns_only`; `test_frame_types` (date parsed from ISO, booleans from 1/0, numeric floats, None -> NaN or ""); `test_duplicate_rows_collapse` (same row in two pages -> one); `test_pulse_csv_maps_columns` (instance_date "05-01-2026" -> 2026-01-05, reg_type "Off-Plan Properties" -> is_offplan True, `source == "pulse"`).
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS.

### Task 5: Cleaner

**Files:**
- Create: `src/dubai_prices/clean.py`
- Test: `tests/test_clean.py`

**Interfaces:**
- Consumes: tidy frame from Task 4.
- Produces: `clean_sales(df) -> tuple[pd.DataFrame, list[CleanStep]]` where `CleanStep(name, rule, dropped, remaining)`; adds `price_per_sqm, ym (str YYYY-MM), month_n (int 1-12), rooms_n (float), parking_n (int)`. Constants `AREA_BOUNDS = {"Flat": (20, 1000), "Villa": (50, 3000)}`, `PPSQM_BOUNDS = (3000, 120000)` (to be confirmed against the real distribution and recorded in docs/data.md).

Rules in order (each a CleanStep): (1) sales only; (2) drop exact duplicates; (3) residential usage and subtype in {Flat, Villa}; (4) drop missing/zero value or area; (5) drop bulk deals: transaction_number shared by >1 row; (6) area bounds by subtype; (7) price per sqm bounds. Rooms map: Studio -> 0, "N B/R" -> N, PENTHOUSE -> NaN, NA -> NaN. Parking: integer string -> int, else number of comma-separated tokens, missing -> 0, capped at 10.

- [ ] Step 1: Tests: `test_bulk_deal_rows_dropped`, `test_implausible_area_dropped`, `test_non_residential_dropped`, `test_rooms_and_parking_parsed`, `test_steps_account_for_every_row` (sum of dropped + remaining == input).
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS.

### Task 6: Build command and snapshot

**Files:**
- Create: `src/dubai_prices/build.py`, `data/snapshot/README.md`
- Modify: `cli.py`

**Interfaces:**
- Produces: `build_dataset(raw_dir, snapshot_dir, processed_dir) -> BuildReport(raw_rows, clean_rows, steps, date_min, date_max)`; writes `data/snapshot/dld_sales_2026.parquet` (typed raw, zstd), `data/snapshot/dld_mortgages_2026.parquet` if present, `data/processed/sales_clean.parquet`, `data/processed/clean_report.json`. If `raw_dir` is empty but the snapshot exists, builds from the snapshot (this is what a screener runs).

- [ ] Step 1: Test `test_build_from_snapshot_only` with a tiny parquet.
- [ ] Step 2-4: FAIL -> implement -> PASS.

### Task 7: Features and models

**Files:**
- Create: `src/dubai_prices/features.py`, `src/dubai_prices/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- `features.py`: `CATEGORICAL = ["area","property_subtype","rooms","nearest_metro","nearest_mall","nearest_landmark"]`, `NUMERIC = ["log_area","is_offplan","is_freehold","building_age","parking_n","month_n"]`, `make_features(df, categories: dict[str, list[str]] | None = None) -> (X: DataFrame, categories)`; unseen labels -> `"__unknown__"`.
- `model.py`: `temporal_split(df, train_end=date(2026,8,31)) -> (train, test)`; `BaselineModel.fit(train) / predict(df) -> ndarray` (median ppsqm by area x subtype x rooms, fallback area x subtype, fallback subtype); `fit_lgbm(X, y, objective, alpha=None, params) -> Booster` with early stopping on the last training month then refit; `metrics(y_true_ppsqm, y_pred_ppsqm, area_sqm) -> dict(mae_ppsqm, mdape_ppsqm, mae_total, mdape_total)`; `coverage(y, lo, hi) -> float`; `train_and_evaluate(clean_df, models_dir) -> dict` writing `models/point.txt`, `models/q10.txt`, `models/q90.txt`, `models/categories.json`, `models/baseline.parquet`, `models/metrics.json`, `models/failures.csv`, `models/importance.csv`.

- [ ] Step 1: Tests on a synthetic 400-row frame: `test_temporal_split_dates`, `test_baseline_fallback_levels`, `test_unknown_category_predicts`, `test_metrics_known_values` (hand-computed MAE/MdAPE), `test_coverage_known_values`.
- [ ] Step 2-4: FAIL -> implement -> PASS.

### Task 8: Model export for the browser and JS evaluator

**Files:**
- Create: `src/dubai_prices/export_model.py`, `web/js/model.js`
- Test: `tests/test_export_model.py`

**Interfaces:**
- `export_model(boosters: dict[str, Booster], categories, out_path) -> dict` writing compact JSON: `{"features": [...], "categories": {...}, "models": {"point": [trees], "q10": [...], "q90": [...]}}`; each tree a flat node list `[feat, kind, thr_or_cats, left, right, value, default_left]`.
- `web/js/model.js`: `predictPpsqm(model, inputs) -> {point, lo, hi}` in AED per sqm.
- Test: `test_export_roundtrip_matches_python` evaluates the exported JSON with a tiny Python reference evaluator and compares to `booster.predict` on 200 rows (max abs diff < 1e-6 on log scale); `test_js_matches_python` runs `node` on the same inputs when node is available (skip otherwise).

### Task 9: Web data export

**Files:**
- Create: `src/dubai_prices/export_web.py`
- Test: `tests/test_export_web.py`

**Interfaces:**
- `export_web(clean_df, metrics, out_dir) -> dict` writing `web/data/stats.json` (per area x subtype x rooms: n, median/p25/p75 ppsqm, median size; also per area x subtype and per area), `web/data/monthly.json` (per area: ym -> median ppsqm, n), `web/data/comps.json` (up to 12 latest sales per cell: date, project, size, value, ppsqm, offplan), `web/data/meta.json` (data_as_of, rows, train/test metrics, generated_at).
- Test: `test_stats_marks_thin_cells` (n<5 -> `thin: true`), `test_comps_limited_and_sorted`, `test_monthly_keys`.

### Task 10: Charts

**Files:** `src/dubai_prices/charts.py`; outputs in `docs/charts/*.png`.
- `make_charts(clean_df, metrics_json, out_dir)`: monthly median ppsqm overall and top communities; distribution of ppsqm by subtype; actual vs predicted (test set); feature importance; interval coverage by predicted decile. Smoke test `test_charts_write_files` on synthetic data.

### Task 11: Explorer UI

**Files:** `web/index.html`, `web/css/style.css`, `web/js/app.js`, `web/js/chart.js`.
- Behaviour: pick community (searchable select), type (Flat/Villa), rooms, size; show price range (p25-p75 x size), model estimate with 80% interval, comparables table, monthly SVG chart; thin-cell warning; "data as of"; method note. Mobile-first. Verified by browser screenshots at desktop and 390 px widths saved to `docs/screenshots/`.

### Task 12: Repo, CI, Pages, README

**Files:** `.github/workflows/tests.yml`, `.github/workflows/pages.yml`, `docs/data.md`, `README.md`.
- Ask about the commit trailer once; `git init`; commits; `gh repo create HamdanEsmail/dubai-property-prices --public`; enable Pages (workflow build type); confirm the live URL; write README with real numbers; final verification run of `make all` from the snapshot.
