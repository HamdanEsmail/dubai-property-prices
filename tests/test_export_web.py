import json

from dubai_prices import export_web
from tests.test_model import synthetic

METRICS = {"model": {"mdape_ppsqm": 9.1, "mae_ppsqm": 1800.0, "coverage_80": 0.79, "mdape_total": 9.0, "mae_total": 150000.0},
           "baseline": {"mdape_ppsqm": 12.5, "mae_ppsqm": 2500.0, "mdape_total": 12.0, "mae_total": 200000.0},
           "train": {"rows": 400, "start": "2026-01-01", "end": "2026-08-31"},
           "test": {"rows": 200, "start": "2026-09-01", "end": "2026-10-24"}}
REPORT = {"raw_rows": 700, "clean_rows": 600, "date_min": "2026-01-01 10:00:00", "date_max": "2026-10-24 15:07:01",
          "steps": [{"name": "sales_only", "rule": "x", "dropped": 0, "remaining": 700}]}


def _export(tmp_path, df=None):
    df = synthetic() if df is None else df
    return df, export_web.export_web(df, METRICS, REPORT, tmp_path, max_comps=5)


def test_stats_marks_thin_cells(tmp_path):
    df = synthetic()
    thin = df.head(3).copy()
    thin["area"] = "Tiny"
    df = __import__("pandas").concat([df, thin], ignore_index=True)
    df, summary = _export(tmp_path, df)
    stats = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    tiny = stats["areas"]["Tiny"]
    assert tiny["n"] == 3
    cell = next(iter(next(iter(tiny["subtypes"].values()))["rooms"].values()))
    assert cell["thin"] is True and cell["n"] <= 3
    alpha = stats["areas"]["Alpha"]["subtypes"]["Flat"]
    assert alpha["n"] > 20 and alpha["p25"] <= alpha["median"] <= alpha["p75"]
    assert set(alpha["defaults"]) >= {"nearest_metro", "nearest_mall", "nearest_landmark", "is_freehold", "parking_n", "offplan_share"}
    assert stats["rooms_order"][:3] == ["Studio", "1 B/R", "2 B/R"]
    assert summary["areas"] == stats["n_areas"] == 4


def test_comps_limited_and_sorted(tmp_path):
    df, _ = _export(tmp_path)
    area_file = tmp_path / "areas" / "alpha.json"
    assert area_file.exists()
    payload = json.loads(area_file.read_text(encoding="utf-8"))
    comps = payload["comps"]["Flat"]["Studio"]
    assert 1 <= len(comps) <= 5
    dates = [c[0] for c in comps]
    assert dates == sorted(dates, reverse=True)
    assert len(comps[0]) == 6  # date, project, size, value, ppsqm, offplan
    assert payload["monthly"]["2026-01"]["n"] > 0


def test_profiles_in_area_file(tmp_path):
    df, _ = _export(tmp_path)
    payload = json.loads((tmp_path / "areas" / "alpha.json").read_text(encoding="utf-8"))
    profiles = payload["profiles"]["Flat"]
    assert "_all" in profiles and "Studio" in profiles
    for prof in profiles["Studio"]:
        assert len(prof) == 6  # metro, mall, landmark, parking, freehold, share
    assert abs(sum(p[5] for p in profiles["Studio"]) - 1) < 0.01
    assert profiles["Studio"][0][5] >= profiles["Studio"][-1][5]  # most common first


def test_explorer_evaluation_scores_test_rows(tmp_path):
    from dubai_prices import model

    df = synthetic()
    models_dir = tmp_path / "models"
    model.train_and_evaluate(df, models_dir, params=model.FAST_PARAMS)
    result = export_web.explorer_evaluation(df, models_dir)
    assert result["n"] == len(df[df["date"] >= "2026-09-01"])
    assert 0 <= result["coverage_80"] <= 1 and result["mdape_ppsqm"] > 0
    assert 1 <= result["profiles_per_row"] <= export_web.MAX_PROFILES
    assert (models_dir / "explorer_metrics.json").exists()


def test_monthly_keys(tmp_path):
    df, _ = _export(tmp_path)
    monthly = json.loads((tmp_path / "monthly.json").read_text(encoding="utf-8"))
    keys = list(monthly["all"].keys())
    assert keys == sorted(keys) and keys[0] == "2026-01" and keys[-1] == "2026-10"
    assert set(monthly["all"]["2026-01"]) == {"median", "n"}


def test_meta_fields(tmp_path):
    _export(tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["data_as_of"] == "2026-10-24"
    assert meta["rows"] == 600 and meta["raw_rows"] == 700
    assert meta["metrics"]["model"]["mdape_ppsqm"] == 9.1
    assert meta["date_min"] == "2026-01-01"


def test_slug():
    assert export_web.slug("Jumeirah Village Circle") == "jumeirah-village-circle"
    assert export_web.slug("Al Yelayiss 1") == "al-yelayiss-1"
    assert export_web.slug("Nad Al Shiba / First") == "nad-al-shiba-first"
