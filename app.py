# rebuild marker 2026-05-29a — labour & prime cost (clears stale Streamlit Cloud module cache)
import os
import json
import datetime as dt
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

import config
import storage
import metrics
from extract import extract_invoice, extract_pos_slip
from lightspeed import get_revenue
import payroll

st.set_page_config(page_title="Chargrill COGS", page_icon="🍗", layout="wide")

COLORS = {"green": "#2faa5e", "amber": "#d9a300", "red": "#e0533d"}
LIGHT = {"green": "🟢", "amber": "🟠", "red": "🔴"}

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap');

:root{
  --bg:#0e1320; --surface:#161d2e; --surface2:#1c2742; --border:#28324d;
  --text:#e8ecf3; --muted:#8b95a7; --accent:#2ec4b6; --accent2:#5eead4;
  --radius:16px; --shadow:0 1px 2px rgba(0,0,0,.35),0 12px 30px rgba(0,0,0,.22);
}

/* typography */
html, body, .stApp, [data-testid="stAppViewContainer"],
button, input, select, textarea, .stMarkdown, p, span, label, div{
  font-family:'Inter',-apple-system,'Segoe UI',Roboto,sans-serif;
}
h1,h2,h3,h4,.hdr,.brand-name,.kpi .v,.tub .v{
  font-family:'Space Grotesk','Inter',sans-serif; letter-spacing:-.01em;
}
.stApp{ background:var(--bg); color:var(--text); }

/* blend Streamlit's top toolbar, keep room for it */
[data-testid="stHeader"]{ background:transparent; }
.block-container{ padding-top:3.5rem; max-width:1280px; }

/* branded app header bar */
.appbar{ display:flex; align-items:center; justify-content:space-between;
  padding:8px 2px 14px; border-bottom:1px solid var(--border); margin-bottom:14px; }
