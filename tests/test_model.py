import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from dubai_prices import features, model


def synthetic(n=600, seed=0):
    rng = np.random.default_rng(seed)
    areas = rng.choice(["Alpha", "Beta", "Gamma"], n)
    subtype = rng.choice(["Flat", "Villa"], n, p=[0.8, 0.2])
    rooms = rng.choice(["Studio", "1 B/R", "2 B/R"], n)
    rooms_n = pd.Series(rooms).map({"Studio": 0, "1 B/R": 1, "2 B/R": 2}).to_numpy(dtype=float)
    size = np.where(subtype == "Villa", rng.uniform(150, 400, n), rng.uniform(35, 150, n))
    base = pd.Series(areas).map({"Alpha": 25000, "Beta": 15000, "Gamma": 9000}).to_numpy(dtype=float)
    ppsqm = base * (1 + 0.1 * rooms_n) * np.exp(rng.normal(0, 0.08, n))
    dates = pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 297, n), unit="D")
    df = pd.DataFrame({
        "transaction_number": [f"1-{i}-2026" for i in range(n)],
        "date": dates,
        "area": areas, "property_subtype": subtype, "rooms": rooms, "rooms_n": rooms_n,
        "actual_area_sqm": size, "is_offplan": rng.random(n) < 0.7, "is_freehold": True,
        "parking_n": rng.integers(0, 3, n), "project": "P",
        "nearest_metro": rng.choice(["M1", "M2", None], n), "nearest_mall": "Dubai Mall", "nearest_landmark": None,
        "price_per_sqm": ppsqm,
    })
    df["is_offplan"] = df["is_offplan"].astype("boolean")
    df["is_freehold"] = df["is_freehold"].astype("boolean")
    df["value_aed"] = df["price_per_sqm"] * df["actual_area_sqm"]
    df["ym"] = df["date"].dt.strftime("%Y-%m")
    df["month_n"] = df["date"].dt.month
    return df.sort_values("date").reset_index(drop=True)


def test_temporal_split_dates():
    train, test = model.temporal_split(synthetic(), train_end=date(2026, 8, 31))
    assert train["date"].max() < pd.Timestamp("2026-09-01")
    assert test["date"].min() >= pd.Timestamp("2026-09-01")
    assert len(train) + len(test) == 600


def test_make_features_unknown_category():
    df = synthetic()
    X, cats = features.make_features(df)
    assert list(X.columns) == features.CATEGORICAL + features.NUMERIC
    assert "__unknown__" in cats["area"] and set(["Alpha", "Beta", "Gamma"]) <= set(cats["area"])
    assert str(X["area"].dtype) == "category"
    new = df.head(3).copy()
    new.loc[0, "area"] = "Zeta"
    X2, cats2 = features.make_features(new, cats)
    assert cats2 == cats
    assert X2["area"].iloc[0] == "__unknown__" and X2["area"].iloc[1] == df["area"].iloc[1]
    assert list(X2["area"].cat.categories) == cats["area"]
    assert X2["log_area"].iloc[0] == pytest.approx(np.log(new["actual_area_sqm"].iloc[0]))
    assert X["nearest_metro"].isna().sum() == 0  # missing text becomes its own level


def test_baseline_fallback_levels():
    df = synthetic()
    bm = model.BaselineModel().fit(df)
    cell = df[(df.area == "Alpha") & (df.property_subtype == "Flat") & (df.rooms == "Studio")]
    q = cell.head(1).copy()
    assert bm.predict(q)[0] == pytest.approx(cell["price_per_sqm"].median())
    q["rooms"] = "9 B/R"  # unseen rooms -> area x subtype median
    sub = df[(df.area == "Alpha") & (df.property_subtype == "Flat")]
    assert bm.predict(q)[0] == pytest.approx(sub["price_per_sqm"].median())
    q["area"] = "Nowhere"  # unseen area -> subtype median
    assert bm.predict(q)[0] == pytest.approx(df[df.property_subtype == "Flat"]["price_per_sqm"].median())


def test_metrics_known_values():
    m = model.metrics(np.array([100.0, 200.0, 300.0]), np.array([110.0, 180.0, 300.0]), np.array([10.0, 10.0, 10.0]))
    assert m["mae_ppsqm"] == pytest.approx(10.0)
    assert m["mdape_ppsqm"] == pytest.approx(10.0)
    assert m["mae_total"] == pytest.approx(100.0)
    assert m["mdape_total"] == pytest.approx(10.0)
    assert m["n"] == 3


def test_coverage_known_values():
    y = np.array([1.0, 2.0, 3.0, 4.0])
    assert model.coverage(y, np.array([0, 0, 4, 0.0]), np.array([2, 3, 5, 3.0])) == pytest.approx(0.5)


def test_train_and_evaluate_writes_artifacts(tmp_path):
    df = synthetic()
    report = model.train_and_evaluate(df, tmp_path, params=model.FAST_PARAMS)
    for name in ["point.txt", "q10.txt", "q90.txt", "categories.json", "metrics.json",
                 "baseline.parquet", "failures.csv", "importance.csv", "test_predictions.parquet"]:
        assert (tmp_path / name).exists(), name
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["train"]["rows"] + metrics["test"]["rows"] == 600
    assert 0 <= metrics["model"]["coverage_80_raw"] <= metrics["model"]["coverage_80"] <= 1
    assert metrics["calibration"]["month"] == "2026-08" and metrics["calibration"]["adjust_log"] >= 0
    assert metrics["month_n"] == 10 and metrics["month_label"] == "October 2026"
    assert metrics["model"]["mdape_ppsqm"] < metrics["baseline"]["mdape_ppsqm"] * 2  # sanity, not a quality bar
    assert "summary" in report and "MdAPE" in report["summary"]
    assert set(metrics["by_segment"].keys()) >= {"Flat", "Villa", "off-plan", "ready"}
    assert len(pd.read_csv(tmp_path / "failures.csv")) <= 10
