"""Tanda workforce labour-cost pull (gross wages).

STATUS: scaffold. The app works today with manual labour entry; wire this up
once you have a Tanda API token.

To enable:
  1. In Tanda (my.tanda.co) -> Settings -> API / Integrations, create a Personal
     Access Token (or OAuth app) with timesheet + cost read access. Note your
     business/organisation id if your account spans multiple sites.
  2. Confirm the timesheets endpoint, date params and the cost field name from
     Tanda's API docs (https://my.tanda.co/api/v2). Fill in TANDA_BASE /
     TIMESHEETS_ENDPOINT below.
  3. Put credentials in .streamlit/secrets.toml:
        TANDA_TOKEN = "..."
        TANDA_BUSINESS_ID = ""   # optional

get_labour_cost() returns GROSS WAGES for the date range, or None if not
configured / on any error -> the UI then falls back to manual entry. It will
never silently return a wrong number.
"""
import datetime as dt
from typing import Optional
import requests

TANDA_BASE = "https://my.tanda.co/api/v2"      # TODO: verify against your Tanda API docs
TIMESHEETS_ENDPOINT = "/timesheets"            # TODO: verify path/params + cost field


def get_labour_cost(start: dt.date, end: dt.date, token: Optional[str],
                    business_id: Optional[str] = None) -> Optional[float]:
    """Sum gross wage cost across the date range, or None if not configured/on error."""
    if not token:
        return None
    try:
        url = TANDA_BASE + TIMESHEETS_ENDPOINT
        # Tanda uses unix timestamps for date ranges. Confirm param names in your portal.
        params = {
            "from": int(dt.datetime.combine(start, dt.time.min).timestamp()),
            "to": int(dt.datetime.combine(end, dt.time.max).timestamp()),
        }
        if business_id:
            params["business_id"] = business_id
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        # TODO: map this to Tanda's actual response shape. Sum the gross wage cost
        # across shifts in the range. The cost field name varies by account/version,
        # so confirm it against your portal before trusting this number.
        timesheets = data if isinstance(data, list) else data.get("timesheets", [])
        total = 0.0
        for ts in timesheets:
            for shift in ts.get("shifts", []):
                total += float(shift.get("cost") or 0)
        return round(total, 2) if total else None
    except Exception:
        return None  # fall back to manual entry; do not guess labour
