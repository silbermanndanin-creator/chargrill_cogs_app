"""Targets and supplier mapping for Chargrill Charlie's (Rose Bay) COGS tracker.

Per-supplier targets are that supplier's share of COGS (from Jun25-Apr26 P&L
actuals) scaled to the total-COGS band: green = 40% of revenue, red = 42%.
Alerts: spend% <= green -> GREEN, <= red -> AMBER, > red -> RED.
"""

GST_RATE = 0.10  # Australian GST

# Total-COGS guardrail band (% of net ex-GST revenue)
TOTAL_COGS_GREEN = 0.40
TOTAL_COGS_RED = 0.42

# Categories keyed by the real supplier names that appear on invoices.
# green_pct/red_pct = optional per-category COGS target (fraction of net ex-GST revenue);
# omit when not yet calibrated. cogs=False = tracked but NOT counted toward food-COGS %
# (packaging, cleaning). Order matters: canonicalize() returns the first matching category.
SUPPLIERS = {
    "Packaging": {"aliases": ["gleam", "horizon", "a-z packaging", "a z packaging",
                              "az packaging", "a-z paper", "az paper", "paper product",
                              "acr", "cleaning rag"], "cogs": False},
    "Meat":      {"aliases": ["artisian", "artisan butcher", "field to fork",
                              "coogee village butcher", "village butcher"]},
    "Chicken":   {"aliases": ["bpl", "baida", "baiada"], "green_pct": 0.129, "red_pct": 0.135},
    "Potatoes":  {"aliases": ["potato group", "the potato",
                              "south pacific", "south pacific fresh"]},
    "Veggies":   {"aliases": ["st george", "st. george", "saint george", "george food",
                              "veggie", "veggies"], "green_pct": 0.078, "red_pct": 0.082},
    "Luxe":      {"aliases": ["luxe"]},
    "Spices":    {"aliases": ["m&j", "m & j", "m and j", "mj ingredient", "win kwong", "kwong"]},
    "Yalla":     {"aliases": ["yalla"]},
    "Blueseas (Broadline)": {"aliases": ["blueseas", "blue seas"],
                             "green_pct": 0.098, "red_pct": 0.102},
    "Other":     {"aliases": []},
}

FALLBACK_SUPPLIER = "Other"


def canonicalize(raw_name: str) -> str:
    """Map an extracted supplier name to a category via alias match."""
    n = (raw_name or "").lower()
    for canonical, cfg in SUPPLIERS.items():
        if any(alias in n for alias in cfg["aliases"]):
            return canonical
    return FALLBACK_SUPPLIER


def is_cogs(supplier: str) -> bool:
    """Does this category count toward the food-COGS %? (Packaging/cleaning don't.)"""
    return SUPPLIERS.get(supplier, {}).get("cogs", True)


def status_for(spend_pct, supplier):
    """'green'|'amber'|'red' vs the category's target, or None if untargeted."""
    cfg = SUPPLIERS.get(supplier)
    if not cfg or cfg.get("green_pct") is None:
        return None
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
BAIDA_SUPPLIER = "Chicken"
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


# ---- Veggie item price tracking (St George Food invoices) ----
# Main produce lines whose unit price we track over time. Order matters: more specific
# names first (e.g. Broccolini before Broccoli, Eggplant before Eggs, Sweet Potato before
# generic). Unit price per line = amount / quantity.
VEGGIES_SUPPLIER = "Veggies"
TRACKED_VEGGIE_ITEMS = {
    "Avocado":            ["avocado"],
    "Broccolini":         ["broccolini"],
    "Broccoli 8kg":       ["broccoli"],
    "Brussel Sprouts":    ["brussel"],
    "Cabbage":            ["cabbage"],
    "Lebanese Cucumber":  ["lebanese cucumber", "lebanese", "cucumber"],
    "Cos Lettuce":        ["cos lettuce"],
    "Iceberg Lettuce":    ["iceberg"],
    "Eggplant":           ["eggplant", "egg plant"],
    "Eggs 700g":          ["egg"],
    "Red Spanish Onions": ["spanish onion", "red spanish", "spanish"],
    "Sweet Potato":       ["sweet potato"],
    "Zucchini":           ["zucchini"],
    "Beans 10kg":         ["bean"],
    "Carrot XXL":         ["carrot"],
    "Cauliflower":        ["cauliflower", "cauli"],
}


def veggie_item(description) -> str | None:
    """Match a line description to a tracked veggie item (first match wins)."""
    d = (description or "").lower()
    for name, kws in TRACKED_VEGGIE_ITEMS.items():
        if any(k in d for k in kws):
            return name
    return None
