from dubai_prices import charts, model
from tests.test_model import synthetic


def test_charts_write_files(tmp_path):
    df = synthetic()
    models_dir = tmp_path / "models"
    model.train_and_evaluate(df, models_dir, params=model.FAST_PARAMS)
    out = charts.make_charts(df, models_dir, tmp_path / "charts")
    names = sorted(p.name for p in out)
    assert names == sorted(charts.CHART_NAMES)
    for p in out:
        assert p.exists() and p.stat().st_size > 5000, p
