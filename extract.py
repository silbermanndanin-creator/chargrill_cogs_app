"""Claude Vision invoice extraction -> structured JSON."""
import base64
import io
from typing import List, Optional
from pydantic import BaseModel
from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageStat
import anthropic

MODEL = "claude-sonnet-4-6"          # fast, strong default for the first read
ESCALATE_MODEL = "claude-opus-4-8"   # most capable — re-reads shaky invoices for reliability

# Claude downscales any image whose long edge exceeds ~1568px before reading it,
# so uploading a full 12MP phone photo just wastes upload time for no accuracy gain.
# We resize/compress to this target ourselves: ~15-20x smaller upload, same (often
# better) read quality. PDFs are left untouched — they're small and read natively.
MAX_EDGE = 1568


# 3x3 Laplacian (edge-detect) kernel. The variance of an image's Laplacian is a standard
# proxy for focus: lots of strong edges -> sharp -> high variance; a blurry photo is smooth
# -> low variance. scale=1 because the kernel sums to zero.
_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1)


def _quality_stats(gray):
    """(contrast, sharpness) of a grayscale image, measured on a centre crop so the page
    background/border doesn't skew it. contrast = stddev of luminance (low = dim/washed-out);
    sharpness = variance of the Laplacian (low = out of focus)."""
    w, h = gray.size
    box = (int(w * 0.1), int(h * 0.1), int(w * 0.9), int(h * 0.9))
    c = gray.crop(box) if (box[2] > box[0] and box[3] > box[1]) else gray
    return ImageStat.Stat(c).stddev[0], ImageStat.Stat(c.filter(_LAPLACIAN)).var[0]


def _auto_enhance(img):
    """Adaptively clean a grayscale photo. Every image gets a light, safe histogram stretch
    and a threshold-gated sharpen; a STRONGER contrast/sharpen boost is added only when the
    photo measures as genuinely dim or soft. So a crisp, well-lit invoice comes through
    almost untouched (we don't crush faint print), while a blurry phone snap gets rescued.
    Thresholds are heuristics tuned for downscaled phone photos of A4/A5 invoices."""
    try:
        contrast, sharpness = _quality_stats(img)
    except Exception:
        contrast, sharpness = 100.0, 10000.0  # measurement failed -> assume good, enhance gently
    # Always: gentle histogram stretch (clips 0.5% tails). Rescues dull photos; ~no-op on good ones.
    img = ImageOps.autocontrast(img, cutoff=0.5)
    # Extra linear contrast ONLY for flat/dim images.
    if contrast < 40:
        img = ImageEnhance.Contrast(img).enhance(1.6)
    elif contrast < 55:
        img = ImageEnhance.Contrast(img).enhance(1.25)
    # Sharpen harder when soft, lighter when already sharp. threshold=3 keeps it off flat
    # paper so it doesn't amplify scanner/sensor noise.
    pct = 170 if sharpness < 200 else 110 if sharpness < 600 else 70
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=pct, threshold=3))


def _prep_image(file_bytes: bytes, media_type: str):
    """Clean up, downscale + JPEG-compress a photo before upload. PDFs pass through unchanged.

    Phone invoice photos are often soft or unevenly lit, so we convert to grayscale, downscale
    to Claude's ~1568px working size, then run an ADAPTIVE enhance (see _auto_enhance) that only
    boosts dim/blurry shots hard and leaves clean ones near-original. EXIF rotation is honoured
    first so sideways photos read upright. On any failure, returns the original bytes/type so
    extraction never breaks because of image handling.
    """
    if media_type == "application/pdf":
        return file_bytes, media_type
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = ImageOps.exif_transpose(img)            # honour phone camera rotation
        img = img.convert("L")                        # grayscale: strip colour noise / shadows
        if max(img.size) > MAX_EDGE:                   # downscale first (Claude works at ~1568px)
            img.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)  # preserves aspect ratio
        img = _auto_enhance(img)                       # measure, then enhance to suit the photo
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
    - pack_size: when the UOM code carries an inner-unit count — e.g. "CTN-6", "CTN-12", \
