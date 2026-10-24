"""Minimal, polite client for the Dubai Land Department open-data transactions endpoint.

The endpoint is the one behind https://dubailand.gov.ae/en/open-data/real-estate-data/
(the Transactions tab). It answers without an account, but only for the current
calendar year. Dates in the request body are MM/DD/YYYY strings.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date

import requests

URL = "https://gateway.dubailand.gov.ae/open-data/transactions"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://dubailand.gov.ae",
    "Referer": "https://dubailand.gov.ae/en/open-data/real-estate-data/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
}

GROUP_SALES = "1"
GROUP_MORTGAGE = "2"
GROUP_GIFTS = "3"
GROUP_NAMES = {GROUP_SALES: "sales", GROUP_MORTGAGE: "mortgages", GROUP_GIFTS: "gifts"}

SORT = "TRANSACTION_NUMBER_ASC"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class DLDError(Exception):
    """The API refused the request or kept failing."""


def build_body(group_id: str, from_date: date, to_date: date, take: int, skip: int) -> dict[str, str]:
    """Request body exactly as the DLD web page sends it (every value a string)."""
    return {
        "P_FROM_DATE": from_date.strftime("%m/%d/%Y"),
        "P_TO_DATE": to_date.strftime("%m/%d/%Y"),
        "P_GROUP_ID": group_id,
        "P_IS_OFFPLAN": "",
        "P_IS_FREE_HOLD": "",
        "P_AREA_ID": "",
        "P_USAGE_ID": "",
        "P_PROP_TYPE_ID": "",
        "P_TAKE": str(take),
        "P_SKIP": str(skip),
        "P_SORT": SORT,
    }


class DLDClient:
    """One request at a time, a pause between requests, retries with backoff."""

    def __init__(
        self,
        session: requests.Session | None = None,
        pause_seconds: float = 1.0,
        max_retries: int = 4,
        timeout: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.pause_seconds = pause_seconds
        self.max_retries = max_retries
        self.timeout = timeout
        self.sleep = sleep
        self._made_a_request = False

    def fetch_page(self, group_id: str, from_date: date, to_date: date, take: int, skip: int) -> dict:
        body = build_body(group_id, from_date, to_date, take, skip)
        if self._made_a_request and self.pause_seconds:
            self.sleep(self.pause_seconds)
        self._made_a_request = True

        last_error: str = "no attempts made"
        for attempt in range(self.max_retries):
            if attempt:
                self.sleep(2 ** (attempt - 1))
            try:
                response = self.session.post(URL, json=body, headers=HEADERS, timeout=self.timeout)
            except requests.RequestException as exc:  # network hiccup: retry
                last_error = f"request failed: {exc}"
                continue
            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code != 200:
                raise DLDError(f"HTTP {response.status_code} for {body}")
            payload = response.json()
            if payload.get("responseCode") != 200:
                raise DLDError(
                    f"responseCode {payload.get('responseCode')}: {payload.get('validationErrorsList')}"
                )
            return payload
        raise DLDError(f"gave up after {self.max_retries} attempts ({last_error}) for {body}")
