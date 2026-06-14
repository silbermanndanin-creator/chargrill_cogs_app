"""AI COGS-reduction advisor for Chargrill Charlie's.

Turns the numbers the app already computes into concrete, dollar-quantified actions to
get food COGS and prime cost down to target. Two entry points:

  cogs_report(facts)            -> a one-shot ranked list of recommendations (COGS Doctor)
  cogs_chat(facts, history, q)  -> a conversational follow-up answer against the same facts

COST DISCIPLINE (this is on the user's bill):
  - build_facts() ships a small, PRE-AGGREGATED snapshot (a few dozen numbers), never raw
    invoice rows, so each call is cheap on input tokens.
  - Sonnet only (no Opus escalation) — recommendations don't need the heavy model.
  - The system prompt is marked cache_control so repeated calls in a session reuse it.
  - The app must gate these behind a button / chat submit AND cache the result, so Claude
    is called on an explicit action, never on every Streamlit rerun.

Pure functions (no Streamlit / I/O) so they're testable: the app loads the data with its
cached wrappers and passes it in.
"""
import json
from typing import Optional

import anthropic
import pandas as pd

import config
import metrics

MODEL = "claude-sonnet-4-6"   # recommendations don't need Opus — keep it cheap


def _round(x, n=0):
    try:
        v = round(float(x), n)
        return int(v) if n == 0 else v
    except (TypeError, ValueError):
        return None


def _pct(num, den, n=1):
    try:
        num, den = float(num), float(den)
        return round(num / den * 100, n) if den else None
    except (TypeError, ValueError):
        return None


def build_facts(*, df, lines, rev_map, labour_cost_map, period_col, period_key,
                revenue, total_cogs, labour_cost, mode,
                weekly_sales=None, catering=None, remittances=None, delivery=None,
                n_periods=8):
    """Compact snapshot of the venue's cost picture for the advisor. All money is ex-GST
    unless a field name says otherwise. Returns a small JSON-serialisable dict.

    df / lines        : invoices and exploded invoice lines (from the app's cached loaders)
    rev_map           : {period_key: revenue} for this mode (POS + manual merged)
    labour_cost_map   : {period_key: labour cost} for this mode
    period_col        : "iso_week" or "month"; period_key : the current period
    revenue/total_cogs/labour_cost : the current period's headline figures
    weekly_sales      : gross incl-GST sales for the week (for the Baida order guide); optional
    catering          : catering_orders df; remittances : platform_remittances df (optional)
    """
    gp, rp = config.TOTAL_COGS_GREEN, config.TOTAL_COGS_RED
    cogs_pct = _pct(total_cogs, revenue)
    labour_pct = _pct(labour_cost, revenue)
    prime_pct = _pct((total_cogs or 0) + (labour_cost or 0), revenue)

    # --- Per-category spend this period: $, % of revenue, target band, status ---
    categories = []
    if df is not None and not df.empty:
        spend, _ = metrics.spend_and_deliveries(df, period_col, period_key)
        for sup, val in sorted(spend.items(), key=lambda kv: -float(kv[1])):
            share = _pct(val, revenue)
            cfg = config.SUPPLIERS.get(sup, {})
            categories.append({
                "category": sup,
                "spend": _round(val),
                "pct_of_rev": share,
                "counts_to_cogs": config.is_cogs(sup),
                "target_pct": (_round(cfg["green_pct"] * 100, 1)
                               if cfg.get("green_pct") is not None else None),
                "status": (config.status_for(val / revenue, sup)
                           if revenue and revenue > 0 else None),
            })

    # --- COGS% and labour/prime trends over recent periods ---
    periods = metrics.recent_periods(df, period_col, n_periods) if df is not None and not df.empty else []
    cogs_trend = (metrics.cogs_pct_trend(df, rev_map, period_col, periods).to_dict("records")
                  if periods else [])
    lp_trend = (metrics.labour_prime_trend(df, rev_map, labour_cost_map, period_col, periods)
                .to_dict("records") if periods else [])

    # --- Silent price creep across the whole catalogue (top movers) ---
    anomalies = []
    if lines is not None and not lines.empty:
        for r in metrics.price_anomalies(lines, min_pct=8.0).head(12).to_dict("records"):
            anomalies.append({"supplier": r.get("Supplier"), "item": r.get("Item"),
                              "was": r.get("Was"), "now": r.get("Now"),
                              "change": r.get("Change"), "last_buy": r.get("Last buy")})

    # --- Veggie price movers (St George) ---
    veg_movers = []
    if lines is not None and not lines.empty:
        for r in metrics.veggie_flux_table(lines).to_dict("records"):
            if r.get("Weekly Δ") not in ("—", None) or r.get("Daily Δ") not in ("—", None):
                veg_movers.append({"item": r.get("Item"),
                                   "latest_per_unit": r.get("Latest $/unit"),
                                   "daily_change": r.get("Daily Δ"),
                                   "weekly_change": r.get("Weekly Δ")})

    # --- Baida (chicken) order size vs the winter turnover guide ---
    baida = None
    if lines is not None and not lines.empty:
        tubs = metrics.baida_tubs(lines, period_col, period_key)
        rec = config.baida_recommended(weekly_sales) if weekly_sales else None
        baida = {
            "actual_whole_chickens": _round(tubs.get("RSPCA", {}).get("chickens")),
            "actual_split_chickens": _round(tubs.get("Split", {}).get("chickens")),
            "recommended_whole": _round(rec[0]) if rec else None,
            "recommended_split": _round(rec[1]) if rec else None,
            "weekly_sales_incl_gst": _round(weekly_sales) if weekly_sales else None,
        }

    # --- Catering volume + money still owed by the platforms ---
    catering_summary = None
    if catering is not None and not catering.empty:
        catering_summary = {"orders_on_record": int(len(catering))}
    outstanding = None
    if remittances is not None and not remittances.empty:
        try:
            outstanding = _round(pd.to_numeric(remittances.get("total_paid"),
                                               errors="coerce").fillna(0).sum())
        except Exception:
            outstanding = None

    return {
        "venue": "Chargrill Charlie's (Rose Bay)",
        "currency": "AUD, ex-GST unless noted",
        "period": {"mode": mode, "key": period_key},
        "targets": {"cogs_green_pct": _round(gp * 100, 0), "cogs_red_pct": _round(rp * 100, 0),
                    "labour_green_pct": _round(config.LABOUR_GREEN * 100, 0),
                    "prime_green_pct": _round(config.PRIME_GREEN * 100, 0)},
        "this_period": {"revenue": _round(revenue), "food_cogs": _round(total_cogs),
                        "cogs_pct": cogs_pct, "labour_cost": _round(labour_cost),
                        "labour_pct": labour_pct, "prime_pct": prime_pct},
        "category_spend": categories,
        "cogs_pct_trend": cogs_trend,
        "labour_prime_trend": lp_trend,
        "price_rises": anomalies,
        "veggie_movers": veg_movers,
        "baida_chicken": baida,
        "catering": catering_summary,
        "platform_payments_received": outstanding,
        "delivery_payouts": delivery,  # actual Uber/DoorDash net vs the 40% assumption
    }


