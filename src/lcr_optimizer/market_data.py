"""
Optional live market data fetch for cost side of objective

Falls back to hardcoded snapshot on network failure

Source: US Treasury "Daily Treasury Par Yield Curve Rates" XML feed.
https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml
"""

import re
from datetime import datetime, timedelta, timezone

import requests

TREASURY_XML_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    "?data=daily_treasury_yield_curve&field_tdr_date_value_month={yyyymm}"
)

# Fallback snapshot used if the live fetch fails
# Captured date mentioned

FALLBACK_YIELDS_AS_OF = "2025-12"
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
    Fetches latest 1-month/2-year US Treasury par yields from Treasury.gov XML feed
    retries previous month if current empty
    falls back to FALLBACK_YIELDS on network error/bad status/unparseable feed

    ARGS: timeout: float - seconds before requests.get() times out, default 5.0
    RETURNS: dict[str, float|str] - "UST_1M"/"UST_2Y": float decimal yield
        "_source": str, "live:treasury.gov" or fallback message with exception detail
    """
    now = datetime.now(timezone.utc)
    yyyymm = now.strftime("%Y%m")
    url = TREASURY_XML_URL.format(yyyymm=yyyymm)
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        xml = resp.text

        # If current month has no published data yet fall back to previous month before giving up
        if "<entry>" not in xml:
            prev_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m")
            url = TREASURY_XML_URL.format(yyyymm=prev_month)
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            xml = resp.text

        # Take LAST <entry> in feed (most recent trading day)
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
        result["_source"] = (
            f"fallback as of {FALLBACK_YIELDS_AS_OF} (live fetch failed: {exc})"
        )
        return result


if __name__ == "__main__":
    y = fetch_treasury_yields()
    print(y)
