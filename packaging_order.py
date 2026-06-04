"""Packaging ordering — the first 'Ordering' section.

Source of truth is the DANIN packaging stocktake sheet (pack.xlsx). Each item has:
  - par      : the FULL/target quantity to hold (the sheet's "QTY Needed" column)
  - supplier : who we buy it from, taken from the sheet's row colour —
               GREEN  (92D050) -> BIOPAK HORIZONS
               YELLOW (FFFF00) -> A-Z Packaging
  - category : the highlighted grouping (BIOPAK items only; A-Z is an unsorted list)
  - half_step: rows 4-48 in the sheet can be counted in 0.5 increments; the order is
               then rounded UP to a whole unit. Later rows are counted in whole units.

Ordering rule: order qty = max(0, ceil(par - on_hand)).
"""
import math

BIOPAK = "BIOPAK HORIZONS"
AZ = "A-Z Packaging"

# Display order for the BIOPAK categories (sheet's highlighted groupings). The sheet
# spells one "Cutlery &Straws" — normalised to "Cutlery & Straws" here.
BIOPAK_CATEGORY_ORDER = [
    "Containers & Lids",
    "Bags",
    "Plates & Trays",
    "Napkins",
    "Cutlery & Straws",
]

# (item, par, supplier, category, half_step) — order matches the sheet (rows 4-67).
# category is "" for A-Z (yellow) items, which are listed unsorted.
_ITEMS = [
    # --- BIOPAK / green block (rows 4-11) ---
    ("Dinner Boxes",                              6, BIOPAK, "Containers & Lids", True),
    ("XL Chicken Bags",                           2, BIOPAK, "Bags",              True),
    ("Small Chicken Bags",                        2, BIOPAK, "Bags",              True),
    ("Cover Bags",                                2, BIOPAK, "Bags",              True),
    ("Large Chips",                               2, BIOPAK, "Bags",              True),
    ("Medium Chip",                               1, BIOPAK, "Bags",              True),
    ("Small Chips",                               1, BIOPAK, "Bags",              True),
    ("Large Wedges",                              1, BIOPAK, "Bags",              True),
    # --- A-Z / yellow block (rows 13-19) ---
    ("1000ml Rectangle Containter",               1, AZ, "", True),
    ("700ml Rectangle Container",                 2, AZ, "", True),
    ("500ml Rectangle Container",                 1, AZ, "", True),
    ("Rectangular Flat Lids",                     2, AZ, "", True),
    ("500ml Round Container",                     2, AZ, "", True),
    ("280ml Round Container",                     2, AZ, "", True),
    ("Round Flat Lids",                           2, AZ, "", True),
    # --- BIOPAK (rows 21-25) ---
    ("Carry Bags",                                3, BIOPAK, "Bags",              True),
    ("Tray 4",                                    4, BIOPAK, "Plates & Trays",    True),
    ("Tray 3",                                    2, BIOPAK, "Plates & Trays",    True),
    ("Tray 1",                                    1, BIOPAK, "Plates & Trays",    True),
    ("Burger Box",                                3, BIOPAK, "Containers & Lids", True),
    # --- A-Z (rows 27-30) ---
    ("Rectangular Dome Lid",                      2, AZ, "", True),
    ("Round Dome Lid",                            2, AZ, "", True),
    ("Large Powder Free Gloves Blue",             3, AZ, "", True),
    ("Medium Powder Free Gloves Blue",            2, AZ, "", True),
    # --- BIOPAK (row 31) ---
    ("Napkins",                                   1, BIOPAK, "Napkins",           True),
    # --- BIOPAK (rows 33-37) ---
    ("Snack Box",                                 2, BIOPAK, "Containers & Lids", True),
    ("Uber Bag",                                  3, BIOPAK, "Bags",              True),
    ("Small Paper Bag",                           2, BIOPAK, "Bags",              True),
    ("Salad Bowls",                               2, BIOPAK, "Plates & Trays",    True),
    ("Salad Bowl Lids",                           2, BIOPAK, "Containers & Lids", True),
    # --- A-Z (rows 38-39) ---
    ("70ml Sauce Container",                      2, AZ, "", True),
    ("80mm Lids (for 70mm Container)",            2, AZ, "", True),
    # --- BIOPAK (rows 40-41) ---
    ("100ml Sauce Container",                     1, BIOPAK, "Containers & Lids", True),
    ("Lids for 100ml",                            1, BIOPAK, "Containers & Lids", True),
    # --- A-Z (rows 42-43) ---
    ("Desert Container",                          1, AZ, "", True),
    ("Family Salad Container (48oz Showbowl)",    1, AZ, "", True),
    # --- BIOPAK (rows 44-45) ---
    ("Square Foil Catering",                      2, BIOPAK, "Plates & Trays",    True),
    ("Round Foil Catering",                       1, BIOPAK, "Plates & Trays",    True),
    # --- A-Z (rows 46-50) ---
    ("Aluminium Foil",                            4, AZ, "", True),
    ("Baking Paper",                              4, AZ, "", True),
    ("Glad Wrap",                                 2, AZ, "", True),
    ("Small Bamboo Spike Skewers",               1, AZ, "", False),   # row 49: whole units
    ("Large Kebab Skewers",                       1, AZ, "", False),   # row 50: whole units
    # --- BIOPAK (row 52) ---
    ("Roll Paper",                                2, BIOPAK, "Bags",              False),
    # --- A-Z (row 53) ---
    ("10oz White Paper Cups",                     1, AZ, "", False),
    # --- BIOPAK (rows 54-57) ---
    ("Milkshake Straws",                          1, BIOPAK, "Cutlery & Straws",  False),
    ("Thickshake Straws",                         1, BIOPAK, "Cutlery & Straws",  False),
    ("Milkshake Cups",                            1, BIOPAK, "Containers & Lids", False),
    ("Milkshake Lids",                            1, BIOPAK, "Containers & Lids", False),
    # --- A-Z (rows 58-59) ---
    ("Thickshake Cups",                           1, AZ, "", False),
    ("Thickshake Lids",                           1, AZ, "", False),
    # --- A-Z (rows 61-64) ---
    ("Dressing Containers (365ml Tamper)",        1, AZ, "", False),
    ("95mm Lids (Dressing Lids)",                 1, AZ, "", False),
    ("Soup Containers (565ml Tamper)",            1, AZ, "", False),
    ("118mm Lids (Soup Lids)",                    1, AZ, "", False),
    # --- BIOPAK (rows 66-67) ---
    ("Large Catering Box",                        1, BIOPAK, "Plates & Trays",    False),
    ("Small Catering Box",                        1, BIOPAK, "Plates & Trays",    False),
]