SYSTEM = """You are a sharp, practical cost analyst for an Australian quick-service \
hospitality venue (Chargrill Charlie's — rotisserie chicken + salads). You are given a \
JSON snapshot of the venue's current cost picture (all figures AUD, ex-GST unless a field \
says otherwise). Food COGS target is 40% of net ex-GST revenue (42% = red); labour target \
~28%; prime cost (food + labour) target ~68%.

Your job: recommend specific, actionable ways to REDUCE food COGS and prime cost toward \
target, ranked by likely dollar impact.

Rules:
- Be concrete and quantified. Name the supplier/category/item and estimate the $/week (or \
$/period) saving where the numbers allow. Prefer "switch X, ~$Y/wk" over vague advice.
- Anchor every point in the supplied data. Quote the actual %, $ or price change you're \
reacting to. NEVER invent numbers, suppliers, or items that aren't in the snapshot.
- Prioritise: (1) categories over their target %, (2) silent price rises (price_rises / \
veggie_movers), (3) over-ordering vs guides (e.g. Baida chickens vs the recommendation), \
(4) labour when prime cost is the binding constraint, (5) delivery economics — if \
delivery_payouts shows the actual Uber/DoorDash net is well below gross, the platform \
commission and ad_spend are real margin leaks worth calling out (these are now ACTUAL \
figures, not estimates).
- If a key input is missing (e.g. revenue is null so COGS% can't be computed, or no labour \
logged), say so briefly and tell them what to enter — don't guess.
- Keep it tight: a few high-value recommendations, not a wall of text. Lead with the single \
biggest opportunity. Use short markdown bullets with bold headers.
- Australian context: GST is 10%; produce/seafood prices are seasonal; this is a real small \
business — keep advice realistic and respectful of supplier relationships."""


def _facts_block(facts: dict) -> str:
    return ("Here is the venue's current cost snapshot as JSON:\n\n```json\n"
            + json.dumps(facts, ensure_ascii=False, default=str) + "\n```")


def _client(client):
    return client or anthropic.Anthropic()


def cogs_report(facts: dict, client: Optional[anthropic.Anthropic] = None) -> str:
    """One-shot ranked recommendations for cutting COGS, as markdown. Cheap (Sonnet,
    pre-aggregated input). The app should cache the result so it runs on a click, not a rerun."""
    resp = _client(client).messages.create(
        model=MODEL, max_tokens=1500,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _facts_block(facts) + "\n\n"
                   "Give me the ranked list of ways to reduce COGS and prime cost this "
                   "period. Lead with the biggest dollar opportunity."}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def cogs_chat(facts: dict, history, question: str,
              client: Optional[anthropic.Anthropic] = None) -> str:
    """Answer a follow-up question against the same facts snapshot. `history` is a list of
    {"role": "user"|"assistant", "content": str} turns (excluding this question)."""
    messages = [{"role": "user", "content": _facts_block(facts) + "\n\n"
                 "Use this snapshot to answer my questions about reducing costs. "
                 "Acknowledge briefly, then I'll ask."},
                {"role": "assistant", "content":
                 "Got it — I've reviewed the snapshot. Ask away."}]
    for turn in (history or []):
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    resp = _client(client).messages.create(
        model=MODEL, max_tokens=1000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
