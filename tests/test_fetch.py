import json
from datetime import date

from dubai_prices import api, fetch


class FakeClient:
    """Serves `rows` as pages of `take`; every row carries TOTAL like the real API."""

    def __init__(self, rows, total=None):
        self.rows = rows
        self.total = len(rows) if total is None else total
        self.calls = []

    def fetch_page(self, group_id, from_date, to_date, take, skip):
        self.calls.append((group_id, from_date, to_date, take, skip))
        chunk = [dict(r, TOTAL=self.total) for r in self.rows[skip : skip + take]]
        return {"responseCode": 200, "validationErrorsList": [], "response": {"result": chunk}}


def rows(n):
    return [{"TRANSACTION_NUMBER": f"1-{i}-2026", "TRANS_VALUE": i * 1000} for i in range(n)]


START, END = date(2026, 1, 1), date(2026, 10, 24)
WINDOW = "2026-01-01_2026-10-24"


def test_fetch_window_writes_pages_and_marker(tmp_path):
    client = FakeClient(rows(5))
    result = fetch.fetch_window(client, api.GROUP_SALES, START, END, tmp_path, take=2)
    window_dir = tmp_path / "sales" / WINDOW
    pages = sorted(p.name for p in window_dir.glob("page-*.json"))
    assert pages == ["page-0000.json", "page-0001.json", "page-0002.json"]
    marker = json.loads((window_dir / "_done.json").read_text())
    assert marker["total"] == 5 and marker["pages"] == 3 and marker["rows"] == 5
    assert result == fetch.FetchResult(window=WINDOW, total=5, pages=3, rows=5)
    assert [c[4] for c in client.calls] == [0, 2, 4]
    assert client.calls[0][1:3] == (START, date(2026, 10, 25))  # the day after `end` goes to the API


def test_fetch_window_skips_completed_window(tmp_path):
    fetch.fetch_window(FakeClient(rows(3)), api.GROUP_SALES, START, END, tmp_path, take=2)
    client2 = FakeClient(rows(3))
    result = fetch.fetch_window(client2, api.GROUP_SALES, START, END, tmp_path, take=2)
    assert client2.calls == []
    assert result.rows == 3


def test_fetch_window_resumes_after_partial_download(tmp_path):
    client = FakeClient(rows(5))
    window_dir = tmp_path / "sales" / WINDOW
    window_dir.mkdir(parents=True)
    first = client.fetch_page(api.GROUP_SALES, START, END, 2, 0)
    (window_dir / "page-0000.json").write_text(json.dumps(first))
    client.calls.clear()
    fetch.fetch_window(client, api.GROUP_SALES, START, END, tmp_path, take=2)
    assert [c[4] for c in client.calls] == [2, 4]


def test_fetch_window_records_total_mismatch(tmp_path):
    client = FakeClient(rows(4), total=6)  # API claims 6 but only serves 4
    result = fetch.fetch_window(client, api.GROUP_SALES, START, END, tmp_path, take=2)
    assert result.total == 6 and result.rows == 4
    marker = json.loads((tmp_path / "sales" / WINDOW / "_done.json").read_text())
    assert marker["total"] == 6 and marker["rows"] == 4


def test_fetch_window_empty(tmp_path):
    result = fetch.fetch_window(FakeClient([]), api.GROUP_GIFTS, START, END, tmp_path, take=2)
    assert result == fetch.FetchResult(window=WINDOW, total=0, pages=0, rows=0)
    assert (tmp_path / "gifts" / WINDOW / "_done.json").exists()


def test_page_paths_sorted_across_windows(tmp_path):
    fetch.fetch_window(FakeClient(rows(3)), api.GROUP_SALES, date(2026, 1, 1), date(2026, 6, 30), tmp_path, take=2)
    fetch.fetch_window(FakeClient(rows(3)), api.GROUP_SALES, date(2026, 7, 1), date(2026, 10, 24), tmp_path, take=2)
    paths = fetch.page_paths(tmp_path, "sales")
    assert [p.parent.name + "/" + p.name for p in paths] == [
        "2026-01-01_2026-06-30/page-0000.json", "2026-01-01_2026-06-30/page-0001.json",
        "2026-07-01_2026-10-24/page-0000.json", "2026-07-01_2026-10-24/page-0001.json",
    ]
