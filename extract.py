"""Claude Vision invoice extraction -> structured JSON."""
import base64
import io
from typing import List, Optional
from pydantic import BaseModel
from PIL import Image, ImageOps
import anthropic

MODEL = "claude-sonnet-4-6"  # swap to claude-opus-4-7 for hard handwritten dockets

# Claude downscales any image whose long edge exceeds ~1568px before reading it,
# so uploading a full 12MP phone photo just wastes upload time for no accuracy gain.
# We resize/compress to this target ourselves: ~15-20x smaller upload, same (often
# better) read quality. PDFs are left untouched — they're small and read natively.
MAX_EDGE = 1568


def _prep_image(file_bytes: bytes, media_type: str):
    """Downscale + JPEG-compress a photo before upload. PDFs pass through unchanged.

    Returns (new_bytes, new_media_type). On any failure, returns the original
    bytes/type so extraction never breaks because of image handling.
    """
    if media_type == "application/pdf":
        return file_bytes, media_type
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = ImageOps.exif_transpose(img)  # honour phone camera rotation
        if img.mode != "RGB":
            img = img.convert("RGB")  # flatten PNG alpha / greyscale -> RGB for JPEG
        w, h = img.size
        long_edge = max(w, h)
        if long_edge > MAX_EDGE:
            scale = MAX_EDGE / long_edge
            img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return file_bytes, media_type

SYSTEM = """You read Australian supplier invoices (photographed or PDF) for a hospitality \
venue (Chargrill Charlie's) and return structured data.

Rules:
- supplier_name: the supplier/business name exactly as printed on the invoice.
- invoice_date: the invoice date in ISO format YYYY-MM-DD. If only DD/MM/YYYY is shown, \
convert it (Australian day-first order).
- line_items: each product line. CAPTURE EVERY LINE. Some produce invoices (e.g. \
St George) and seafood invoices (e.g. Blueseas) run to 30-60 lines across one or more \
pages — read them top to bottom and include every product row. Never skip, merge, \
summarise, or stop early; a missing line means an undercounted COGS. Each line has:
    - description: the product name.
    - quantity: the numeric quantity billed (e.g. 12, 4.5). Null if not shown. \
IMPORTANT: when the line is priced per kg, quantity is the KILOGRAM WEIGHT billed \
(e.g. 15.0), NOT the number of cartons/boxes/pieces. If both a pack count and a kg \
weight are shown, use the kg weight as the quantity and set unit to kg.
    - unit: the unit of measure, lowercased and normalised to one of: kg, ea, carton, \
box, case, tray, bag, litre, dozen, tub. Map "each"/"unit"/"units" -> ea, "ctn" -> \
carton, "kgs"/"kilo" -> kg, "tubs" -> tub. Use kg whenever the line is priced per kg. \
Otherwise record the unit as printed — do not convert between units. Null if not shown.
    - unit_price: the printed PRICE PER UNIT for this line. If the line shows a per-kg \
price (e.g. "$12.50/kg", "12.50 P/KG", "@ 12.50 kg"), record THAT per-kg figure here \
and set unit to kg — this is the number to prefer. If instead only a per-each/per-carton \
price is printed, record that. Null if no per-unit price is printed (only a line total). \
Record the price exactly as printed; do not compute or round it.
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
    unit_price: Optional[float] = None  # printed per-unit price; per-kg rate when shown
    amount: float


class InvoiceData(BaseModel):
    supplier_name: str
    invoice_date: str
    line_items: List[LineItem]
    total_ex_gst: float
    total_inc_gst: Optional[float] = None
    gst_amount: Optional[float] = None
    confidence: str


def _doc_block(b64: str, media_type: str) -> dict:
    """A PDF goes in a `document` block; an image goes in an `image` block."""
    if media_type == "application/pdf":
        return {"type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    return {"type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64}}


def _content_blocks(b64: str, media_type: str) -> list:
    return [_doc_block(b64, media_type), {"type": "text", "text": "Extract this supplier invoice."}]


def extract_invoice(pages, media_type: Optional[str] = None,
                    client: Optional[anthropic.Anthropic] = None) -> InvoiceData:
    """Extract ONE invoice from one or more pages/photos.

    `pages` may be a single bytes object (with media_type) or a list of
    (file_bytes, media_type) tuples — all pages are combined into one invoice.
    """
    client = client or anthropic.Anthropic()
    if isinstance(pages, (bytes, bytearray)):
        pages = [(pages, media_type or "image/jpeg")]
    content = []
    for fb, mt in pages:
        fb, mt = _prep_image(fb, mt or "image/jpeg")
        b64 = base64.standard_b64encode(fb).decode("utf-8")
        content.append(_doc_block(b64, mt))
    n = len(content)
    content.append({"type": "text", "text":
                    f"Extract this supplier invoice. It is provided as {n} page(s)/photo(s) — "
                    "treat them as ONE invoice and combine all line items across the pages. "
                    "Use the grand total from the final page if a running total spans pages."})
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
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
- bite_incl_gst: the amount on the line labelled exactly 'Bite Business'. Read it from the \
Recorded column in the Summary, or equivalently from the 'Total' under 'Bite Business' in \
the 'Breakdown by payment means' section lower on the slip (both show the same figure). \
This is its OWN line — do NOT use the separate 'App Payments' or 'App Ordering' lines, \
which are different and usually 0.00. Use 0 only if there is no 'Bite Business' line.
- cash_incl_gst: the amount on the 'Cash' line — the Recorded column in the Summary, or \
the 'Cash' Total in the 'Breakdown by payment means' section. 0 if there is no Cash line.
- confidence: "high" if clear and the figures reconcile, else "medium"/"low".
Return numbers as plain decimals (no $ or thousands separators)."""


class PosSlip(BaseModel):
    business_date: str
    total_incl_gst: float
    doordash_incl_gst: float = 0.0
    ubereats_incl_gst: float = 0.0
    bite_incl_gst: float = 0.0
    cash_incl_gst: float = 0.0
    confidence: str


def extract_pos_slip(file_bytes: bytes, media_type: str,
                     client: Optional[anthropic.Anthropic] = None) -> PosSlip:
    client = client or anthropic.Anthropic()
    file_bytes, media_type = _prep_image(file_bytes, media_type)
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
