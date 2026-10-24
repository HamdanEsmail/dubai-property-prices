"""Baseline and LightGBM models for log price per square metre, with temporal validation.

Train: January to August. Test: September onwards. Early stopping uses the last
training month as a validation set, then the model is refitted on all training
rows with that number of trees, so the test months are never touched during fitting.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from .features import CATEGORICAL, FEATURES, make_features

# Point model. Chosen for accuracy per byte: the exported trees must stay small enough
# to ship to a phone (see README, "Model"). A 63-leaf, 1,300-tree model is ~0.4 points
# of MdAPE better and five times the size.
DEFAULT_PARAMS = {
    "num_leaves": 31,
    "learning_rate": 0.1,
    "min_child_samples": 30,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "cat_smooth": 10,
    "max_cat_threshold": 16,
    "max_rounds": 3000,
    "early_stopping": 50,
    "seed": 42,
    "deterministic": True,  # same data + same params -> same trees, whatever the thread count
    "force_row_wise": True,
}
LARGE_PARAMS = {**DEFAULT_PARAMS, "num_leaves": 63, "learning_rate": 0.05, "min_child_samples": 20, "early_stopping": 100}
FAST_PARAMS = {**DEFAULT_PARAMS, "num_leaves": 7, "learning_rate": 0.2, "min_child_samples": 5, "max_rounds": 60, "early_stopping": 10}
COVERAGE_TARGET = 0.8


def quantile_params(params: dict) -> dict:
    """Smaller trees for the two interval models; they are calibrated afterwards anyway."""
    return {**params, "num_leaves": min(params["num_leaves"], 15), "max_rounds": min(params["max_rounds"], 800)}


TARGETS = [("point", "regression", None), ("q10", "quantile", 0.1), ("q90", "quantile", 0.9)]


def temporal_split(df: pd.DataFrame, train_end: date = date(2026, 8, 31)) -> tuple[pd.DataFrame, pd.DataFrame]:
    cutoff = pd.Timestamp(train_end) + pd.Timedelta(days=1)
    return df[df["date"] < cutoff].copy(), df[df["date"] >= cutoff].copy()


class BaselineModel:
    """Median price per sqm by community x sub-type x rooms, falling back to
    community x sub-type, then sub-type, then the overall median."""

    LEVELS = [["area", "property_subtype", "rooms"], ["area", "property_subtype"], ["property_subtype"]]

    def fit(self, df: pd.DataFrame) -> "BaselineModel":
        self.tables = [
            df.groupby(keys, observed=True)["price_per_sqm"].agg(["median", "size"]).reset_index()
            for keys in self.LEVELS
        ]
        self.global_median = float(df["price_per_sqm"].median())
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        out = pd.Series(np.nan, index=df.index)
        for keys, table in zip(self.LEVELS, self.tables):
            probe = df[keys].astype(object).reset_index(drop=True)
            lookup = table[keys + ["median"]].astype({k: object for k in keys})
            merged = probe.merge(lookup, on=keys, how="left")
            out = out.fillna(pd.Series(merged["median"].to_numpy(), index=df.index))
        return out.fillna(self.global_median).to_numpy(dtype=float)

    def to_frame(self) -> pd.DataFrame:
        frames = []
        for level, table in enumerate(self.tables):
            t = table.copy()
            t["level"] = level
            frames.append(t)
        return pd.concat(frames, ignore_index=True)


def _lgb_params(params: dict, objective: str, alpha: float | None) -> dict:
    p = {k: v for k, v in params.items() if k not in {"max_rounds", "early_stopping"}}
    p.update({"objective": objective, "verbose": -1, "num_threads": 0})
    if alpha is not None:
        p["alpha"] = alpha
    return p


def fit_lgbm(
    X: pd.DataFrame,
    y: np.ndarray,
    objective: str,
    alpha: float | None = None,
    params: dict = DEFAULT_PARAMS,
    valid: tuple[pd.DataFrame, np.ndarray] | None = None,
    fit_subset: pd.Index | None = None,
    refit: bool = True,
) -> tuple[lgb.Booster, int]:
    """Fit on `fit_subset` rows with early stopping on `valid`, then (if `refit`) refit on
    all of X with the chosen number of rounds. With refit=False the booster fitted on
    `fit_subset` is returned, truncated to the best round. Returns (booster, rounds)."""
    p = _lgb_params(params, objective, alpha)
    rounds = params["max_rounds"]
    if valid is not None:
        if fit_subset is not None:
            X_fit, y_fit = X.loc[fit_subset], y[X.index.get_indexer(fit_subset)]
        else:
            X_fit, y_fit = X, y
        dfit = lgb.Dataset(X_fit, label=y_fit, categorical_feature=CATEGORICAL, free_raw_data=False)
        dval = lgb.Dataset(valid[0], label=valid[1], reference=dfit, categorical_feature=CATEGORICAL, free_raw_data=False)
        probe = lgb.train(
            p, dfit, num_boost_round=rounds, valid_sets=[dval],
            callbacks=[lgb.early_stopping(params["early_stopping"], verbose=False)],
        )
        rounds = max(int(probe.best_iteration), 10)
        if not refit:
            return lgb.Booster(model_str=probe.model_to_string(num_iteration=rounds)), rounds
    dtrain = lgb.Dataset(X, label=y, categorical_feature=CATEGORICAL, free_raw_data=False)
    return lgb.train(p, dtrain, num_boost_round=rounds), rounds


def metrics(y_true: np.ndarray, y_pred: np.ndarray, area_sqm: np.ndarray) -> dict:
    y_true, y_pred, area_sqm = (np.asarray(a, dtype=float) for a in (y_true, y_pred, area_sqm))
    err = np.abs(y_true - y_pred)
    total_err = np.abs(y_true * area_sqm - y_pred * area_sqm)
    return {
        "n": int(len(y_true)),
        "mae_ppsqm": float(err.mean()),
        "mdape_ppsqm": float(np.median(err / y_true * 100)),
        "mae_total": float(total_err.mean()),
        "mdape_total": float(np.median(total_err / (y_true * area_sqm) * 100)),
    }


def coverage(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    y, lo, hi = (np.asarray(a, dtype=float) for a in (y, lo, hi))
    return float(np.mean((y >= lo) & (y <= hi)))


def _segments(test: pd.DataFrame) -> dict[str, pd.Series]:
    offplan = test["is_offplan"].fillna(False).astype(bool)
    return {
        "Flat": test["property_subtype"] == "Flat",
        "Villa": test["property_subtype"] == "Villa",
        "off-plan": offplan,
        "ready": ~offplan,
    }


def conformal_adjust(y: np.ndarray, lo: np.ndarray, hi: np.ndarray, target: float = COVERAGE_TARGET) -> float:
    """Split-conformal widening (Romano et al., 2019): the quantile of how far held-out
    values fall outside [lo, hi] such that `target` of them are covered after widening."""
    scores = np.maximum(lo - y, y - hi)
    n = len(scores)
    return float(np.quantile(scores, min(1.0, target * (1 + 1 / n))))


def train_and_evaluate(
    source: Path | pd.DataFrame,
    models_dir: Path,
    params: dict = DEFAULT_PARAMS,
    train_end: date = date(2026, 8, 31),
) -> dict:
    df = pd.read_parquet(source) if isinstance(source, (str, Path)) else source
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    train, test = temporal_split(df, train_end)
    y_train = np.log(train["price_per_sqm"].to_numpy(dtype=float))
    X_train, categories = make_features(train)
    X_test, _ = make_features(test, categories)
    last_month = train["ym"].max()
    fit_idx = train.index[train["ym"] < last_month]
    val_idx = train.index[train["ym"] == last_month]
    valid = (X_train.loc[val_idx], y_train[train.index.get_indexer(val_idx)])

    # Point model: early stopping on the last training month, then refit on all training rows.
    # Interval models: fitted on the earlier months only, so the last month can calibrate them.
    boosters: dict[str, lgb.Booster] = {}
    rounds: dict[str, int] = {}
    for name, objective, alpha in TARGETS:
        is_point = name == "point"
        boosters[name], rounds[name] = fit_lgbm(
            X_train, y_train, objective, alpha, params if is_point else quantile_params(params),
            valid=valid, fit_subset=fit_idx, refit=is_point,
        )
        boosters[name].save_model(str(models_dir / f"{name}.txt"))
        boosters[name] = lgb.Booster(model_file=str(models_dir / f"{name}.txt"))  # predict with what was saved
    (models_dir / "categories.json").write_text(json.dumps(categories, ensure_ascii=False), encoding="utf-8")

    lo_val = boosters["q10"].predict(valid[0])
    hi_val = boosters["q90"].predict(valid[0])
    adjust = conformal_adjust(valid[1], lo_val, hi_val)
    calibration = {
        "month": last_month, "rows": int(len(val_idx)), "target": COVERAGE_TARGET,
        "raw_coverage": coverage(valid[1], lo_val, hi_val), "adjust_log": adjust,
    }

    baseline = BaselineModel().fit(train)
    baseline.to_frame().to_parquet(models_dir / "baseline.parquet", index=False)

    y_test = test["price_per_sqm"].to_numpy(dtype=float)
    area = test["actual_area_sqm"].to_numpy(dtype=float)
    pred = np.exp(boosters["point"].predict(X_test))
    lo_raw = np.exp(boosters["q10"].predict(X_test))
    hi_raw = np.exp(boosters["q90"].predict(X_test))
    lo = np.exp(np.log(np.minimum(lo_raw, hi_raw)) - adjust)
    hi = np.exp(np.log(np.maximum(lo_raw, hi_raw)) + adjust)
    # The band always contains the point estimate (the three models are fitted separately).
    lo, hi = np.minimum(lo, pred), np.maximum(hi, pred)
    base = baseline.predict(test)

    model_metrics = metrics(y_test, pred, area)
    model_metrics["coverage_80"] = coverage(y_test, lo, hi)
    model_metrics["coverage_80_raw"] = coverage(y_test, lo_raw, hi_raw)
    model_metrics["interval_adjust_log"] = adjust
    model_metrics["median_interval_width_pct"] = float(np.median((hi - lo) / pred * 100))
    by_segment = {}
    for seg, mask in _segments(test).items():
        m = mask.to_numpy()
        if m.sum():
            by_segment[seg] = {
                "model": metrics(y_test[m], pred[m], area[m]),
                "baseline": metrics(y_test[m], base[m], area[m]),
                "coverage_80": coverage(y_test[m], lo[m], hi[m]),
            }

    importance = pd.DataFrame({
        "feature": FEATURES,
        "gain": boosters["point"].feature_importance("gain"),
        "splits": boosters["point"].feature_importance("split"),
    }).sort_values("gain", ascending=False)
    importance["gain_share"] = importance["gain"] / importance["gain"].sum()
    importance.to_csv(models_dir / "importance.csv", index=False)

    preds = test[[
        "transaction_number", "date", "area", "project", "property_subtype", "rooms", "actual_area_sqm",
        "is_offplan", "value_aed", "price_per_sqm",
    ]].copy()
    preds["pred_ppsqm"] = pred
    preds["lo_ppsqm"] = lo
    preds["hi_ppsqm"] = hi
    preds["baseline_ppsqm"] = base
    preds["abs_pct_error"] = np.abs(pred - y_test) / y_test * 100
    preds.to_parquet(models_dir / "test_predictions.parquet", index=False)
    preds.sort_values("abs_pct_error", ascending=False).head(8).to_csv(models_dir / "failures.csv", index=False)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "train": {
            "rows": int(len(train)), "start": str(train["date"].min().date()), "end": str(train["date"].max().date()),
            "early_stopping_month": last_month,
        },
        "test": {"rows": int(len(test)), "start": str(test["date"].min().date()), "end": str(test["date"].max().date())},
        "baseline": metrics(y_test, base, area),
        "model": model_metrics,
        "by_segment": by_segment,
        "calibration": calibration,
        "rounds": rounds,
        "params": params,
        "quantile_params": quantile_params(params),
        "month_n": int(df["month_n"].max()),
        "month_label": df["date"].max().strftime("%B %Y"),
        "features": FEATURES,
        "importance": importance[["feature", "gain_share"]].to_dict("records"),
    }
    result["summary"] = (
        f"train {result['train']['rows']:,} rows ({result['train']['start']} to {result['train']['end']}), "
        f"test {result['test']['rows']:,} rows ({result['test']['start']} to {result['test']['end']})\n"
        f"baseline: MAE {result['baseline']['mae_ppsqm']:,.0f} AED/sqm, MdAPE {result['baseline']['mdape_ppsqm']:.1f}%\n"
        f"model:    MAE {model_metrics['mae_ppsqm']:,.0f} AED/sqm, MdAPE {model_metrics['mdape_ppsqm']:.1f}%, "
        f"80% interval coverage {model_metrics['coverage_80'] * 100:.1f}% "
        f"(raw {model_metrics['coverage_80_raw'] * 100:.1f}%, calibrated on {last_month})"
    )
    (models_dir / "metrics.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result
