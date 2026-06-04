"""Drinks ordering — second 'Ordering' section.

Source of truth is the DANIN drinks stocktake sheet (drink.xlsx). Unlike packaging,
the sheet has NO row colours, so there is no supplier split and no highlighted
categories — it's a single order list. The blank-row groupings in the sheet are kept
as display "sections" (390ml cans, 600ml bottles, etc.) to make counting/checking easy.

Each item has:
  - par     : the FULL/target quantity to hold (the sheet's "Qnty Needed" column)
  - section : the sheet's visual grouping (for display only)

Ordering rule (same as packaging): order qty = max(0, ceil(par - on_hand)).
"""
import math

# Display order for the sheet's natural (blank-row separated) groupings.
SECTION_ORDER = [
    "390ml Cans",
    "600ml Bottles",
    "Fuze Tea",
    "Powerade",
    "1.25L & 1.5L",
    "Juice",
]

# (item, par, section) — order matches the sheet (rows 5-39).
_ITEMS = [
    # --- 390ml cans (rows 5-9) ---
    ("Coke 390ml",              6, "390ml Cans"),
    ("Coke Zero 390ml",         8, "390ml Cans"),
    ("Diet Coke 390ml",         2, "390ml Cans"),
    ("Sprite 390ml",            2, "390ml Cans"),
    ("Fanta 390ml",             2, "390ml Cans"),
    # --- 600ml bottles + water (rows 11-21) ---
    ("Coke 600ml",              6, "600ml Bottles"),
    ("Coke Zero 600ml",         8, "600ml Bottles"),
    ("Diet Coke 600ml",         2, "600ml Bottles"),
    ("Vanilla Coke Zero 600ml", 2, "600ml Bottles"),
    ("Fanta 600ml",             3, "600ml Bottles"),
    ("Sprite 600ml",            3, "600ml Bottles"),
    ("Sprite Zero 600ml",       2, "600ml Bottles"),
    ("Fanta Lemon 600ml",       2, "600ml Bottles"),
    ("Pasito 600ml",            2, "600ml Bottles"),
    ("Sparkling Water",         3, "600ml Bottles"),
    ("Water",                   8, "600ml Bottles"),
    # --- Fuze Tea (rows 23-25) ---
    ("Peach Fuze Tea",          3, "Fuze Tea"),
    ("Lemon Fuze Tea",          2, "Fuze Tea"),
    ("Mango Fuze Tea",          3, "Fuze Tea"),
    # --- Powerade (rows 27-31) ---
    ("Purple Powerade",         2, "Powerade"),
    ("Blue Powerade",           3, "Powerade"),
    ("Yellow Powerade",         2, "Powerade"),
    ("Red Powerade",            3, "Powerade"),
    ("Orange Powerade",         2, "Powerade"),
    # --- large bottles (rows 34-36) ---
    ("Coke 1.25L",              2, "1.25L & 1.5L"),
    ("Coke Zero 1.25L",         2, "1.25L & 1.5L"),
    ("Water 1.5L",              2, "1.25L & 1.5L"),
    # --- juice (rows 38-39) ---
    ("Apple Juice",             2, "Juice"),
    ("Orange Juice",            2, "Juice"),
]

# Public list of dicts, in sheet order.
DRINK_ITEMS = [
    {"item": it, "par": par, "section": sec} for (it, par, sec) in _ITEMS
]


def order_qty(par, on_hand):
    """Units to order to refill to par, rounded UP. Never negative."""
    try:
        oh = float(on_hand or 0)
    except (TypeError, ValueError):
        oh = 0.0
    return max(0, math.ceil(float(par) - oh))


def build_order(counts):
    """Turn an {item: on_hand} map into the order, grouped by section.

    Returns {section: [{"item", "par", "on_hand", "order"}, ...]} in SECTION_ORDER,
    only including items with order > 0 and dropping empty sections.
    """
    counts = counts or {}
    out = {sec: [] for sec in SECTION_ORDER}
    for row in DRINK_ITEMS:
        oh = counts.get(row["item"])
        qty = order_qty(row["par"], oh)
        if qty <= 0:
            continue
        out.setdefault(row["section"], []).append(
            {"item": row["item"], "par": row["par"],
             "on_hand": float(oh or 0), "order": qty})
    return {sec: items for sec, items in out.items() if items}


def order_text(order_by_section):
    """Plain-text drinks order, grouped by section."""
    lines = ["Drinks order", ""]
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
