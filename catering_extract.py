"""Claude extraction for catering orders (PDF/image OR email-body text) -> structured JSON.

One function handles all four platforms:
  - Eat First / Yordar  -> PDF attachment  (pass file_bytes + media_type)
  - Hampr               -> HTML email body (pass text=...; raw HTML is fine, Claude parses it)
  - Online Catering     -> PDF or text from Slack (whichever arrives)

Mirrors extract.py: first read on Sonnet, escalate to Opus if confidence isn't 'high'.
Reuses extract.py's image/PDF helpers so there's one place that knows how to prep a file.
"""
import base64
import json
from typing import List, Optional

import anthropic
from pydantic import BaseModel, ValidationError

from extract import _prep_image, _doc_block, MODEL, ESCALATE_MODEL

SYSTEM = """You read catering orders for an Australian salad/chicken venue (Chargrill \
Charlie's) — the venue PREPARES these orders. The order arrives as a PDF/image, or as the \
text/HTML body of an order email, from one of: Hampr, EatFirst, Yordar, Online Catering. \
Return structured data.

Rules:
- platform: which platform the order is from. Use the platform hint if one is given; \
otherwise read it from the logo/header ("EatFirst", "Yordar", etc.).
- order_type: "delivery" or "pickup". A "Packing Slip / Pick Up", or a pickup time slot, \
is "pickup"; anything with a delivery address/time is "delivery".
- deliver_date: the delivery OR pickup date, ISO YYYY-MM-DD. Australian day-first \
(e.g. "10 June 2026" -> 2026-06-10, "11-Jun-2026" -> 2026-06-11).
- deliver_time: the delivery/pickup time, 24h HH:MM ("11:45am" -> "11:45", "12:00 pm" -> "12:00").
- headcount: the number of people the order feeds if shown (e.g. "GROUP SIZE", "Number of \
People"). Integer, else null.
- company: the business/organisation the order is FOR — the customer company (e.g. EatFirst \
"COMPANY REF" -> "DHL Global Forwarding Pty Ltd"; Yordar "Company name" -> "Anduril"). NOT \
"Chargrill Charlie's" / a "Partner:" field (that's the venue). "" for a personal order.
- contact_name, phone: the CUSTOMER's on-site contact for this order (the delivery contact, \
or the person picking up). Do NOT use "Chargrill Charlie's" or a "Partner:" field — that is \
the venue, not the customer.
- address: the DELIVERY address (for a pickup order, the pickup location). If BOTH a pickup \
address and a delivery address are shown, use the DELIVERY one. Put it on one line.
- order_ref: the platform's order number/reference (e.g. "ORD-423198", "#573262", "27438").
- line_items: every FOOD/menu line — capture them all, never merge or summarise. EXCLUDE \
non-food lines: "Delivery fee", "Service fee", "Surcharge", discounts, and totals (those go \
in items_total, never as items). Each item has:
    - item: the menu item name as printed (e.g. "Greek Salad, Large Platter").
    - quantity: the quantity for that line (the QTY/Quantity column). A per-person line is 1.
    - person: the individual's name if the order is named per-person (common on Hampr, e.g. \
"Shannon Ingrey"); else null.
    - unit_price: the per-item price if shown (the "Price (ex GST)" / "Per Item" column), \
else null. Plain decimal.
    - note: any special instruction, side, pack size or dietary tag on that line (e.g. \
"No Chicken, No Mushroom", "Sides: Mac + Cheese", "Box of 20", "VEGAN"). Else null. Do NOT \
put the generic "Serves X people" marketing blurb here.
- items_total: the FINAL payable order total. If a discounted total is shown ("Total with \
discount"), use that; otherwise the GST-inclusive Total. Plain decimal, else null.
- confidence: "high" if clear and complete, "medium" if some fields inferred, "low" if \
blurry/partial.
Return all numbers as plain decimals (no $ or thousands separators). Every field is \
required: when a value is not present on the document, use an empty string "" for text \
fields and 0 for number fields — never omit a field.

Return ONLY a single JSON object — no prose, no markdown code fences — with EXACTLY these keys:
{
  "platform": "string",
  "order_type": "delivery" | "pickup" | "",
  "company": "string",
  "deliver_date": "YYYY-MM-DD",
  "deliver_time": "HH:MM" or "",
  "headcount": integer,
  "contact_name": "string",
  "address": "string",
  "phone": "string",
  "order_ref": "string",
  "line_items": [
    {"item": "string", "quantity": number, "person": "string", "unit_price": number, "note": "string"}
  ],
  "items_total": number,
  "confidence": "high" | "medium" | "low"
}"""


class CateringItem(BaseModel):
    item: str
    quantity: float = 1
    person: str = ""        # per-person name (Hampr); "" if not a named line
    unit_price: float = 0   # 0 if no per-item price shown
    note: str = ""          # special instruction / side / pack size / dietary; "" if none


class CateringOrder(BaseModel):
    platform: str
    order_type: str = ""    # "delivery" | "pickup" | ""
    company: str = ""       # the business/org the order is FOR (DHL, Anduril); "" if personal
    deliver_date: str
    deliver_time: str = ""
    headcount: int = 0      # 0 if not shown
    contact_name: str = ""
    address: str = ""
    phone: str = ""
    order_ref: str = ""
    line_items: List[CateringItem]
    items_total: float = 0  # 0 if no total shown
    confidence: str


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's reply (tolerates ```fences``` or stray prose)."""
    s = text.strip()
    if "```" in s:                       # strip a ```json ... ``` fence if present
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    i, j = s.find("{"), s.rfind("}")     # outermost object
    if i != -1 and j != -1 and j > i:
        s = s[i:j + 1]
    return json.loads(s)


def _read(client, model, content) -> CateringOrder:
    """Plain JSON-mode read (no constrained-grammar/output_format — that path times out
    compiling this list-of-objects schema). We validate the JSON with the pydantic model,
    so the result is the same shape as before."""
    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return CateringOrder(**_extract_json(text))


def extract_catering(file_bytes: Optional[bytes] = None, media_type: Optional[str] = None,
                     text: Optional[str] = None, platform_hint: Optional[str] = None,
                     client: Optional[anthropic.Anthropic] = None) -> CateringOrder:
    """Extract ONE catering order.

    PDF/image: pass file_bytes (+ media_type, e.g. "application/pdf").
    Email body (Hampr): pass text — raw HTML is accepted, Claude reads it directly.

    Escalates to Opus when the first (Sonnet) read isn't 'high' confidence, so the
    messy ones get the strongest model while the clean ones stay fast.
    """
    client = client or anthropic.Anthropic()
    if text:
        content = [{"type": "text", "text": text}]
    else:
        fb, mt = _prep_image(file_bytes, media_type or "application/pdf")
        content = [_doc_block(base64.standard_b64encode(fb).decode("utf-8"), mt)]
    hint = f" The platform is {platform_hint}." if platform_hint else ""
    content.append({"type": "text", "text": f"Extract this catering order.{hint}"})

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
