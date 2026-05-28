# rebuild marker 2026-05-28b — forces a clean Streamlit Cloud rebuild (clears stale module cache)
import os
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

st.set_page_config(page_title="Chargrill COGS", page_icon="🍗", layout="wide")

COLORS = {"green": "#2faa5e", "amber": "#d9a300", "red": "#e0533d"}
LIGHT = {"green": "🟢", "amber": "🟠", "red": "🔴"}

st.markdown("""<style>
.block-container{padding-top:1.3rem;}
.kpi{background:#161d2e;border:1px solid #243049;border-radius:14px;padding:14px 16px;height:100%;}
.kpi .t{color:#8b95a7;font-size:.70rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;}
.kpi .v{font-size:1.65rem;font-weight:800;color:#fff;line-height:1.15;margin-top:6px;}
.kpi .s{font-size:.76rem;margin-top:4px;}
.hdr{font-size:1.5rem;font-weight:800;color:#fff;margin-bottom:.3rem;}
.tub{background:#161d2e;border:1px solid #243049;border-radius:14px;padding:12px 6px;text-align:center;}
.tub .v{font-size:1.8rem;font-weight:800;color:#fff;}
.tub .t{color:#8b95a7;font-size:.70rem;font-weight:700;text-transform:uppercase;}
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


def cogs_gauge(pct, gp, rp):
    v = pct * 100
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=v,
        number={"suffix": "%", "font": {"size": 38, "color": "#fff"}},
        gauge={"axis": {"range": [0, 55], "tickcolor": "#8b95a7"},
               "bar": {"color": "rgba(0,0,0,0)"}, "borderwidth": 0,
               "steps": [{"range": [0, gp * 100], "color": "#1f7a4d"},
                         {"range": [gp * 100, rp * 100], "color": "#b8860b"},
                         {"range": [rp * 100, 55], "color": "#9c3a28"}],
               "threshold": {"line": {"color": "#fff", "width": 4}, "thickness": 0.8, "value": v}}))
    fig.update_layout(height=230, margin=dict(l=24, r=24, t=16, b=8),
                      paper_bgcolor="rgba(0,0,0,0)", font_color="#E8ECF3")
    return fig


def dark(fig, h=320):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=h,
                      margin=dict(l=10, r=10, t=10, b=10),
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

df = storage.load_invoices()
lines = metrics.explode_lines(df)

tab_dash, tab_inv, tab_pos, tab_veg = st.tabs(
    ["📊 Dashboard", "📸 Add invoice", "💰 Daily takings", "🥬 Veggie prices"])

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
