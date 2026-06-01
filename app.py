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
try:
    import reconcile_app as recon
except Exception:
    recon = None
try:
    import foodsafety as fsafe
except Exception:
    fsafe = None

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


# ============ Roles: chef (default) vs owner ============
# The app opens in the restricted "chef" view. The owner taps the 🔒 box at the
# bottom of the sidebar and enters the PIN to unlock revenue + wages + full tabs.
# PIN comes from the OWNER_PIN secret/env; falls back to 4321 if unset.
def _owner_pin():
    try:
        p = st.secrets.get("OWNER_PIN")
    except Exception:
        p = None
    return str(p or os.environ.get("OWNER_PIN") or "1111")


if "is_owner" not in st.session_state:
    st.session_state["is_owner"] = False
if "role_chosen" not in st.session_state:
    st.session_state["role_chosen"] = False

# ---- Landing gate: pick Chef or Owner (PIN) before the app loads ----
if not st.session_state["role_chosen"]:
    st.markdown("""<div style='max-width:460px;margin:7vh auto .5rem;text-align:center'>
      <div style='font-size:3rem'>🍗</div>
      <div style='font-size:1.55rem;font-weight:700;color:#fff;margin:.2rem 0'>Chargrill COGS</div>
      <div style='color:#8b95a7;margin-bottom:1.2rem'>Choose how you want to sign in</div>
    </div>""", unsafe_allow_html=True)
    _g = st.columns([1, 2, 1])[1]
    with _g:
        if st.button("👨‍🍳  Chef / Team", width="stretch", key="gate_chef"):
            st.session_state["is_owner"] = False
            st.session_state["role_chosen"] = True
            st.rerun()
        st.write("")
        if st.button("👑  Owner", width="stretch", key="gate_owner"):
            st.session_state["gate_pin_open"] = True
        if st.session_state.get("gate_pin_open"):
            _pin = st.text_input("Owner PIN", type="password", key="gate_pin")
            if st.button("Enter as owner", type="primary", width="stretch", key="gate_enter"):
                if _pin == _owner_pin():
                    st.session_state["is_owner"] = True
                    st.session_state["role_chosen"] = True
                    st.session_state.pop("gate_pin_open", None)
                    st.rerun()
                else:
                    st.error("Incorrect PIN.")
    st.stop()

owner = st.session_state["is_owner"]


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
    if "period_ref" not in st.session_state:
        st.session_state["period_ref"] = dt.date.today()
    _nav = st.columns([1, 1, 1])
    if _nav[0].button("◀ Prev", width="stretch", help=f"Previous {mode.lower()}"):
        r = st.session_state["period_ref"]
        st.session_state["period_ref"] = (r - dt.timedelta(days=7) if mode == "Week"
                                          else (r.replace(day=1) - dt.timedelta(days=1)).replace(day=1))
        st.rerun()
    if _nav[1].button("Today", width="stretch"):
        st.session_state["period_ref"] = dt.date.today()
        st.rerun()
    if _nav[2].button("Next ▶", width="stretch", help=f"Next {mode.lower()}"):
        r = st.session_state["period_ref"]
        st.session_state["period_ref"] = (r + dt.timedelta(days=7) if mode == "Week"
                                          else (r.replace(day=1) + pd.offsets.MonthBegin(1)).date())
        st.rerun()
    ref = st.date_input("Or jump to a date", key="period_ref")
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
    st.caption(f"📆 Showing: **{period_label}**")

    pos_df = storage.load_pos_days()
    pos_map = metrics.pos_revenue_map(pos_df, p_col)
    manual_map = storage.revenue_map(p_type)

    # Revenue: owner picks the source and sees the figure. Chef never sees revenue,
    # but we still compute it silently so COGS % / Labour % can be shown.
    if owner:
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
    else:
        # Chef view: use POS revenue, fall back to manual entry — never displayed.
        revenue = float(pos_map.get(period_key, 0.0)) or float(manual_map.get(period_key, 0.0))
    trend_rev_map = {**manual_map, **pos_map}

    # ---- Labour ----
    # Hours feed the dashboard's BOH-hours card (visible to everyone). Gross wages
    # are owner-only and shown/edited here only in the owner view.
    labour_cost, labour_hours, labour_foh, labour_boh = storage.labour_for_period(mode, period_key)
    if owner:
        st.divider()
        st.markdown("**Labour (gross wages)**")
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
                    storage.set_labour("week", period_key, mc, mh, labour_foh, labour_boh)
                    labour_cost, labour_hours = mc, mh
        else:
            if labour_cost:
                st.caption(f"**${labour_cost:,.0f}** gross (sum of weeks in {period_label})")
            else:
                st.caption("No labour logged this month — add weeks in **🧮 Labour**.")
    labour_cost_map = storage.labour_cost_map_for(mode)

    # ---- Current role / switch user ----
    st.divider()
    st.caption("👑 Owner view — full access" if owner else "👨‍🍳 Chef / Team view")
    if st.button("↩️ Switch user", width="stretch", key="switchuser"):
        for _k in ("role_chosen", "is_owner", "gate_pin_open"):
            st.session_state.pop(_k, None)
        st.rerun()

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