"CTN-4", "CTN-3" — the INTEGER number printed after the code (6, 12, 4, 3): how many \
single sellable units are in one billed carton/pack. For those lines the unit_price is the \
price of the WHOLE carton, so this lets the app compare prices on a true per-unit basis. \
Null for single units (EA, kg, litre) and for a plain "CTN"/"CARTON" with no number.
    - unit_price: the printed PRICE PER UNIT for this line. If the line shows a per-kg \
price (e.g. "$12.50/kg", "12.50 P/KG", "@ 12.50 kg"), record THAT per-kg figure here \
and set unit to kg — this is the number to prefer. If instead only a per-each/per-carton \
price is printed, record that. Null if no per-unit price is printed (only a line total). \
Record the price exactly as printed; do not compute or round it.
    - amount: the line total AS PRINTED in the amount/total column. This is the NET amount \
AFTER any line discount — record it exactly as printed; do NOT add the discount back. \
(ex-GST if the invoice lists ex-GST, otherwise the printed line amount.)
    - discount: the per-line discount shown for this line (the discount column), as a POSITIVE \
number. Many distributors (e.g. St George) discount every line, so amount = quantity x \
unit_price - discount. Null or 0 when there is no discount column or no discount on the line.
- total_ex_gst: the invoice total EXCLUDING GST. Australian GST is 10%. If the invoice \
only shows a GST-inclusive total, compute ex-GST = inclusive_total / 1.1.
- total_inc_gst and gst_amount: include if printed; otherwise leave null.
- confidence: "high" if the image is clear and totals reconcile, "medium" if some fields \
were inferred, "low" if the image is blurry/partial or numbers are hard to read.
Return numbers as plain decimals (no currency symbols or thousands separators).

