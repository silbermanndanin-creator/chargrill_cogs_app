"""Claude extraction for delivery-platform WEEKLY PAYMENT emails -> structured JSON.

Uber Eats and DoorDash both email a weekly payment summary. They replace the app's flat
40%-commission ESTIMATE with the ACTUAL net the venue is paid, so revenue (and therefore
COGS %) is true rather than assumed.

The two formats differ a lot:
  - Uber Eats  -> RICH: weekly gross sales (inc GST) + a per-day breakdown + a full fee
    list, ending in "Total Payment" (the actual deposit).
  - DoorDash   -> SPARSE: the payout email carries ONLY the net payout ("Your store will
    receive a payment of $X") and the period; the detail lives in the Merchant Portal.
    That's fine — the app already has DoorDash GROSS from the POS slips, and pairs it with
    this net.

IMPORTANT: Uber/DoorDash print dates US month-first (M/D/YY) — the prompt converts them to
ISO. Mirrors remittance_extract.py: Sonnet first read, escalate to Opus if not 'high'.
Both reports arrive as the email BODY (Uber HTML, DoorDash text) — pass text=...; a PDF/
image path is supported too for completeness.
"""
import base64
import json
from typing import List, Optional

import anthropic
from pydantic import BaseModel, ValidationError

from extract import _prep_image, _doc_block, MODEL, ESCALATE_MODEL

SYSTEM = """You read a WEEKLY PAYMENT SUMMARY email that a food-delivery platform (Uber \
Eats or DoorDash) sends to an Australian venue (Chargrill Charlie's). These say how much \
the platform is paying the venue for a week of delivery orders. Return structured data.

Two formats:
- Uber Eats ("Payment Summary for ...") is detailed: a weekly "Total Sales"/"Sales (Inc. \
GST)" gross, a per-day table, an "UberEATS Fee", ad spend, adjustments, and a final \
"Total Payment" — that Total Payment is the actual net deposited.
- DoorDash ("Your store will receive a payment of $X") is minimal: usually ONLY the net \
payout amount and the date range. When a field isn't present, return 0 / "" — do NOT guess.

Rules:
- platform: "Uber Eats" or "DoorDash" (use the hint if given, else read the header/sender).
- period_start, period_end: the pay week's start and end dates, ISO YYYY-MM-DD. These \
emails print dates US MONTH-FIRST (M/D/YY): "5/25/26" -> 2026-05-25, "06/01/2026" -> \
2026-06-01. Convert carefully — the month comes first.
- gross_incl_gst: the week's TOTAL sales INCLUDING GST (Uber "Total" of the Sales (Inc. \
GST) column, e.g. 14761.20). 0 if the email doesn't state gross sales (typical DoorDash).
- net_payout: the ACTUAL money the platform pays the venue this week — Uber "Total \
Payment"; DoorDash "Your store will receive a payment of $X". Plain decimal, positive.
- ad_spend: marketing / ad spend charged this week (Uber "Total ad spend"), reported as a \
POSITIVE number. 0 if none shown.
- fees_total: the platform's main service/commission fee for the week (Uber "UberEATS \
Fee"), as a POSITIVE number. 0 if not broken out.
- orders: the number of orders in the week if shown (Uber "ORDERS"/"Total"). Integer, else 0.
- confidence: "high" if clear and the figures are consistent, else "medium"/"low".
Return all numbers as plain decimals (no $, no thousands separators; fees/ad positive even \
though printed negative). Every field is required: missing text -> "", missing number -> 0.

Return ONLY a single JSON object — no prose, no markdown fences — with EXACTLY these keys:
{
  "platform": "Uber Eats" | "DoorDash",
  "period_start": "YYYY-MM-DD",
  "period_end": "YYYY-MM-DD",
  "gross_incl_gst": number,
  "net_payout": number,
  "ad_spend": number,
  "fees_total": number,
  "orders": integer,
  "confidence": "high" | "medium" | "low"
}"""


class DeliveryPayout(BaseModel):
    platform: str
    period_start: str
    period_end: str = ""
    gross_incl_gst: float = 0   # weekly sales incl GST (Uber); 0 if not shown (DoorDash)
    net_payout: float = 0       # actual money deposited
    ad_spend: float = 0         # marketing/ad spend (positive); 0 if none
    fees_total: float = 0       # platform service/commission fee (positive); 0 if not shown
    orders: int = 0
    confidence: str = "medium"


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's reply (tolerates ```fences``` or stray prose)."""
    s = text.strip()
    if "```" in s:
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        s = s[i:j + 1]
    return json.loads(s)


def _read(client, model, content) -> DeliveryPayout:
    """Plain JSON-mode read, validated with the pydantic model (same approach as
    remittance_extract — a flat object, but kept consistent with the other body extractors)."""
    resp = client.messages.create(
        model=model,
        max_tokens=1500,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return DeliveryPayout(**_extract_json(text))


def extract_delivery_payout(file_bytes: Optional[bytes] = None, media_type: Optional[str] = None,
                            text: Optional[str] = None, platform_hint: Optional[str] = None,
                            client: Optional[anthropic.Anthropic] = None) -> DeliveryPayout:
    """Extract ONE weekly delivery payment summary.

    Email body (the normal case): pass text — raw HTML (Uber) or plain text (DoorDash) are
    both fine, Claude reads them directly. PDF/image: pass file_bytes (+ media_type).

    Escalates to Opus when the first (Sonnet) read isn't 'high' confidence.
    """
    client = client or anthropic.Anthropic()
    if text:
        content = [{"type": "text", "text": text}]
    else:
        fb, mt = _prep_image(file_bytes, media_type or "application/pdf")
        content = [_doc_block(base64.standard_b64encode(fb).decode("utf-8"), mt)]
    hint = f" The platform is {platform_hint}." if platform_hint else ""
    content.append({"type": "text", "text":
                    f"Extract this weekly delivery payment summary.{hint}"})

    try:
        data = _read(client, MODEL, content)
    except (json.JSONDecodeError, ValidationError, anthropic.APIError):
        data = None  # Sonnet returned unparseable JSON / API hiccup -> let Opus try
    if data is None or data.confidence != "high":
        try:
            data = _read(client, ESCALATE_MODEL, content)
        except Exception:
            if data is None:
                raise  # both reads failed -> surface it so the ingest Action logs the file
    return data
