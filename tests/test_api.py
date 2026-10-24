from datetime import date

import pytest

from dubai_prices import api


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """Returns the queued responses in order; records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


GOOD = {"responseCode": 200, "validationErrorsList": [], "response": {"result": [{"TOTAL": 1}]}}


def test_build_body_formats_dates_mmddyyyy():
    body = api.build_body(api.GROUP_SALES, date(2026, 1, 5), date(2026, 10, 24), take=2000, skip=4000)
    assert body["P_FROM_DATE"] == "01/05/2026"
    assert body["P_TO_DATE"] == "10/24/2026"
    assert body["P_GROUP_ID"] == "1"
    assert body["P_TAKE"] == "2000"
    assert body["P_SKIP"] == "4000"
    assert body["P_SORT"] == "TRANSACTION_NUMBER_ASC"
    assert all(isinstance(v, str) for v in body.values())


def test_fetch_page_retries_on_5xx_then_succeeds():
    session = FakeSession([FakeResponse(503), FakeResponse(502), FakeResponse(200, GOOD)])
    sleeps = []
    client = api.DLDClient(session=session, pause_seconds=0, max_retries=4, sleep=sleeps.append)
    page = client.fetch_page(api.GROUP_SALES, date(2026, 1, 1), date(2026, 1, 31), take=10, skip=0)
    assert page == GOOD
    assert len(session.calls) == 3
    assert session.calls[0]["headers"]["Origin"] == "https://dubailand.gov.ae"
    assert sleeps == [1, 2]  # exponential backoff between attempts


def test_fetch_page_raises_after_max_retries():
    session = FakeSession([FakeResponse(503)] * 3)
    client = api.DLDClient(session=session, pause_seconds=0, max_retries=3, sleep=lambda s: None)
    with pytest.raises(api.DLDError):
        client.fetch_page(api.GROUP_SALES, date(2026, 1, 1), date(2026, 1, 31), take=10, skip=0)
    assert len(session.calls) == 3


def test_fetch_page_rejects_bad_response_code():
    bad = {"responseCode": 400, "validationErrorsList": ["bad date"], "response": None}
    session = FakeSession([FakeResponse(200, bad)])
    client = api.DLDClient(session=session, pause_seconds=0, max_retries=1, sleep=lambda s: None)
    with pytest.raises(api.DLDError, match="bad date"):
        client.fetch_page(api.GROUP_SALES, date(2026, 1, 1), date(2026, 1, 31), take=10, skip=0)


def test_pause_between_successful_calls():
    session = FakeSession([FakeResponse(200, GOOD), FakeResponse(200, GOOD)])
    sleeps = []
    client = api.DLDClient(session=session, pause_seconds=1.5, max_retries=1, sleep=sleeps.append)
    client.fetch_page(api.GROUP_SALES, date(2026, 1, 1), date(2026, 1, 31), take=10, skip=0)
    client.fetch_page(api.GROUP_SALES, date(2026, 1, 1), date(2026, 1, 31), take=10, skip=10)
    assert sleeps == [1.5]  # pause before the second request only
