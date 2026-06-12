"""One-shot backfill of the catering platform receivables into catering_orders.

The app's 💰 Platform payments & outstanding section only counts orders captured
since the catering feed went live — but Hampr / Eat First / Yordar remittances are
about to arrive for ~$57k of OLDER orders whose invoices live in the Chargrill
Google Drive Catering folder ("Catering Platform Reconciliation.xlsx", 11 Jun 2026).
This script inserts those outstanding orders so incoming payment documents have
rows to tick off and the owed-$ figures are complete.

Decisions baked in (confirmed by the owner, 12 Jun 2026):
  - Source of truth is the reconciliation sheet, not a re-read of the PDFs (the
    invoice PDFs carry no platform order numbers — only inv #, customer, totals).
  - The two flagged duplicates are EXCLUDED: INV1038 (= INV1052, only one Hampr
    order 100158 exists for BCG 21 May) and INV995 (= INV1007, already paid).
  - PLATFORM invoices only — anything in the folder not prefixed Hampr / Yordar /
    Eat First (OLSH, UNSW, Swans, EHC, schools, …) is a direct customer, paid
    directly, and stays out of the platform receivables count.
  - "Hampt Rokt 15 May INV1037" is a typo'd Hampr invoice the sheet missed (it
    flagged order 100390 as not-yet-invoiced) — included at its real $737.17.
  - The one genuinely uninvoiced delivered Hampr order IS included (103670
    Commerce 11 Jun) with $0 until its invoice is raised.
  - Amounts are inc-GST invoice values (what Hampr/Yordar actually deposit).

Re-running is safe: rows are upserted on source_file ('driveback/INV1061').

Requires env / repo secrets: SUPABASE_URL, SUPABASE_KEY.
Run via the "Catering backfill" GitHub Action, or locally: python catering_backfill.py
"""
import storage

# (our inv #, customer/company, invoice date DD/MM/YYYY, platform order #, $ inc GST)
HAMPR = [
    ("1001", "Luxury Escapes", "17/04/2026", "97327", 1965.21),
    ("1010", "Finder", "21/04/2026", "97939", 718.96),
    ("1011", "Rokt", "22/04/2026", "97981", 779.66),
    # Filed in Drive as "Yordar Neos" but order 98410 is a Hampr order.
    ("1013", "NEOS Group", "23/04/2026", "98410", 2254.18),
    ("1017", "Luxury Escapes", "29/04/2026", "98878", 2023.00),
    ("1026", "Finder", "04/05/2026", "99169", 817.37),
    ("1027", "Riot Games", "06/05/2026", "99099", 2190.18),
    ("1041", "Okta", "06/05/2026", "99714", 785.98),
    ("1036", "Immutable", "08/05/2026", "100031", 526.02),
    # The recon sheet flagged order 100390 as "not yet invoiced", but the invoice
    # exists — filed as "Hampt Rokt 15 May INV1037.pdf" (typo'd platform prefix),
    # confirmed Hampr / Rokt / 15-05 / $737.17 inside the PDF.
    ("1037", "Rokt", "15/05/2026", "100390", 737.17),
    ("1050", "Klaviyo", "20/05/2026", "101284", 1318.98),
    ("1051", "Finder", "20/05/2026", "101022", 778.44),
    ("1052", "BCG", "21/05/2026", "100158", 710.12),
    ("1053", "Intuit", "21/05/2026", "101268", 449.41),
    ("1054", "Docusign", "26/05/2026", "101254", 1682.05),
    ("1064", "ARUP", "28/05/2026", "102049", 537.75),
    ("1070", "Luxury Escapes", "01/06/2026", "102822", 2023.00),
    ("1071", "Finder", "03/06/2026", "102243", 742.67),
    ("1072", "Meraki", "04/06/2026", "102550", 2512.09),
    ("1073", "ARUP", "04/06/2026", "102483", 935.00),
    ("1074", "Docusign", "04/06/2026", "102889", 1606.92),
    ("1076", "Airwallex", "04/06/2026", "102947", 1048.05),
    ("1077", "BCG", "09/06/2026", "103033", 1113.63),
    ("1083", "Nearmap", "09/06/2026", "103371", 1572.63),
]
HAMPR_TOTAL = 29091.30 + 737.17  # sheet total + INV1037, which the sheet missed

# Delivered but no invoice raised yet — included at $0 so the order exists for the
# remittance to match; the owed $ understates until the invoice is raised.
HAMPR_UNINVOICED = [
    ("Commerce", "11/06/2026", "103670"),
]

# Eat First documents carry no platform order number — matched by amount + date.
EATFIRST = [
    ("973", "Novo Nordisk", "27/03/2026", "", 343.34),   # OVERDUE (~30 Apr)
    ("1022", "Amazon", "06/05/2026", "", 376.54),
    ("1032", "McKinsey & Co", "08/05/2026", "", 1493.42),
    ("1033", "NRL", "11/05/2026", "", 339.49),
    ("1034", "Factory Mutual", "12/05/2026", "", 813.96),
    ("1035", "We Work Tenants", "14/05/2026", "", 488.09),
    ("1043", "Talent Int", "20/05/2026", "", 791.04),
    ("1063", "DHL", "25/05/2026", "", 1148.81),
    ("1082", "DHL", "10/06/2026", "", 866.50),
]
EATFIRST_TOTAL = 6661.19

