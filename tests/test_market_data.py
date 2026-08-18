"""
Validation suite for market_data.py live Treasury-yield fetch

Unlike other modules, this one talks to network and degrades *silently by design on failure
parse regression could return wrong dict with nothing to flag it
Mocks requests.get to exercise every branch of fetch_treasury_yields() without real network calls
"""

from unittest.mock import Mock

import requests

from lcr_optimizer import market_data
from lcr_optimizer.market_data import FALLBACK_YIELDS, FALLBACK_YIELDS_AS_OF, fetch_treasury_yields


class _FakeResponse:
    """Minimal stand in for requests
    Response only .text, .raise_for_status()
    ARGS: (constructor): 
        text: str
        ok: bool, if False raise_for_status() raises HTTPError. Default True
    """
    def __init__(self, text: str, ok: bool = True):
        self.text = text
        self._ok = ok

    def raise_for_status(self):
        """Raises: HTTPError if ok=False
        RETURNS: None otherwise"""
        if not self._ok:
            raise requests.exceptions.HTTPError("500 Server Error")


def _entry(bc_1month=None, bc_2year=None) -> str:
    """Builds fake Treasury XML <entry> block, real feed's tag shape"""
    fields = ""
    if bc_1month is not None:
        fields += f'<d:BC_1MONTH m:type="Edm.Double">{bc_1month}</d:BC_1MONTH>'
    if bc_2year is not None:
        fields += f'<d:BC_2YEAR m:type="Edm.Double">{bc_2year}</d:BC_2YEAR>'
    return f"<entry>{fields}</entry>"


def test_happy_path_parses_live_feed(monkeypatch):
    """Well formed feed: both yields parsed, tagged live, requests.get() called once"""
    mock_get = Mock(return_value=_FakeResponse(f"<feed>{_entry(4.20, 3.75)}</feed>"))
    monkeypatch.setattr(market_data.requests, "get", mock_get)

    result = fetch_treasury_yields()

    assert result["UST_1M"] == 0.042
    assert result["UST_2Y"] == 0.0375
    assert result["_source"] == "live:treasury.gov"
    assert mock_get.call_count == 1


def test_partial_feed_backfills_missing_field_from_fallback(monkeypatch):
    """Only BC_1MONTH present -- UST_2Y must backfill from FALLBACK_YIELDS via setdefault(), not drop or raise
    Least obvious behavior in function"""
    mock_get = Mock(return_value=_FakeResponse(f"<feed>{_entry(4.20)}</feed>"))
    monkeypatch.setattr(market_data.requests, "get", mock_get)

    result = fetch_treasury_yields()

    assert result["UST_1M"] == 0.042
    assert result["UST_2Y"] == FALLBACK_YIELDS["UST_2Y"]
    assert result["_source"] == "live:treasury.gov"


def test_current_month_empty_falls_back_to_previous_month(monkeypatch):
    """Current month's feed empty 
    must retry previous month's URL, return that data tagged live, not fall back to hardcoded snapshot"""
    mock_get = Mock(side_effect=[
        _FakeResponse("<feed></feed>"),  # current month: no <entry> at all
        _FakeResponse(f"<feed>{_entry(4.10, 3.60)}</feed>"),  # previous month: valid
    ])
    monkeypatch.setattr(market_data.requests, "get", mock_get)

    result = fetch_treasury_yields()

    assert mock_get.call_count == 2
    first_url = mock_get.call_args_list[0].args[0]
    second_url = mock_get.call_args_list[1].args[0]
    assert first_url != second_url
    assert result["UST_1M"] == 0.041
    assert result["UST_2Y"] == 0.036
    assert result["_source"] == "live:treasury.gov"


def test_network_error_falls_back_with_exception_message(monkeypatch):
    """Network exception must be caught, not propagated 
    falls back to FALLBACK_YIELDS, embeds staleness date + exception message in `_source`"""
    mock_get = Mock(side_effect=requests.exceptions.ConnectionError("no route to host"))
    monkeypatch.setattr(market_data.requests, "get", mock_get)

    result = fetch_treasury_yields()

    assert result["UST_1M"] == FALLBACK_YIELDS["UST_1M"]
    assert result["UST_2Y"] == FALLBACK_YIELDS["UST_2Y"]
    assert FALLBACK_YIELDS_AS_OF in result["_source"]
    assert "no route to host" in result["_source"]


def test_bad_http_status_falls_back(monkeypatch):
    """Non-2xx status must be caught, fall back to FALLBACK_YIELDS, tagged 'fallback'"""
    mock_get = Mock(return_value=_FakeResponse("irrelevant", ok=False))
    monkeypatch.setattr(market_data.requests, "get", mock_get)

    result = fetch_treasury_yields()

    assert result["UST_1M"] == FALLBACK_YIELDS["UST_1M"]
    assert result["UST_2Y"] == FALLBACK_YIELDS["UST_2Y"]
    assert "fallback" in result["_source"]


def test_malformed_feed_with_no_matching_fields_falls_back(monkeypatch):
    """<entry> present (no retry) but no tracked field matches 
    hits function's own `raise ValueError`, not external exception"""
    mock_get = Mock(return_value=_FakeResponse("<feed><entry>no yield fields here</entry></feed>"))
    monkeypatch.setattr(market_data.requests, "get", mock_get)

    result = fetch_treasury_yields()

    assert result["UST_1M"] == FALLBACK_YIELDS["UST_1M"]
    assert result["UST_2Y"] == FALLBACK_YIELDS["UST_2Y"]
    assert "fallback" in result["_source"]


def test_fallback_never_mutates_module_level_dict(monkeypatch):
    """result must stay copy of FALLBACK_YIELDS 
    guards against accidentally returning/mutating shared module dict"""
    mock_get = Mock(side_effect=requests.exceptions.ConnectionError("boom"))
    monkeypatch.setattr(market_data.requests, "get", mock_get)
    snapshot_before = dict(FALLBACK_YIELDS)

    first = fetch_treasury_yields()
    first["UST_1M"] = -999.0  # mutate returned dict
    second = fetch_treasury_yields()

    assert FALLBACK_YIELDS == snapshot_before
    assert second["UST_1M"] == FALLBACK_YIELDS["UST_1M"]
