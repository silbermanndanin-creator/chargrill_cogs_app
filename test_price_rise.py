"""Tests for pack-size-aware price-rise detection.

The bug these guard against: a Blueseas line billed per single unit one week and per
multi-unit carton the next was flagged as a huge price rise, because the detector
compared the raw printed unit price ($28.75/pack vs $172.50/carton-of-6) instead of a
true per-single-unit price. metrics.price_anomalies now compares on unit_price_each,
which config.units_per_pack normalises.

Run: `python3 test_price_rise.py`  (or `pytest test_price_rise.py`).
"""
import json
import pandas as pd

import config
import metrics


def _invoices(rows):
    """Build an invoices DataFrame (one row per delivery) from
    (supplier, date, [line dicts]) tuples, the shape metrics.explode_lines reads."""
    recs = []
    for supplier, date, lines in rows:
        recs.append({
            "supplier": supplier, "invoice_date": date,
            "iso_week": str(date)[:7], "month": str(date)[:7],
            "line_items": json.dumps(lines),
        })
    return pd.DataFrame(recs)


def _anoms(rows):
    lines = metrics.explode_lines(_invoices(rows))
    return metrics.price_anomalies(lines, min_pct=8.0)


# ---- config.units_per_pack -------------------------------------------------
def test_units_per_pack():
    f = config.units_per_pack
    # explicit pack_size (captured at extraction from the CTN-N code) is the primary
    # source — the stored unit is normalised to "carton" and no longer carries the number
    assert f("Eggs Chilled Hard Boiled 2.5kg", "carton", 6) == 6
    assert f("Sugar Brown 1kg", "carton", 10) == 10
    assert f("Butter Salted 500g Dairy Farmers", "carton", 12) == 12
    assert f("Any New Pack Item", "carton", 4) == 4
    # pack_size only applies to real multi-unit counts; junk/1 falls through
    assert f("Cheese Mozz Shred 2kg", "ea", None) == 1
    assert f("Wedges Sweet Potato", "carton", 1) == 1
    # single units are never divided (protects genuine per-each deliveries)
    assert f("Quinoa Three Mix 1kg", "ea") == 1
    assert f("Salmon Fillet", "kg") == 1
    assert f("Milk 2Litre Plain Full Cream Norco", "ea") == 1
    # UOM string still parsed when it carries the count (manual entry / un-normalised)
    assert f("Eggs Hard Boiled 2.5kg", "CTN-6") == 6
    assert f("Cottonseed Oil", "CTN-12") == 12
    assert f("Tomato Sauce", "6PK") == 6
    # pack count parsed from the description when the UOM is generic
    assert f("Soya Beans 6 x 2.5kg", "carton") == 6
    # owner override only for the rare item whose UOM/description/pack_size all omit it
    assert f("Mustard Seeded 2.5kg", "pack") == 6
    # a bare size is NOT a pack count, plain CTN and unknown pack are left unchanged
    assert f("Cottonseed Oil 20L Round Drum", "drum") == 1
    assert f("Wedges Sweet Potato Crinkle Cut", "carton") == 1


def test_generalises_beyond_curated_items():
    """An item NOT in PACK_UNITS_OVERRIDES, billed per-each then per-carton-of-6, is
    normalised from the extracted pack_size — proving the fix scales to every CTN-N item
    with no per-item config. Mirrors real stored data: unit 'carton', pack_size 6."""
    rows = [
        ("Blueseas (Broadline)", "2026-05-01",
         [{"description": "Sugar Brown 1kg", "quantity": 10, "unit": "ea",
           "unit_price": 3.72, "amount": 37.20}]),
        ("Blueseas (Broadline)", "2026-06-01",
         [{"description": "Sugar Brown 1kg", "quantity": 1, "unit": "carton",
           "pack_size": 10, "unit_price": 37.20, "amount": 37.20}]),
    ]
    a = _anoms(rows)
    assert "sugar brown 1kg" not in config.PACK_UNITS_OVERRIDES  # not hardcoded
    assert a[a["Item"].str.contains("Sugar")].empty, \
        f"same-format basis change wrongly flagged:\n{a}"


def test_line_total_not_used_as_unit_price():
    """If the printed unit_price is mis-captured as the line TOTAL (e.g. Quinoa qty 10
    stored with unit_price 69.10 instead of 6.91), detection must still use amount/qty
    (6.91) and NOT flag a phantom +900%."""
    a = _anoms([
        ("Blueseas (Broadline)", "2026-05-01",
         [{"description": "Quinoa Three Mix 1kg", "quantity": 2, "unit": "ea",
           "unit_price": 6.91, "amount": 13.82}]),
        ("Blueseas (Broadline)", "2026-06-01",
         [{"description": "Quinoa Three Mix 1kg", "quantity": 10, "unit": "ea",
           "unit_price": 69.10, "amount": 69.10}]),   # unit_price wrongly = line total
    ])
    assert a[a["Item"].str.contains("Quinoa")].empty, \
        f"line total wrongly treated as a unit-price rise:\n{a}"