# Owner sees all six tabs; chef sees only the four cost/operations tabs.
if owner:
    tab_dash, tab_inv, tab_pos, tab_lab, tab_veg, tab_list, tab_recon, tab_temp = st.tabs(
        ["📊 Dashboard", "📸 Add invoice", "💰 Daily takings", "🧮 Labour",
         "🥬 Veggie prices", "📋 Invoices", "🧾 Reconciliation", "🌡️ Temp records"])
else:
    tab_dash, tab_inv, tab_veg, tab_list, tab_temp = st.tabs(
        ["📊 Dashboard", "📸 Add invoice", "🥬 Veggie prices", "📋 Invoices", "🌡️ Temp records"])
    tab_pos = tab_lab = tab_recon = None

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
        dup = storage.find_duplicate(config.canonicalize(supplier_raw), inv_date, float(total))
        dup_ok = True
        if dup is not None:
            st.warning(f"⚠️ Possible duplicate — already saved **{dup['invoice_date']} · "
                       f"{dup['supplier_raw']} · ${float(dup['total_ex_gst']):,.2f}** "
                       f"(saved {str(dup.get('saved_at',''))[:16]}).")
            dup_ok = st.checkbox("Save anyway — this is a different invoice", key="dupok")
        if st.button("✅ Save invoice", key="invsave", disabled=not dup_ok):
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

# ============ Daily takings tab (owner only) ============
if tab_pos is not None:
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

        # ---- This week's takings, day by day ----
        st.divider()
        monday = ref - dt.timedelta(days=ref.weekday())
        week_iso = storage.iso_week_of(ref)
        st.markdown(f"#### 📅 Takings — week of {monday:%d %b %Y}")
        wk = pos_df[pos_df["iso_week"] == week_iso] if not pos_df.empty else pos_df
        rows = []
        for i in range(7):
            dday = monday + dt.timedelta(days=i)
            match = wk[wk["date"].astype(str) == dday.isoformat()] if (wk is not None and not wk.empty) \
                else None
            if match is not None and not match.empty:
                r = match.iloc[0]
                num = lambda c: float(pd.to_numeric(r[c], errors="coerce") or 0)
                incl, deliv, net = num("total_incl_gst"), num("doordash") + num("ubereats"), num("adjusted_ex_gst")
            else:
                incl = deliv = net = 0.0
            rows.append({"Day": dday.strftime("%a %d %b"), "Takings (incl GST)": round(incl, 2),
                         "Delivery (incl GST)": round(deliv, 2), "Net (ex-GST)": round(net, 2)})
        wkdf = pd.DataFrame(rows)
        if wkdf["Net (ex-GST)"].sum() > 0:
            fig = px.bar(wkdf, x="Day", y="Net (ex-GST)", text_auto=".0f")
            fig.update_traces(marker_color="#2ec4b6", textposition="outside")
            fig.update_yaxes(title="Net $ ex-GST")
            fig.update_xaxes(title="")
            st.plotly_chart(dark(fig, h=270), width="stretch", config={"displayModeBar": False})
            st.dataframe(wkdf, hide_index=True, width="stretch")
            n_days = int((wkdf["Net (ex-GST)"] > 0).sum())
            st.caption(f"Week net ex-GST: **${wkdf['Net (ex-GST)'].sum():,.0f}** · {n_days} day(s) logged")
        else:
            st.caption(f"No takings logged for the week of {monday:%d %b} yet — add a day above.")

