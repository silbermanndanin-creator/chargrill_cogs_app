"""Daily/weekly digest for Chargrill COGS — runs headless (GitHub Actions cron).

Reads the SAME data the app uses (Supabase when SUPABASE_URL/KEY are set, else local
CSV) and emails a short summary: yesterday's sales, week-to-date COGS % and labour %
vs target, supplier price rises, and any category over its budget.

Configure via environment / GitHub repo secrets:
  SUPABASE_URL, SUPABASE_KEY                 (same as the app — to read cloud data)
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS
  DIGEST_FROM (defaults to SMTP_USER), DIGEST_TO (comma-separated recipients)

If SMTP isn't configured the digest is printed to stdout (visible in the Actions
log) rather than emailed — so it's safe to run before you add the secrets.

Run locally:  python digest.py            # uses today's date
"""
import os
import json
import smtplib
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd

import config
import storage
import metrics


def build_digest(today=None):
    """Return a dict of the numbers behind the digest for a given day."""
    today = today or dt.date.today()
    yest = today - dt.timedelta(days=1)
    wk = storage.iso_week_of(today)

    df = storage.load_invoices()
    lines = metrics.explode_lines(df)
    pos = storage.load_pos_days()

    def _sum(frame, col):
        return float(pd.to_numeric(frame[col], errors="coerce").fillna(0).sum()) if not frame.empty else 0.0

    # Use ACTUAL Uber/DoorDash payouts where a weekly statement has landed (else the flat
    # 40% estimate) — keeps the digest's revenue/COGS% in step with the app.
    deliv = storage.load_delivery_payouts()
    keep = metrics.delivery_keep_map(deliv, pos)
    yrow = pos[pos["date"].astype(str) == yest.isoformat()] if not pos.empty else pos
    wk_rev = metrics.pos_revenue_map(pos, "iso_week", keep_map=keep).get(wk, 0.0)
    day_net = metrics.pos_revenue_map(pos, "date", keep_map=keep) if not pos.empty else {}
    wk_cogs = metrics.food_cogs_for_period(df, "iso_week", wk)
    lab = storage.labour_map("week").get(wk, {}).get("cost", 0.0)

    spend, _ = (metrics.spend_and_deliveries(df, "iso_week", wk)
                if not df.empty else (pd.Series(dtype=float), None))
    over = []
    if wk_rev > 0 and len(spend):
        for s, v in spend.items():
            stt = config.status_for(v / wk_rev, s)
            if stt in ("amber", "red"):
                over.append((s, v / wk_rev * 100, stt))
        over.sort(key=lambda x: -x[1])

    anom = metrics.price_anomalies(lines, min_pct=8.0)

    cat = storage.load_catering_orders()

    def _catering_for(day):
        out = []
        if cat.empty:
            return out
        for _, r in cat.iterrows():
            try:
                if pd.to_datetime(r["deliver_date"]).date() != day:
                    continue
            except Exception:
                continue
            try:
                raw = r["line_items"]
                items = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                items = []
            n = sum(float(li.get("quantity") or 1) for li in items)
            try:
                hc = int(r.get("headcount")) if pd.notna(r.get("headcount")) else None
            except (TypeError, ValueError):
                hc = None
            out.append({"platform": r.get("platform") or "Catering",
                        "time": r.get("deliver_time") or "",
                        "company": r.get("company") or "",
                        "contact": r.get("contact_name") or "",
                        "headcount": hc,
                        "items": int(n) if n == int(n) else round(n, 2)})
        out.sort(key=lambda x: x["time"])
        return out

    return {
        "today": today, "yesterday": yest, "week": wk,
        "y_net": day_net.get(yest.isoformat(), 0.0), "y_incl": _sum(yrow, "total_incl_gst"),
        "wk_rev": wk_rev, "wk_cogs": wk_cogs,
        "cogs_pct": (wk_cogs / wk_rev) if wk_rev > 0 else None,
        "lab": lab, "lab_pct": (lab / wk_rev) if wk_rev > 0 else None,
        "over": over, "price_rises": anom,
        "cater_today": _catering_for(today),
        "cater_tomorrow": _catering_for(today + dt.timedelta(days=1)),
    }


