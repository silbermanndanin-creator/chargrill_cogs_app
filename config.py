"""Targets and supplier mapping for Chargrill Charlie's (Rose Bay) COGS tracker.

Per-supplier targets are that supplier's share of COGS (from Jun25-Apr26 P&L
actuals) scaled to the total-COGS band: green = 40% of revenue, red = 42%.
Alerts: spend% <= green -> GREEN, <= red -> AMBER, > red -> RED.
"""

import datetime as dt
import re

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
# For a week's EX-GST sales — with delivery at full order value (the platform commission
# is NOT netted off, which is how the spreadsheet was built) — the recommended number of
# whole/charcoal birds ('RSPCA' tubs) and split chickens. Used to flag when an order runs
# high. (sales_ex_gst, whole_birds, split_chickens)
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


# ---- Baida cut tracking (order guide: aimed vs actual quantities per cut) ----
# Real Baida invoice lines look like "FRESH RSPCA CHICKEN CHARCOAL BULK SZ 16 CRTE",
# "...SPLIT CHICKEN 1.1KG CRT", "...DRUMSTICKS BULK 12KG CRTE", "...CHICKEN STRIPS/TLOIN...",
# "CGRILL CHARLIES FLATTENED FOB 171-190G 15KG CRT" (large) / "...FOB 95-125G 11.2KG CRT"
# (small). The order guide buckets each line into one of these cuts and compares the week's
# actual quantity to the aimed quantity (learned per $ of sales). 'whole' is an alias for the
# charcoal bird. NB this is broader than TUB_TYPES (which only splits whole vs split tubs).
def baida_cut(description) -> str | None:
    """Bucket a Baida line into a cut: Whole/Charcoal | Split | Drums | Strips | Flat-L |
    Flat-S. None for non-cut lines (e.g. the TUB DEPOSIT). Specific cuts win over generic."""
    d = (description or "").lower()
    if "split" in d:
        return "Split"
    if "charcoal" in d or "whole" in d:
        return "Whole/Charcoal"
    if "drumstick" in d:
        return "Drums"
    if "strip" in d or "tloin" in d or "tenderloin" in d:
        return "Strips"
    if "flattened" in d or "flat" in d:
        if "171" in d or "190" in d:
            return "Flat-L"           # FLATTENED FOB 171-190G (larger bird)
        if "95" in d or "125" in d or "11.2" in d:
            return "Flat-S"           # FLATTENED FOB 95-125G (smaller bird)
        return "Flat"
    return None


# ---- Blueseas (Broadline) main-item tracking (order guide) ----
# Blueseas is a broadline distributor (hundreds of SKUs); the order guide tracks only the
# highest-volume "main" items where over-ordering moves the needle. Keyword-matched against
# the printed description; first match wins, so list more specific names first.
BLUESEAS_SUPPLIER = "Blueseas (Broadline)"
BLUESEAS_MAINS = {
    "Chips":               ["chips"],
    "Sweet Potato Wedges": ["sweet potato"],
    "Salmon":              ["salmon"],
    "Cream":               ["cream"],
    "Soya Beans":          ["soya"],
    "Chicken Wings":       ["wings"],
    "Breadcrumbs":         ["breadcrumb"],
    "Tomato Sauce":        ["tomato sauce"],
    "Mayo":                ["mayo"],
    "Cottonseed Oil":      ["cottonseed"],
    "Mozzarella":          ["mozz"],
    "Milk":                ["milk"],
}


def blueseas_main(description) -> str | None:
    """Match a Blueseas line to a tracked main item (first match wins); None otherwise."""
    d = (description or "").lower()
    for name, kws in BLUESEAS_MAINS.items():
        if any(k in d for k in kws):
            return name
    return None


# ---- Pack-size normalisation for price-rise detection ----
# Some lines are billed per multi-unit CONTAINER (a carton/case/box/pack) while the
# SAME product is billed per single unit on another delivery. Comparing the raw printed
# unit price across those two deliveries reads the basis change as a price spike — e.g.
# $28.75 per 2.5 kg pack one week vs $172.50 per carton-of-6 the next is a phantom +500%,
# not a real rise. To compare like for like, the price-rise detector (metrics.py) divides
# each line's unit price down to a TRUE per-single-unit price using the inner-unit count
# resolved here.
#
# units_per_pack() returns how many single sellable units one BILLED unit contains:
#   1  -> already per single unit (the default; leaves the price untouched)
#   N  -> billed per N-unit container; the price is divided by N before comparison
#
# It is deliberately conservative so a GENUINE rise is never masked. The count comes from,
# in order: the line's explicit pack_size (captured at extraction from a "CTN-6"/"CTN-12"
# UOM code — the reliable source on these Blue Seas invoices, since the unit itself is
# normalised to "carton" and loses the number); then the UOM string when it still carries
# the count; then a count printed in the description ("6 x 2.5kg"); then an owner-curated
# override. A clear single unit (ea / kg / litre / ...) always returns 1, so a real
# per-each delivery is never divided, and an unrecognised pack stays at 1 (manual review).

