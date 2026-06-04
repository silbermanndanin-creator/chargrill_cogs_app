"""Drinks ordering — second 'Ordering' section.

Source of truth is the DANIN drinks stocktake sheet (drink.xlsx). Unlike packaging,
the sheet has NO row colours, so there is no supplier split and no highlighted
categories — it's a single order list. The blank-row groupings in the sheet are kept
as display "sections" (390ml cans, 600ml bottles, etc.) to make counting/checking easy.

Each item has:
  - par     : the normal FULL/target quantity to hold ("Qnty Needed" column)
  - ph_par  : the FULL/target quantity for a PUBLIC HOLIDAY week
              ("IF PUBLIC HOLIDAY ORDER Qnty Needed" column)
  - section : the sheet's visual grouping (for display only)

Ordering rule (same as packaging): order qty = max(0, ceil(par - on_hand)), where par
is the normal par, or ph_par when there's a public holiday in the coming week.
"""
import math
import datetime as dt

AU_STATES = ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]

# Display order for the sheet's natural (blank-row separated) groupings.
SECTION_ORDER = [
    "390ml Cans",
    "600ml Bottles",
    "Fuze Tea",
    "Powerade",
    "1.25L & 1.5L",
    "Juice",
]

# (item, par, ph_par, section) — order matches the sheet (rows 5-39).
_ITEMS = [
    # --- 390ml cans (rows 5-9) ---
    ("Coke 390ml",              5,  6, "390ml Cans"),
    ("Coke Zero 390ml",         8, 10, "390ml Cans"),
    ("Diet Coke 390ml",         2,  3, "390ml Cans"),
    ("Sprite 390ml",            2,  3, "390ml Cans"),
    ("Fanta 390ml",             2,  3, "390ml Cans"),
    # --- 600ml bottles + water (rows 11-21) ---
    ("Coke 600ml",              6,  8, "600ml Bottles"),
    ("Coke Zero 600ml",         7, 10, "600ml Bottles"),
    ("Diet Coke 600ml",         2,  2, "600ml Bottles"),
    ("Vanilla Coke Zero 600ml", 2,  2, "600ml Bottles"),
    ("Fanta 600ml",             3,  3, "600ml Bottles"),
    ("Sprite 600ml",            3,  3, "600ml Bottles"),
    ("Sprite Zero 600ml",       2,  2, "600ml Bottles"),
    ("Fanta Lemon 600ml",       2,  2, "600ml Bottles"),
    ("Pasito 600ml",            2,  2, "600ml Bottles"),
    ("Sparkling Water",         2,  3, "600ml Bottles"),
    ("Water",                   7,  9, "600ml Bottles"),
    # --- Fuze Tea (rows 23-25) ---
    ("Peach Fuze Tea",          3,  3, "Fuze Tea"),
    ("Lemon Fuze Tea",          2,  2, "Fuze Tea"),
    ("Mango Fuze Tea",          3,  3, "Fuze Tea"),
    # --- Powerade (rows 27-31) — no public-holiday uplift ---
    ("Purple Powerade",         2,  2, "Powerade"),
    ("Blue Powerade",           3,  3, "Powerade"),
    ("Yellow Powerade",         2,  2, "Powerade"),
    ("Red Powerade",            3,  3, "Powerade"),
    ("Orange Powerade",         2,  2, "Powerade"),
    # --- large bottles (rows 34-36) ---
    ("Coke 1.25L",              2,  2, "1.25L & 1.5L"),
    ("Coke Zero 1.25L",         2,  3, "1.25L & 1.5L"),
    ("Water 1.5L",              2,  2, "1.25L & 1.5L"),
    # --- juice (rows 38-39) ---
    ("Apple Juice",             2,  3, "Juice"),
    ("Orange Juice",            2,  3, "Juice"),
]

# Public list of dicts, in sheet order.
DRINK_ITEMS = [
    {"item": it, "par": par, "ph_par": ph_par, "section": sec}
    for (it, par, ph_par, sec) in _ITEMS
]


def par_for(item, public_holiday=False):
    """The active par for an item dict, honouring the public-holiday uplift."""
    return item["ph_par"] if public_holiday else item["par"]


def order_qty(par, on_hand):
    """Units to order to refill to par, rounded UP. Never negative."""
    try:
        oh = float(on_hand or 0)
    except (TypeError, ValueError):
        oh = 0.0
    return max(0, math.ceil(float(par) - oh))


def build_order(counts, public_holiday=False):
    """Turn an {item: on_hand} map into the order, grouped by section.

    When public_holiday is True, each drink refills to its ph_par instead of par.
    Returns {section: [{"item", "par", "on_hand", "order"}, ...]} in SECTION_ORDER,
    only including items with order > 0 and dropping empty sections.
    """
    counts = counts or {}
    out = {sec: [] for sec in SECTION_ORDER}
    for row in DRINK_ITEMS:
        par = par_for(row, public_holiday)
        oh = counts.get(row["item"])
        qty = order_qty(par, oh)
        if qty <= 0:
            continue
        out.setdefault(row["section"], []).append(
            {"item": row["item"], "par": par,
             "on_hand": float(oh or 0), "order": qty})
    return {sec: items for sec, items in out.items() if items}


def order_text(order_by_section, public_holiday=False):
    """Plain-text drinks order, grouped by section."""
    head = "Drinks order" + (" (PUBLIC HOLIDAY quantities)" if public_holiday else "")
    lines = [head, ""]
    if not order_by_section:
        lines.append("(nothing to order)")
        return "\n".join(lines)
    for sec in SECTION_ORDER:
        items = order_by_section.get(sec)
        if not items:
            continue
        lines.append(f"{sec}:")
        for e in items:
            lines.append(f"  {e['order']:g} x {e['item']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def public_holidays_within(state, start, days=7):
    """List of (date, name) public holidays in [start, start+days] for an AU state.

    Returns [] if the `holidays` library isn't available (auto-detect simply degrades
    to the manual toggle) or the state is unknown.
    """
    try:
        import holidays as _holidays
    except Exception:
        return []
    if state not in AU_STATES:
        return []
    end = start + dt.timedelta(days=days)
    try:
        cal = _holidays.Australia(subdiv=state, years=range(start.year, end.year + 1))
    except Exception:
        return []
    hits = [(d, cal.get(d)) for d in cal if start <= d <= end]
    return sorted(hits)


def next_public_holiday(state, start, days=7):
    """(detected: bool, name: str|None, date: date|None) for the soonest public
    holiday within the window — drives the auto-detect default for the toggle."""
    hits = public_holidays_within(state, start, days)
    if not hits:
        return False, None, None
    d, name = hits[0]
    return True, name, d
