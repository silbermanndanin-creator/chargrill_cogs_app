"""Helpers for part-time contracted-pattern matching (used by variation letters).

The actual employee contracts are NOT stored here (they're personal data and this repo
is public) — they live in the database via storage.load_contracts() / save_contract(),
edited in the app's Variations tab. This module only holds weekday constants + name
matching, so no staff names or schedules are committed to git.

A "contracts map" (cmap) is: {employee_name: {weekday: (start, finish)}} with weekday
in Mon..Sun and times as 'HH:MM'.
"""
import re

WEEKDAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
WEEKDAY_FULL = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
                "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}
IDX_TO_DAY = {v: k for k, v in WEEKDAYS.items()}
DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def name_key(name):
    """Order-insensitive key (sorted lowercased tokens) so 'Tej Rijal' matches
    'Rijal, Tej' / 'TEJ RIJAL' between the contract list and the Tanda CSV."""
    return " ".join(sorted(re.sub(r"[^a-z ]", " ", str(name).lower()).split()))


def match_contract(name, cmap):
    """(canonical_name, days_dict) for a Tanda name, or (None, None) if not tracked."""
    k = name_key(name)
    for cname, days in cmap.items():
        if name_key(cname) == k:
            return cname, days
    return None, None
