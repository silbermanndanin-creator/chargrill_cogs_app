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

# (item, par, ph_par, section, seq)
#   par/ph_par  : normal & public-holiday target quantities (from the sheet)
#   section     : the sheet's grouping (kept for reference)
#   seq         : position on the Coca-Cola (CCEP) ordering site's "Frequently Ordered"
#                 page, so the produced order follows that site top-to-bottom for easy
#                 add-to-cart. Site products we don't stock are simply not listed here.
_ITEMS = [
    # --- 390ml cans (rows 5-9) ---
    ("Coke 390ml",              5,  6, "390ml Cans",     2),
    ("Coke Zero 390ml",         8, 10, "390ml Cans",     5),
    ("Diet Coke 390ml",         2,  3, "390ml Cans",     8),
    ("Sprite 390ml",            2,  3, "390ml Cans",    13),
    ("Fanta 390ml",             2,  3, "390ml Cans",    16),
    # --- 600ml bottles + water (rows 11-21) ---
    ("Coke 600ml",              6,  8, "600ml Bottles",  1),
    ("Coke Zero 600ml",         7, 10, "600ml Bottles",  4),
    ("Diet Coke 600ml",         2,  2, "600ml Bottles",  7),
    ("Vanilla Coke Zero 600ml", 2,  2, "600ml Bottles", 11),
    ("Fanta 600ml",             3,  3, "600ml Bottles", 15),
    ("Sprite 600ml",            3,  3, "600ml Bottles", 12),
    ("Sprite Zero 600ml",       2,  2, "600ml Bottles", 14),
    ("Fanta Lemon 600ml",       2,  2, "600ml Bottles", 17),
    ("Pasito 600ml",            2,  2, "600ml Bottles", 20),
    ("Sparkling Water",         2,  3, "600ml Bottles", 24),
    ("Water",                   7,  9, "600ml Bottles", 21),
    # --- Fuze Tea (rows 23-25) ---
    ("Peach Fuze Tea",          3,  3, "Fuze Tea",      34),
    ("Lemon Fuze Tea",          2,  2, "Fuze Tea",      33),
    ("Mango Fuze Tea",          3,  3, "Fuze Tea",      35),
    # --- Powerade (rows 27-31) — no public-holiday uplift ---
    ("Purple Powerade",         2,  2, "Powerade",      29),  # Blackcurrant
    ("Blue Powerade",           3,  3, "Powerade",      26),  # Mt Blast
    ("Yellow Powerade",         2,  2, "Powerade",      28),  # Lemon Lime
    ("Red Powerade",            3,  3, "Powerade",      27),  # Berry Ice
    ("Orange Powerade",         2,  2, "Powerade",      30),  # Gold Rush
    # --- large bottles (rows 34-36) ---
    ("Coke 1.25L",              2,  2, "1.25L & 1.5L",   3),
    ("Coke Zero 1.25L",         2,  3, "1.25L & 1.5L",   6),
    ("Water 1.5L",              2,  2, "1.25L & 1.5L",  22),
    # --- juice (rows 38-39) — Keri ---
    ("Apple Juice",             2,  3, "Juice",         32),
    ("Orange Juice",            2,  3, "Juice",         31),
]

# Public list of dicts, in SHEET order (the counting grid follows the physical fridge).
# The produced order is sorted into CCEP site order (seq) by build_order().
DRINK_ITEMS = [
    {"item": it, "par": par, "ph_par": ph_par, "section": sec, "seq": seq}
    for (it, par, ph_par, sec, seq) in _ITEMS
]


def par_for(item, public_holiday=False):
    """The active par for an item dict, honouring the public-holiday uplift."""
    return item["ph_par"] if public_holiday else item["par"]


def default_delivery(today, weekday=1):
    """Next delivery date strictly after today for the given weekday (Mon=0..Sun=6).
    Defaults to the next Tuesday (1)."""
    ahead = (weekday - today.weekday()) % 7
    return today + dt.timedelta(days=ahead or 7)


def coverage(today, cover_until):
    """(days, weeks) the order must cover — from today through cover_until, inclusive.
    weeks = days / 7 since 'Qnty Needed' on the sheet is a per-week usage rate."""
    days = max((cover_until - today).days + 1, 1)
    return days, days / 7.0


def order_qty(weekly_use, on_hand, weeks=1.0):
    """Units to order, rounded UP, never negative.

    weekly_use is the per-week usage ("Qnty Needed"). The order must cover `weeks`
    of usage (the delivery window), less what's already on hand:
        order = ceil(weekly_use * weeks - on_hand)
    """
    try:
        oh = float(on_hand or 0)
    except (TypeError, ValueError):
        oh = 0.0
    try:
        wk = float(weeks)
    except (TypeError, ValueError):
        wk = 1.0
    return max(0, math.ceil(float(weekly_use) * wk - oh))


def build_order(counts, weeks=1.0):
    """Turn an {item: on_hand} map into the order, as a single flat list.

    `weeks` scales the weekly usage ('Qnty Needed') to the delivery window (e.g. 1.7
    weeks). NB: a public holiday does not change the weekly rate — it just lengthens
    the window, which `weeks` already captures. Returns [{"item", "weekly_use", "need",
    "on_hand", "order", "seq"}, ...] in CCEP ordering-site order (by seq), order > 0 only.
    """
    counts = counts or {}
    out = []
    for row in DRINK_ITEMS:
        weekly_use = row["par"]
        oh = counts.get(row["item"])
        qty = order_qty(weekly_use, oh, weeks)
        if qty <= 0:
            continue
        out.append({"item": row["item"], "weekly_use": weekly_use, "seq": row["seq"],
                    "need": round(float(weekly_use) * float(weeks), 1),
                    "on_hand": float(oh or 0), "order": qty})
    out.sort(key=lambda e: e["seq"])
    return out


def order_text(order_list):
    """Plain-text drinks order — one line per item, in CCEP site order."""
    lines = ["Drinks order", ""]
    if not order_list:
        lines.append("(nothing to order)")
        return "\n".join(lines)
    for e in order_list:
        lines.append(f"{e['order']:g} x {e['item']}")
    return "\n".join(lines)


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