def _fmt_pct(p):
    return "—" if p is None else f"{p * 100:.1f}%"


def render_text(d):
    L = [f"Chargrill COGS — digest for {d['today']:%a %d %b %Y}", ""]
    L.append(f"Yesterday ({d['yesterday']:%a %d %b}): ${d['y_net']:,.0f} net ex-GST "
             f"(${d['y_incl']:,.0f} incl GST)")
    L.append("")
    L.append(f"Week to date ({d['week']}):")
    gp, rp = config.TOTAL_COGS_GREEN, config.TOTAL_COGS_RED
    L.append(f"  Revenue:  ${d['wk_rev']:,.0f}")
    L.append(f"  COGS:     ${d['wk_cogs']:,.0f}  ({_fmt_pct(d['cogs_pct'])}, target <={gp*100:.0f}%)")
    L.append(f"  Labour:   ${d['lab']:,.0f}  ({_fmt_pct(d['lab_pct'])}, target <={config.LABOUR_GREEN*100:.0f}%)")
    L.append("")
    if d["over"]:
        L.append("Over budget this week:")
        for s, pct, stt in d["over"]:
            L.append(f"  {'🔴' if stt == 'red' else '🟠'} {s}: {pct:.1f}% of sales")
        L.append("")
    if not d["price_rises"].empty:
        L.append("Supplier price rises (vs last delivery):")
        for r in d["price_rises"].head(8).itertuples():
            L.append(f"  ▲ {r.Item} ({r.Supplier}) {r.Change} -> ${r.Now:,.2f}/unit")
        L.append("")
    for label, key in [("Today", "cater_today"), ("Tomorrow", "cater_tomorrow")]:
        orders = d.get(key) or []
        if orders:
            L.append(f"Catering {label.lower()}:")
            for o in orders:
                t = f" {o['time']}" if o["time"] else ""
                whofor = o.get("company") or o.get("contact") or ""
                who = f" — {whofor}" if whofor else ""
                ppl = f" ({o['headcount']} ppl)" if o.get("headcount") else ""
                L.append(f"  🥗{t} {o['platform']}: {o['items']} item(s){who}{ppl}")
            L.append("")
    L.append("— Chargrill COGS")
    return "\n".join(L)


def render_html(d):
    gp = config.TOTAL_COGS_GREEN
    rows = render_text(d).split("\n")
    body = "<br>".join(x.replace("  ", "&nbsp;&nbsp;") for x in rows)
    return f"<div style='font-family:Inter,Arial,sans-serif;font-size:14px;color:#0f172a'>{body}</div>"


def send_email(subject, text, html=None):
    host = os.environ.get("SMTP_HOST")
    to = os.environ.get("DIGEST_TO")
    if not (host and to):
        print("[digest] SMTP not configured — printing instead of emailing:\n")
        print(text)
        return False
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    sender = os.environ.get("DIGEST_FROM") or user or "noreply@chargrill.local"
    recipients = [a.strip() for a in to.split(",") if a.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text, "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.ehlo()
        try:
            s.starttls()
            s.ehlo()
        except smtplib.SMTPException:
            pass  # server without STARTTLS
        if user:
            s.login(user, pw)
        s.sendmail(sender, recipients, msg.as_string())
    print(f"[digest] Emailed to {', '.join(recipients)}")
    return True


def main():
    d = build_digest()
    subject = f"Chargrill COGS — {d['today']:%a %d %b}: COGS {_fmt_pct(d['cogs_pct'])}"
    send_email(subject, render_text(d), render_html(d))


if __name__ == "__main__":
    main()
