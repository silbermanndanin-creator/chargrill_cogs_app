"""Targets and supplier mapping for Chargrill Charlie's (Rose Bay) COGS tracker.

Per-supplier targets are that supplier's share of COGS (from Jun25-Apr26 P&L
actuals) scaled to the total-COGS band: green = 40% of revenue, red = 42%.
Alerts: spend% <= green -> GREEN, <= red -> AMBER, > red -> RED.
"""

GST_RATE = 0.10  # Australian GST

# Total-COGS guardrail band (% of net ex-GST revenue)
TOTAL_COGS_GREEN = 0.40
TOTAL_COGS_RED = 0.42

# Per-supplier targets as a fraction of net ex-GST revenue.
# green_pct = share_of_COGS * 0.40 ; red_pct = share_of_COGS * 0.42
# Order matters: canonicalize() returns the first supplier whose alias matches.
SUPPLIERS = {
    "Baida Chicken (BPL)":  {"aliases": ["bpl", "baida", "baiada"],        "green_pct": 0.129, "red_pct": 0.135},
    "Blueseas (Broadline)": {"aliases": ["blueseas", "blue seas"],          "green_pct": 0.098, "red_pct": 0.102},
    "Cafia Chicken":        {"aliases": ["cafia"],                          "green_pct": 0.015, "red_pct": 0.016},
    "Veggie (StG Foods)":   {"aliases": ["veggie", "veggies", "stg", "veg"],"green_pct": 0.078, "red_pct": 0.082},
    "Meat - Field to Fork": {"aliases": ["field to fork", "meat"],          "green_pct": 0.026, "red_pct": 0.027},
    "Coca Cola":            {"aliases": ["coca", "cola", "coke"],           "green_pct": 0.018, "red_pct": 0.019},
    "Other Food":           {"aliases": ["other"],                          "green_pct": 0.037, "red_pct": 0.039},
}

FALLBACK_SUPPLIER = "Other Food"


def canonicalize(raw_name: str) -> str:
    """Map an extracted supplier name to a canonical category via alias match."""
    n = (raw_name or "").lower()
    for canonical, cfg in SUPPLIERS.items():
        if any(alias in n for alias in cfg["aliases"]):
            return canonical
    return FALLBACK_SUPPLIER


def status_for(spend_pct: float, supplier: str) -> str:
    """Return 'green' | 'amber' | 'red' for a supplier's spend % of revenue."""
    cfg = SUPPLIERS.get(supplier, SUPPLIERS[FALLBACK_SUPPLIER])
    if spend_pct <= cfg["green_pct"]:
        return "green"
    if spend_pct <= cfg["red_pct"]:
        return "amber"
    return "red"


def total_status(cogs_pct: float) -> str:
    if cogs_pct <= TOTAL_COGS_GREEN:
        return "green"
    if cogs_pct <= TOTAL_COGS_RED:
        return "amber"
    return "red"


# ---- Baida tub tracking ----
# Baida invoices list the number of individual CHICKENS (unit "ea"); tubs = chickens / per_tub.
# 'RSPCA' is the welfare standard printed on most lines, so it does NOT identify the
# whole-chicken tub — match the whole tub on 'charcoal'/'whole' and the split tub on 'split'.
# Split is checked first because split lines also say 'RSPCA'. Other cuts (strips, drumsticks,
# flattened FOB) and the deposit line are not tub products. The review screen overrides per line.
BAIDA_SUPPLIER = "Baida Chicken (BPL)"
TUB_TYPES = {
    "Split": {"keywords": ["split"], "per_tub": 12},
    "RSPCA": {"keywords": ["charcoal", "whole"], "per_tub": 8},
}
DEPOSIT_KEYWORD = "deposit"


def tub_type(description) -> str | None:
    """Return 'Split' | 'RSPCA' | None for a Baida line description (Split wins ties)."""
    d = (description or "").lower()
    for name, cfg in TUB_TYPES.items():
        if any(k in d for k in cfg["keywords"]):
            return name
    return None


# ---- POS end-of-day takings ----
# DoorDash & UberEats (both via Deliverect) are recorded at full order value, but the
# platform takes a commission, so the venue nets only (1 - commission). Group avg = 40%.
DELIVERY_COMMISSION = 0.40


def delivery_adjust(total_incl_gst, doordash, ubereats):
    """Net the delivery commission off the day's takings.
    Returns (adjusted_incl_gst, adjusted_ex_gst)."""
    cut = DELIVERY_COMMISSION * ((doordash or 0) + (ubereats or 0))
    adj_incl = (total_incl_gst or 0) - cut
    return round(adj_incl, 2), round(adj_incl / (1 + GST_RATE), 2)