def test_carton_of_n_genuine_rise_uses_pack_size():
    """Consistently per-carton item (unit 'carton', pack_size 6) with a real rise fires,
    reported on the true per-unit basis: $172.50/6 = $28.75 -> $189.00/6 = $31.50."""
    rise = _anoms([
        ("Blueseas (Broadline)", "2026-05-01",
         [{"description": "Eggs Chilled Hard Boiled 2.5kg", "quantity": 1, "unit": "carton",
           "pack_size": 6, "unit_price": 172.50, "amount": 172.50}]),
        ("Blueseas (Broadline)", "2026-06-01",
         [{"description": "Eggs Chilled Hard Boiled 2.5kg", "quantity": 1, "unit": "carton",
           "pack_size": 6, "unit_price": 189.00, "amount": 189.00}]),
    ])
    assert len(rise) == 1 and rise.iloc[0]["Was"] == 28.75 and rise.iloc[0]["Now"] == 31.50, \
        f"carton-of-N rise reported on wrong basis:\n{rise}"


# ---- the original false-alert case -----------------------------------------
def test_billing_basis_change_is_not_a_price_rise():
    """Same item billed per-each ($28.75) then per-carton-of-6 ($172.50/carton) — the
    per-unit price is unchanged, so it must NOT be flagged."""
    rows = [
        ("Blueseas (Broadline)", "2026-05-01",
         [{"description": "Eggs Hard Boiled 2.5kg", "quantity": 6, "unit": "ea",
           "unit_price": 28.75, "amount": 172.50}]),
        ("Blueseas (Broadline)", "2026-06-01",
         [{"description": "Eggs Hard Boiled 2.5kg", "quantity": 1, "unit": "carton",
           "unit_price": 172.50, "amount": 172.50}]),
    ]
    a = _anoms(rows)
    assert a[a["Item"].str.contains("Eggs")].empty, \
        f"basis change wrongly flagged:\n{a}"


def test_per_each_items_are_unchanged():
    """EA lines (the app already divides by qty) keep their old behaviour: a flat price
    is not flagged, a genuine rise is."""
    flat = _anoms([
        ("Blueseas (Broadline)", "2026-05-01",
         [{"description": "Quinoa Three Mix 1kg", "quantity": 2, "unit": "ea",
           "unit_price": 6.91, "amount": 13.82}]),
        ("Blueseas (Broadline)", "2026-06-01",
         [{"description": "Quinoa Three Mix 1kg", "quantity": 3, "unit": "ea",
           "unit_price": 6.91, "amount": 20.73}]),
    ])
    assert flat.empty, f"flat per-each price wrongly flagged:\n{flat}"

    rise = _anoms([
        ("Blueseas (Broadline)", "2026-05-01",
         [{"description": "Milk 2L Norco", "quantity": 7, "unit": "ea",
           "unit_price": 3.79, "amount": 26.53}]),
        ("Blueseas (Broadline)", "2026-06-01",
         [{"description": "Milk 2L Norco", "quantity": 7, "unit": "ea",
           "unit_price": 4.50, "amount": 31.50}]),
    ])
    assert len(rise) == 1 and rise.iloc[0]["Was"] == 3.79 and rise.iloc[0]["Now"] == 4.50, \
        f"genuine per-each rise not reported correctly:\n{rise}"


def test_genuine_pack_rise_still_flagged():
    """A real rise on a consistently per-carton item must still fire, reported on the
    true per-unit basis ($28.75 -> $31.50)."""
    rise = _anoms([
        ("Blueseas (Broadline)", "2026-05-01",
         [{"description": "Eggs Hard Boiled 2.5kg", "quantity": 1, "unit": "carton",
           "unit_price": 172.50, "amount": 172.50}]),
        ("Blueseas (Broadline)", "2026-06-01",
         [{"description": "Eggs Hard Boiled 2.5kg", "quantity": 1, "unit": "carton",
           "unit_price": 189.00, "amount": 189.00}]),
    ])
    assert len(rise) == 1, f"genuine pack rise not flagged:\n{rise}"
    assert rise.iloc[0]["Was"] == 28.75 and rise.iloc[0]["Now"] == 31.50, \
        f"pack rise reported on wrong basis:\n{rise}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nAll price-rise tests passed.")