# Public list of dicts, in sheet order.
PACKAGING_ITEMS = [
    {"item": it, "par": par, "supplier": sup, "category": cat, "half_step": half}
    for (it, par, sup, cat, half) in _ITEMS
]


def order_qty(par, on_hand):
    """Units to order to refill to par, rounded UP. Never negative."""
    try:
        oh = float(on_hand or 0)
    except (TypeError, ValueError):
        oh = 0.0
    return max(0, math.ceil(float(par) - oh))


def build_order(counts):
    """Turn an {item: on_hand} map into the split, ready-to-send order.

    Returns a dict:
      {
        "BIOPAK HORIZONS": {            # grouped by category, in BIOPAK_CATEGORY_ORDER
            "Containers & Lids": [{"item", "par", "on_hand", "order"}, ...],
            ...
        },
        "A-Z Packaging": [ {...}, ... ],  # flat list, sheet order
      }
    Only items with order > 0 are included.
    """
    counts = counts or {}
    biopak = {cat: [] for cat in BIOPAK_CATEGORY_ORDER}
    az = []
    for row in PACKAGING_ITEMS:
        oh = counts.get(row["item"])
        qty = order_qty(row["par"], oh)
        if qty <= 0:
            continue
        entry = {"item": row["item"], "par": row["par"],
                 "on_hand": float(oh or 0), "order": qty}
        if row["supplier"] == BIOPAK:
            biopak.setdefault(row["category"], []).append(entry)
        else:
            az.append(entry)
    # drop empty categories so the UI/text stays tidy
    biopak = {cat: items for cat, items in biopak.items() if items}
    return {BIOPAK: biopak, AZ: az}


def order_text_biopak(biopak_by_cat):
    """Plain-text order for BIOPAK, grouped by its highlighted categories."""
    lines = [f"Order — {BIOPAK}", ""]
    if not biopak_by_cat:
        lines.append("(nothing to order)")
        return "\n".join(lines)
    for cat in BIOPAK_CATEGORY_ORDER:
        items = biopak_by_cat.get(cat)
        if not items:
            continue
        lines.append(f"{cat}:")
        for e in items:
            lines.append(f"  {e['order']:g} x {e['item']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def order_text_az(az_items):
    """Plain-text order for A-Z (flat, unsorted list)."""
    lines = [f"Order — {AZ}", ""]
    if not az_items:
        lines.append("(nothing to order)")
        return "\n".join(lines)
    for e in az_items:
        lines.append(f"{e['order']:g} x {e['item']}")
    return "\n".join(lines)
