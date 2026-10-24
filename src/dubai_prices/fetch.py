"""Download transaction pages for one date window into a resumable on-disk cache.

Layout: ``raw_dir/<group>/<start>_<end>/page-0000.json`` plus a ``_done.json``
marker once the window is complete. Re-running skips a finished window and
resumes a part-finished one, so an interrupted download never re-fetches a page.

One window per download, not one per month: the API's P_TO_DATE is not a clean
end-of-day bound. Fetching 2026 month by month on 2026-10-24 returned 627 fewer
rows than one request for the whole year, all of them dated on the first or last
day of a month. The day after ``end`` is therefore sent as P_TO_DATE, and the
rows received are checked against the TOTAL the API reports on every row.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .api import GROUP_NAMES

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchResult:
    window: str
    total: int
    pages: int
    rows: int


def window_name(start: date, end: date) -> str:
    return f"{start:%Y-%m-%d}_{end:%Y-%m-%d}"


def _rows(page: dict) -> list[dict]:
    return (page.get("response") or {}).get("result") or []


def fetch_window(client, group_id: str, start: date, end: date, raw_dir: Path, take: int = 2000) -> FetchResult:
    """Fetch every page for start..end (inclusive) into the cache and return the counts."""
    name = GROUP_NAMES[group_id]
    window = window_name(start, end)
    window_dir = raw_dir / name / window
    window_dir.mkdir(parents=True, exist_ok=True)
    marker = window_dir / "_done.json"
    if marker.exists():
        done = json.loads(marker.read_text(encoding="utf-8"))
        log.info("%s %s already complete (%s rows)", name, window, done["rows"])
        return FetchResult(window, done["total"], done["pages"], done["rows"])

    to_date = end + timedelta(days=1)
    n = 0
    rows = 0
    total: int | None = None
    while True:
        path = window_dir / f"page-{n:04d}.json"
        if path.exists():
            page = json.loads(path.read_text(encoding="utf-8"))
        else:
            page = client.fetch_page(group_id, start, to_date, take=take, skip=n * take)
            path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        page_rows = _rows(page)
        if page_rows:
            total = int(page_rows[0].get("TOTAL", total or 0))
            rows += len(page_rows)
            n += 1
            log.info("%s %s page %d: %d rows (total %s)", name, window, n, len(page_rows), total)
        else:
            path.unlink(missing_ok=True)  # never keep an empty page in the cache
        if not page_rows or len(page_rows) < take or (total is not None and rows >= total):
            break

    total = total or 0
    if rows != total:
        log.warning("%s %s: fetched %d rows but the API reported TOTAL=%d", name, window, rows, total)
    result = FetchResult(window, total, n, rows)
    marker.write_text(
        json.dumps({**asdict(result), "fetched_at": datetime.now(timezone.utc).isoformat()}, indent=1),
        encoding="utf-8",
    )
    return result


def page_paths(raw_dir: Path, group_name: str) -> list[Path]:
    """Every cached page for a group, in window then page order."""
    return sorted((raw_dir / group_name).glob("*/page-*.json"))