# ============ Labour tab (owner only) ============
if tab_lab is not None:
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

                # ---- Annual / sick leave (paid at flat rate, FT/PT only) ----
                with st.expander("➕ Annual / sick leave (FT/PT, paid at flat rate)"):
                    st.caption("Enter AL/SL hours — added to each person's gross at their flat "
                               "rate and carried into the report's SL/AL columns.")
                    perm = [r for r in out["results"] if r["emp_type"] != "Casual"]
                    leave_in = pd.DataFrame([{"Employee": r["name"],
                                              "AL hrs": float(r.get("al_hrs", 0.0)),
                                              "SL hrs": float(r.get("sl_hrs", 0.0))} for r in perm])
                    edited = st.data_editor(
                        leave_in, hide_index=True, width="stretch", key="leave_ed",
                        column_config={
                            "Employee": st.column_config.TextColumn(disabled=True),
                            "AL hrs": st.column_config.NumberColumn(min_value=0.0, step=0.01, format="%.2f"),
                            "SL hrs": st.column_config.NumberColumn(min_value=0.0, step=0.01, format="%.2f")})
                    leave = {row["Employee"]: {"al": row["AL hrs"], "sl": row["SL hrs"]}
                             for _, row in edited.iterrows()}
                out = payroll.apply_leave(out, leave)
                st.session_state["pay"] = out

                st.divider()
                mc = st.columns(4)
                mc[0].metric("Week ending", wk_end.strftime("%d %b %Y"))
                mc[1].metric("Total gross wages", f"${out['total_gross']:,.0f}")
                mc[2].metric("Total hours", f"{out['total_hours']:,.1f}")
                mc[3].metric("Top-ups", f"${out['total_topup']:,.0f}")
                if out.get("total_leave"):
                    st.caption(f"Gross includes **${out['total_leave']:,.0f}** annual/sick leave.")
                if out["unmatched"]:
                    st.warning("Not found in setup (paid on defaults — add them to the setup sheet): "
                               + ", ".join(out["unmatched"]))

                results = out["results"]
                summary_df = pd.DataFrame([{
                    "Employee": r["name"], "Type": r["emp_type"], "Section": r["section"],
                    "Worked Hrs": round(r["hrs"]["total"], 2),
                    "AL hrs": round(r.get("al_hrs", 0), 2), "SL hrs": round(r.get("sl_hrs", 0), 2),
                    "Flat Pay": round(r["flat_pay"], 2), "Award Pay": round(r["award_pay"], 2),
                    "Top Up": round(r["topup"], 2), "Leave Pay": round(r.get("leave_pay", 0), 2),
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
                foh_hours = round(sum(r["hrs"]["total"] for r in results
                                      if str(r["section"]).upper() == "FOH"), 2)
                boh_hours = round(sum(r["hrs"]["total"] for r in results
                                      if str(r["section"]).upper() == "BOH"), 2)
                if st.button(f"✅ Save labour to {iso}", key="savelab"):
                    storage.set_labour("week", iso, out["total_gross"], out["total_hours"],
                                       foh_hours, boh_hours)
                    st.session_state.pop("pay", None)
                    st.session_state["lab_flash"] = iso
                    st.rerun()
            if st.session_state.pop("lab_flash", None):
                st.success("Labour saved — check the Dashboard's Prime Cost section.")


# ============ Reconciliation tab (owner only) ============
if tab_recon is not None:
    with tab_recon:
        st.markdown("#### 🧾 Weekly reconciliation")
        if recon is None:
            st.error("Reconciliation unavailable — `python-calamine` isn't installed. "
                     "Add it to requirements.txt and redeploy.")
        else:
            st.caption("Upload the 7 Tyro location reports → terminal nets auto-fill → add cash, "
                       "turnover, deliveries & Bite → download the filled weekly template.")

            def _rnum(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None

            rec_mon = ref - dt.timedelta(days=ref.weekday())
            wk_start = st.date_input("Week commencing (Monday)", value=rec_mon, key="rec_mon")
            rdays = [wk_start + dt.timedelta(days=i) for i in range(7)]
            rlabels = [d.strftime("%a %d %b") for d in rdays]

            # Pre-fill deliveries + turnover from saved daily POS slips for this week.
            _pos_by_date = {}
            if pos_df is not None and not pos_df.empty:
                _tmp = pos_df.copy()
                _tmp["date"] = _tmp["date"].astype(str)
                _pos_by_date = {r["date"]: r for _, r in _tmp.iterrows()}

            def _posval(d, col):
                r = _pos_by_date.get(d.isoformat())
                if r is None:
                    return None
                v = pd.to_numeric(r.get(col), errors="coerce")
                return None if pd.isna(v) else round(float(v), 2)

            uber_def = [_posval(rdays[i], "ubereats") for i in range(7)]
            dd_def = [_posval(rdays[i], "doordash") for i in range(7)]
            turn_def = [_posval(rdays[i], "total_incl_gst") for i in range(7)]
            n_pos = sum(1 for d in rdays if d.isoformat() in _pos_by_date)

            st.markdown("##### 1 · Upload Tyro location reports")
            st.caption("Download them **Monday → Sunday in order** — the tool sorts by download time.")
            rfiles = st.file_uploader("Location report .xlsx files", type=["xlsx"],
                                      accept_multiple_files=True, key="rec_up")
            nets = [[None, None, None] for _ in range(7)]
            if rfiles:
                ordered = sorted(rfiles, key=lambda f: recon.ts_key(f.name))
                parsed = []
                for f in ordered:
                    try:
                        parsed.append(recon.parse_report(f.getvalue()))
                    except Exception as e:
                        st.error(f"Couldn't read {f.name}: {e}")
                if len(parsed) != 7:
                    st.warning(f"Got {len(parsed)} report(s); expected 7 (one per day). "
                               "Fill any gaps in the table below.")
                for i, n in enumerate(parsed[:7]):
                    nets[i] = n

            st.markdown("##### 2 · Terminal nets (auto-filled — edit if needed)")
            tyro_in = st.data_editor(pd.DataFrame({
                "Day": rlabels,
                "Terminal 1 Net": [nets[i][0] for i in range(7)],
                "Terminal 2 Net": [nets[i][1] for i in range(7)],
                "Terminal 3 Net": [nets[i][2] for i in range(7)],
            }), hide_index=True, width="stretch", disabled=["Day"], key="rec_tyro")
            tyro_in = tyro_in.copy()
            tyro_in["Daily Total"] = tyro_in[["Terminal 1 Net", "Terminal 2 Net",
                                              "Terminal 3 Net"]].sum(axis=1, numeric_only=True)
            st.caption("Daily totals: " + " · ".join(
                f"{rlabels[i].split()[0]} ${tyro_in.iloc[i]['Daily Total']:,.0f}" for i in range(7)))

            st.markdown("##### 3 · Cash + POS slip turnover")
            st.caption("Turnover pre-filled from saved POS slips — POS/ACT/Adjustment are typed in.")
            cash_in = st.data_editor(pd.DataFrame({
                "Day": rlabels, "POS": [None]*7, "ACT": [None]*7,
                "Adjustment": [None]*7, "Turnover (POS slip)": turn_def,
            }), hide_index=True, width="stretch", disabled=["Day"], key="rec_cash")

            st.markdown("##### 4 · Deliveries + Bite")
            st.caption(f"Uber Eats & DoorDash auto-filled from {n_pos} saved POS slip(s) this week — "
                       "edit if needed. **Bite** isn't on the POS slip, so enter it manually.")
            deliv_in = st.data_editor(pd.DataFrame({
                "Day": rlabels, "Uber Eats gross": uber_def,
                "DoorDash gross": dd_def, "Bite (App pymt)": [None]*7,
            }), hide_index=True, width="stretch", disabled=["Day"], key="rec_deliv")

            tyro_days = [{"t1": _rnum(tyro_in.iloc[i]["Terminal 1 Net"]),
                          "t2": _rnum(tyro_in.iloc[i]["Terminal 2 Net"]),
                          "t3": _rnum(tyro_in.iloc[i]["Terminal 3 Net"]),
                          "pos": _rnum(cash_in.iloc[i]["POS"]), "act": _rnum(cash_in.iloc[i]["ACT"]),
                          "adj": _rnum(cash_in.iloc[i]["Adjustment"]),
                          "turnover": _rnum(cash_in.iloc[i]["Turnover (POS slip)"])} for i in range(7)]
            deliv_days = [{"uber": _rnum(deliv_in.iloc[i]["Uber Eats gross"]),
                           "doordash": _rnum(deliv_in.iloc[i]["DoorDash gross"]),
                           "bite": _rnum(deliv_in.iloc[i]["Bite (App pymt)"])} for i in range(7)]

            st.markdown("##### 5 · Download")
            buf = recon.build_workbook(dt.datetime.combine(wk_start, dt.time()), tyro_days, deliv_days)
            fname = f"{rdays[0]:%d %b} - {rdays[6]:%d %b %Y} Reconciliation.xlsx"
            st.download_button("⬇️ Download filled template", buf, file_name=fname,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key="rec_dl")
            st.caption("Then paste the Deliveries & Bite columns into Uber.xlsx / App payments.xlsx "
                       "(the map is on that sheet).")


# ============ Food Safety temp records tab ============
XLMIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FS_SECTIONS = [
    ("deliveries", "Temperature of Deliveries", ("Before 11am", "Before 5pm")),
    ("products", "Products & Holding Temperatures", ("2hrs after Open", "2hrs before Close")),
    ("hotbar", "Hot Bar Holding Temperatures", ("2hrs after Open", "2hrs before Close")),
    ("burger_chilled", "Burger / Grilled — chilled", ("2hrs after Open", "2hrs before Close")),
    ("burger_cooked", "Burger / Grilled — cooked", ("2hrs after Open", "2hrs before Close")),
    ("salad", "Salad Bar Fridge", ("2hrs after Open", "2hrs before Close")),
    ("desserts", "Desserts / Sauces / Soups", ("2hrs after Open", "2hrs before Close")),
    ("equipment", "Equipment", ("2hrs after Open", "2hrs before Close")),
]


def _fs_cell(x):
    """Clean one editor cell -> float, trimmed str, or None (handles NaN/blank)."""
    if x is None:
        return None
    if isinstance(x, str):
        return x.strip() or None
    try:
        xf = float(x)
        return None if xf != xf else xf  # NaN -> None
    except (TypeError, ValueError):
        return None


def _fs_due(d):
    """A day is 'complete' (safe to auto-fill blanks) once it's past, or after 9pm today."""
    now = dt.datetime.now()
    return d < now.date() or (d == now.date() and now.hour >= 21)


def _fs_export(rec, d):
    """Data to render/download: finalised records as-is; drafts only auto-filled once due."""
    if not rec:
        return None
    if rec.get("_final", True):
        return rec
    return {**fsafe.merge_entry(d, rec), "_final": True} if _fs_due(d) else rec


if tab_temp is not None:
    with tab_temp:
        st.markdown("#### 🌡️ Food Safety daily temperature records")
        if fsafe is None:
            st.error("Temp-records module unavailable.")
        else:
            view = st.radio("view", ["✍️ Daily entry", "🗂️ History"], horizontal=True,
                            label_visibility="collapsed", key="ts_view")

            if view == "✍️ Daily entry":
                tday = st.date_input("Date", value=dt.date.today(), key="ts_day")
                saved = storage.food_safety_for(tday)
                # Lazily finalise a draft once the day is complete (past, or after 9pm today).
                if saved and not saved.get("_final", True) and _fs_due(tday):
                    saved = {**fsafe.merge_entry(tday, saved), "_final": True}
                    storage.save_food_safety(tday, saved)
                base = saved if saved else fsafe.blank_entry(tday)
                is_final = bool(saved) and saved.get("_final", True)

                if is_final:
                    st.success("✅ Finalised — blank readings were auto-filled. Edit and re-finalise to change.")
                elif saved:
                    st.info("📝 Draft saved — your readings are kept and blanks stay empty. "
                            "Add more through the day, then **Finalise** (or it auto-fills after 9pm).")
                else:
                    st.caption("Enter the temperatures you took. Use **Save progress** through the day — "
                               "blanks are kept and only auto-filled when you **Finalise** (or after 9pm).")

                mc = st.columns(2)
                mo = mc[0].text_input("Manager Open", value=(base["managers"][0] or ""), key="ts_mo")
                mcl = mc[1].text_input("Manager Close", value=(base["managers"][1] or ""), key="ts_mc")

                ent = {"managers": [_fs_cell(mo), _fs_cell(mcl)]}
                for _sk, _lbl, _cols in FS_SECTIONS:
                    _rows = base.get(_sk, {})
                    if not _rows:
                        st.caption(f"**{_lbl}** — no scheduled deliveries today." if _sk == "deliveries"
                                   else f"**{_lbl}** — none.")
                        ent[_sk] = {}
                        continue
                    st.markdown(f"**{_lbl}**")
                    _sdf = pd.DataFrame([{"Item": k, _cols[0]: _rows[k][0], _cols[1]: _rows[k][1]} for k in _rows])
                    _sed = st.data_editor(
                        _sdf, hide_index=True, width="stretch", key=f"ts_{_sk}",
                        column_config={"Item": st.column_config.TextColumn(disabled=True),
                                       _cols[0]: st.column_config.NumberColumn(format="%.1f"),
                                       _cols[1]: st.column_config.NumberColumn(format="%.1f")})
                    ent[_sk] = {r["Item"]: [_fs_cell(r[_cols[0]]), _fs_cell(r[_cols[1]])] for _, r in _sed.iterrows()}

                st.markdown("**Chicken Temp Records**")
                ck = pd.DataFrame(base["cooks"]).rename(
                    columns={"size": "Cook Size", "in": "Time In", "out": "Time Out", "temp": "Temperature"})
                cked = st.data_editor(
                    ck, hide_index=True, width="stretch", key="ts_cooks",
                    column_config={"Cook Size": st.column_config.NumberColumn(format="%d"),
                                   "Temperature": st.column_config.NumberColumn(format="%.1f")})
                ent["cooks"] = [{"size": (int(_fs_cell(r["Cook Size"])) if _fs_cell(r["Cook Size"]) else None),
                                 "in": _fs_cell(r["Time In"]), "out": _fs_cell(r["Time Out"]),
                                 "temp": _fs_cell(r["Temperature"])} for _, r in cked.iterrows()]

                b = st.columns(2)
                if b[0].button("💾 Save progress (keep blanks)", key="ts_draft"):
                    storage.save_food_safety(tday, {**ent, "_final": False})
                    st.session_state["ts_flash"] = "📝 Progress saved — blanks kept for later."
                    st.rerun()
                if b[1].button("✅ Finalise day (auto-fill blanks)", type="primary", key="ts_final"):
                    storage.save_food_safety(tday, {**fsafe.merge_entry(tday, ent), "_final": True})
                    st.session_state["ts_flash"] = "✅ Finalised — blanks auto-filled and saved to history."
                    st.rerun()
                if st.session_state.get("ts_flash"):
                    st.success(st.session_state.pop("ts_flash"))

                data_dl = _fs_export(saved, tday) or fsafe.merge_entry(tday, ent)
                buf = fsafe.build_workbook_data([(tday, data_dl)])
                st.download_button("⬇️ Download this day", buf, key="ts_dl",
                                   file_name=f"Food Safety Temps - {tday:%d %b %Y}.xlsx", mime=XLMIME)

            else:  # History
                hist = storage.load_food_safety()
                if hist.empty:
                    st.info("No saved records yet — enter a day under ✍️ Daily entry and save it.")
                else:
                    days = sorted(hist["date"].astype(str).tolist())
                    st.caption(f"{len(days)} day(s) saved · {days[0]} → {days[-1]}")
                    c = st.columns(2)
                    hf = c[0].date_input("From", value=pd.to_datetime(days[0]).date(), key="ts_hf")
                    ht = c[1].date_input("To", value=pd.to_datetime(days[-1]).date(), key="ts_ht")
                    sel = [d for d in days if hf.isoformat() <= d <= ht.isoformat()]
                    if sel:
                        items = [(pd.to_datetime(d).date(), storage.food_safety_for(d)) for d in sel]
                        items = [(d, _fs_export(x, d)) for d, x in items if x]
                        buf = fsafe.build_workbook_data(items)
                        st.download_button(f"⬇️ Download {len(items)} saved day(s)", buf, key="ts_hdl",
                                           file_name=f"Food Safety Temps - {hf:%d %b} to {ht:%d %b %Y}.xlsx",
                                           mime=XLMIME)
                    st.dataframe(pd.DataFrame({"Saved records (date)": days[::-1]}),
                                 hide_index=True, width="stretch")


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
    # Week-over-week & month-over-month labour change (independent of the view mode)
    _wkmap = storage.labour_map("week")
    def _wk_cost(d):
        return _wkmap.get(storage.iso_week_of(d), {}).get("cost", 0.0)
    def _mo_cost(d):
        m = d.strftime("%Y-%m")
        return sum(v["cost"] for kk, v in _wkmap.items() if storage._iso_week_month(kk) == m)
    _lw_prev = _wk_cost(ref - dt.timedelta(days=7))
    _lm_prev = _mo_cost(ref.replace(day=1) - dt.timedelta(days=1))
    wow = ((_wk_cost(ref) - _lw_prev) / _lw_prev * 100) if _lw_prev else None
    mom = ((_mo_cost(ref) - _lm_prev) / _lm_prev * 100) if _lm_prev else None

    # ---- KPI cards ----
    if owner:
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
    else:
        # Chef view: spend $ + percentages + BOH hours, but no revenue / target $.
        k = st.columns(4)
        kpi(k[0], "Total supplier spend", f"${total_cogs:,.0f}", "food COGS this period")
        kpi(k[1], "COGS %", f"{cogs_pct*100:.1f}%" if cogs_pct is not None else "—",
            ((("▼ " if tstat == "green" else "▲ ") + f"{(cogs_pct-gp)*100:+.1f} pts vs {gp*100:.0f}%")
             if cogs_pct is not None else f"target ≤{gp*100:.0f}%"),
            COLORS[tstat] if cogs_pct is not None else "#8b95a7")
        kpi(k[2], "BOH hours", f"{labour_boh:g}" if labour_boh else "—", "kitchen")
        kpi(k[3], "Deliveries", f"{n_del}", "supplier drops")
    st.write("")

    # ---- Gauge + Baida tubs ----
    g1, g2 = st.columns([1, 1.3])
    with g1:
        st.markdown("**Total COGS vs target**")
        if cogs_pct is not None:
            st.plotly_chart(cogs_gauge(cogs_pct, gp, rp), use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("COGS % unavailable for this period." if not owner
                       else "Add revenue (sidebar) to see COGS %.")
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
        # Order-vs-turnover guide references weekly sales $, so owner-only.
        if owner and mode == "Week" and not pos_df.empty:
            gross_wk = float(pd.to_numeric(
                pos_df[pos_df["iso_week"] == period_key]["total_incl_gst"],
                errors="coerce").fillna(0).sum())
            rec = config.baida_recommended(gross_wk)
            if rec and gross_wk > 0:
                rec_bird, rec_split = rec
                wpt = config.TUB_TYPES["RSPCA"]["per_tub"]   # birds per whole tub (8)
                spt = config.TUB_TYPES["Split"]["per_tub"]   # birds per split tub (12)
                act_bird, act_split = tubs["RSPCA"]["chickens"], tubs["Split"]["chickens"]
                act_wt, act_st = tubs["RSPCA"]["tubs"], tubs["Split"]["tubs"]
                rec_wt, rec_st = rec_bird / wpt, rec_split / spt
                over = []
                if rec_bird and act_bird > rec_bird * (1 + config.BAIDA_OVER_PCT):
                    over.append(f"whole **{act_bird:.0f} birds = {act_wt:.0f} tubs** "
                                f"vs guide ~{rec_bird:.0f} ({rec_wt:.0f} tubs)")
                if rec_split and act_split > rec_split * (1 + config.BAIDA_OVER_PCT):
                    over.append(f"split **{act_split:.0f} = {act_st:.0f} tubs** "
                                f"vs guide ~{rec_split:.0f} ({rec_st:.0f} tubs)")
                if over:
                    st.warning(f"🐔 Baida order high for ${gross_wk:,.0f} sales — " + " · ".join(over))
                else:
                    st.caption(f"✅ Order in line with ${gross_wk:,.0f} sales — guide "
                               f"~{rec_bird:.0f} whole ({rec_wt:.0f} tubs) · "
                               f"{rec_split:.0f} split ({rec_st:.0f} tubs).")
    st.write("")

    # ---- Labour & Prime Cost (owner) / Kitchen hours (chef) ----
    if owner:
        st.markdown("**💼 Labour & Prime Cost**")

        def _chg(p):
            if p is None:
                return "—"
            return ("▲ " if p > 0 else "▼ ") + f"{abs(p):.0f}%"

        lc_cols = st.columns(5)
        kpi(lc_cols[0], "Labour (gross wages)", f"${labour_cost:,.0f}" if labour_cost > 0 else "—",
            (f"wk {_chg(wow)} · mth {_chg(mom)}" if (wow is not None or mom is not None) else "this period"))
        kpi(lc_cols[1], "Labour %", f"{labour_pct*100:.1f}%" if labour_pct is not None else "—",
            ((("▼ " if lstat == "green" else "▲ ") + f"{(labour_pct-config.LABOUR_GREEN)*100:+.1f} pts vs {config.LABOUR_GREEN*100:.0f}%")
             if labour_pct is not None else f"target ≤{config.LABOUR_GREEN*100:.0f}%"),
            COLORS[lstat] if labour_pct is not None else "#8b95a7")
        kpi(lc_cols[2], "Prime cost %", f"{prime_pct*100:.1f}%" if prime_pct is not None else "—",
            ((("▼ " if pstat == "green" else "▲ ") + f"{(prime_pct-config.PRIME_GREEN)*100:+.1f} pts vs {config.PRIME_GREEN*100:.0f}%")
             if prime_pct is not None else f"target ≤{config.PRIME_GREEN*100:.0f}%"),
            COLORS[pstat] if prime_pct is not None else "#8b95a7")
        kpi(lc_cols[3], "FOH hours", f"{labour_foh:g}" if labour_foh else "—", "front of house")
        kpi(lc_cols[4], "BOH hours", f"{labour_boh:g}" if labour_boh else "—", "kitchen")
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
    else:
        st.markdown("**👨‍🍳 Kitchen labour hours**")
        hc = st.columns(3)
        kpi(hc[0], "BOH hours", f"{labour_boh:g}" if labour_boh else "—", "kitchen")
        kpi(hc[1], "FOH hours", f"{labour_foh:g}" if labour_foh else "—", "front of house")
        kpi(hc[2], "Total hours", f"{labour_hours:g}" if labour_hours else "—", "all staff")
        st.write("")

    # ---- Veggie price alerts ----
    if not lines.empty:
        d_ups, w_ups = metrics.veggie_increases(lines)
        if w_ups or d_ups:
            st.markdown("**🥬 Veggie price alerts** — St George produce going up")
            if w_ups:
                st.warning("🔺 Up this week: "
                           + "  ·  ".join(f"**{n}** +{p:.0f}%" for n, p in w_ups))
            if d_ups:
                st.info("🔺 Up since last delivery: "
                        + "  ·  ".join(f"**{n}** +{p:.0f}%" for n, p in d_ups))
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

        # ---- Duplicate check ----
        st.divider()
        with st.expander("🔍 Duplicate check — same supplier, date & total"):
            groups = storage.duplicate_groups(df)
            if not groups:
                st.success("No duplicates detected.")
            else:
                st.warning(f"{len(groups)} possible duplicate group(s) found.")
                for i, grp in enumerate(groups):
                    r0 = grp.iloc[0]
                    st.markdown(f"**{r0['supplier']}** · {r0['invoice_date']} · "
                                f"${float(r0['total_ex_gst']):,.2f} — {len(grp)} copies")
                    st.dataframe(grp[["invoice_date", "supplier_raw", "total_ex_gst", "saved_at"]],
                                 hide_index=True, width="stretch")
                    if st.button(f"Remove {len(grp)-1} duplicate(s), keep earliest", key=f"dedup{i}"):
                        for sa in grp["saved_at"].astype(str).tolist()[1:]:
                            storage.delete_invoice(sa)
                        st.session_state["del_flash"] = f"Removed {len(grp) - 1} duplicate(s)"
                        st.rerun()

        # ---- Delete an invoice (owner only) ----
        if owner:
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
