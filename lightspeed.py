"""Lightspeed Restaurant (K-Series) revenue pull.

STATUS: scaffold. The app works today with manual revenue entry; wire this up
once you have K-Series API access.

To enable:
  1. In Lightspeed Back Office, create an API client / personal access token
     (Settings > Integrations / API). Note your business_id.
  2. Confirm the exact reports endpoint + base URL from your Lightspeed developer
     portal (K-Series API). Fill in REVENUE_ENDPOINT below.
  3. Put credentials in .streamlit/secrets.toml (see secrets.toml.example).

get_revenue() returns net ex-GST revenue for the date range, or None if not
configured / on any error -> the UI then falls back to manual entry. It will
never silently return a wrong number.
"""
import datetime as dt
from typing import Optional
import requests

LSK_BASE = "https://api.lsk.lightspeed.app"   # TODO: verify against your portal
REVENUE_ENDPOINT = "/resto/financial/v1/{business_id}/sales"  # TODO: verify path/params
GST_DIVISOR = 1.10  # convert GST-inclusive POS sales to ex-GST


def get_revenue(start: dt.date, end: dt.date, token: Optional[str],
                business_id: Optional[str], gross_includes_gst: bool = True) -> Optional[float]:
    if not token or not business_id:
        return None
    try:
        url = LSK_BASE + REVENUE_ENDPOINT.format(business_id=business_id)
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"from": start.isoformat(), "to": end.isoformat()},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        # TODO: map this to the actual K-Series response shape from your portal.
        gross = float(data.get("total_sales") or data.get("net_sales") or 0)
        return round(gross / GST_DIVISOR if gross_includes_gst else gross, 2)
    except Exception:
        return None  # fall back to manual entry; do not guess revenue