You are an expert OCR invoice parser extracting long multi-line documents.
- Always cross-reference rows horizontally to prevent line-skip errors.
- If a value looks blurry, use the surrounding line totals or subtotals to logically infer \
whether a character is an '8' vs '3', or a '0' vs '6'."""


class LineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    pack_size: Optional[int] = None     # inner-unit count from a pack UOM (CTN-6 -> 6); null otherwise
    unit_price: Optional[float] = None  # printed per-unit price; per-kg rate when shown
    discount: Optional[float] = None    # per-line discount (positive $); amount is already net of it
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


# ---------------- Document triage (gate the emailed inbox) ----------------
# Suppliers email more than just invoices: monthly ACCOUNT STATEMENTS (which sum a whole
# month of invoices into one big total — catastrophic if read as a single invoice), credit
# notes, remittances, and the odd non-COGS expense. classify_document() does a cheap first
# read so inbox_ingest can route anything that isn't a single supplier invoice to review/
# instead of into COGS. The app's own upload path doesn't use this — it's inbox-only.
CLASSIFY_SYSTEM = """You are triaging a PDF/photo emailed to an Australian hospitality venue \
(Chargrill Charlie's) to decide if it is a single SUPPLIER INVOICE that should be entered into \
the food-cost system.

Return:
- document_type: exactly one of —
    'invoice'      a single tax invoice billing for the goods/services of ONE order/delivery. \
A tax invoice counts as 'invoice' even if the goods are delivered later (some suppliers invoice \
the order ahead of delivery).
    'statement'    an ACCOUNT STATEMENT / month-end statement: it summarises MULTIPLE invoices, \
lists several invoice numbers/dates, or shows an account balance or aged balances \
(current/30/60/90 days) or a 'balance brought forward'. This is NOT one invoice — choose \
'statement' whenever the document totals up more than one invoice.
    'credit_note'  a credit note, refund or adjustment.
    'remittance'   a remittance advice / payment confirmation.
    'order'        a purchase order or order confirmation that is not itself a tax invoice.
    'other'        anything else (price list, newsletter, contract, etc.).
- supplier_name: the business that issued the document, exactly as printed.
- confidence: 'high' | 'medium' | 'low'.
Return only these three fields."""


class DocClass(BaseModel):
    document_type: str
    supplier_name: str
    confidence: str = "medium"


def classify_document(pages, media_type: Optional[str] = None,
                      client: Optional[anthropic.Anthropic] = None) -> DocClass:
    """Cheap first-pass triage of an emailed file: a single supplier INVOICE, or a
    statement / credit note / remittance / order / other? Returns document_type +
    supplier_name so the inbox can skip anything that isn't a real invoice before paying
    for a full extraction. `pages` is bytes (with media_type) or a list of (bytes, mt)."""
    client = client or anthropic.Anthropic()
    if isinstance(pages, (bytes, bytearray)):
        pages = [(pages, media_type or "image/jpeg")]
    content = []
    for fb, mt in pages:
        fb, mt = _prep_image(fb, mt or "image/jpeg")
        b64 = base64.standard_b64encode(fb).decode("utf-8")
        content.append(_doc_block(b64, mt))
    content.append({"type": "text", "text":
                    "Classify this document (it may be one of several pages of the same document)."})
    resp = client.messages.parse(
        model=MODEL, max_tokens=300,
        system=[{"type": "text", "text": CLASSIFY_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_format=DocClass,
    )
    return resp.parsed_output


def _line_sum(inv: dict) -> float:
    """Sum of the line-item amounts (robust to nulls / bad values)."""
    s = 0.0
    for li in inv.get("line_items") or []:
        try:
            s += float(li.get("amount") or 0)
        except (TypeError, ValueError):
            pass
    return round(s, 2)


def reconciliation(inv: dict) -> dict:
    """Do the line amounts add up to the invoice total?

    Line amounts can be ex-GST OR GST-inclusive depending on the invoice, so a match to
    the ex-GST total, the inc-GST total, or ex-GST x1.1 all count as reconciling (otherwise
    every GST-inclusive invoice would false-alarm). Tolerance is 2% or $0.50, whichever is
    larger, to allow rounding / freight. Returns {checkable, ok, line_sum, target, diff}.
    """
    s = _line_sum(inv)
    targets = []
    for key in ("total_ex_gst", "total_inc_gst"):
        v = inv.get(key)
        if v:
            try:
                targets.append(round(float(v), 2))
            except (TypeError, ValueError):
                pass
    ex = inv.get("total_ex_gst")
    if ex:
        try:
            targets.append(round(float(ex) * 1.10, 2))  # inc-GST lines vs an ex-GST total
        except (TypeError, ValueError):
            pass
    if not targets or s <= 0:
        return {"checkable": False, "ok": True, "line_sum": s, "target": None, "diff": 0.0}
    target = min(targets, key=lambda t: abs(s - t))  # closest plausible total
    diff = round(s - target, 2)
    ok = abs(diff) <= max(0.50, 0.02 * target)
    return {"checkable": True, "ok": ok, "line_sum": s, "target": target, "diff": diff}


def reconciliation_hints(inv: dict) -> dict:
    """Pinpoint WHERE a line-vs-total mismatch likely sits, so it's quick to check against
    the paper invoice. Returns the reconciliation result plus:
      gap            signed line_sum - target (+ => lines exceed the total)
      direction      'high' (lines > total) | 'low' (lines < total)
      line_flags     lines whose amount != quantity*unit_price (a misread WITHIN a line)
      gap_candidates lines whose amount ~= |gap| (a likely duplicate / extra / missing line)
    Empty lists when nothing specific stands out (then it's a spread misread or a
    missing/extra line with no single matching amount)."""
    rec = reconciliation(inv)
    out = {**rec, "gap": rec["diff"],
           "direction": "high" if rec["diff"] > 0 else "low",
           "line_flags": [], "gap_candidates": []}
    if not rec["checkable"] or rec["ok"]:
        return out
    items = inv.get("line_items") or []
    g = abs(rec["diff"])
    for i, li in enumerate(items, 1):
        if not isinstance(li, dict):
            continue
        try:
            amt = round(float(li.get("amount") or 0), 2)
        except (TypeError, ValueError):
            continue
        desc = li.get("description") or f"line {i}"
        q, up = li.get("quantity"), li.get("unit_price")
        if q is not None and up is not None:
            try:
                comp = round(float(q) * float(up), 2)
                if abs(comp - amt) > 0.02:
                    out["line_flags"].append({"idx": i, "description": desc,
                                              "printed": amt, "computed": comp,
                                              "diff": round(amt - comp, 2)})
            except (TypeError, ValueError):
                pass
        if g > 0 and abs(amt - g) <= max(0.05, 0.01 * g):
            out["gap_candidates"].append({"idx": i, "description": desc, "amount": amt})
    return out


def _read_invoice(client, model, content) -> InvoiceData:
    resp = client.messages.parse(
        model=model,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_format=InvoiceData,
    )
    return resp.parsed_output


def extract_invoice(pages, media_type: Optional[str] = None,
                    client: Optional[anthropic.Anthropic] = None) -> InvoiceData:
    """Extract ONE invoice from one or more pages/photos.

    `pages` may be a single bytes object (with media_type) or a list of
    (file_bytes, media_type) tuples — all pages are combined into one invoice.

    Reliability: the first read uses Sonnet. If it comes back below 'high' confidence
    OR its line items don't reconcile to the total, the SAME invoice is re-read on Opus
    (the most capable model) and that result is preferred — so the hard invoices get the
    strongest model while the easy ones stay fast.
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
                    "Use the grand total from the final page if a running total spans pages. "
                    "This can be a long invoice (20+ rows) photographed on a phone: trace "
                    "horizontally across each row carefully, do not shift or skip lines, and map "
                    "every line item's description, quantity, unit price and line total exactly."})

    data = _read_invoice(client, MODEL, content)
    if data.confidence != "high" or not reconciliation(data.model_dump())["ok"]:
        try:
            strong = _read_invoice(client, ESCALATE_MODEL, content)
            # Prefer the Opus read unless it reconciles worse than the first pass.
            if reconciliation(strong.model_dump())["ok"] or \
                    not reconciliation(data.model_dump())["ok"]:
                data = strong
        except Exception:
            pass  # escalation failed (network/limit) -> keep the first read, never crash
    _verify_line_math(data)
    return data


