import pytest

from dubai_prices.cli import main


def test_cli_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in ["fetch", "build", "train", "export", "all"]:
        assert name in out
