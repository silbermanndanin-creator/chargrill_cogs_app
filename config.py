"""Targets and supplier mapping for Chargrill Charlie's (Rose Bay) COGS tracker.

Per-supplier targets are that supplier's share of COGS (from Jun25-Apr26 P&L
actuals) scaled to the total-COGS band: green = 40% of revenue, red = 42%.
Alerts: spend% <= green -> GREEN, <= red -> AMBER, > red -> RED.
"""

import datetime as dt

GST_RATE = 0.10  # Australian GST

# Total-COGS guardrail band (% of net ex-GST revenue)
TOTAL_COGS_GREEN = 0.40
TOTAL_COGS_RED = 0.42

# Labour guardrail band (% of net ex-GST revenue). Tracks GROSS WAGES (Tanda).
LABOUR_GREEN = 0.28
LABOUR_RED = 0.30   # 2-pt amber band mirroring the COGS 40/42 band; widen if needed

# Prime cost = food COGS + labour. Because both are shares of the same revenue,
# the prime-cost target is exactly the sum of the COGS and labour targets
# (green 68%, red 72%) — it auto-follows if you change either band above.
PRIME_GREEN = TOTAL_COGS_GREEN + LABOUR_GREEN
PRIME_RED = TOTAL_COGS_RED + LABOUR_RED

# Categories keyed by the real supplier names that appear on invoices.
# green_pct/red_pct = optional per-category COGS target (fraction of net ex-GST revenue);
# omit when not yet calibrated. cogs=False = tracked but NOT counted toward food-COGS %
# (packaging, cleaning). Order matters: canonicalize() returns the first matching category.
# senders = optional list of extra email domains/addresses this supplier mails from, used
# by supplier_for_sender() to let the inbox process their mail (and skip everyone else's)
# WITHOUT a Claude read. Aliases are matched too, so add `senders` only when the sending
# domain doesn't contain the supplier's name (e.g. "senders": ["bidfood.com.au"]).
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
    # Non-food supplier: excluded from food-COGS % (cogs=False) but still counted in
    # BAS (bas_summary sums all invoice spend regardless of category).
    "Lotus Commercial": {"aliases": ["lotus commercial", "lotus"], "cogs": False},
    # Drinks for resale (Coca Cola Amatil). cogs=False so it doesn't distort the
    # food-calibrated COGS % band — still tracked + counted in BAS. Flip cogs to True
    # to fold beverages into the COGS % figure.
    "Drinks":    {"aliases": ["coca cola", "coca-cola", "ccamatil", "amatil"], "cogs": False},
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


def supplier_for_sender(addr: str):
    """Canonical supplier this email address belongs to, or None if the sender isn't a
    known supplier. Lets the inbox skip a (paid) Claude read on mail from anyone we're
    not tracking — only our suppliers' emails get the full extraction pipeline.

    Matches each category's aliases (and any extra addresses/domains in its optional
    `senders` list) as substrings of the sender address. Separators are stripped too, so
    a multi-word name like 'st george' matches a domain such as 'stgeorgefoods.com.au'.
    To onboard a supplier whose sending domain doesn't contain its name, add the domain
    (or full address) to that category's `senders` list above — no code change needed."""
    full = (addr or "").strip().lower().strip("<>")
    if "@" not in full:
        return None
    squashed = full.replace(".", "").replace("-", "").replace("_", "")
    for canonical, cfg in SUPPLIERS.items():
        for needle in list(cfg.get("aliases", [])) + list(cfg.get("senders", [])):
            nd = needle.lower().strip()
            if nd and (nd in full or nd.replace(" ", "") in squashed):
                return canonical
    return None


def is_cogs(supplier: str) -> bool:
    """Does this category count toward the food-COGS %? (Packaging/cleaning don't.)"""
    return SUPPLIERS.get(supplier, {}).get("cogs", True)


# ---- Delivery-date bucketing ----
# Some suppliers' invoice is the ORDER, placed ahead of delivery. BPL (Chicken) is
# ordered on Saturday for Monday delivery, so a weekend order should count in the
# DELIVERY week/month, not the order week. Spend, tubs and trends bucket by this date.
DELIVERY_SHIFT_SUPPLIERS = {"Chicken"}


def effective_date(d, supplier):
    """Date used for week/month bucketing. For order-ahead suppliers, a weekend
    order (Sat/Sun) is shifted forward to the Monday it is delivered. Weekday
    invoices are left on their own date."""
    if supplier in DELIVERY_SHIFT_SUPPLIERS:
        wd = d.weekday()  # Mon=0 … Fri=4, Sat=5, Sun=6
        if wd == 5:       # Saturday -> Monday (+2)
            return d + dt.timedelta(days=2)
        if wd == 6:       # Sunday -> Monday (+1)
            return d + dt.timedelta(days=1)
    return d


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


def labour_status(labour_pct: float) -> str:
    if labour_pct <= LABOUR_GREEN:
        return "green"
    if labour_pct <= LABOUR_RED:
        return "amber"
    return "red"


def prime_status(prime_pct: float) -> str:
    if prime_pct <= PRIME_GREEN:
        return "green"
    if prime_pct <= PRIME_RED:
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


# ---- Baida order-vs-turnover guide (DANIN/baida order.xlsx — winter weekly) ----
# For a week's gross sales ($ incl GST), the recommended number of whole/charcoal
# birds ('RSPCA' tubs) and split chickens. Used to flag when an order runs high.
# (sales, whole_birds, split_chickens)
BAIDA_ORDER_GUIDE = [
    (65000, 520, 192), (70000, 560, 204), (75000, 600, 216),
    (80000, 664, 228), (85000, 720, 240), (90000, 736, 252),
]
BAIDA_OVER_PCT = 0.10  # flag when actual chickens exceed the guide by >10%


def baida_recommended(weekly_sales):
    """(whole_birds, split_chickens) recommended for a week's gross sales, linearly
    interpolated within the guide and clamped to its range. None if no/zero sales."""
    g = BAIDA_ORDER_GUIDE
    if not g or not weekly_sales or weekly_sales <= 0:
        return None
    if weekly_sales <= g[0][0]:
        return (float(g[0][1]), float(g[0][2]))
    if weekly_sales >= g[-1][0]:
        return (float(g[-1][1]), float(g[-1][2]))
    for (s0, b0, sp0), (s1, b1, sp1) in zip(g, g[1:]):
        if s0 <= weekly_sales <= s1:
            t = (weekly_sales - s0) / (s1 - s0)
            return (b0 + t * (b1 - b0), sp0 + t * (sp1 - sp0))
    return (float(g[-1][1]), float(g[-1][2]))


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
