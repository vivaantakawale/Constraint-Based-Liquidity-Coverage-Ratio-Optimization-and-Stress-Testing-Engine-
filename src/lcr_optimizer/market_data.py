"""
Optional live market-data fetch for the *cost side* of the objective.

The balance sheet and asset universe in data.py are synthetic (no real bank's
numbers), but there's no reason the Treasury yields we price Level 1 assets
off of need to be made up too -- the US Treasury publishes the daily par
yield curve for free, no API key required. This module fetches it; if the
network call fails (offline, firewalled, etc.) it falls back to a hardcoded
snapshot so the rest of the project still runs deterministically.

Source: US Treasury "Daily Treasury Par Yield Curve Rates" XML feed.
https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml
"""

import re
from datetime import datetime, timedelta

import requests

TREASURY_XML_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    "?data=daily_treasury_yield_curve&field_tdr_date_value_month={yyyymm}"
)

# Fallback snapshot (approx late-2025 levels) used if the live fetch fails.
FALLBACK_YIELDS = {
    "UST_1M": 0.042,
    "UST_2Y": 0.038,
}

_FIELD_TO_KEY = {
    "BC_1MONTH": "UST_1M",
    "BC_2YEAR": "UST_2Y",
}


def fetch_treasury_yields(timeout: float = 5.0) -> dict:
    """
    Returns {"UST_1M": 0.0421, "UST_2Y": 0.0383, ...} as decimals.
    Falls back to FALLBACK_YIELDS (with a warning) on any failure.
    """
    yyyymm = datetime.utcnow().strftime("%Y%m")
    url = TREASURY_XML_URL.format(yyyymm=yyyymm)
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        xml = resp.text

        # If the current month has no published data yet (e.g. first day of
        # the month), fall back to the previous month before giving up.
        if "<entry>" not in xml:
            prev_month = (datetime.utcnow().replace(day=1) - timedelta(days=1)).strftime("%Y%m")
            url = TREASURY_XML_URL.format(yyyymm=prev_month)
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            xml = resp.text

        # Take the LAST <entry> in the feed (most recent trading day).
        entries = xml.split("<entry>")
        last_entry = entries[-1]

        yields = {}
        for field, key in _FIELD_TO_KEY.items():
            m = re.search(rf"<d:{field}[^>]*>([\d.]+)</d:{field}>", last_entry)
            if m:
                yields[key] = round(float(m.group(1)) / 100.0, 5)

        if not yields:
            raise ValueError("no matching yield fields found in Treasury feed")

        for key, val in FALLBACK_YIELDS.items():
            yields.setdefault(key, val)
        yields["_source"] = "live:treasury.gov"
        return yields

    except Exception as exc:
        result = dict(FALLBACK_YIELDS)
        result["_source"] = f"fallback (live fetch failed: {exc})"
        return result


if __name__ == "__main__":
    y = fetch_treasury_yields()
    print(y)
