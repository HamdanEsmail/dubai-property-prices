import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from dubai_prices import export_model, features, model
from tests.test_model import synthetic

WEB_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "model.js"


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    df = synthetic()
    models_dir = tmp_path_factory.mktemp("models")
    model.train_and_evaluate(df, models_dir, params=model.FAST_PARAMS)
    return df, models_dir


def _text(value):
    return None if value is None or (isinstance(value, float) and np.isnan(value)) else str(value)


def _rows(df):
    out = []
    for _, r in df.iterrows():
        out.append({
            "area": r["area"], "property_subtype": r["property_subtype"], "rooms": r["rooms"],
            "nearest_metro": _text(r["nearest_metro"]),
            "nearest_mall": _text(r["nearest_mall"]), "nearest_landmark": _text(r["nearest_landmark"]),
            "actual_area_sqm": float(r["actual_area_sqm"]), "rooms_n": float(r["rooms_n"]),
            "is_offplan": bool(r["is_offplan"]), "is_freehold": bool(r["is_freehold"]),
            "parking_n": int(r["parking_n"]), "month_n": int(r["month_n"]),
        })
    return out


def test_export_roundtrip_matches_python(trained, tmp_path):
    df, models_dir = trained
    out = export_model.export_model(models_dir, tmp_path / "model.json")
    exported = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))
    assert exported["features"] == features.FEATURES
    assert set(exported["models"]) == {"point", "q10", "q90"}
    assert exported["month_n"] == 10 and exported["month_label"] == "October 2026"
    assert out["bytes"] == (tmp_path / "model.json").stat().st_size
    sample = df.sample(200, random_state=1)
    sample.loc[sample.index[0], "area"] = "Nowhere"  # unknown category must still predict
    X, _ = features.make_features(sample, json.loads((models_dir / "categories.json").read_text(encoding="utf-8")))
    for name in ["point", "q10", "q90"]:
        booster = export_model.load_booster(models_dir / f"{name}.txt")
        expected = booster.predict(X)
        got = export_model.predict_exported(exported, name, _rows(sample))
        assert np.max(np.abs(got - expected)) < 5e-4, name  # leaf values are rounded to 6 decimals


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_matches_python(trained, tmp_path):
    df, models_dir = trained
    export_model.export_model(models_dir, tmp_path / "model.json")
    exported = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))
    sample = df.sample(150, random_state=2)
    sample.loc[sample.index[0], "area"] = "Nowhere"
    rows = _rows(sample)
    (tmp_path / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    script = tmp_path / "run.mjs"
    script.write_text(
        "import { readFileSync } from 'node:fs';\n"
        f"import {{ predictPpsqm }} from {json.dumps(WEB_JS.resolve().as_uri())};\n"
        f"const model = JSON.parse(readFileSync({json.dumps(str(tmp_path / 'model.json'))}, 'utf8'));\n"
        f"const rows = JSON.parse(readFileSync({json.dumps(str(tmp_path / 'rows.json'))}, 'utf8'));\n"
        "console.log(JSON.stringify(rows.map(r => predictPpsqm(model, r))));\n",
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, check=True)
    js = json.loads(result.stdout)
    adjust = exported["interval_adjust"]
    assert adjust > 0  # the synthetic run calibrates on its last training month
    point = np.exp(export_model.predict_exported(exported, "point", rows))
    lo = np.exp(export_model.predict_exported(exported, "q10", rows) - adjust)
    hi = np.exp(export_model.predict_exported(exported, "q90", rows) + adjust)
    expected = {"point": point, "lo": np.minimum(np.minimum(lo, hi), point), "hi": np.maximum(np.maximum(lo, hi), point)}
    for key, py in expected.items():
        got = np.array([row[key] for row in js], dtype=float)
        assert np.max(np.abs(got / py - 1)) < 1e-9, key