def _verify_line_math(data: InvoiceData) -> None:
    """Deterministic safety net for a MISREAD line total — but only when it actually helps.

    Where a line has a quantity and a printed per-unit price, the expected net amount is
    quantity x unit_price - discount. We only overwrite the printed amount with that figure
    when doing so brings the line sum CLOSER to the invoice total. So an invoice that already
    reconciles is left exactly as printed — crucially including invoices with per-line
    discounts (St George etc.), where amount = qty x unit_price - discount and a naive
    qty x unit_price would wrongly add every discount back. Mutates `data` in place."""
    base = reconciliation(data.model_dump())
    if not base["checkable"] or base["ok"]:
        return  # already adds up (or no total to check) -> trust the printed line amounts
    fixed = []  # (item, old_amount, new_amount)
    for item in data.line_items:
        if item.quantity is None or item.unit_price is None:
            continue
        try:
            disc = float(item.discount or 0)
            recomputed = round(float(item.quantity) * float(item.unit_price) - disc, 2)
        except (TypeError, ValueError):
            continue
        if recomputed > 0 and abs((item.amount or 0) - recomputed) > 0.01:
            fixed.append((item, item.amount, recomputed))
    if not fixed:
        return
    for item, _old, new in fixed:
        item.amount = new
    if abs(reconciliation(data.model_dump())["diff"]) > abs(base["diff"]):
        for item, old, _new in fixed:  # corrections didn't help -> revert, leave as printed
            item.amount = old


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
