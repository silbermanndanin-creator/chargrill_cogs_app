"""Lightspeed Restaurant (K-Series) revenue pull.

STATUS: drop-in scaffold. The app works today with manual / POS revenue entry.
Once you have K-Series API access you can enable this WITHOUT editing code — just
add the credentials (and, if your portal's path differs, the endpoint overrides)
to .streamlit/secrets.toml or the environment.

To enable:
  1. In Lightspeed Back Office, create an API client / personal access token
     (Settings > Integrations / API). Note your business_id.
  2. Confirm the exact base URL + sales/reports path from your K-Series developer
     portal. If they differ from the defaults below, set them in secrets:
         LIGHTSPEED_TOKEN        = "..."
         LIGHTSPEED_BUSINESS_ID  = "..."
         LIGHTSPEED_BASE         = "https://api.lsk.lightspeed.app"      # optional override
         LIGHTSPEED_REVENUE_PATH = "/resto/financial/v1/{business_id}/sales"  # optional override
         LIGHTSPEED_TOTAL_FIELD  = "total_sales"   # optional: field holding the $ total
  3. (Optional) use the sidebar "Test connection" button to confirm it works.

get_revenue() returns net ex-GST revenue for the date range, or None if not
configured / on any error -> the UI falls back to manual/POS entry. It will never
silently return a wrong number.
"""
import os
import datetime as dt
from typing import Optional
import requests

# Defaults — overridable via secrets/env so no code change is needed to go live.
DEFAULT_BASE = "https://api.lsk.lightspeed.app"
DEFAULT_REVENUE_PATH = "/resto/financial/v1/{business_id}/sales"
GST_DIVISOR = 1.10  # convert GST-inclusive POS sales to ex-GST


def _cfg(name, default=None):
    """Read a setting from the environment (Streamlit copies st.secrets into env)."""
    return os.environ.get(name, default)


def _sum_sales(data, total_field=None):
    """Pull a $ total out of common K-Series response shapes:
    a flat dict, a {'data': {...}} / {'totals': {...}} wrapper, or a list of daily
    rows that we sum. Returns float or None if nothing recognisable is found."""
    candidates = [total_field, "total_sales", "net_sales", "gross_sales",
                  "total", "amount", "value"]
    candidates = [c for c in candidates if c]

    def from_dict(d):
        for k in candidates:
            if k in d and d[k] is not None:
                try:
                    return float(d[k])
                except (TypeError, ValueError):
                    pass
        return None

    if isinstance(data, dict):
        # unwrap a single nested container if present
        for wrap in ("data", "totals", "result", "summary"):
            if isinstance(data.get(wrap), (dict, list)):
                inner = _sum_sales(data[wrap], total_field)
                if inner is not None:
                    return inner
        return from_dict(data)
    if isinstance(data, list):
        total = 0.0
        found = False
        for row in data:
            if isinstance(row, dict):
                v = from_dict(row)
                if v is not None:
                    total += v
                    found = True
        return total if found else None
    return None


def get_revenue(start: dt.date, end: dt.date, token: Optional[str],
                business_id: Optional[str], gross_includes_gst: bool = True) -> Optional[float]:
    if not token or not business_id:
        return None
    base = _cfg("LIGHTSPEED_BASE", DEFAULT_BASE).rstrip("/")
    path = _cfg("LIGHTSPEED_REVENUE_PATH", DEFAULT_REVENUE_PATH).format(business_id=business_id)
    total_field = _cfg("LIGHTSPEED_TOTAL_FIELD")
    try:
        resp = requests.get(
            base + path,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"from": start.isoformat(), "to": end.isoformat()},
            timeout=20,
        )
        resp.raise_for_status()
        gross = _sum_sales(resp.json(), total_field)
        if gross is None:
            return None  # response shape not recognised -> fall back, don't guess
        return round(gross / GST_DIVISOR if gross_includes_gst else gross, 2)
    except Exception:
        return None  # network/auth/parse error -> fall back to manual entry


def lightspeed_status(token: Optional[str], business_id: Optional[str]) -> str:
    """Short human-readable connection status for the UI."""
    if not token or not business_id:
        return "not configured"
    try:
        today = dt.date.today()
        r = get_revenue(today, today, token, business_id)
        return "connected" if r is not None else "configured, but no recognisable sales response"
    except Exception:
        return "error contacting Lightspeed"
