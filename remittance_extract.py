"""Claude extraction for platform remittances -> structured JSON.

One function handles the three payment documents the catering platforms send:
  - Hampr     -> "Remittance Advice" PDF (lists Order #s with amount paid)
  - Yordar    -> "Recipient Generated Invoice" (RGI) PDF — weekly self-billed doc
  - Eat First -> "Recipient Created Tax Invoice" (RCTI) PDF — monthly, ORD-xxxxxx lines

These are the money-received side of catering_orders: each document lists the platform
orders it pays for, which the app matches back to catering_orders by order number to
show what's still outstanding per platform.

Mirrors catering_extract.py: first read on Sonnet, escalate to Opus if confidence
isn't 'high'. Reuses extract.py's image/PDF helpers.
"""
import base64
import json
from typing import List, Optional

import anthropic
from pydantic import BaseModel, ValidationError

from extract import _prep_image, _doc_block, MODEL, ESCALATE_MODEL

SYSTEM = """You read PAYMENT documents that catering platforms send to an Australian \
venue (Chargrill Charlie's) — the venue SUPPLIED the orders and these documents say \
which orders are being paid for. Three formats arrive:
- Hampr: "REMITTANCE ADVICE" — rows like "Order#97241on13-Apr|899" with InvoiceTotal / \
AmountPaid columns (the |899 is the venue id, not part of the order number).
- Yordar: "Recipient Generated Invoice" (#RGI-...) — weekly; per customer it lists \
"Order #575816 <description> <date> <GST> <total>".
- Eat First / Order-In: "Recipient Created Tax Invoice" (e.g. AU60031-308187) — monthly; \
an Order Summary table of ORD-xxxxxx rows with sales and commission columns.
Return structured data.

Rules:
- platform: which platform sent it. Use the platform hint if one is given; otherwise read \
it from the document ("Hampr Pty Ltd" -> "Hampr"; "yordar.com.au" -> "Yordar"; "Order-In \
Pty Ltd trading as EatFirst" -> "Eat First").
- doc_ref: the document's own reference — Yordar "#RGI-260608006" -> "RGI-260608006"; \
Eat First "Invoice No. AU60031-308187" -> "AU60031-308187". Hampr remittances have no \
number: use "" .
- doc_date: the payment / invoice date, ISO YYYY-MM-DD. Australian day-first \
("10Jun2026" -> 2026-06-10, "27/02/2026" -> 2026-02-27).
- total_paid: the total money the platform pays the venue with this document — Hampr \
"TotalAUDpaid"; Yordar "Total (inc. GST)"; Eat First "Amount to be deposited to your \
account". Plain decimal.
- lines: one entry per ORDER the document pays for — capture them all, never merge or \
summarise. Each line has:
    - order_ref: the platform's order number ONLY — "Order#97241on13-Apr|899" -> "97241"; \
"Order #575816" -> "575816"; "ORD-378600" -> "ORD-378600". Never include the date or \
venue id.
    - order_date: that order's delivery/invoice date, ISO YYYY-MM-DD ("13Apr2026" -> \
2026-04-13; a "13-Apr" with no year takes the document's year).
    - company: the customer/company named for that order if shown (Yordar shows it, e.g. \
"Anduril"); "" if the document doesn't name one (Hampr, Eat First).
    - amount: the money amount for that order as printed — Hampr "AmountPaid"; Yordar the \
order's Total; Eat First the order's "GST (10%) Applicable Sales (ex GST)" plus "GST Free \
Sales". Plain decimal.
    - commission: the commission deducted for that order where shown (Eat First — add the \
GST-free and GST-applicable commission amounts and report a POSITIVE number); 0 if the \
document doesn't show per-order commission.
- confidence: "high" if clear and complete, "medium" if some fields inferred, "low" if \
blurry/partial.
Return all numbers as plain decimals (no $ or thousands separators; commission positive \
even if printed negative). Every field is required: when a value is not present on the \
document, use an empty string "" for text fields and 0 for number fields — never omit a \
field.

Return ONLY a single JSON object — no prose, no markdown code fences — with EXACTLY these keys:
{
  "platform": "string",
  "doc_ref": "string",
  "doc_date": "YYYY-MM-DD",
  "total_paid": number,
  "lines": [
    {"order_ref": "string", "order_date": "YYYY-MM-DD", "company": "string", "amount": number, "commission": number}
  ],
  "confidence": "high" | "medium" | "low"
}"""


class RemittanceLine(BaseModel):
    order_ref: str
    order_date: str = ""
    company: str = ""       # customer named for the order (Yordar); "" if not shown
    amount: float = 0       # money for that order as printed
    commission: float = 0   # per-order commission where shown (Eat First); 0 otherwise


class RemittanceDoc(BaseModel):
    platform: str
    doc_ref: str = ""       # RGI / RCTI number; "" for Hampr remittances (no number)
    doc_date: str
    total_paid: float = 0   # the deposit this document pays
    lines: List[RemittanceLine]
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


def _read(client, model, content) -> RemittanceDoc:
    """Plain JSON-mode read, validated with the pydantic model (same approach as
    catering_extract — constrained-grammar output times out on list-of-objects schemas)."""
    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return RemittanceDoc(**_extract_json(text))


def extract_remittance(file_bytes: Optional[bytes] = None, media_type: Optional[str] = None,
                       text: Optional[str] = None, platform_hint: Optional[str] = None,
                       client: Optional[anthropic.Anthropic] = None) -> RemittanceDoc:
    """Extract ONE remittance / RGI / RCTI document.

    PDF/image: pass file_bytes (+ media_type, e.g. "application/pdf").
    Email body: pass text — raw HTML is accepted, Claude reads it directly.

    Escalates to Opus when the first (Sonnet) read isn't 'high' confidence.
    """
    client = client or anthropic.Anthropic()
    if text:
        content = [{"type": "text", "text": text}]
    else:
        fb, mt = _prep_image(file_bytes, media_type or "application/pdf")
        content = [_doc_block(base64.standard_b64encode(fb).decode("utf-8"), mt)]
    hint = f" The platform is {platform_hint}." if platform_hint else ""
    content.append({"type": "text", "text": f"Extract this remittance/payment document.{hint}"})

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
