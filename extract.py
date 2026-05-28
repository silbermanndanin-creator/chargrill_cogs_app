"""Claude Vision invoice extraction -> structured JSON."""
import base64
from typing import List, Optional
from pydantic import BaseModel
import anthropic

MODEL = "claude-sonnet-4-6"  # swap to claude-opus-4-7 for hard handwritten dockets

SYSTEM = """You read Australian supplier invoices (photographed or PDF) for a hospitality \
venue (Chargrill Charlie's) and return structured data.

Rules:
- supplier_name: the supplier/business name exactly as printed on the invoice.
- invoice_date: the invoice date in ISO format YYYY-MM-DD. If only DD/MM/YYYY is shown, \
convert it (Australian day-first order).
- line_items: each product line with:
    - description: the product name.
    - quantity: the numeric quantity ordered (e.g. 12, 4.5). Null if not shown.
    - unit: the unit of measure, lowercased and normalised to one of: kg, ea, carton, \
box, case, tray, bag, litre, dozen, tub. Map "each"/"unit"/"units" -> ea, "ctn" -> \
carton, "kgs"/"kilo" -> kg, "tubs" -> tub. Record quantity and unit exactly as printed \
(e.g. 240 ea) — do not convert between units. Null if not shown.
    - amount: the line total (ex-GST if the invoice lists ex-GST, otherwise the printed \
line amount).
- total_ex_gst: the invoice total EXCLUDING GST. Australian GST is 10%. If the invoice \
only shows a GST-inclusive total, compute ex-GST = inclusive_total / 1.1.
- total_inc_gst and gst_amount: include if printed; otherwise leave null.
- confidence: "high" if the image is clear and totals reconcile, "medium" if some fields \
were inferred, "low" if the image is blurry/partial or numbers are hard to read.
Return numbers as plain decimals (no currency symbols or thousands separators)."""


class LineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    amount: float


class InvoiceData(BaseModel):
    supplier_name: str
    invoice_date: str
    line_items: List[LineItem]
    total_ex_gst: float
    total_inc_gst: Optional[float] = None
    gst_amount: Optional[float] = None
    confidence: str


def _content_blocks(b64: str, media_type: str) -> list:
    """A PDF goes in a `document` block; an image goes in an `image` block."""
    if media_type == "application/pdf":
        doc = {"type": "document",
               "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    else:
        doc = {"type": "image",
               "source": {"type": "base64", "media_type": media_type, "data": b64}}
    return [doc, {"type": "text", "text": "Extract this supplier invoice."}]


def extract_invoice(file_bytes: bytes, media_type: str,
                    client: Optional[anthropic.Anthropic] = None) -> InvoiceData:
    client = client or anthropic.Anthropic()
    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _content_blocks(b64, media_type)}],
        output_format=InvoiceData,
    )
    return resp.parsed_output


# ---------------- POS end-of-day takings slip ----------------
POS_SYSTEM = """You read a POS 'End Of Day' / 'Finalised Takings' slip from an Australian \
hospitality venue (Lightspeed). Use the top SUMMARY section and its 'Recorded' column \
(NOT the 'Counted' column). All amounts INCLUDE GST.

Return:
- business_date: the date printed on the slip, ISO format YYYY-MM-DD.
- total_incl_gst: the 'Total' value in the Recorded column (overall takings for the day).
- doordash_incl_gst: the Recorded amount on the 'Doordash' (or 'Doordash - Deliverect') \
line. 0 if there is no such line.
- ubereats_incl_gst: the Recorded amount on the 'UberEats' (or 'UberEats - Deliverect') \
line. 0 if there is no such line.
- confidence: "high" if clear and the figures reconcile, else "medium"/"low".
Return numbers as plain decimals (no $ or thousands separators)."""


class PosSlip(BaseModel):
    business_date: str
    total_incl_gst: float
    doordash_incl_gst: float = 0.0
    ubereats_incl_gst: float = 0.0
    confidence: str


def extract_pos_slip(file_bytes: bytes, media_type: str,
                     client: Optional[anthropic.Anthropic] = None) -> PosSlip:
    client = client or anthropic.Anthropic()
    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=1000,
        system=[{"type": "text", "text": POS_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [
            _content_blocks(b64, media_type)[0],
            {"type": "text", "text": "Extract the daily takings from this POS slip."}]}],
        output_format=PosSlip,
    )
    return resp.parsed_output