# Yordar order numbers only appear once an RGI arrives — blank until then. The last
# three are on RGI-260608006 (1-7 Jun) awaiting deposit, so their refs are known.
YORDAR = [
    ("1002", "Joe (office order)", "13/04/2026", "", 84.12),
    ("1029", "Joe (office order)", "04/05/2026", "", 72.70),
    ("1024", "Maddox (Anduril Alexandria)", "07/05/2026", "", 1409.29),
    ("1025", "Barang (Anduril Barangaroo)", "07/05/2026", "", 1511.99),
    ("1042", "Joe (office order)", "11/05/2026", "", 102.51),
    ("1030", "Maddox (Anduril Alexandria)", "14/05/2026", "", 1264.29),
    ("1031", "Barang (Anduril Barangaroo)", "14/05/2026", "", 1484.80),
    ("1055", "Joe (office order)", "18/05/2026", "", 87.62),
    ("1046", "Nutanix", "19/05/2026", "", 1791.09),
    ("1047", "Ralph Lauren", "19/05/2026", "", 835.64),
    ("1048", "Maddox (Anduril Alexandria)", "21/05/2026", "", 1732.49),
    ("1049", "Barang (Anduril Barangaroo)", "21/05/2026", "", 1732.49),
    ("1060", "Joe (office order)", "23/05/2026", "", 80.20),
    ("1065", "Joe (office order)", "25/05/2026", "", 80.10),
    ("1061", "Maddox (Anduril Alexandria)", "28/05/2026", "", 1426.29),
    ("1062", "Barang (Anduril Barangaroo)", "28/05/2026", "", 1463.74),
    # Drive files named "July" but the PDFs are dated June.
    ("1009", "Joe (office order)", "09/06/2026", "", 80.10),
    ("1018", "Maddox (Anduril Alexandria)", "11/06/2026", "", 1426.29),
    ("1081", "Barang (Anduril Barangaroo)", "11/06/2026", "", 1535.75),
    ("1075", "Joe (office order)", "01/06/2026", "575816", 80.10),
    ("1068", "Maddox (Anduril Alexandria)", "04/06/2026", "572807", 1732.49),
    ("1069", "Barang (Anduril Barangaroo)", "04/06/2026", "572808", 1732.49),
]
YORDAR_TOTAL = 18201.50 + 3545.08  # outstanding + RGI-received-awaiting-payment


def _iso(d: str) -> str:
    day, month, year = d.split("/")
    return f"{year}-{month}-{day}"


def _row(platform, inv, company, date, order_ref, amount):
    return {
        "platform": platform,
        "order_type": "delivery",
        "company": company,
        "deliver_date": _iso(date),
        "deliver_time": "",
        "headcount": None,
        "contact_name": "",
        "address": "",
        "phone": "",
        "order_ref": order_ref,
        "line_items": [],
        "items_total": amount,
        "confidence": "high",
    }, f"driveback/INV{inv}" if inv else f"driveback/hampr-{order_ref}"


def main():
    orders = []
    for inv, company, date, ref, amount in HAMPR:
        orders.append(_row("Hampr", inv, company, date, ref, amount))
    for company, date, ref in HAMPR_UNINVOICED:
        orders.append(_row("Hampr", "", company, date, ref, 0.0))
    for inv, company, date, ref, amount in EATFIRST:
        orders.append(_row("Eat First", inv, company, date, ref, amount))
    for inv, company, date, ref, amount in YORDAR:
        orders.append(_row("Yordar", inv, company, date, ref, amount))

    # Totals must agree with the reconciliation sheet before anything is written.
    for plat, want in (("Hampr", HAMPR_TOTAL), ("Eat First", EATFIRST_TOTAL),
                       ("Yordar", YORDAR_TOTAL)):
        got = round(sum(o["items_total"] for o, _ in orders if o["platform"] == plat), 2)
        assert got == round(want, 2), f"{plat}: ${got:,.2f} != sheet ${want:,.2f}"

    for order, source_file in orders:
        storage.save_catering_order(order, source_file=source_file)
        print(f"[backfill] {order['platform']:<10} {source_file:<24} "
              f"{order['deliver_date']}  ref={order['order_ref'] or '-':<8} "
              f"${order['items_total']:,.2f}  {order['company']}")
    total = round(sum(o["items_total"] for o, _ in orders), 2)
    print(f"[backfill] done: {len(orders)} orders, ${total:,.2f} inc GST "
          f"(sheet TOTAL OWED $57,499.07 + INV1037 $737.17 the sheet missed "
          f"+ $0 for uninvoiced order 103670)")


if __name__ == "__main__":
    main()