# Units that denote a single sellable unit — these are never divided.
SINGLE_UNITS = {"ea", "each", "unit", "units", "kg", "g", "gram", "grams",
                "l", "litre", "liter", "ml", "dozen", "doz"}

# Owner-curated pack sizes for the rare item whose UOM and description BOTH omit the pack
# count. Keyed by a lowercase description keyword. Most pack lines never reach this —
# their UOM already carries the number (CTN-6, MUSTARD-6, ...) — so this stays small.
PACK_UNITS_OVERRIDES = {
    "mustard seeded": 6,  # Mustard Seeded 2.5kg billed per 6-pack ($115.98 / 6 = $19.33)
    "hard boiled": 6,     # Eggs Chilled Hard Boiled 2.5kg, carton of 6 ($172.50 / 6 = $28.75)
}

# A UOM is a pack code when it carries a pack indicator: a dash (CTN-6, MUSTARD-6) or a
# pack word (ctn / carton / case / box / pk / pack / tray / bag / pcs). The number in such
# a code is the inner-unit count. Requiring an indicator stops a bare size like "20l"
# being mistaken for a 20-pack.
_UOM_PACK_HINT = re.compile(r"-|ctn|carton|case|box|pk|pkt|pack|pcs|tray|bag")

# Pack count printed in a description: "ctn 6", "carton of 12", "6 x 2.5kg", "6pk",
# "6 pack", "x12". The captured number is the count of inner units.
_PACK_PATTERNS = (
    re.compile(r"(?:ctn|carton|case|box)\s*(?:of\s*)?(\d{1,3})\b"),
    re.compile(r"\b(\d{1,3})\s*x\s*\d"),          # "6 x 2.5kg"
    re.compile(r"\b(\d{1,3})\s*(?:pk|pkt|pack)\b"),
    re.compile(r"\bx\s*(\d{1,3})\b"),             # "...x12"
)


def _uom_pack_count(unit) -> int | None:
    """Inner-unit count encoded in a pack UOM code (CTN-6, CTN-12, MUSTARD-6, 6PK, ...),
    or None when the unit carries no pack indicator. Returns the first sane number
    (1 < n <= 144) so a real per-each line ('ea'/'kg') is never divided."""
    u = str(unit or "").strip().lower()
    if not _UOM_PACK_HINT.search(u):
        return None  # no pack indicator -> a stray size number is not a pack count
    for raw in re.findall(r"\d{1,3}", u):
        n = int(raw)
        if 1 < n <= 144:
            return n
    return None


def _parse_pack_count(text) -> int | None:
    """First sane pack count (1 < n <= 144) found in `text`, else None. Punctuation is
    flattened to spaces first so 'CTN-6' / '6pk' / '6 x 2.5kg' all parse."""
    t = re.sub(r"[^a-z0-9.]+", " ", str(text or "").lower())
    for pat in _PACK_PATTERNS:
        m = pat.search(t)
        if m:
            n = int(m.group(1))
            if 1 < n <= 144:
                return n
    return None


def _sane_count(value) -> int | None:
    """value as an inner-unit count (1 < n <= 144), else None."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 1 < n <= 144 else None


def units_per_pack(description, unit=None, pack_size=None) -> int:
    """Inner single-unit count for one billed line (>= 1). 1 means the line is already
    priced per single unit. See the module note above — used only by the price-rise
    detector to put every delivery on a true per-unit basis before comparing."""
    n = _sane_count(pack_size)  # explicit pack size captured at extraction (CTN-6 -> 6)
    if n:
        return n
    u = str(unit or "").strip().lower()
    if u in SINGLE_UNITS:
        return 1  # billed per single unit -> never divide (protects genuine per-each lines)
    n = _uom_pack_count(u) or _parse_pack_count(f"{u} {description or ''}")
    if n:
        return n
    d = f"{u} {str(description or '').lower()}"
    for key, cnt in PACK_UNITS_OVERRIDES.items():
        if key in d:
            return cnt
    return 1  # unknown pack size -> leave unchanged (manual review)