.brand{ display:flex; align-items:center; gap:12px; }
.brand-name{ font-size:1.18rem; font-weight:700; color:#fff; line-height:1.05; }
.brand-sub{ font-size:.72rem; color:var(--muted); font-weight:500; margin-top:2px; }
.appbar-period{ font-size:.78rem; color:var(--text); font-weight:600; background:var(--surface);
  border:1px solid var(--border); padding:7px 14px; border-radius:999px; white-space:nowrap; }

/* KPI cards */
.kpi{ background:linear-gradient(180deg,var(--surface2),var(--surface));
  border:1px solid var(--border); border-radius:var(--radius); padding:16px 18px;
  height:100%; box-shadow:var(--shadow); }
.kpi .t{ color:var(--muted); font-size:.67rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }
.kpi .v{ font-size:1.72rem; font-weight:700; color:#fff; line-height:1.15; margin-top:8px; }
.kpi .s{ font-size:.77rem; margin-top:6px; font-weight:600; }

.hdr{ font-size:1.6rem; font-weight:700; color:#fff; margin-bottom:.3rem; }

/* tub cards */
.tub{ background:linear-gradient(180deg,var(--surface2),var(--surface)); border:1px solid var(--border);
  border-radius:var(--radius); padding:14px 6px; text-align:center; box-shadow:var(--shadow); }
.tub .v{ font-size:1.85rem; font-weight:700; color:#fff; }
.tub .t{ color:var(--muted); font-size:.67rem; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }

/* tabs -> pill style with accent underline */
.stTabs [data-baseweb="tab-list"]{ gap:4px; border-bottom:1px solid var(--border); }
.stTabs [data-baseweb="tab"]{ height:auto; padding:9px 14px; background:transparent;
  border-radius:10px 10px 0 0; color:var(--muted); font-weight:600; font-size:.9rem; }
.stTabs [aria-selected="true"]{ color:#fff; background:var(--surface); }
.stTabs [data-baseweb="tab-highlight"]{ background:var(--accent); height:3px; }
.stTabs [data-baseweb="tab-border"]{ background:transparent; }

/* buttons */
.stButton>button, .stDownloadButton>button{ border-radius:10px; font-weight:600;
  border:1px solid var(--border); transition:all .12s ease; }
.stButton>button:hover, .stDownloadButton>button:hover{ border-color:var(--accent); color:var(--accent2); }
.stButton>button[kind="primary"]{ background:var(--accent); border-color:var(--accent); color:#06231f; }
.stButton>button[kind="primary"]:hover{ filter:brightness(1.08); color:#06231f; }

/* st.metric cards */
[data-testid="stMetric"]{ background:var(--surface); border:1px solid var(--border);
  border-radius:14px; padding:12px 16px; box-shadow:var(--shadow); }
[data-testid="stMetricValue"]{ font-family:'Space Grotesk',sans-serif; }

/* bordered containers, expanders, sidebar */
[data-testid="stExpander"]{ border:1px solid var(--border); border-radius:12px; background:var(--surface); }
[data-testid="stSidebar"]{ background:#0b101b; border-right:1px solid var(--border); }
section[data-testid="stSidebar"] h3{ color:#fff; }

hr{ border-color:var(--border); }
[data-testid="stAlert"]{ border-radius:12px; }
</style>""", unsafe_allow_html=True)


def get_api_key():
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY")


if get_api_key():
    os.environ["ANTHROPIC_API_KEY"] = get_api_key()
for _k in ("SUPABASE_URL", "SUPABASE_KEY"):
    try:
        _v = st.secrets.get(_k)
    except Exception:
        _v = None
    if _v:
        os.environ[_k] = _v


def prev_period_key(mode, ref):
    if mode == "Week":
        return storage.iso_week_of(ref - dt.timedelta(days=7))
    return (ref.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")


def fmt_qty(um):
    if not um:
        return "—"
    return " · ".join(f"{d['qty']:g} {u}" for u, d in sorted(um.items(), key=lambda kv: -kv[1]["qty"]))


def kpi(col, title, value, sub="", color="#8b95a7"):
    col.markdown(f"<div class='kpi'><div class='t'>{title}</div><div class='v'>{value}</div>"
                 f"<div class='s' style='color:{color}'>{sub}</div></div>", unsafe_allow_html=True)


def cogs_gauge(pct, gp, rp, axis_max=55):
    v = pct * 100
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=v,
        number={"suffix": "%", "font": {"size": 38, "color": "#fff", "family": "Space Grotesk"}},
        gauge={"axis": {"range": [0, axis_max], "tickcolor": "#8b95a7"},
               "bar": {"color": "rgba(0,0,0,0)"}, "borderwidth": 0,
               "steps": [{"range": [0, gp * 100], "color": "#1f7a4d"},
                         {"range": [gp * 100, rp * 100], "color": "#b8860b"},
                         {"range": [rp * 100, axis_max], "color": "#9c3a28"}],
               "threshold": {"line": {"color": "#fff", "width": 4}, "thickness": 0.8, "value": v}}))
    fig.update_layout(height=230, margin=dict(l=24, r=24, t=16, b=8),
                      paper_bgcolor="rgba(0,0,0,0)", font_color="#E8ECF3")
    return fig


def dark(fig, h=320):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=h,
                      margin=dict(l=10, r=10, t=10, b=10),
                      font=dict(family="Inter, sans-serif", color="#E8ECF3"),
                      legend=dict(orientation="h", y=-0.2))
    return fig


# ============ Sidebar: period + revenue ============
with st.sidebar:
    st.markdown("### 🍗 Chargrill COGS")
    mode = st.radio("Track by", ["Week", "Month"], horizontal=True)
    ref = st.date_input("Date in period", value=dt.date.today())
    if mode == "Week":
        monday = ref - dt.timedelta(days=ref.weekday())
        sunday = monday + dt.timedelta(days=6)
        period_key = storage.iso_week_of(ref)
        period_label = f"Week of {monday:%d %b} – {sunday:%d %b %Y}"
        p_start, p_end, p_col, p_type = monday, sunday, "iso_week", "week"
    else:
        period_key = ref.strftime("%Y-%m")
        period_label = ref.strftime("%B %Y")
        p_start = ref.replace(day=1)
        p_end = (pd.Timestamp(p_start) + pd.offsets.MonthEnd(1)).date()
        p_col, p_type = "month", "month"

    pos_df = storage.load_pos_days()
    pos_map = metrics.pos_revenue_map(pos_df, p_col)
    manual_map = storage.revenue_map(p_type)

    st.divider()
    st.markdown("**Revenue (ex-GST)**")
    rev_mode = st.radio("Source", ["POS slips (daily)", "Manual entry", "Lightspeed (K-Series)"],
                        label_visibility="collapsed")
    revenue = 0.0
    if rev_mode == "POS slips (daily)":
        revenue = float(pos_map.get(period_key, 0.0))
        bd = metrics.pos_breakdown(pos_df, p_col, period_key)
        if bd["days"]:
            st.caption(f"{bd['days']} day(s) · **${revenue:,.0f}** ex-GST — "
                       f"net after −{config.DELIVERY_COMMISSION*100:.0f}% on "
                       f"${bd['delivery_gross']:,.0f} delivery")
        else:
            st.caption("No POS slips this period — add one in **💰 Daily takings**.")
    elif rev_mode == "Manual entry":
        stored = float(manual_map.get(period_key, 0.0))
        revenue = st.number_input(f"{mode} net sales $", min_value=0.0, step=1000.0, value=stored)
        if revenue > 0 and revenue != stored:
            storage.set_revenue(p_type, period_key, revenue)
            manual_map[period_key] = revenue
    else:
        try:
            token = st.secrets.get("LIGHTSPEED_TOKEN")
            biz = st.secrets.get("LIGHTSPEED_BUSINESS_ID")
        except Exception:
            token = biz = None
        r = get_revenue(p_start, p_end, token, biz)
        revenue = float(r) if r else 0.0
        if not r:
            st.caption("Lightspeed not connected.")
    trend_rev_map = {**manual_map, **pos_map}

    # ---- Labour (gross wages) ----
    # Labour is logged per week from the Tanda CSV in the 🧮 Labour tab. Month mode
    # sums the weeks that fall in the month. A manual override is available per week.
    st.divider()
    st.markdown("**Labour (gross wages)**")
    labour_cost, labour_hours = storage.labour_for_period(mode, period_key)
    if mode == "Week":
        if labour_cost:
            st.caption(f"**${labour_cost:,.0f}** gross · {labour_hours:g} hrs — from 🧮 Labour")
        else:
            st.caption("No labour yet — upload this week's Tanda CSV in **🧮 Labour**.")
        with st.expander("✏️ Override this week manually"):
            mc = st.number_input("Gross wages $", min_value=0.0, step=100.0,
                                 value=float(labour_cost), key="lab_cost")
            mh = st.number_input("Hours", min_value=0.0, step=10.0,
                                 value=float(labour_hours), key="lab_hours")
            if mc > 0 and (mc != labour_cost or mh != labour_hours):
                storage.set_labour("week", period_key, mc, mh)
                labour_cost, labour_hours = mc, mh
    else:
        if labour_cost:
            st.caption(f"**${labour_cost:,.0f}** gross (sum of weeks in {period_label})")
        else:
            st.caption("No labour logged this month — add weeks in **🧮 Labour**.")
    labour_cost_map = storage.labour_cost_map_for(mode)

df = storage.load_invoices()
lines = metrics.explode_lines(df)

st.markdown(f"""<div class="appbar">
  <div class="brand">
    <svg width="36" height="36" viewBox="0 0 34 34" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="34" height="34" rx="9" fill="url(#brandg)"/>
      <rect x="9" y="18" width="3.6" height="7" rx="1.5" fill="#0b1f1b"/>
      <rect x="15.2" y="13" width="3.6" height="12" rx="1.5" fill="#0b1f1b"/>
      <rect x="21.4" y="9" width="3.6" height="16" rx="1.5" fill="#0b1f1b"/>
      <defs><linearGradient id="brandg" x1="0" y1="0" x2="34" y2="34" gradientUnits="userSpaceOnUse">
        <stop stop-color="#2EC4B6"/><stop offset="1" stop-color="#5eead4"/></linearGradient></defs>
    </svg>
    <div><div class="brand-name">Chargrill COGS</div>
    <div class="brand-sub">Cost &amp; labour intelligence</div></div>
  </div>
  <div class="appbar-period">{period_label}</div>
</div>""", unsafe_allow_html=True)

tab_dash, tab_inv, tab_pos, tab_lab, tab_veg, tab_list = st.tabs(
    ["📊 Dashboard", "📸 Add invoice", "💰 Daily takings", "🧮 Labour",
     "🥬 Veggie prices", "📋 Invoices"])

# ============ Add-invoice tab ============
with tab_inv:
    if st.session_state.pop("flash", None):
        st.success(st.session_state.pop("flash_msg", "Saved."))
    st.markdown("#### Add a supplier invoice")
    src = st.radio("Source", ["Take photo", "Upload file"], horizontal=True, key="invsrc")
    up = st.camera_input("Photograph the invoice") if src == "Take photo" \
        else st.file_uploader("Upload invoice (photo or PDF)",
                              type=["jpg", "jpeg", "png", "webp", "pdf"], key="invup")
    if up is not None:
        if not get_api_key():
            st.error("No ANTHROPIC_API_KEY set — add it to .streamlit/secrets.toml.")
        elif st.button("Extract with Claude Vision", type="primary", key="invbtn"):
            with st.spinner("Reading invoice…"):
                media = getattr(up, "type", "image/jpeg")
                try:
                    st.session_state["inv"] = extract_invoice(up.getvalue(), media).model_dump()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

    inv = st.session_state.get("inv")
    if inv:
        st.divider()
        canon = config.canonicalize(inv["supplier_name"])
        st.caption(f"Confidence **{inv.get('confidence','?')}** · {inv['supplier_name']} → **{canon}**")
        c1, c2, c3 = st.columns(3)
        supplier_raw = c1.text_input("Supplier", inv["supplier_name"])
        inv_date = c2.text_input("Invoice date", inv["invoice_date"])
        total = c3.number_input("Total ex-GST $", value=float(inv["total_ex_gst"]), step=0.01)
        li_df = pd.DataFrame(inv.get("line_items", []))
        if canon == config.BAIDA_SUPPLIER and not li_df.empty:
            li_df["tub_type"] = [config.tub_type(d) or "—" for d in li_df.get("description", [])]
            st.caption("🐔 Baida — quantity = chickens. Check each line's tub type (RSPCA ÷8, Split ÷12):")
            edited = st.data_editor(
                li_df, hide_index=True, width="stretch", key="baida_edit",
                column_config={"tub_type": st.column_config.SelectboxColumn(
                    "Tub type", options=["RSPCA", "Split", "—"], required=True)})
            save_lines = edited.to_dict("records")
        else:
            st.dataframe(li_df, hide_index=True, width="stretch")
            save_lines = inv.get("line_items", [])
        if st.button("✅ Save invoice", key="invsave"):
            cleaned = []
            for r in save_lines:
                r = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in dict(r).items()}
                if r.get("tub_type") in ("—", "", None):
                    r.pop("tub_type", None)
                cleaned.append(r)
            storage.save_invoice(supplier_raw, inv_date, total, cleaned)
            st.session_state["flash"] = True
            st.session_state["flash_msg"] = f"Saved {canon} — ${total:,.2f}"
            st.session_state.pop("inv", None)
            st.rerun()

# ============ Daily takings tab ============
with tab_pos:
    if st.session_state.pop("pflash", None):
        st.success(st.session_state.pop("pflash_msg", "Saved."))
    st.markdown("#### Add a finalised POS day (end-of-day slip)")
    psrc = st.radio("Source", ["Take photo", "Upload file"], horizontal=True, key="possrc")
    pup = st.camera_input("Photograph the POS slip") if psrc == "Take photo" \
        else st.file_uploader("Upload POS slip (photo or PDF)",
                              type=["jpg", "jpeg", "png", "webp", "pdf"], key="posup")
    if pup is not None:
        if not get_api_key():
            st.error("No ANTHROPIC_API_KEY set.")
        elif st.button("Read takings", type="primary", key="posbtn"):
            with st.spinner("Reading slip…"):
                media = getattr(pup, "type", "image/jpeg")
                try:
                    st.session_state["pos"] = extract_pos_slip(pup.getvalue(), media).model_dump()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

    pos = st.session_state.get("pos")
    if pos:
        st.divider()
        st.caption(f"Confidence **{pos.get('confidence','?')}** · all figures incl GST")
        c1, c2 = st.columns(2)
        pdate = c1.text_input("Date", pos["business_date"])
        ptot = c2.number_input("Total takings (incl GST) $", value=float(pos["total_incl_gst"]), step=0.01)
        c3, c4 = st.columns(2)
        pdd = c3.number_input("DoorDash (incl GST) $", value=float(pos.get("doordash_incl_gst", 0)), step=0.01)
        pue = c4.number_input("UberEats (incl GST) $", value=float(pos.get("ubereats_incl_gst", 0)), step=0.01)
        adj_incl, adj_ex = config.delivery_adjust(ptot, pdd, pue)
        cut = config.DELIVERY_COMMISSION * (pdd + pue)
        st.info(f"Delivery −{config.DELIVERY_COMMISSION*100:.0f}% on ${pdd+pue:,.2f} = −${cut:,.2f}  →  "
                f"**${adj_incl:,.2f} incl GST**  =  **${adj_ex:,.2f} ex-GST** for the day.")
        if st.button("✅ Save day's takings", key="possave"):
            storage.save_pos_day(pdate, ptot, pdd, pue)
            st.session_state["pflash"] = True
            st.session_state["pflash_msg"] = f"Saved {pdate}: ${adj_ex:,.0f} ex-GST"
            st.session_state.pop("pos", None)
            st.rerun()

# ============ Labour tab ============
with tab_lab:
    st.markdown("#### 🧮 Weekly labour — Tanda shift CSV → award pay")
    st.caption("Computes Fast Food Industry Award 2020 pay (flat-vs-award top-ups for FT/PT, "
               "full penalty rates for casuals) and feeds the week's gross wages into "
               "Labour % / Prime cost % on the dashboard.")

    setup = storage.load_payroll_setup()
    with st.expander("⚙️ Payroll setup file" + ("" if setup else "  — REQUIRED"),
                     expanded=not setup):
        if setup:
            st.caption(f"Loaded **{setup[0]}** · uploaded {setup[2]}")
        else:
            st.warning("Upload **Payroll Setup.xlsx** (staff, award rates, public holidays) "
                       "to enable labour processing. Stored privately in Supabase — never in git.")
        su = st.file_uploader("Upload / replace Payroll Setup.xlsx", type=["xlsx"], key="setupup")
        if su is not None and st.button("Save setup", key="setupsave"):
            try:
                payroll.load_setup_from_bytes(su.getvalue())  # validate it parses first
                storage.save_payroll_setup(su.name, su.getvalue())
                st.success("Setup saved.")
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't read that setup file: {e}")

    if not setup:
        st.info("Add the setup file above, then upload a weekly shift CSV here.")
    else:
        st.markdown("##### Upload this week's Tanda shift CSV")
        csvf = st.file_uploader("Tanda shift report (CSV)", type=["csv"], key="shiftcsv")
        if csvf is not None and st.button("Calculate award pay", type="primary", key="calcpay"):
            with st.spinner("Crunching the award…"):
                try:
                    st.session_state["pay"] = payroll.process_shift_csv(csvf.getvalue(), setup[1])
                except Exception as e:
                    st.error(f"Processing failed: {e}")
                    st.session_state.pop("pay", None)

        out = st.session_state.get("pay")
        if out:
            wk_end = pd.Timestamp(out["week_ending"])
            iso = storage.iso_week_of(wk_end.date())
            st.divider()
            mc = st.columns(4)
            mc[0].metric("Week ending", wk_end.strftime("%d %b %Y"))
            mc[1].metric("Total gross wages", f"${out['total_gross']:,.0f}")
            mc[2].metric("Total hours", f"{out['total_hours']:,.1f}")
            mc[3].metric("Top-ups", f"${out['total_topup']:,.0f}")
            if out["unmatched"]:
                st.warning("Not found in setup (paid on defaults — add them to the setup sheet): "
                           + ", ".join(out["unmatched"]))

            results = out["results"]
            summary_df = pd.DataFrame([{
                "Employee": r["name"], "Type": r["emp_type"], "Section": r["section"],
                "Total Hrs": round(r["hrs"]["total"], 2), "Flat Pay": round(r["flat_pay"], 2),
                "Award Pay": round(r["award_pay"], 2), "Top Up": round(r["topup"], 2),
                "Gross Pay": round(r["gross"], 2)} for r in results])
            cas = [r for r in results if r["emp_type"] == "Casual"]
            casual_df = pd.DataFrame([{
                "Employee": r["name"], "WD": r["hrs"]["wd"], "Sat": r["hrs"]["sat"],
                "Sun": r["hrs"]["sun"], "Sun OT": r["hrs"]["sun_ot"], "PH": r["hrs"]["ph"],
                "Daily OT": round(r["hrs"]["daily_ot1"] + r["hrs"]["daily_ot2"], 2),
                "Weekly OT": round(r["hrs"]["weekly_ot1"] + r["hrs"]["weekly_ot2"], 2),
                "Late Night": round(r["hrs"]["late_night"], 2),
                "Total Hrs": round(r["hrs"]["total"], 2),
                "Total Pay": round(r["pay"]["total"], 2)} for r in cas])
            secs = {}
            for r in results:
                sec = r.get("section") or "Unknown"
                d = secs.setdefault(sec, {"FT/PT Hrs": 0.0, "FT/PT Cost": 0.0,
                                          "Casual Hrs": 0.0, "Casual Cost": 0.0})
                if r["emp_type"] == "Casual":
                    d["Casual Hrs"] += r["hrs"]["total"]; d["Casual Cost"] += r["award_pay"]
                else:
                    d["FT/PT Hrs"] += r["hrs"]["total"]; d["FT/PT Cost"] += r["gross"]
            section_df = pd.DataFrame([{
                "Section": s, "FT/PT Hrs": round(d["FT/PT Hrs"], 2),
                "FT/PT Cost": round(d["FT/PT Cost"], 2), "Casual Hrs": round(d["Casual Hrs"], 2),
                "Casual Cost": round(d["Casual Cost"], 2),
                "Total Hrs": round(d["FT/PT Hrs"] + d["Casual Hrs"], 2),
                "Total Cost": round(d["FT/PT Cost"] + d["Casual Cost"], 2)}
                for s, d in sorted(secs.items())])
            daily_df = pd.DataFrame([{
                "Employee": r["name"], "Date": pd.Timestamp(day["date"]).strftime("%d/%m"),
                "Day": day["day_name"], "Type": day["day_type"], "Shifts": day["shifts"],
                "Total Hrs": round(day["total_hrs"], 2), "Ordinary": round(day["ordinary_hrs"], 2),
                "Daily OT": round(day.get("daily_ot1", 0) + day.get("daily_ot2", 0), 2),
                "Late Night": round(day["late_night"], 2), "Section": r["section"]}
                for r in results for day in r["day_rows"]])

            bd = st.tabs(["Summary", "Casual detail", "By section", "Daily"])
            with bd[0]:
                st.dataframe(summary_df, hide_index=True, width="stretch")
            with bd[1]:
                if casual_df.empty:
                    st.caption("No casual employees this week.")
                else:
                    st.dataframe(casual_df, hide_index=True, width="stretch")
            with bd[2]:
                st.dataframe(section_df, hide_index=True, width="stretch")
            with bd[3]:
                st.dataframe(daily_df, hide_index=True, width="stretch")

            st.download_button(
                "⬇️ Download full Excel report",
                payroll.build_workbook(results, out["week_ending"]),
                file_name=f"Payroll_WeekEnding_{wk_end.strftime('%Y-%m-%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            st.divider()
            st.caption(f"Save sets labour for **{iso}** — gross **${out['total_gross']:,.0f}**, "
                       f"{out['total_hours']:g} hrs → feeds Labour % / Prime cost %.")
            if st.button(f"✅ Save labour to {iso}", key="savelab"):
                storage.set_labour("week", iso, out["total_gross"], out["total_hours"])
                st.session_state.pop("pay", None)
                st.session_state["lab_flash"] = iso
                st.rerun()
        if st.session_state.pop("lab_flash", None):
            st.success("Labour saved — check the Dashboard's Prime Cost section.")


# ============ Dashboard tab ============
with tab_dash:
    st.markdown(f"<div class='hdr'>🍗 {period_label}</div>", unsafe_allow_html=True)

    spend_by, deliveries = (metrics.spend_and_deliveries(df, p_col, period_key)
                            if not df.empty else (pd.Series(dtype=float), pd.Series(dtype=int)))
    qty = metrics.qty_by_supplier_unit(lines, p_col, period_key)
    # Food-COGS total excludes non-COGS categories (packaging, cleaning)
    total_cogs = float(sum(v for s, v in spend_by.items() if config.is_cogs(s))) if len(spend_by) else 0.0
    prev_key = prev_period_key(mode, ref)
    prev_spend, _ = (metrics.spend_and_deliveries(df, p_col, prev_key)
                     if not df.empty else (pd.Series(dtype=float), None))
    prev_total = float(sum(v for s, v in prev_spend.items() if config.is_cogs(s))) if len(prev_spend) else 0.0
    gp, rp = config.TOTAL_COGS_GREEN, config.TOTAL_COGS_RED
    cogs_pct = (total_cogs / revenue) if revenue > 0 else None
    tstat = config.total_status(cogs_pct) if cogs_pct is not None else "amber"
    target_cogs = revenue * gp if revenue > 0 else 0.0
    var = total_cogs - target_cogs
    n_del = int(deliveries.sum()) if len(deliveries) else 0

    # Labour + prime cost (labour_cost / labour_hours come from the sidebar input)
    labour_pct = (labour_cost / revenue) if revenue > 0 else None
    lstat = config.labour_status(labour_pct) if labour_pct is not None else "amber"
    prime_cost = total_cogs + labour_cost
    prime_pct = (prime_cost / revenue) if revenue > 0 else None
    pstat = config.prime_status(prime_pct) if prime_pct is not None else "amber"
    splh = (revenue / labour_hours) if (labour_hours and labour_hours > 0 and revenue > 0) else None

    # ---- KPI cards ----
    k = st.columns(5)
    kpi(k[0], "Revenue (ex-GST)", f"${revenue:,.0f}" if revenue > 0 else "—", "net of delivery cut")
    kpi(k[1], "Total COGS", f"${total_cogs:,.0f}",
        f"{var:+,.0f} vs target" if revenue > 0 else "", COLORS[tstat] if revenue > 0 else "#8b95a7")
    kpi(k[2], "COGS %", f"{cogs_pct*100:.1f}%" if cogs_pct is not None else "—",
        ((("▼ " if tstat == "green" else "▲ ") + f"{(cogs_pct-gp)*100:+.1f} pts vs {gp*100:.0f}%")
         if cogs_pct is not None else f"target ≤{gp*100:.0f}%"),
        COLORS[tstat] if cogs_pct is not None else "#8b95a7")
    kpi(k[3], f"Target COGS ({gp*100:.0f}%)", f"${target_cogs:,.0f}" if revenue > 0 else "—", "the 40% line")
    kpi(k[4], "Deliveries", f"{n_del}", "supplier drops")
    st.write("")

    # ---- Gauge + Baida tubs ----
    g1, g2 = st.columns([1, 1.3])
    with g1:
        st.markdown("**Total COGS vs target**")
        if cogs_pct is not None:
            st.plotly_chart(cogs_gauge(cogs_pct, gp, rp), use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Add revenue (sidebar) to see COGS %.")
        st.caption(f"🟢 ≤{gp*100:.0f}% · 🟠 {gp*100:.0f}–{rp*100:.0f}% · 🔴 >{rp*100:.0f}%")
    with g2:
        tubs = metrics.baida_tubs(lines, p_col, period_key)
        st.markdown(f"**🐔 Baida chicken — tubs this {p_type}**")
        tc = st.columns(3)
        cards = [("RSPCA tubs", tubs["RSPCA"]["tubs"]), ("Split tubs", tubs["Split"]["tubs"]),
                 ("Total tubs", tubs["total_tubs"])]
        for col, (lbl, val) in zip(tc, cards):
            col.markdown(f"<div class='tub'><div class='t'>{lbl}</div><div class='v'>{val:g}</div></div>",
                         unsafe_allow_html=True)
        dep = tubs.get("tub_deposit", 0)
        st.caption(f"{int(tubs['total_chickens'])} chickens "
                   f"(RSPCA {int(tubs['RSPCA']['chickens'])}÷8 · Split {int(tubs['Split']['chickens'])}÷12)"
                   + (f" · TUB DEPOSIT {dep:g}" if dep else ""))
    st.write("")

    # ---- Labour & Prime Cost ----
    st.markdown("**💼 Labour & Prime Cost**")
    lc_cols = st.columns(4)
    kpi(lc_cols[0], "Labour (gross wages)", f"${labour_cost:,.0f}" if labour_cost > 0 else "—",
        "this period")
    kpi(lc_cols[1], "Labour %", f"{labour_pct*100:.1f}%" if labour_pct is not None else "—",
        ((("▼ " if lstat == "green" else "▲ ") + f"{(labour_pct-config.LABOUR_GREEN)*100:+.1f} pts vs {config.LABOUR_GREEN*100:.0f}%")
         if labour_pct is not None else f"target ≤{config.LABOUR_GREEN*100:.0f}%"),
        COLORS[lstat] if labour_pct is not None else "#8b95a7")
    kpi(lc_cols[2], "Prime cost %", f"{prime_pct*100:.1f}%" if prime_pct is not None else "—",
        ((("▼ " if pstat == "green" else "▲ ") + f"{(prime_pct-config.PRIME_GREEN)*100:+.1f} pts vs {config.PRIME_GREEN*100:.0f}%")
         if prime_pct is not None else f"target ≤{config.PRIME_GREEN*100:.0f}%"),
        COLORS[pstat] if prime_pct is not None else "#8b95a7")
    kpi(lc_cols[3], "Sales / labour hr", f"${splh:,.0f}" if splh else "—",
        f"{labour_hours:g} hrs worked" if labour_hours else "add hours in sidebar")
    st.write("")

    pg1, pg2 = st.columns([1, 1.3])
    with pg1:
        st.markdown("**Prime cost vs target** (COGS + labour)")
        if prime_pct is not None:
            st.plotly_chart(cogs_gauge(prime_pct, config.PRIME_GREEN, config.PRIME_RED, axis_max=90),
                            use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("Add revenue + labour (sidebar) to see prime cost %.")
        st.caption(f"🟢 ≤{config.PRIME_GREEN*100:.0f}% · 🟠 {config.PRIME_GREEN*100:.0f}–{config.PRIME_RED*100:.0f}% · "
                   f"🔴 >{config.PRIME_RED*100:.0f}%  ·  ${total_cogs:,.0f} COGS + ${labour_cost:,.0f} labour")
    with pg2:
        st.markdown("**Labour % / Prime % trend**")
        ltrend = (metrics.labour_prime_trend(df, trend_rev_map, labour_cost_map, p_col,
                                             metrics.recent_periods(df, p_col, n=8))
                  if not df.empty else pd.DataFrame())
        if not ltrend.empty:
            fig = px.line(ltrend, x="Period", y=["Labour %", "Prime %"], markers=True)
            fig.update_yaxes(title="%")
            st.plotly_chart(dark(fig), width="stretch", config={"displayModeBar": False})
        else:
            st.caption("Log labour across a few periods to see the trend.")
    if prime_pct is not None and pstat == "red":
        st.error(f"🔴 Prime cost {prime_pct*100:.1f}% is over the {config.PRIME_RED*100:.0f}% ceiling "
                 f"(target ≤{config.PRIME_GREEN*100:.0f}%).")
    st.write("")

    # ---- Charts ----
    if not df.empty:
        periods = metrics.recent_periods(df, p_col, n=8)
        spend_long = metrics.weekly_supplier_spend(df, p_col, periods)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Spend by supplier — last {len(periods)} {p_type}s**")
            if not spend_long.empty:
                fig = px.bar(spend_long, x="Period", y="Spend", color="Supplier", barmode="stack")
                st.plotly_chart(dark(fig), width="stretch", config={"displayModeBar": False})
        with c2:
            st.markdown("**COGS % trend**")
            trend = metrics.cogs_pct_trend(df, trend_rev_map, p_col, periods)
            if not trend.empty:
                fig = px.line(trend, x="Period", y=["COGS %", "Target 40%", "Red 42%"], markers=True)
                fig.update_yaxes(title="%")
                st.plotly_chart(dark(fig), width="stretch", config={"displayModeBar": False})
            else:
                st.caption("Log revenue across a few periods to see the % trend.")

    # ---- Category scorecards ----
    st.markdown("**Spend by category**")
    cols = st.columns(2)
    reds = []
    for i, (sup, cfg) in enumerate(config.SUPPLIERS.items()):
        spend = float(spend_by.get(sup, 0.0))
        prev = float(prev_spend.get(sup, 0.0)) if len(prev_spend) else 0.0
        pct = (spend / revenue) if revenue > 0 else None
        stat = config.status_for(pct, sup) if pct is not None else None
        if stat == "red":
            reds.append(sup)
        tgt = cfg.get("green_pct")
        nd = int(deliveries.get(sup, 0)) if len(deliveries) else 0
        with cols[i % 2].container(border=True):
            top = st.columns([3, 1])
            note = "" if config.is_cogs(sup) else " · not in COGS"
            top[0].markdown(f"**{sup}**{note}")
            top[1].markdown(f"<div style='text-align:right;font-size:1.2em'>"
                            f"{LIGHT.get(stat, '⚪')}</div>", unsafe_allow_html=True)
            m = st.columns(2)
            m[0].metric("Spend", f"${spend:,.0f}",
                        delta=f"{spend-prev:+,.0f}" if prev else None, delta_color="inverse")
            m[1].metric("% of rev", f"{pct*100:.1f}%" if pct is not None else "—",
                        delta=(f"≤{tgt*100:.1f}% target" if tgt else None), delta_color="off")
            st.caption(f"📦 {nd} deliver{'y' if nd == 1 else 'ies'} · ⚖️ {fmt_qty(qty.get(sup, {}))}")
    if reds:
        st.error("🔴 High Variance Alert — over target this period: " + ", ".join(reds))

    # ---- Price watch ----
    if not lines.empty:
        qprev = metrics.qty_by_supplier_unit(lines, p_col, prev_key)
        pw = []
        for sup, units in qty.items():
            for unit, d in units.items():
                if not d["per_unit"]:
                    continue
                pp = (qprev.get(sup, {}).get(unit, {}) or {}).get("per_unit")
                chg = ((d["per_unit"] - pp) / pp * 100) if pp else None
                pw.append({"Supplier": sup, "Unit": unit, "$/unit now": round(d["per_unit"], 2),
                           "$/unit prev": round(pp, 2) if pp else None,
                           "Change": f"{chg:+.1f}%" if chg is not None else "—"})
        if pw:
            with st.expander("💲 Price watch — $/unit vs last period"):
                st.dataframe(pd.DataFrame(pw), hide_index=True, width="stretch")

# ============ Veggie prices tab ============
with tab_veg:
    st.markdown("#### 🥬 Veggie price tracker — St George Food")
    st.caption(f"Tracking {len(config.TRACKED_VEGGIE_ITEMS)} key produce lines. "
               "Unit price = $ ÷ quantity per line; updates automatically as veggie "
               "invoices are uploaded.")
    vprices = metrics.veggie_prices(lines)
    flux = metrics.veggie_flux_table(lines)
    st.dataframe(flux, hide_index=True, width="stretch")
    if vprices.empty:
        st.info("No St George Food (Veggies) invoices logged yet — add one in **📸 Add invoice** "
                "and the latest prices, daily change and weekly change will fill in here.")
    else:
        items = sorted(vprices["item"].unique())
        pick = st.multiselect("Plot price history for:", items, default=items[:5])
        plot = vprices[vprices["item"].isin(pick)].assign(date=lambda d: pd.to_datetime(d["date"]))
        if not plot.empty:
            fig = px.line(plot, x="date", y="unit_price", color="item", markers=True)
            fig.update_yaxes(title="$ / unit")
            fig.update_xaxes(title="")
            st.plotly_chart(dark(fig), width="stretch", config={"displayModeBar": False})

# ============ Invoices list tab ============
with tab_list:
    st.markdown("#### 📋 Submitted invoices")
    deleted = st.session_state.pop("del_flash", None)
    if deleted:
        st.success(f"Deleted: {deleted}")
    if df.empty:
        st.info("No invoices submitted yet — add one in **📸 Add invoice**.")
    else:
        cats = ["All categories"] + list(config.SUPPLIERS.keys())
        pick = st.selectbox("Filter by category", cats, key="invlist_cat")
        view = df if pick == "All categories" else df[df["supplier"] == pick]
        view = view.sort_values("invoice_date", ascending=False)
        total = pd.to_numeric(view["total_ex_gst"], errors="coerce").sum()
        st.caption(f"{len(view)} invoice(s) · ${total:,.0f} ex-GST")
        show = view[["invoice_date", "supplier_raw", "supplier", "total_ex_gst"]].rename(
            columns={"invoice_date": "Date", "supplier_raw": "Supplier (as invoiced)",
                     "supplier": "Category", "total_ex_gst": "Total ex-GST $"})
        st.dataframe(show, hide_index=True, width="stretch")
        with st.expander("🔍 View line items"):
            for _, r in view.iterrows():
                st.markdown(f"**{r['invoice_date']} · {r['supplier_raw']}** → "
                            f"{r['supplier']} · ${float(r['total_ex_gst']):,.2f} ex-GST")
                raw = r.get("line_items")
                if isinstance(raw, str) and raw.strip():
                    try:
                        items = json.loads(raw)
                        if items:
                            st.table(pd.DataFrame(items))
                    except Exception:
                        pass

        # ---- Delete an invoice ----
        st.divider()
        st.markdown("**🗑️ Delete an invoice** (permanent)")
        vs = view.sort_values("invoice_date", ascending=False)
        sa_list = vs["saved_at"].astype(str).tolist()
        labels = {str(r["saved_at"]): f"{r['invoice_date']} · {r['supplier_raw']} · "
                                      f"${float(r['total_ex_gst']):,.2f}"
                  for _, r in vs.iterrows()}
        chosen = st.selectbox("Invoice to delete", sa_list,
                              format_func=lambda s: labels.get(s, s), key="del_sel")
        confirm = st.checkbox("Yes, permanently delete this invoice", key="del_confirm")
        if st.button("Delete invoice", key="del_btn", disabled=not confirm):
            storage.delete_invoice(chosen)
            st.session_state["del_flash"] = labels.get(chosen, "invoice")
            st.rerun()
