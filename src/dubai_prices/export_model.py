"""Export the LightGBM boosters to one compact JSON file the browser can evaluate.

Each tree is a flat list of nodes. Internal node: [feature, threshold, left, right, flags]
where threshold is a number for numeric splits or a list of category codes that go
left for categorical splits, and flags = default_left + 2 * missing_type
(missing_type: 0 None, 1 Zero, 2 NaN, as in LightGBM). A leaf is a bare number.
Predictions are on the log scale; sum the leaf values of every tree.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np

from .features import CATEGORICAL, FEATURES, MISSING, NUMERIC, UNKNOWN

MISSING_TYPES = {"None": 0, "Zero": 1, "NaN": 2}
MODEL_NAMES = ["point", "q10", "q90"]


def load_booster(path: Path) -> lgb.Booster:
    return lgb.Booster(model_file=str(path))


def _flatten(tree: dict) -> list:
    nodes: list = []

    def visit(node: dict) -> int:
        idx = len(nodes)
        nodes.append(None)
        if "leaf_value" in node:
            nodes[idx] = round(float(node["leaf_value"]), 6)
            return idx
        if node["decision_type"] == "==":
            threshold = [int(c) for c in str(node["threshold"]).split("||")]
        else:
            threshold = round(float(node["threshold"]), 6)
        flags = int(bool(node.get("default_left"))) + 2 * MISSING_TYPES[node.get("missing_type", "None")]
        left = visit(node["left_child"])
        right = visit(node["right_child"])
        nodes[idx] = [int(node["split_feature"]), threshold, left, right, flags]
        return idx

    visit(tree)
    return nodes


def export_model(models_dir: Path, out_path: Path) -> dict:
    models_dir, out_path = Path(models_dir), Path(out_path)
    categories = json.loads((models_dir / "categories.json").read_text(encoding="utf-8"))
    metrics_path = models_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    payload = {
        "version": 1,
        "features": FEATURES,
        "categorical": CATEGORICAL,
        "categories": categories,
        "unknown": UNKNOWN,
        "missing": MISSING,
        "interval_adjust": float(metrics.get("model", {}).get("interval_adjust_log", 0.0)),
        "month_n": int(metrics.get("month_n", 12)),
        "month_label": metrics.get("month_label", ""),
        "models": {},
    }
    for name in MODEL_NAMES:
        dump = load_booster(models_dir / f"{name}.txt").dump_model()
        assert dump["feature_names"] == FEATURES, dump["feature_names"]
        payload["models"][name] = [_flatten(t["tree_structure"]) for t in dump["tree_info"]]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return {"bytes": out_path.stat().st_size, "trees": {k: len(v) for k, v in payload["models"].items()}}


def encode_row(exported: dict, row: dict) -> list[float]:
    """Raw input dict -> feature vector in model order (category codes and floats)."""
    x: list[float] = []
    for name in exported["features"]:
        if name in exported["categorical"]:
            levels = exported["categories"][name]
            value = row.get(name)
            label = exported["missing"] if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
            code = levels.index(label) if label in levels else levels.index(exported["unknown"])
            x.append(float(code))
        elif name == "log_area":
            x.append(math.log(float(row["actual_area_sqm"])))
        else:
            value = row.get(name)
            x.append(float("nan") if value is None else float(value))
    return x


def _decide(node: list, value: float, categorical: bool) -> bool:
    """True -> go left. Mirrors LightGBM's NumericalDecision / CategoricalDecision."""
    threshold, flags = node[1], node[4]
    default_left, missing_type = flags & 1, flags >> 1
    if categorical:
        if math.isnan(value):
            if missing_type == 2:
                return False
            value = 0.0
        code = int(value)
        if code < 0:
            return False
        return code in threshold
    if math.isnan(value) and missing_type != 2:
        value = 0.0
    if (missing_type == 1 and abs(value) <= 1e-35) or (missing_type == 2 and math.isnan(value)):
        return bool(default_left)
    return value <= threshold


def predict_exported(exported: dict, name: str, rows: list[dict]) -> np.ndarray:
    """Reference evaluator for the exported JSON (log scale), used to test the export."""
    categorical_idx = {exported["features"].index(c) for c in exported["categorical"]}
    out = np.zeros(len(rows))
    for i, row in enumerate(rows):
        x = encode_row(exported, row)
        total = 0.0
        for nodes in exported["models"][name]:
            node = nodes[0]
            while isinstance(node, list):
                node = nodes[node[2] if _decide(node, x[node[0]], node[0] in categorical_idx) else node[3]]
            total += node
        out[i] = total
    return out
