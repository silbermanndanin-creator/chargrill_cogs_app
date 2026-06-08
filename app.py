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
import packaging_order as packaging  # NB: file is packaging_order.py — must NOT shadow the 'packaging' PyPI lib
import drinks
from extract import extract_invoice, extract_pos_slip
import extract
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

# ============ Theme system: light (default) ⇄ dark (slate-950) ============
# A toggle in the top-right of the header flips st.session_state["theme"]; the
# whole script re-runs top-to-bottom, so every colour below is recomputed for
# the chosen theme. Streamlit's own data-grids (st.dataframe / st.data_editor)
# follow the config.toml base theme and can't be flipped via CSS — see README.
from string import Template

if "theme" not in st.session_state:
    st.session_state["theme"] = "dark"
THEME = st.session_state["theme"]

_THEMES = {
    "light": {  # warm stone / off-white
        "bg": "#faf9f7", "surface": "#ffffff", "surface2": "#f5f5f4",
        "border": "#e7e5e4", "border_hov": "#d6d3d1",
        "text": "#1c1917", "muted": "#78716c",
        "accent": "#d97706", "accent2": "#ea580c",
        "card_grad": "#ffffff",
        "shadow_sm": "0 1px 2px rgba(28,25,23,.06),0 1px 3px rgba(28,25,23,.07)",
        "shadow_md": "0 10px 25px rgba(28,25,23,.10),0 4px 10px rgba(28,25,23,.06)",
        "pri_btn_text": "#ffffff",
    },
    "dark": {  # warm charcoal / stone-950 + ember accent
        "bg": "#0c0a09", "surface": "#1c1917", "surface2": "#292524",
        "border": "#292524", "border_hov": "#44403c",
        "text": "#fafaf9", "muted": "#a8a29e",
        "accent": "#f59e0b", "accent2": "#fb923c",
        "card_grad": "linear-gradient(180deg,#1c1917,#141110)",
        "shadow_sm": "0 1px 2px rgba(0,0,0,.4),0 1px 3px rgba(0,0,0,.5)",
        "shadow_md": "0 12px 30px rgba(0,0,0,.55),0 4px 10px rgba(0,0,0,.4)",
        "pri_btn_text": "#1c1207",
    },
}
T = _THEMES[THEME]

# Chart palette — warm ember / orange primaries, stone neutrals, amber accent.
# High data-ink: no vertical gridlines, faint horizontal grid, soft-gray axes.
_CHARTS = {
    "light": {
        "navy": "#c2410c", "slate": "#78716c", "accent": "#d97706",
        "red": "#dc2626", "axis": "#d6d3d1", "grid": "rgba(120,113,108,.12)",
        "font": "#57534e", "muted": "#a8a29e", "tmpl": "plotly_white",
        "seq": ["#c2410c", "#d97706", "#ea580c", "#78716c", "#dc2626", "#b45309", "#f59e0b"],
    },
    "dark": {
        "navy": "#fb923c", "slate": "#a8a29e", "accent": "#f59e0b",
        "red": "#f87171", "axis": "#44403c", "grid": "rgba(168,162,158,.12)",
        "font": "#d6d3d1", "muted": "#a8a29e", "tmpl": "plotly_dark",
        "seq": ["#f59e0b", "#fb923c", "#fbbf24", "#a8a29e", "#f87171", "#d97706", "#fcd34d"],
    },
}
C = _CHARTS[THEME]

_CSS = Template("""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap');

:root{
  --bg:$bg; --surface:$surface; --surface2:$surface2; --border:$border;
  --text:$text; --muted:$muted; --accent:$accent; --accent2:$accent2;
  --radius:14px; --shadow:$shadow_sm;
}

/* typography */
html, body, .stApp, [data-testid="stAppViewContainer"],
button, input, select, textarea, .stMarkdown, p, span, label, div{
  font-family:'Inter',-apple-system,'Segoe UI',Roboto,sans-serif;
}
h1,h2,h3,h4,.hdr,.brand-name,.kpi .v,.tub .v{
  font-family:'Space Grotesk','Inter',sans-serif; letter-spacing:-.01em;
}
.stApp,[data-testid="stAppViewContainer"]{ background:$bg; color:$text; }
h1,h2,h3,h4,h5{ color:$text; }

/* blend Streamlit's top toolbar, keep room for it */
[data-testid="stHeader"]{ background:transparent; }
.block-container{ padding-top:3.5rem; max-width:1280px; }

/* spacing & hierarchy — space-y-6 (1.5rem) between content sections */
.block-container [data-testid="stVerticalBlock"]{ gap:1.5rem; }
[data-testid="stHorizontalBlock"]{ gap:1rem; }

/* branded app header bar */
.appbar{ display:flex; align-items:center; justify-content:space-between;
  padding:8px 2px 14px; border-bottom:1px solid $border; margin-bottom:14px; }
.brand{ display:flex; align-items:center; gap:12px; }
.brand-name{ font-size:1.18rem; font-weight:700; color:$text; line-height:1.05; }
.brand-sub{ font-size:.72rem; color:$muted; font-weight:500; margin-top:2px; }
.appbar-period{ font-size:.78rem; color:$text; font-weight:600; background:$surface;
  border:1px solid $border; padding:7px 14px; border-radius:999px; white-space:nowrap; }

/* floating metric cards — rounded-xl, shadow-sm, hover:elevate + accent border */
.kpi{ background:$card_grad; border:1px solid $border; border-radius:14px; padding:16px 18px;
  height:100%; box-shadow:$shadow_sm; transition:all .2s ease-in-out; }
.kpi:hover{ box-shadow:$shadow_md; border-color:$accent; transform:translateY(-2px); }
.kpi .t{ color:$muted; font-size:.67rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }
.kpi .v{ font-size:1.72rem; font-weight:700; color:$text; line-height:1.15; margin-top:8px; }
.kpi .s{ font-size:.77rem; margin-top:6px; font-weight:600; }

.hdr{ font-size:1.6rem; font-weight:700; color:$text; margin-bottom:.3rem; }

/* tub cards */
.tub{ background:$card_grad; border:1px solid $border; border-radius:14px;
  padding:14px 6px; text-align:center; box-shadow:$shadow_sm; transition:all .2s ease-in-out; }
.tub:hover{ box-shadow:$shadow_md; border-color:$accent; transform:translateY(-2px); }
.tub .v{ font-size:1.85rem; font-weight:700; color:$text; }
.tub .t{ color:$muted; font-size:.67rem; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }

/* tabs -> pill style with accent underline + hover transition */
.stTabs [data-baseweb="tab-list"]{ gap:4px; border-bottom:1px solid $border; }
.stTabs [data-baseweb="tab"]{ height:auto; padding:9px 14px; background:transparent;
  border-radius:10px 10px 0 0; color:$muted; font-weight:600; font-size:.9rem;
  transition:all .2s ease-in-out; }
.stTabs [data-baseweb="tab"]:hover{ color:$text; background:$surface2; }
.stTabs [aria-selected="true"]{ color:$text; background:$surface; }
.stTabs [data-baseweb="tab-highlight"]{ background:$accent; height:3px; }
.stTabs [data-baseweb="tab-border"]{ background:transparent; }

/* buttons (incl. sidebar nav items) — smooth hover, subtle lift */
.stButton>button, .stDownloadButton>button{ border-radius:10px; font-weight:600;
  background:$surface; color:$text; border:1px solid $border; transition:all .2s ease-in-out; }
.stButton>button:hover, .stDownloadButton>button:hover{ border-color:$accent; color:$accent;
  box-shadow:$shadow_sm; transform:translateY(-1px); }
.stButton>button[kind="primary"]{ background:$accent; border-color:$accent; color:$pri_btn_text; }
.stButton>button[kind="primary"]:hover{ filter:brightness(1.08); color:$pri_btn_text; }

/* radio / nav options hover */
.stRadio [role="radiogroup"] label{ transition:all .2s ease-in-out; border-radius:8px; padding:1px 6px; }
.stRadio [role="radiogroup"] label:hover{ background:rgba(100,116,139,.10); }

/* form fields adopt the theme surface (so light & dark both read cleanly) */
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"]>div,
.stTextInput input, .stNumberInput input, .stDateInput input, .stTextArea textarea{
  background:$surface !important; color:$text !important; }
[data-baseweb="select"] *{ color:$text; }
input::placeholder, textarea::placeholder{ color:$muted !important; }

/* st.metric cards — floating + hover */
[data-testid="stMetric"]{ background:$surface; border:1px solid $border;
  border-radius:14px; padding:14px 18px; box-shadow:$shadow_sm; transition:all .2s ease-in-out; }
[data-testid="stMetric"]:hover{ box-shadow:$shadow_md; border-color:$accent; transform:translateY(-2px); }
[data-testid="stMetricValue"]{ font-family:'Space Grotesk',sans-serif; color:$text; }

/* bordered containers, expanders, sidebar, tables */
[data-testid="stExpander"]{ border:1px solid $border; border-radius:12px; background:$surface;
  transition:all .2s ease-in-out; }
[data-testid="stExpander"]:hover{ border-color:$border_hov; }
[data-testid="stSidebar"]{ background:$surface; border-right:1px solid $border; }
section[data-testid="stSidebar"] h3{ color:$text; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"]{ border:1px solid $border; border-radius:12px; }

hr{ border-color:$border; }
[data-testid="stAlert"]{ border-radius:12px; }

/* dark-mode toggle — pinned top-right of the nav bar */
.st-key-theme_toggle{ position:fixed; top:.5rem; right:4.5rem; z-index:1000; width:auto; }
.st-key-theme_toggle button{ border-radius:999px !important; padding:3px 14px !important;
  min-height:auto !important; font-size:.85rem !important; font-weight:600 !important;
  background:$surface !important; color:$text !important; border:1px solid $border !important;
  box-shadow:$shadow_sm; transition:all .2s ease-in-out; }
.st-key-theme_toggle button:hover{ border-color:$accent !important; color:$accent !important;
  transform:translateY(-1px); }
""")
st.markdown(f"<style>{_CSS.substitute(**T)}</style>", unsafe_allow_html=True)


def get_api_key():
    """Anthropic key from Streamlit secrets (top-level OR under any [section]), else env.
    Tolerant of stray whitespace / smart-quote paste issues."""
    try:
        v = st.secrets.get("ANTHROPIC_API_KEY")
        if not v:  # maybe nested under a [section] table
            for _sect in st.secrets.values():
                try:
                    if isinstance(_sect, dict) and _sect.get("ANTHROPIC_API_KEY"):
                        v = _sect["ANTHROPIC_API_KEY"]
                        break
                except Exception:
                    pass
        if v:
            return str(v).strip().strip('"').strip("'").strip()
    except Exception:
        pass
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None


def _secret_names():
    """Top-level secret names the app can currently see (names only, never values) —
    used to diagnose a missing/misnamed/misformatted ANTHROPIC_API_KEY."""
    try:
        return list(st.secrets.keys())
    except Exception:
        return []  # secrets file failed to parse (e.g. a TOML/quote error) -> nothing visible


def _api_key_help():
    names = _secret_names()
    seen = ", ".join(str(n) for n in names) if names else "none (secrets didn't load)"
    return (f"No ANTHROPIC_API_KEY readable. Secret names the app sees: **{seen}**. "
            "Fix in **Manage app → Settings → Secrets**: add the line exactly\n\n"
            '`ANTHROPIC_API_KEY = "sk-ant-..."`\n\n'
            "— use straight quotes (not “ ”), keep it on one line, and don't put it under a "
            "`[section]` heading. If the names above show *none*, another line in Secrets has a "
            "typo and is breaking the whole file.")


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
# PIN comes from the OWNER_PIN secret/env; falls back to 1811 if unset.
def _owner_pin():
    try:
        p = st.secrets.get("OWNER_PIN")
    except Exception:
        p = None
    return str(p or os.environ.get("OWNER_PIN") or "1811")


if "is_owner" not in st.session_state:
    st.session_state["is_owner"] = False
if "role_chosen" not in st.session_state:
    st.session_state["role_chosen"] = False

# ---- Landing gate: pick Chef or Owner (PIN) before the app loads ----
if not st.session_state["role_chosen"]:
    st.markdown(f"""<div style='max-width:460px;margin:7vh auto .5rem;text-align:center'>
      <div style='font-size:3rem'>🍗</div>
      <div style='font-size:1.55rem;font-weight:700;color:{T["text"]};margin:.2rem 0'>Chargrill COGS</div>
      <div style='color:{T["muted"]};margin-bottom:1.2rem'>Choose how you want to sign in</div>
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
        number={"suffix": "%", "font": {"size": 38, "color": T["text"], "family": "Space Grotesk"}},
        gauge={"axis": {"range": [0, axis_max], "tickcolor": C["axis"], "tickfont": {"color": C["font"]}},
               "bar": {"color": "rgba(0,0,0,0)"}, "borderwidth": 0,
               "steps": [{"range": [0, gp * 100], "color": "#1f7a4d"},
                         {"range": [gp * 100, rp * 100], "color": "#b8860b"},
                         {"range": [rp * 100, axis_max], "color": "#9c3a28"}],
               "threshold": {"line": {"color": T["text"], "width": 4}, "thickness": 0.8, "value": v}}))
    fig.update_layout(height=230, margin=dict(l=24, r=24, t=16, b=8),
                      paper_bgcolor="rgba(0,0,0,0)", font_color=T["text"])
    return fig


def dark(fig, h=320):
    """Apply the high-data-ink chart style: transparent canvas, no vertical
    gridlines, faint horizontal grid, soft-gray axes and bold (3px) data lines."""
    fig.update_layout(template=C["tmpl"], paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=h,
                      margin=dict(l=10, r=10, t=10, b=10), colorway=C["seq"],
                      font=dict(family="Inter, sans-serif", color=C["font"], size=12),
                      legend=dict(orientation="h", y=-0.2, font=dict(color=C["font"])))
    fig.update_xaxes(showgrid=False, zeroline=False, showline=True, linewidth=1,
                     linecolor=C["axis"], tickcolor=C["axis"],
                     tickfont=dict(color=C["font"]), title_font=dict(color=C["muted"]))
    fig.update_yaxes(showgrid=True, gridcolor=C["grid"], gridwidth=1, zeroline=False,
                     showline=False, tickcolor=C["axis"],
                     tickfont=dict(color=C["font"]), title_font=dict(color=C["muted"]))
    # bold the primary data lines (markers stay compact)
    fig.update_traces(selector=dict(type="scatter"), line=dict(width=3), marker=dict(size=6))
    return fig


# ============ Cached data loaders (performance) ============
# Streamlit reruns the WHOLE script on every interaction (tap, keystroke, tab switch),
# and st.tabs runs every tab's body each time. Without caching that meant ~17 Supabase
# round-trips + re-parsing every invoice on every rerun — the main source of lag.
# These memoise the reads; any write calls bust_caches() so the UI still reflects saves.
_CACHE_TTL = 600  # seconds — safety net; writes clear the cache immediately anyway


def bust_caches():
    """Drop all cached reads — call right after any save/update/delete."""
    st.cache_data.clear()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_invoices():
    return storage.load_invoices()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_explode_lines():
    return metrics.explode_lines(c_load_invoices())


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_pos_days():
    return storage.load_pos_days()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_revenue_map(period_type):
    return storage.revenue_map(period_type)


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_labour_for_period(mode, period_key):
    return storage.labour_for_period(mode, period_key)


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_labour_cost_map_for(mode):
    return storage.labour_cost_map_for(mode)


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_labour_map(period_type):
    return storage.labour_map(period_type)


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_stock_items():
    return storage.load_stock_items()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_stock_value_map():
    return storage.stock_value_map()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_contracts():
    return storage.load_contracts()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_variation_events():
    return storage.load_variation_events()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_food_safety():
    return storage.load_food_safety()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_payroll_setup():
    return storage.load_payroll_setup()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_packaging_counts():
    return storage.load_packaging_counts()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_drinks_counts():
    return storage.load_drinks_counts()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_load_invoice_images(saved_at):
    return storage.load_invoice_images(saved_at)


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_supplier_cadence():
    return metrics.supplier_cadence(c_load_invoices())


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_invoice_checks(period_key):
    return storage.invoice_checks_for(period_key)


@st.cache_data(ttl=300, show_spinner=False)
def c_lightspeed_revenue(start, end, token, business_id):
    # Network call (up to 20s). Cached 5 min so it never blocks every rerun.
    return get_revenue(start, end, token, business_id)


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def c_build_digest(day):
    # build_digest re-loads invoices/POS/labour; cache it (busted on any write).
    import digest as _digest
    return _digest.build_digest(day)


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

    pos_df = c_load_pos_days()
    pos_map = metrics.pos_revenue_map(pos_df, p_col)
    manual_map = c_revenue_map(p_type)

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
                bust_caches()
                manual_map[period_key] = revenue
        else:
            try:
                token = st.secrets.get("LIGHTSPEED_TOKEN")
                biz = st.secrets.get("LIGHTSPEED_BUSINESS_ID")
            except Exception:
                token = biz = None
            r = c_lightspeed_revenue(p_start, p_end, token, biz)
            revenue = float(r) if r else 0.0
            if r:
                st.caption(f"🟢 Lightspeed: **${revenue:,.0f}** ex-GST for this {mode.lower()}.")
            else:
                st.caption("Lightspeed not connected — using manual/POS revenue. "
                           "Add LIGHTSPEED_TOKEN + LIGHTSPEED_BUSINESS_ID to secrets.")
                if st.button("🔌 Test Lightspeed connection", key="ls_test"):
                    import lightspeed as _ls
                    st.caption(f"Status: **{_ls.lightspeed_status(token, biz)}**")
    else:
        # Chef view: use POS revenue, fall back to manual entry — never displayed.
        revenue = float(pos_map.get(period_key, 0.0)) or float(manual_map.get(period_key, 0.0))
    trend_rev_map = {**manual_map, **pos_map}

    # ---- Labour ----
    # Hours feed the dashboard's BOH-hours card (visible to everyone). Gross wages
    # are owner-only and shown/edited here only in the owner view.
    labour_cost, labour_hours, labour_foh, labour_boh = c_labour_for_period(mode, period_key)
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
                    bust_caches()
                    labour_cost, labour_hours = mc, mh
        else:
            if labour_cost:
                st.caption(f"**${labour_cost:,.0f}** gross (sum of weeks in {period_label})")
            else:
                st.caption("No labour logged this month — add weeks in **🧮 Labour**.")
    labour_cost_map = c_labour_cost_map_for(mode)

    # ---- Current role / switch user ----
    st.divider()
    st.caption("👑 Owner view — full access" if owner else "👨‍🍳 Chef / Team view")
    if st.button("↩️ Switch user", width="stretch", key="switchuser"):
        for _k in ("role_chosen", "is_owner", "gate_pin_open"):
            st.session_state.pop(_k, None)
        st.rerun()

df = c_load_invoices()
lines = c_explode_lines()

st.markdown(f"""<div class="appbar">
  <div class="brand">
    <svg width="36" height="36" viewBox="0 0 34 34" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="34" height="34" rx="9" fill="url(#brandg)"/>
      <rect x="9" y="18" width="3.6" height="7" rx="1.5" fill="#1c1207"/>
      <rect x="15.2" y="13" width="3.6" height="12" rx="1.5" fill="#1c1207"/>
      <rect x="21.4" y="9" width="3.6" height="16" rx="1.5" fill="#1c1207"/>
      <defs><linearGradient id="brandg" x1="0" y1="0" x2="34" y2="34" gradientUnits="userSpaceOnUse">
        <stop stop-color="#f59e0b"/><stop offset="1" stop-color="#fb923c"/></linearGradient></defs>
    </svg>
    <div><div class="brand-name">Chargrill COGS</div>
    <div class="brand-sub">Cost &amp; labour intelligence</div></div>
  </div>
  <div class="appbar-period">{period_label}</div>
</div>""", unsafe_allow_html=True)

# Dark-mode toggle — pinned top-right of the nav bar (CSS: .st-key-theme_toggle)
_toggle_label = "🌙 Dark" if THEME == "light" else "☀️ Light"
if st.button(_toggle_label, key="theme_toggle", help="Toggle light / dark mode"):
    st.session_state["theme"] = "dark" if THEME == "light" else "light"
    st.rerun()

# Owner sees all tabs; chef sees only the cost/operations tabs.
if owner:
    (tab_dash, tab_inv, tab_pos, tab_lab, tab_veg, tab_pack, tab_list, tab_track,
     tab_recon, tab_temp, tab_rep, tab_order, tab_digest, tab_var) = st.tabs(
        ["📊 Dashboard", "📸 Add invoice", "💰 Daily takings", "🧮 Labour",
         "🥬 Veggie prices", "📦 Ordering", "📋 Invoices", "✅ Invoice tracker",
         "🧾 Reconciliation", "🌡️ Temp records", "📈 Reports", "🛒 Order pad",
         "📨 Daily digest", "📝 Variations"])
else:
    (tab_dash, tab_inv, tab_veg, tab_pack, tab_list, tab_temp,
     tab_order, tab_digest) = st.tabs(
        ["📊 Dashboard", "📸 Add invoice", "🥬 Veggie prices", "📦 Ordering", "📋 Invoices",
         "🌡️ Temp records", "🛒 Order pad", "📨 Daily digest"])
    tab_pos = tab_lab = tab_recon = tab_rep = tab_var = tab_track = None

# ============ Add-invoice tab ============
with tab_inv:
    if st.session_state.pop("flash", None):
        st.success(st.session_state.pop("flash_msg", "Saved."))
    st.markdown("#### Add a supplier invoice")
    st.caption("Multi-page invoice? Snap each page (or upload several files) — they're combined into one.")
    src = st.radio("Source", ["Take photo", "Upload file"], horizontal=True, key="invsrc")

    pages = []  # list of (bytes, media_type) making up ONE invoice
    if src == "Take photo":
        st.info("💡 **Focus tip:** if the text looks blurry, pull the camera back to "
                "30–40 cm — don't get too close. Fill the frame with the invoice and keep it flat.")
        shots = st.session_state.setdefault("inv_shots", [])
        cam = st.camera_input(f"Photograph page {len(shots) + 1}", key=f"invcam{len(shots)}")
        if cam is not None:
            shots.append((cam.getvalue(), getattr(cam, "type", "image/jpeg")))
            st.rerun()  # reset the camera for the next page
        if shots:
            c1, c2 = st.columns([3, 1])
            c1.success(f"📄 {len(shots)} page(s) captured — snap another, or extract below.")
            if c2.button("↺ Clear", key="invclear"):
                st.session_state["inv_shots"] = []
                st.rerun()
        pages = list(shots)
    else:
        ups = st.file_uploader("Upload invoice page(s) — photos or PDF",
                               type=["jpg", "jpeg", "png", "webp", "pdf"],
                               accept_multiple_files=True, key="invup")
        pages = [(u.getvalue(), getattr(u, "type", "image/jpeg")) for u in (ups or [])]
        if len(pages) > 1:
            st.caption(f"{len(pages)} files — combined into one invoice.")

    if pages:
        if not get_api_key():
            st.error(_api_key_help())
        elif st.button("Extract with Claude Vision", type="primary", key="invbtn"):
            with st.spinner(f"Reading invoice ({len(pages)} page(s))…"):
                try:
                    st.session_state["inv"] = extract_invoice(pages).model_dump()
                    # keep ALL page originals to file with the saved invoice (#7) so a
                    # multi-page invoice can be fully re-read / audited later
                    st.session_state["inv_img"] = list(pages)
                    st.session_state.pop("inv_shots", None)
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
        # Reliability check: do the (possibly edited) line amounts add up to the total?
        _inv_dict = {"line_items": save_lines, "total_ex_gst": total,
                     "total_inc_gst": inv.get("total_inc_gst")}
        rec = extract.reconciliation(_inv_dict)
        if rec["checkable"] and not rec["ok"]:
            h = extract.reconciliation_hints(_inv_dict)
            gap = abs(h["gap"])
            hi_lo = "**higher than**" if h["direction"] == "high" else "**lower than**"
            st.warning(f"⚠️ Lines add up to **${rec['line_sum']:,.2f}** — that's "
                       f"**${gap:,.2f}** {hi_lo} the invoice total of **${rec['target']:,.2f}**.")
            tips = []
            if h["direction"] == "high":
                tips.append("Lines total **more** than the invoice → look for a **duplicated** "
                            "line, an **over-read amount**, or a **discount / credit** line on "
                            "the invoice that wasn't captured (it should subtract).")
            else:
                tips.append("Lines total **less** than the invoice → a line is probably "
                            "**missing**, or an **amount was under-read**.")
            for c in h["gap_candidates"]:
                tips.append(f"Line {c['idx']} **{c['description']}** = **${c['amount']:,.2f}**, "
                            f"which equals the ${gap:,.2f} gap — likely a **duplicate** or a line "
                            "that shouldn't be here.")
            for f in h["line_flags"]:
                tips.append(f"Line {f['idx']} **{f['description']}**: amount **${f['printed']:,.2f}** "
                            f"≠ qty × price (**${f['computed']:,.2f}**) — likely a **misread digit**.")
            if not h["gap_candidates"] and not h["line_flags"]:
                tips.append(f"No single line matches the ${gap:,.2f} gap, so it's likely one "
                            "**misread amount** or a **missing / extra** line — scan the list "
                            "against the paper invoice.")
            st.markdown("**Where to check:**\n" + "\n".join(f"- {t}" for t in tips))
            st.caption("Fix it in **📋 Invoices → ✏️ Edit / fix an invoice** after saving, "
                       "or correct the total above if the lines are right.")
        elif rec["checkable"]:
            st.caption(f"✅ Lines reconcile with the total (${rec['line_sum']:,.2f}).")
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
            _row = storage.save_invoice(supplier_raw, inv_date, total, cleaned)
            _imgs = st.session_state.get("inv_img")
            if _imgs:
                storage.save_invoice_image(_row["saved_at"], _imgs)
            bust_caches()
            st.session_state["flash"] = True
            st.session_state["flash_msg"] = f"Saved {canon} — ${total:,.2f}"
            st.session_state.pop("inv", None)
            st.session_state.pop("inv_img", None)
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
                st.error(_api_key_help())
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
            c3, c4, c5, c6 = st.columns(4)
            pdd = c3.number_input("DoorDash (incl GST) $", value=float(pos.get("doordash_incl_gst", 0)), step=0.01)
            pue = c4.number_input("UberEats (incl GST) $", value=float(pos.get("ubereats_incl_gst", 0)), step=0.01)
            pbite = c5.number_input("Bite Business / App (incl GST) $",
                                    value=float(pos.get("bite_incl_gst", 0)), step=0.01, key="posbite")
            pcash = c6.number_input("Cash (incl GST) $",
                                    value=float(pos.get("cash_incl_gst", 0)), step=0.01, key="poscash")
            adj_incl, adj_ex = config.delivery_adjust(ptot, pdd, pue)
            cut = config.DELIVERY_COMMISSION * (pdd + pue)
            st.info(f"Delivery −{config.DELIVERY_COMMISSION*100:.0f}% on ${pdd+pue:,.2f} = −${cut:,.2f}  →  "
                    f"**${adj_incl:,.2f} incl GST**  =  **${adj_ex:,.2f} ex-GST** for the day."
                    + (f"  ·  Bite ${pbite:,.2f}" if pbite else ""))
            if st.button("✅ Save day's takings", key="possave"):
                storage.save_pos_day(pdate, ptot, pdd, pue, pbite, pcash)
                bust_caches()
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
            fig = dark(fig, h=270)
            fig.update_traces(marker_color=C["accent"], marker_line_width=0,
                              textposition="outside", textfont=dict(color=C["font"]))
            fig.update_yaxes(title="Net $ ex-GST")
            fig.update_xaxes(title="")
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
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

        setup = c_load_payroll_setup()
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
                    bust_caches()
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
                        st.session_state["shift_csv_bytes"] = csvf.getvalue()  # reused by Variations tab
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
                               "rate and carried into the report's SL/AL columns. Everyone on "
                               "the setup sheet is listed, including anyone who didn't work this "
                               "week (Worked hrs = 0, e.g. on leave).")
                    perm = [r for r in out["results"] if r["emp_type"] != "Casual"]
                    worked_names = {r["name"] for r in perm}
                    try:
                        roster = payroll.permanent_roster(setup[1])
                    except Exception:
                        roster = []
                    absent = sorted((m for m in roster if m["name"] not in worked_names),
                                    key=lambda m: m["name"])
                    # Stable 0.0 base so the editor doesn't reset/lose focus on each
                    # keystroke (the keyed widget remembers what you type); apply_leave
                    # below reads the typed values without feeding back into this input.
                    # Workers first (with their worked hours), then absentees at 0 hrs.
                    leave_in = pd.DataFrame(
                        [{"Employee": r["name"], "Worked hrs": round(r["hrs"]["total"], 2),
                          "AL hrs": 0.0, "SL hrs": 0.0} for r in perm]
                        + [{"Employee": m["name"], "Worked hrs": 0.0,
                            "AL hrs": 0.0, "SL hrs": 0.0} for m in absent])
                    edited = st.data_editor(
                        leave_in, hide_index=True, width="stretch", key="leave_ed",
                        column_config={
                            "Employee": st.column_config.TextColumn(disabled=True),
                            "Worked hrs": st.column_config.NumberColumn(disabled=True, format="%.2f"),
                            "AL hrs": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
                            "SL hrs": st.column_config.NumberColumn(min_value=0.0, format="%.2f")})

                    def _lv(x):
                        try:
                            return 0.0 if x is None or float(x) != float(x) else float(x)
                        except (TypeError, ValueError):
                            return 0.0
                    leave = {row["Employee"]: {"al": _lv(row["AL hrs"]), "sl": _lv(row["SL hrs"])}
                             for _, row in edited.iterrows()}
                out = payroll.apply_leave(out, leave, roster)
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
                    bust_caches()
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
            bite_def = [_posval(rdays[i], "bite") for i in range(7)]
            cash_def = [_posval(rdays[i], "cash") for i in range(7)]
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
            st.caption("POS (cash) and Turnover pre-filled from saved POS slips — type ACT (counted "
                       "cash) and any Adjustment.")
            cash_in = st.data_editor(pd.DataFrame({
                "Day": rlabels, "POS": cash_def, "ACT": [None]*7,
                "Adjustment": [None]*7, "Turnover (POS slip)": turn_def,
            }), hide_index=True, width="stretch", disabled=["Day"], key="rec_cash")

            st.markdown("##### 4 · Deliveries + Bite")
            st.caption(f"Uber Eats, DoorDash & Bite auto-filled from {n_pos} saved POS slip(s) "
                       "this week — edit if needed.")
            deliv_in = st.data_editor(pd.DataFrame({
                "Day": rlabels, "Uber Eats gross": uber_def,
                "DoorDash gross": dd_def, "Bite (App pymt)": bite_def,
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
                    bust_caches()
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
                    bust_caches()
                    st.session_state["ts_flash"] = "📝 Progress saved — blanks kept for later."
                    st.rerun()
                if b[1].button("✅ Finalise day (auto-fill blanks)", type="primary", key="ts_final"):
                    storage.save_food_safety(tday, {**fsafe.merge_entry(tday, ent), "_final": True})
                    bust_caches()
                    st.session_state["ts_flash"] = "✅ Finalised — blanks auto-filled and saved to history."
                    st.rerun()
                if st.session_state.get("ts_flash"):
                    st.success(st.session_state.pop("ts_flash"))

                data_dl = _fs_export(saved, tday) or fsafe.merge_entry(tday, ent)
                buf = fsafe.build_workbook_data([(tday, data_dl)])
                st.download_button("⬇️ Download this day", buf, key="ts_dl",
                                   file_name=f"Food Safety Temps - {tday:%d %b %Y}.xlsx", mime=XLMIME)

            else:  # History
                hist = c_load_food_safety()
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

    # ---- Missing-invoice nudge (owner, week view) ----
    # Surfaces suppliers that normally deliver by now but have no invoice this week, so
    # COGS isn't understated by a forgotten upload. Full checklist lives in ✅ Invoice tracker.
    if owner and mode == "Week" and not df.empty:
        _checks = c_invoice_checks(period_key)
        _trk = metrics.weekly_invoice_status(df, period_key, cadence=c_supplier_cadence())
        _miss = [r["supplier"] for r in _trk
                 if r["status"] == "missing"
                 and _checks.get(r["supplier"], {}).get("state", "") not in ("confirmed", "skipped")]
        if _miss:
            st.warning(f"🔴 Possibly missing this week: **{', '.join(_miss)}** — "
                       "check **✅ Invoice tracker** to upload or mark them not coming.")

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
    _wkmap = c_labour_map("week")
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
        # ---- End-of-period pacing/forecast (#3) ----
        if owner:
            _proj = metrics.pace_projection(p_start, p_end, dt.date.today(), total_cogs, revenue, gp)
            if _proj:
                _ps = config.total_status(_proj["proj_pct"])
                st.caption(
                    f"📈 Day {_proj['elapsed']}/{_proj['total']}: at this pace ≈ "
                    f"**${_proj['proj_cogs']:,.0f}** food spend by {mode.lower()}-end vs "
                    f"**${_proj['target_cogs']:,.0f}** target "
                    f"({LIGHT[_ps]} {_proj['delta']:+,.0f} → {_proj['proj_pct']*100:.1f}%).")
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

    # ---- Weekly stocktake → TRUE COGS (count-based, by supplier, Week mode) (#4) ----
    STOCK_SUPPLIERS = ["Chicken", "Veggies", "Blueseas (Broadline)"]
    STOCK_SUP_LABEL = {"Chicken": "🐔 Baida (Chicken)", "Veggies": "🥬 Veggies",
                       "Blueseas (Broadline)": "🌊 Blueseas"}
    if mode == "Week":
        st.markdown("**📦 Weekly stocktake → true COGS**")
        _items = [i for i in c_load_stock_items() if i.get("supplier") in STOCK_SUPPLIERS]

        # 1) Manage the products counted (Baida / Veggies / Blueseas only)
        with st.expander(f"🧾 Stock items & prices ({len(_items)})", expanded=not _items):
            st.caption("Only **Baida, Veggies and Blueseas** products are counted. Pick the "
                       "**supplier**, set the **unit** (kg, ea, carton…) and **price per unit** — "
                       "e.g. salmon as unit `kg`, price `37.25` shows **$37.25/kg**. "
                       "*Suggest from invoices* pre-fills from what you buy; then adjust.")
            _seed = st.session_state.pop("_stock_seed", None)
            _src = _seed if _seed is not None else (_items or [{"item": "", "supplier": "Veggies",
                                                                "unit": "", "unit_price": 0.0}])
            _idf = pd.DataFrame(_src)
            for _c, _dv in [("item", ""), ("supplier", "Veggies"), ("unit", ""), ("unit_price", 0.0)]:
                if _c not in _idf.columns:
                    _idf[_c] = _dv
            _ie = st.data_editor(
                _idf[["item", "supplier", "unit", "unit_price"]], num_rows="dynamic",
                hide_index=True, width="stretch", key="stockitems_ed",
                column_config={
                    "item": st.column_config.TextColumn("Item"),
                    "supplier": st.column_config.SelectboxColumn("Supplier", options=STOCK_SUPPLIERS,
                                                                 required=True),
                    "unit": st.column_config.TextColumn("Unit (kg/ea/…)"),
                    "unit_price": st.column_config.NumberColumn("Price per unit", format="$%.2f",
                                                                min_value=0.0)})
            _b = st.columns(2)
            if _b[0].button("💡 Suggest from invoices", key="stock_suggest"):
                st.session_state["_stock_seed"] = metrics.suggest_stock_items(lines, STOCK_SUPPLIERS, 60)
                st.rerun()
            if _b[1].button("💾 Save stock items", key="stockitems_save"):
                _keep = [r for r in _ie.to_dict("records") if r.get("supplier") in STOCK_SUPPLIERS]
                storage.save_stock_items(_keep)
                bust_caches()
                st.session_state["stk_flash"] = True
                st.rerun()
        if st.session_state.pop("stk_flash", None):
            st.success("Stock items saved.")

        # 2) This week's count sheet, grouped by supplier -> closing value -> true COGS
        if not _items:
            st.info("Add Baida / Veggies / Blueseas items above (or **Suggest from invoices**) "
                    "to start counting.")
        else:
            st.caption("Enter the **count on hand** at the end of this week:")
            _total = 0.0
            for _sup in STOCK_SUPPLIERS:
                _sit = [i for i in _items if i.get("supplier") == _sup]
                if not _sit:
                    continue
                st.markdown(f"**{STOCK_SUP_LABEL[_sup]}**")
                _csheet = pd.DataFrame([
                    {"Item": i["item"], "Price": f"${i['unit_price']:.2f}/{i['unit'] or 'unit'}",
                     "Count": 0.0, "_p": i["unit_price"]} for i in _sit])
                _ce = st.data_editor(
                    _csheet[["Item", "Price", "Count"]], hide_index=True, width="stretch",
                    key=f"stockcount_{_sup.replace(' ', '_').replace('(', '').replace(')', '')}",
                    column_config={
                        "Item": st.column_config.TextColumn(disabled=True),
                        "Price": st.column_config.TextColumn("Price", disabled=True),
                        "Count": st.column_config.NumberColumn(min_value=0.0, step=1.0)})
                _cnt = pd.to_numeric(_ce["Count"], errors="coerce").fillna(0).values
                _sub = float((_cnt * _csheet["_p"].values).sum())
                _total += _sub
                st.caption(f"{STOCK_SUP_LABEL[_sup]} subtotal: **${_sub:,.0f}**")
            st.markdown(f"**Closing stock value this week: ${_total:,.0f}**")
            if st.button("💾 Save this week's stocktake", type="primary", key="stock_save_wk"):
                storage.set_stock_value(period_key, _total)
                bust_caches()
                st.session_state["stk_flash2"] = f"Saved ${_total:,.0f} closing stock for {period_key}."
                st.rerun()
            if st.session_state.pop("stk_flash2", None):
                st.success("Stocktake saved.")

            _smap = c_stock_value_map()
            _prev_wk = storage.iso_week_of(ref - dt.timedelta(days=7))
            _opening = _smap.get(_prev_wk)
            _close = _smap.get(period_key, _total)
            if _opening is not None and _close > 0:
                _actual = metrics.true_cogs(total_cogs, _opening, _close)
                _act_pct = (_actual / revenue) if revenue > 0 else None
                cc = st.columns(3)
                kpi(cc[0], "Opening stock", f"${_opening:,.0f}", f"end of {_prev_wk}")
                kpi(cc[1], "True COGS (used)", f"${_actual:,.0f}", f"vs ${total_cogs:,.0f} purchased")
                if _act_pct is not None:
                    kpi(cc[2], "True COGS %", f"{_act_pct*100:.1f}%",
                        f"purchases read {cogs_pct*100:.1f}%" if cogs_pct is not None else "",
                        COLORS[config.total_status(_act_pct)])
            elif _close > 0:
                st.caption(f"Save last week's ({_prev_wk}) stocktake too — it becomes this week's "
                           "opening — to unlock true COGS.")
    if mode == "Month":
        st.info("📦 **Weekly stocktake** is weekly — switch **Track by → Week** in the sidebar "
                "to record closing stock and see true COGS.")

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
                fig = px.line(ltrend, x="Period", y=["Labour %", "Prime %"], markers=True,
                              color_discrete_sequence=[C["navy"], C["slate"]])
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

    # ---- Supplier price-rise alerts (every item, #1) ----
    if not lines.empty:
        _anom = metrics.price_anomalies(lines, min_pct=8.0)
        if not _anom.empty:
            st.markdown("**💸 Supplier price rises** — items costing more than the last delivery")
            _top = _anom.head(6)
            st.warning("🔺 " + "  ·  ".join(
                f"**{r.Item}** ({r.Supplier}) {r.Change} → ${r.Now:,.2f}/unit"
                for r in _top.itertuples()))
            with st.expander(f"See all {len(_anom)} price rise(s) since last buy"):
                st.dataframe(_anom.drop(columns=["_pct"]), hide_index=True, width="stretch")
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
                fig = px.bar(spend_long, x="Period", y="Spend", color="Supplier", barmode="stack",
                             color_discrete_sequence=C["seq"])
                fig.update_traces(marker_line_width=0)
                st.plotly_chart(dark(fig), width="stretch", config={"displayModeBar": False})
        with c2:
            st.markdown("**COGS % trend**")
            trend = metrics.cogs_pct_trend(df, trend_rev_map, p_col, periods)
            if not trend.empty:
                fig = px.line(trend, x="Period", y=["COGS %", "Target 40%", "Red 42%"], markers=True,
                              color_discrete_sequence=[C["navy"], C["slate"], C["red"]])
                fig = dark(fig)
                # reference lines stay thin & dashed so the COGS % line reads as primary
                fig.update_traces(selector=dict(name="Target 40%"),
                                  line=dict(dash="dash", width=1.5), marker=dict(size=0))
                fig.update_traces(selector=dict(name="Red 42%"),
                                  line=dict(dash="dot", width=1.5), marker=dict(size=0))
                fig.update_yaxes(title="%")
                st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
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
            fig = px.line(plot, x="date", y="unit_price", color="item", markers=True,
                          color_discrete_sequence=C["seq"])
            fig.update_yaxes(title="$ / unit")
            fig.update_xaxes(title="")
            st.plotly_chart(dark(fig), width="stretch", config={"displayModeBar": False})

# ============ Invoices list tab ============
with tab_list:
    st.markdown("#### 📋 Submitted invoices")
    deleted = st.session_state.pop("del_flash", None)
    if deleted:
        st.success(f"Deleted: {deleted}")
    edited_f = st.session_state.pop("edit_flash", None)
    if edited_f:
        st.success(edited_f)
    if df.empty:
        st.info("No invoices submitted yet — add one in **📸 Add invoice**.")
    else:
        fc = st.columns([1.2, 1.8, 1, 1])
        cats = ["All categories"] + list(config.SUPPLIERS.keys())
        pick = fc[0].selectbox("Category", cats, key="invlist_cat")
        q = fc[1].text_input("Search supplier or item", key="invlist_q",
                             placeholder="e.g. chicken, st george…").strip().lower()
        _alld = pd.to_datetime(df["invoice_date"], errors="coerce")
        _dmin = _alld.min()
        _dmax = _alld.max()
        _lo = _dmin.date() if pd.notna(_dmin) else dt.date.today()
        _hi = _dmax.date() if pd.notna(_dmax) else dt.date.today()
        # A keyed date_input ignores `value=` after its first render, so the To bound
        # would otherwise stay frozen at the latest date that existed when the filter was
        # first drawn — hiding any invoices added since. Initialise the range once, then
        # keep To following newer invoices, but only when the user hadn't manually pulled
        # the top bound in (so deliberate narrowing is still respected).
        _prev_hi = st.session_state.get("_invlist_prev_hi")
        if "invlist_from" not in st.session_state:
            st.session_state["invlist_from"] = _lo
        if "invlist_to" not in st.session_state:
            st.session_state["invlist_to"] = _hi
        elif _prev_hi is not None and _hi > _prev_hi and st.session_state["invlist_to"] == _prev_hi:
            st.session_state["invlist_to"] = _hi
        st.session_state["_invlist_prev_hi"] = _hi
        d_from = fc[2].date_input("From", key="invlist_from")
        d_to = fc[3].date_input("To", key="invlist_to")

        view = df if pick == "All categories" else df[df["supplier"] == pick]
        _vd = pd.to_datetime(view["invoice_date"], errors="coerce").dt.date
        view = view[(_vd >= d_from) & (_vd <= d_to)]
        if q:
            view = view[view["supplier_raw"].astype(str).str.lower().str.contains(q, na=False)
                        | view["line_items"].astype(str).str.lower().str.contains(q, na=False)]
        view = view.assign(_sortd=pd.to_datetime(view["invoice_date"], errors="coerce"))
        view = view.sort_values(["_sortd", "saved_at"], ascending=False).drop(columns="_sortd")
        total = pd.to_numeric(view["total_ex_gst"], errors="coerce").sum()
        st.caption(f"{len(view)} invoice(s) · ${total:,.0f} ex-GST")
        show = view[["invoice_date", "supplier_raw", "supplier", "total_ex_gst"]].rename(
            columns={"invoice_date": "Date", "supplier_raw": "Supplier (as invoiced)",
                     "supplier": "Category", "total_ex_gst": "Total ex-GST $"})
        # Real date dtype so tapping the Date header sorts chronologically (not as text),
        # shown in Australian DD/MM/YYYY; money right-aligned with a $ format.
        show["Date"] = pd.to_datetime(show["Date"], errors="coerce")
        show["Total ex-GST $"] = pd.to_numeric(show["Total ex-GST $"], errors="coerce")
        st.dataframe(show, hide_index=True, width="stretch", column_config={
            "Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
            "Total ex-GST $": st.column_config.NumberColumn("Total ex-GST $", format="$%.2f"),
        })
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
                        bust_caches()
                        st.session_state["del_flash"] = f"Removed {len(grp) - 1} duplicate(s)"
                        st.rerun()

        # ---- Original invoice photo (#7) ----
        st.divider()
        with st.expander("📷 View original invoice photo"):
            pv = view.sort_values("invoice_date", ascending=False)
            if pv.empty:
                st.caption("No invoices match the current filter.")
            else:
                plabels = {str(r["saved_at"]): f"{r['invoice_date']} · {r['supplier_raw']} · "
                                              f"${float(r['total_ex_gst']):,.2f}"
                           for _, r in pv.iterrows()}
                psel = st.selectbox("Invoice", list(plabels),
                                    format_func=lambda s: plabels[s], key="photo_sel")
                _imgs = c_load_invoice_images(psel)
                if not _imgs:
                    st.caption("No photo stored for this invoice "
                               "(only invoices added after this update keep their image).")
                else:
                    n = len(_imgs)
                    for _i, (_b, _mt) in enumerate(_imgs, 1):
                        _lbl = plabels[psel] if n == 1 else f"{plabels[psel]} — page {_i}/{n}"
                        if (_mt or "").startswith("image/"):
                            st.image(_b, caption=_lbl, width="stretch")
                        else:
                            st.download_button(
                                f"⬇️ Download original (PDF){'' if n == 1 else f' — page {_i}'}",
                                _b, key=f"photo_dl_{_i}",
                                file_name=f"invoice_{psel[:16].replace(':','-')}_{_i}.pdf",
                                mime=_mt or "application/pdf")

        # ---- Edit / fix a mis-scanned invoice (owner only) ----
        if owner:
            st.divider()
            with st.expander("✏️ Edit / fix an invoice"):
                if view.empty:
                    st.caption("No invoices match the current filter.")
                else:
                    ev = view.sort_values("invoice_date", ascending=False)
                    elabels = {str(r["saved_at"]): f"{r['invoice_date']} · {r['supplier_raw']} · "
                                                   f"${float(r['total_ex_gst']):,.2f}"
                               for _, r in ev.iterrows()}
                    esel = st.selectbox("Pick an invoice to correct", list(elabels),
                                        format_func=lambda s: elabels[s], key="edit_sel")
                    erow = df[df["saved_at"].astype(str) == esel].iloc[0]
                    ec = st.columns(3)
                    e_sup = ec[0].text_input("Supplier (as invoiced)",
                                             value=str(erow["supplier_raw"]), key="edit_sup")
                    try:
                        _ed = pd.to_datetime(erow["invoice_date"]).date()
                    except Exception:
                        _ed = dt.date.today()
                    e_date = ec[1].date_input("Invoice date", value=_ed, key="edit_date")
                    e_total = ec[2].number_input("Total ex-GST $", min_value=0.0, step=1.0,
                                                 value=float(erow["total_ex_gst"]), key="edit_total")
                    st.caption(f"Category re-derives from the supplier name → "
                               f"**{config.canonicalize(e_sup)}**")
                    try:
                        _items = (json.loads(erow["line_items"])
                                  if isinstance(erow["line_items"], str) and erow["line_items"].strip()
                                  else [])
                    except Exception:
                        _items = []
                    _idf = pd.DataFrame(_items)
                    for _c in ["description", "quantity", "unit", "amount"]:
                        if _c not in _idf.columns:
                            _idf[_c] = None
                    _idf = _idf[["description", "quantity", "unit", "amount"]]
                    edf = st.data_editor(_idf, num_rows="dynamic", hide_index=True,
                                         width="stretch", key="edit_items")
                    if st.button("💾 Save corrections", type="primary", key="edit_save"):
                        new_items = [{"description": r["description"], "quantity": r["quantity"],
                                      "unit": r["unit"], "amount": r["amount"]}
                                     for _, r in edf.iterrows()
                                     if str(r.get("description") or "").strip()
                                     or pd.notna(r.get("amount"))]
                        storage.update_invoice(esel, e_sup, e_date.isoformat(),
                                               float(e_total), new_items)
                        bust_caches()
                        st.session_state["edit_flash"] = f"Updated: {e_date} · {e_sup}"
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
                bust_caches()
                st.session_state["del_flash"] = labels.get(chosen, "invoice")
                st.rerun()


# ============ Invoice tracker tab (owner): weekly upload completeness ============
if tab_track is not None:
    with tab_track:
        st.markdown("#### ✅ Invoice tracker — is every supplier's invoice uploaded?")
        st.caption("Learns each supplier's normal delivery pattern from your history, then "
                   "flags any week missing an invoice you'd usually have by now. Tick "
                   "**All in** once a supplier's done, or **Not coming** when they aren't "
                   "delivering that week.")
        if st.session_state.pop("track_flash", None):
            st.success(st.session_state.pop("track_flash_msg", "Saved."))

        cadence = c_supplier_cadence()
        if df.empty or not cadence:
            st.info("No invoice history yet — add invoices in **📸 Add invoice** and the "
                    "tracker will start learning each supplier's delivery pattern.")
        else:
            today = dt.date.today()
            cur_week = storage.iso_week_of(today)
            hist_weeks = {str(w) for w in df["iso_week"].dropna()}
            recent = {storage.iso_week_of(today - dt.timedelta(weeks=i)) for i in range(12)}
            default_wk = period_key if (mode == "Week") else cur_week
            week_opts = sorted(hist_weeks | recent | {default_wk}, reverse=True)

            def _wk_label(wk):
                mon = metrics._week_to_monday(wk)
                if not mon:
                    return wk
                sun = mon + dt.timedelta(days=6)
                tag = " · this week" if wk == cur_week else ""
                return f"{wk} ({mon:%d %b} – {sun:%d %b}){tag}"

            sel_week = st.selectbox("Week", week_opts, index=week_opts.index(default_wk),
                                    format_func=_wk_label, key="track_week")

            checks = c_invoice_checks(sel_week)
            rows = metrics.weekly_invoice_status(df, sel_week, today=today, cadence=cadence)

            # (emoji, label, sort rank) per effective status — missing surfaces first.
            EFFECT = {
                "missing":   ("🔴", "Missing", 0),
                "partial":   ("🟡", "Partial", 1),
                "due":       ("⏳", "Due", 2),
                "confirmed": ("☑️", "Confirmed in", 3),
                "recorded":  ("✅", "Recorded", 4),
                "upcoming":  ("⚪", "Upcoming", 5),
                "skipped":   ("🚫", "Not coming", 6),
                "none":      ("·", "Occasional", 7),
            }
            enriched = []
            for r in rows:
                state = checks.get(r["supplier"], {}).get("state", "")
                if state == "skipped":
                    eff = "skipped"
                elif state == "confirmed":
                    eff = "recorded" if r["received"] else "confirmed"
                else:
                    eff = r["status"]
                enriched.append(dict(r, state=state, effective=eff))

            n_missing = sum(1 for r in enriched if r["effective"] == "missing")
            n_due = sum(1 for r in enriched if r["effective"] == "due")
            n_expected = sum(1 for r in enriched if r["regular"] or r["received"])
            n_done = sum(1 for r in enriched if r["effective"] in ("recorded", "confirmed"))

            cA, cB, cC = st.columns(3)
            cA.metric("Recorded", f"{n_done}/{n_expected}")
            cB.metric("Missing", n_missing)
            cC.metric("Due (not yet)", n_due)

            if n_missing:
                miss = [r["supplier"] for r in enriched if r["effective"] == "missing"]
                st.error("🔴 **Missing this week:** " + ", ".join(miss) +
                         " — upload these, or mark **Not coming** below.")
            elif n_due:
                st.info("⏳ Still expected: " + ", ".join(
                    r["supplier"] for r in enriched if r["effective"] == "due"))
            else:
                st.success("✅ All expected suppliers for this week are accounted for.")

            order = sorted(enriched, key=lambda r: (EFFECT[r["effective"]][2], r["supplier"]))
            disp = pd.DataFrame([{
                "Status": f"{EFFECT[r['effective']][0]} {EFFECT[r['effective']][1]}",
                "Supplier": r["supplier"],
                "Typically": (f"{r['expected']}×/wk · {r['weekdays_label']}"
                              if r["regular"] else "occasional"),
                "Received": r["received"],
                "$ ex-GST": r["amount"],
                "Last delivery": r["last_date"] or "—",
                "Your check": ("✓ All in" if r["state"] == "confirmed"
                               else "🚫 Not coming" if r["state"] == "skipped" else "—"),
            } for r in order])
            edited = st.data_editor(
                disp, hide_index=True, width="stretch", key=f"track_editor_{sel_week}",
                column_config={
                    "Status": st.column_config.TextColumn("Status", disabled=True),
                    "Supplier": st.column_config.TextColumn("Supplier", disabled=True),
                    "Typically": st.column_config.TextColumn("Typically", disabled=True),
                    "Received": st.column_config.NumberColumn("Received", disabled=True),
                    "$ ex-GST": st.column_config.NumberColumn("$ ex-GST", format="$%.2f", disabled=True),
                    "Last delivery": st.column_config.TextColumn("Last delivery", disabled=True),
                    "Your check": st.column_config.SelectboxColumn(
                        "Your check", options=["—", "✓ All in", "🚫 Not coming"], required=True),
                })
            if st.button("💾 Save checklist", type="primary", key="track_save"):
                label_to_state = {"✓ All in": "confirmed", "🚫 Not coming": "skipped", "—": ""}
                n_changed = 0
                for _, er in edited.iterrows():
                    sup = er["Supplier"]
                    new_state = label_to_state.get(er["Your check"], "")
                    if new_state != checks.get(sup, {}).get("state", ""):
                        storage.set_invoice_check(sel_week, sup, new_state)
                        n_changed += 1
                bust_caches()
                st.session_state["track_flash"] = True
                st.session_state["track_flash_msg"] = (f"Updated {n_changed} supplier(s)."
                                                       if n_changed else "No changes to save.")
                st.rerun()

            # ---- Invoices received this week (quick price check) ----
            wk_inv = df[df["iso_week"].astype(str) == str(sel_week)].copy()
            with st.expander(f"🧾 Invoices received this week ({len(wk_inv)})", expanded=True):
                if wk_inv.empty:
                    st.caption("No invoices recorded for this week yet.")
                else:
                    wk_inv["_d"] = pd.to_datetime(wk_inv["invoice_date"], errors="coerce")
                    wk_inv["_t"] = pd.to_numeric(wk_inv["total_ex_gst"], errors="coerce")
                    wk_inv = wk_inv.sort_values(["supplier", "_d"])
                    st.caption(f"{len(wk_inv)} invoice(s) · ${wk_inv['_t'].sum():,.2f} ex-GST — "
                               "tick each against the paper copy.")
                    recv = wk_inv[["_d", "supplier_raw", "supplier", "_t"]].rename(columns={
                        "_d": "Date", "supplier_raw": "Supplier (as invoiced)",
                        "supplier": "Category", "_t": "Total ex-GST $"})
                    st.dataframe(recv, hide_index=True, width="stretch", column_config={
                        "Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
                        "Total ex-GST $": st.column_config.NumberColumn("Total ex-GST $", format="$%.2f"),
                    })

            with st.expander("🧠 Learned supplier patterns (from your invoice history)"):
                cad_rows = [{
                    "Supplier": sup,
                    "Pattern": "Weekly" if c["regular"] else "Occasional",
                    "Per week": c["per_week"],
                    "Usual days": metrics.weekdays_label(c["weekdays"]),
                    "Weeks seen": c["weeks_active"],
                    "Recent presence": f"{c['recent_presence']*100:.0f}%",
                    "Last delivery": c["last_date"],
                } for sup, c in sorted(cadence.items(),
                                       key=lambda kv: (not kv[1]["regular"], kv[0]))]
                st.dataframe(pd.DataFrame(cad_rows), hide_index=True, width="stretch")
                st.caption("“Weekly” suppliers get flagged when a week is missing their invoice. "
                           "“Recent presence” = how many of the last 12 weeks had a delivery — "
                           "the tracker leans on this to decide what to expect each week.")


# ============ Reports tab (owner): BAS/GST + accountant pack ============
def _fin_quarters(today, n=6):
    """Recent Australian FY quarters as (label, [YYYY-MM, ...]) newest first.
    Q1 Jul-Sep, Q2 Oct-Dec, Q3 Jan-Mar, Q4 Apr-Jun."""
    q_starts = {1: 7, 2: 10, 3: 1, 4: 4}
    # which quarter is 'today' in?
    m = today.month
    qnum = 1 if m >= 7 and m <= 9 else 2 if m >= 10 else 3 if m <= 3 else 4
    fy = today.year if m >= 7 else today.year - 1  # FY starting year
    out = []
    for _ in range(n):
        sm = q_starts[qnum]
        sy = fy if sm >= 7 else fy + 1  # Jan-Jun months fall in the next calendar year
        months = [f"{sy + ((sm + i - 1) // 12)}-{((sm + i - 1) % 12) + 1:02d}" for i in range(3)]
        label = f"Q{qnum} FY{str(fy)[2:]}/{str(fy + 1)[2:]} ({months[0]} – {months[-1]})"
        out.append((label, months))
        # step back one quarter
        qnum -= 1
        if qnum == 0:
            qnum = 4
            fy -= 1
    return out


def _accountant_pack_xlsx(month_key, inv_df, pos_df):
    """Monthly accountant pack as .xlsx bytes: Summary, Invoices, By category,
    Labour, Daily takings."""
    import io
    buf = io.BytesIO()
    inv = inv_df[inv_df["month"] == month_key] if not inv_df.empty else inv_df.iloc[0:0]
    pos = pos_df[pos_df["month"] == month_key] if not pos_df.empty else pos_df.iloc[0:0]
    lab = c_labour_map("week")
    lab_rows = [{"ISO week": k, "Gross wages $": v["cost"], "Hours": v["hours"],
                 "FOH hours": v["foh"], "BOH hours": v["boh"]}
                for k, v in sorted(lab.items()) if storage._iso_week_month(k) == month_key]
    inv_tot = float(pd.to_numeric(inv["total_ex_gst"], errors="coerce").fillna(0).sum()) if not inv.empty else 0.0
    sales_incl = float(pd.to_numeric(pos["total_incl_gst"], errors="coerce").fillna(0).sum()) if not pos.empty else 0.0
    summary = pd.DataFrame([
        {"Metric": "Month", "Value": month_key},
        {"Metric": "Supplier spend (ex-GST)", "Value": round(inv_tot, 2)},
        {"Metric": "Sales (incl GST)", "Value": round(sales_incl, 2)},
        {"Metric": "Gross wages", "Value": round(sum(r["Gross wages $"] for r in lab_rows), 2)},
        {"Metric": "Invoices count", "Value": int(len(inv))},
    ])
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        if not inv.empty:
            inv[["invoice_date", "supplier_raw", "supplier", "total_ex_gst"]].rename(
                columns={"invoice_date": "Date", "supplier_raw": "Supplier (as invoiced)",
                         "supplier": "Category", "total_ex_gst": "Total ex-GST $"}
            ).sort_values("Date").to_excel(xw, sheet_name="Invoices", index=False)
            inv.groupby("supplier")["total_ex_gst"].sum().round(2).reset_index().rename(
                columns={"supplier": "Category", "total_ex_gst": "Spend ex-GST $"}
            ).to_excel(xw, sheet_name="By category", index=False)
        if lab_rows:
            pd.DataFrame(lab_rows).to_excel(xw, sheet_name="Labour", index=False)
        if not pos.empty:
            pos[["date", "total_incl_gst", "doordash", "ubereats", "adjusted_ex_gst"]].rename(
                columns={"date": "Date", "total_incl_gst": "Takings incl GST",
                         "doordash": "DoorDash", "ubereats": "UberEats",
                         "adjusted_ex_gst": "Net ex-GST"}
            ).sort_values("Date").to_excel(xw, sheet_name="Daily takings", index=False)
    buf.seek(0)
    return buf.getvalue()


if tab_rep is not None:
    with tab_rep:
        st.markdown("#### 📈 Reports")

        # ---- BAS / GST summary (#9) — monthly (default) or quarterly ----
        st.markdown("**🧾 BAS / GST summary**")
        bc0 = st.columns([1, 2])
        bas_period = bc0[0].radio("BAS cycle", ["Monthly", "Quarterly"], key="bas_cycle")
        if bas_period == "Monthly":
            _today = dt.date.today()
            recent_months = [((_today.replace(day=1) - pd.offsets.MonthBegin(i)).strftime("%Y-%m"))
                             for i in range(12)]
            mlabel = bc0[1].selectbox("BAS month", recent_months, key="bas_m")
            bas_months = [mlabel]
        else:
            quarters = _fin_quarters(dt.date.today(), n=6)
            qlabel = bc0[1].selectbox("BAS quarter", [q[0] for q in quarters], key="bas_q")
            bas_months = dict(quarters)[qlabel]
        bas = metrics.bas_summary(pos_df, df, bas_months)
        bc = st.columns(4)
        kpi(bc[0], "Sales (incl GST)", f"${bas['sales_incl']:,.0f}", "G1")
        kpi(bc[1], "GST on sales", f"${bas['gst_on_sales']:,.0f}", "1A")
        kpi(bc[2], "GST credits (est.)", f"${bas['gst_credits_est']:,.0f}", "1B — estimate")
        kpi(bc[3], "Net GST payable", f"${bas['net_gst']:,.0f}",
            "1A − 1B", COLORS["red"] if bas["net_gst"] >= 0 else COLORS["green"])
        st.caption("⚠️ **GST credits are an estimate** (supplier spend × 10%). GST-free items "
                   "— fresh produce, meat, plain milk, etc. — carry no GST, so the real credit "
                   "is usually lower. Always reconcile against your actual tax invoices before lodging.")
        st.write("")

        # ---- One-click accountant pack (#10) ----
        st.divider()
        st.markdown("**📦 Accountant pack — monthly export**")
        months_avail = sorted(set(df["month"].dropna().tolist()) | set(pos_df["month"].dropna().tolist()),
                              reverse=True) if (not df.empty or not pos_df.empty) else []
        if not months_avail:
            st.caption("No data yet to export.")
        else:
            mk = st.selectbox("Month", months_avail, key="pack_month")
            st.caption("Excel workbook: Summary · Invoices · By category · Labour · Daily takings.")
            st.download_button(
                "⬇️ Download accountant pack (.xlsx)",
                _accountant_pack_xlsx(mk, df, pos_df), key="pack_dl",
                file_name=f"Accountant Pack {mk}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ============ Order pad tab (owner): par-level order assistant (#8) ============
if tab_order is not None:
    with tab_order:
        st.markdown("#### 🛒 Order pad")
        st.caption("Pick a supplier — every item you buy from them, pre-filled with the "
                   "last-paid price and your average order quantity. Adjust quantities to "
                   "build an order and see the estimated cost.")
        sups = sorted(lines["supplier"].dropna().unique()) if not lines.empty else []
        if not sups:
            st.info("No invoice history yet — add invoices so the pad can pre-fill prices.")
        else:
            osup = st.selectbox("Supplier / category", sups, key="order_sup")
            pad = metrics.order_pad(lines, osup)
            if pad.empty:
                st.caption("No items found for this supplier.")
            else:
                pad = pad.copy()
                pad["Order qty"] = pad["Avg qty/order"].fillna(0.0)
                oedit = st.data_editor(
                    pad[["Item", "Unit", "Last $/unit", "Avg qty/order", "Order qty", "Last bought"]],
                    hide_index=True, width="stretch", key="order_edit",
                    column_config={
                        "Item": st.column_config.TextColumn(disabled=True),
                        "Unit": st.column_config.TextColumn(disabled=True),
                        "Last $/unit": st.column_config.NumberColumn(format="$%.2f", disabled=True),
                        "Avg qty/order": st.column_config.NumberColumn(disabled=True),
                        "Order qty": st.column_config.NumberColumn(min_value=0.0, step=1.0),
                        "Last bought": st.column_config.TextColumn(disabled=True)})
                _q = pd.to_numeric(oedit["Order qty"], errors="coerce").fillna(0)
                _p = pd.to_numeric(oedit["Last $/unit"], errors="coerce").fillna(0)
                est = float((_q * _p).sum())
                st.caption(f"Estimated order cost at last-paid prices: **${est:,.2f}** ex-GST")
                ordered = oedit[_q > 0]
                if not ordered.empty:
                    txt = (f"Order — {osup}\n"
                           + "\n".join(f"{float(r['Order qty']):g} x {r['Item']}"
                                       for _, r in ordered.iterrows()))
                    st.download_button("⬇️ Download order list (.txt)", txt, key="order_dl",
                                       file_name=f"Order {osup}.txt", mime="text/plain")


# ============ Ordering tab: packaging stocktake -> split supplier order (#9) ============
if tab_pack is not None:
    with tab_pack:
        st.markdown("#### 📦 Ordering")
        # Packaging is the first ordering section; more can be added as sub-tabs.
        sub_pack, sub_drink = st.tabs(["Packaging", "Drinks"])
        with sub_pack:
            st.caption("Count what's on the shelf and enter **QTY on hand** — you can use halves "
                       "(e.g. 2.5) for part-used boxes. The app refills each item to par, rounds "
                       "the order **up** to whole units, then splits it by supplier: "
                       "🟢 BIOPAK Horizons (grouped by category) and 🟡 A-Z Packaging.")

            saved = c_load_packaging_counts()
            # Supplier + par are kept in the master list (packaging.PACKAGING_ITEMS) and drive
            # the finalised split below — the count grid stays minimal: just item + on hand.
            rows = []
            for it in packaging.PACKAGING_ITEMS:
                rows.append({
                    "Item": it["item"],
                    "QTY on hand": float(saved.get(it["item"], 0.0) or 0),
                })
            pack_df = pd.DataFrame(rows)

            pedit = st.data_editor(
                pack_df, hide_index=True, width="stretch", key="pack_edit",
                column_config={
                    "Item": st.column_config.TextColumn(disabled=True),
                    "QTY on hand": st.column_config.NumberColumn(min_value=0.0, step=0.5)})

            # Recompute the order live from the edited on-hand column every rerun.
            counts = {}
            for _, r in pedit.iterrows():
                v = pd.to_numeric(r["QTY on hand"], errors="coerce")
                counts[r["Item"]] = 0.0 if pd.isna(v) else float(v)

            csave, cinfo = st.columns([1, 3])
            if csave.button("💾 Save counts", key="pack_save"):
                storage.save_packaging_counts(counts)
                bust_caches()
                st.success("Counts saved.")
            cinfo.caption("Saved to the app so a reload on your phone won't wipe your count.")

            order = packaging.build_order(counts)
            biopak, az = order[packaging.BIOPAK], order[packaging.AZ]
            n_bio = sum(len(v) for v in biopak.values())
            n_az = len(az)

            st.divider()
            st.markdown("### 🧾 Order to place")
            if n_bio == 0 and n_az == 0:
                st.success("Nothing to order — everything's at or above par.")
            else:
                oc1, oc2 = st.columns(2)
                with oc1:
                    st.markdown(f"#### 🟢 {packaging.BIOPAK}")
                    if n_bio == 0:
                        st.caption("Nothing to order.")
                    else:
                        for cat in packaging.BIOPAK_CATEGORY_ORDER:
                            items = biopak.get(cat)
                            if not items:
                                continue
                            st.markdown(f"**{cat}**")
                            st.dataframe(
                                pd.DataFrame([{"Order": f"{e['order']:g}", "Item": e["item"]}
                                              for e in items]),
                                hide_index=True, width="stretch")
                        st.caption("Copy-ready (tap the ⧉ icon top-right):")
                        st.code(packaging.order_text_biopak(biopak), language=None)
                with oc2:
                    st.markdown(f"#### 🟡 {packaging.AZ}")
                    if n_az == 0:
                        st.caption("Nothing to order.")
                    else:
                        st.dataframe(
                            pd.DataFrame([{"Order": f"{e['order']:g}", "Item": e["item"]}
                                          for e in az]),
                            hide_index=True, width="stretch")
                        st.caption("Copy-ready (tap the ⧉ icon top-right):")
                        st.code(packaging.order_text_az(az), language=None)

        with sub_drink:
            st.caption("'Qnty Needed' on the sheet is **per-week** usage. Set the delivery "
                       "window below, count the fridge (**QTY on hand**, halves OK), and the "
                       "app scales each drink to the window and rounds the order **up**.")

            # --- Delivery run. Normal cadence is two orders a week, each covering until
            #     the next delivery: Mon order -> Wed delivery (last till Sun), and
            #     Thu order -> Mon delivery (last till Tue). ---
            _today = dt.date.today()
            _runs = ["Mon order → Wed delivery  ·  last till Sun",
                     "Thu order → Mon delivery  ·  last till Tue",
                     "Custom dates"]
            _def_run = 1 if _today.weekday() in (2, 3, 4) else 0  # Wed/Thu/Fri -> Thu run
            run = st.radio("Which order run is this?", _runs, index=_def_run, key="drink_run")
            if run == _runs[0]:            # Mon -> Wed delivery, last till Sun
                deliv = drinks.default_delivery(_today, 2)        # Wednesday
                until = deliv + dt.timedelta(days=4)              # the Sunday after
            elif run == _runs[1]:          # Thu -> Mon delivery, last till Tue
                deliv = drinks.default_delivery(_today, 0)        # Monday
                until = deliv + dt.timedelta(days=1)              # the Tuesday after
            else:                          # Custom
                _cd = drinks.default_delivery(_today)
                wc1, wc2 = st.columns(2)
                deliv = wc1.date_input("Delivery date", value=_cd, key="drink_deliv",
                                       help="When this order arrives.")
                until = wc2.date_input("Stock must last until", value=_cd + dt.timedelta(days=6),
                                       key="drink_until", help="Day before the next delivery.")
            cov_days, weeks = drinks.coverage(_today, until)
            st.caption(f"📦 Ordering today (**{_today:%a %d %b}**) for **{deliv:%a %d %b}** "
                       f"delivery, to last until **{until:%a %d %b}** — covering "
                       f"**{cov_days} days (~{weeks:.1f} weeks)**. Weekly quantities scale to this.")

            # --- Public-holiday heads-up. A public holiday does NOT change the weekly usage
            #     rate — it just makes the delivery window longer (bigger gap between
            #     deliveries). So we only nudge the user to set a longer Custom window; the
            #     window-scaling above produces the larger order automatically. ---
            _phs = drinks.public_holidays_within("NSW", _today, days=cov_days - 1)
            if _phs:
                _names = ", ".join(f"{n} ({d:%a %d %b})" for d, n in _phs)
                st.warning(f"🎉 Public holiday in this window — {_names}. Deliveries usually "
                           "shift and the gap is longer, so pick **Custom dates** above and set "
                           "the delivery + 'last until' from your supplier's schedule. (Per-week "
                           "quantities don't change — the longer window does the work.)")

            dsaved = c_load_drinks_counts()
            # Section + par stay in the master list (drinks.DRINK_ITEMS) and drive the
            # finalised order below — the count grid stays minimal: just item + on hand.
            drows = []
            for it in drinks.DRINK_ITEMS:
                drows.append({
                    "Item": it["item"],
                    "QTY on hand": float(dsaved.get(it["item"], 0.0) or 0),
                })
            drink_df = pd.DataFrame(drows)

            dedit = st.data_editor(
                drink_df, hide_index=True, width="stretch", key="drink_edit",
                column_config={
                    "Item": st.column_config.TextColumn(disabled=True),
                    "QTY on hand": st.column_config.NumberColumn(min_value=0.0, step=0.5)})

            dcounts = {}
            for _, r in dedit.iterrows():
                v = pd.to_numeric(r["QTY on hand"], errors="coerce")
                dcounts[r["Item"]] = 0.0 if pd.isna(v) else float(v)

            dsave, dinfo = st.columns([1, 3])
            if dsave.button("💾 Save counts", key="drink_save"):
                storage.save_drinks_counts(dcounts)
                bust_caches()
                st.success("Counts saved.")
            dinfo.caption("Saved to the app so a reload on your phone won't wipe your count.")

            dorder = drinks.build_order(dcounts, weeks=weeks)
            n_drink = len(dorder)

            st.divider()
            st.markdown(f"### 🧾 Drinks order to place  ·  _~{weeks:.1f} wk window_")
            if n_drink == 0:
                st.success("Nothing to order — on-hand already covers the whole window.")
            else:
                st.caption("Listed in the Coca-Cola order-site sequence — follow it straight "
                           "down the supplier's 'Frequently Ordered' page.")
                st.dataframe(
                    pd.DataFrame([{"Order": f"{e['order']:g}", "Item": e["item"]}
                                  for e in dorder]),
                    hide_index=True, width="stretch")
                st.caption("Copy-ready (tap the ⧉ icon top-right):")
                st.code(drinks.order_text(dorder), language=None)


# ============ Daily digest tab (all roles; role-aware) ============
if tab_digest is not None:
    with tab_digest:
        st.markdown("#### 📨 Today's digest")
        st.caption("A snapshot you can glance at any time — the same summary is emailed each "
                   "morning once the email digest is set up.")
        d = c_build_digest(dt.date.today())
        gp = config.TOTAL_COGS_GREEN
        _cp = d["cogs_pct"]
        _cp_status = COLORS[config.total_status(_cp)] if _cp is not None else "#8b95a7"

        if owner:
            dc = st.columns(3)
            kpi(dc[0], f"Yesterday ({d['yesterday']:%a %d %b})",
                f"${d['y_net']:,.0f}" if d["y_net"] else "—", "net ex-GST")
            kpi(dc[1], "Week revenue", f"${d['wk_rev']:,.0f}" if d["wk_rev"] else "—", "to date")
            kpi(dc[2], "Week COGS %", f"{_cp*100:.1f}%" if _cp is not None else "—",
                f"target ≤{gp*100:.0f}%", _cp_status)
            lc = st.columns(2)
            kpi(lc[0], "Week food spend", f"${d['wk_cogs']:,.0f}", "COGS $ WTD")
            kpi(lc[1], "Week labour", f"${d['lab']:,.0f}" if d["lab"] else "—",
                f"{d['lab_pct']*100:.1f}%" if d["lab_pct"] is not None else "gross wages")
        else:
            # Chef view mirrors the dashboard: COGS % + spend $, never revenue/wages $.
            dc = st.columns(2)
            kpi(dc[0], "Week COGS %", f"{_cp*100:.1f}%" if _cp is not None else "—",
                f"target ≤{gp*100:.0f}%", _cp_status)
            kpi(dc[1], "Week food spend", f"${d['wk_cogs']:,.0f}", "to date")
        st.write("")

        if d["over"]:
            st.markdown("**⚠️ Over budget this week**")
            st.warning("  ·  ".join(
                f"{'🔴' if stt == 'red' else '🟠'} **{s}** {pct:.1f}% of sales"
                for s, pct, stt in d["over"]))
        if not d["price_rises"].empty:
            st.markdown("**💸 Supplier price rises (vs last delivery)**")
            st.dataframe(d["price_rises"].drop(columns=["_pct"]).head(15),
                         hide_index=True, width="stretch")
        if not d["over"] and d["price_rises"].empty:
            st.success("✅ Nothing flagged — COGS on track and no price spikes.")


# ============ Variations tab (owner): part-time variation letters ============
if tab_var is not None:
    with tab_var:
        import variations as V
        import contracts as _contracts
        st.markdown("#### 📝 Part-time variation letters")
        st.caption("Compares each part-timer's actual start times against their contract. "
                   "Drafts a *Variation of Employment Agreement* letter for material start-time "
                   "changes (>15 min) or shifts on a non-contracted day; recurring changes "
                   "combine into one dated letter. **Letters are drafts — review and sign before issuing.**")

        # ---- Manage contracted patterns (stored in DB, not in git) ----
        cmap = c_load_contracts()
        with st.expander(f"✏️ Part-time contracts ({len(cmap)} on file)", expanded=not cmap):
            st.caption("One row per employee + contracted day. Day = Mon/Tue/.../Sun; "
                       "times as 24h HH:MM. Edits are saved to your database.")
            crows = [{"Employee": emp, "Day": wd, "Start": s, "Finish": f}
                     for emp, days in cmap.items()
                     for wd, (s, f) in sorted(days.items(), key=lambda kv: _contracts.WEEKDAYS.get(kv[0], 9))]
            if not crows:
                crows = [{"Employee": "", "Day": "", "Start": "", "Finish": ""}]
            ced = st.data_editor(
                pd.DataFrame(crows), num_rows="dynamic", hide_index=True, width="stretch",
                key="contract_ed",
                column_config={"Day": st.column_config.SelectboxColumn(options=_contracts.DAY_ORDER)})
            if st.button("💾 Save contracts", key="contract_save"):
                new_map = {}
                for _, r in ced.iterrows():
                    emp = str(r["Employee"]).strip()
                    wd = str(r["Day"]).strip()
                    if not emp or wd not in _contracts.WEEKDAYS:
                        continue
                    new_map.setdefault(emp, {})[wd] = (str(r["Start"]).strip(), str(r["Finish"]).strip())
                for emp in set(cmap) - set(new_map):
                    storage.delete_contract(emp)
                for emp, days in new_map.items():
                    storage.save_contract(emp, days)
                bust_caches()
                st.session_state["var_flash"] = f"Saved contracts for {len(new_map)} employee(s)."
                st.rerun()
        if not cmap:
            st.info("Add your part-time contracts above to start detecting variations.")

        csv_bytes = st.session_state.get("shift_csv_bytes")
        if csv_bytes is None:
            vf = st.file_uploader("This week's Tanda shift CSV", type=["csv"], key="var_csv")
            if vf is not None:
                csv_bytes = vf.getvalue()
                st.session_state["shift_csv_bytes"] = csv_bytes
        else:
            st.caption("Using the Tanda CSV from the 🧮 Labour tab (or upload a different one there).")

        if csv_bytes:
            try:
                sdf = payroll.load_csv_from_bytes(csv_bytes)
                wk_end = pd.to_datetime(sdf["Date"]).max().date()
                vmap = V.detect_variations(sdf, cmap)
            except Exception as e:
                st.error(f"Could not read the shift CSV: {e}")
                vmap, wk_end = {}, None
            if wk_end is not None and not vmap:
                st.success(f"✅ Week ending {wk_end:%d %b %Y}: every part-timer matched their "
                           "contracted start times — no letters needed.")
            elif vmap:
                n = sum(len(v) for v in vmap.values())
                st.markdown(f"**Week ending {wk_end:%d %b %Y} — {n} variation(s)**")
                vrows = [{"Employee": emp, "Date": str(e["date"]), "Day": e["weekday"],
                          "Contracted start": V._fmt(e["contracted_start"]) if e["contracted_start"] else "—",
                          "Actual start": V._fmt(e["actual_start"]),
                          "Type": "extra day" if e["kind"] == "extra_day" else "start time"}
                         for emp, evs in vmap.items() for e in evs]
                st.dataframe(pd.DataFrame(vrows), hide_index=True, width="stretch")
                if st.button("💾 Save this week's variations to file", type="primary", key="var_save"):
                    saved = storage.save_variation_events(vmap, wk_end)
                    bust_caches()
                    st.session_state["var_flash"] = f"Saved {saved} variation event(s) to file."
                    st.rerun()
        if st.session_state.pop("var_flash", None):
            st.success(st.session_state.get("var_flash") or "Saved.")

        st.divider()
        st.markdown("**📄 Variation letters — recurring changes combined**")
        allev = c_load_variation_events()
        if allev.empty:
            st.caption("No variations on file yet — save a week above to start building the history.")
        else:
            by_emp = {}
            for _, r in allev.iterrows():
                by_emp.setdefault(r["employee"], []).append({
                    "date": pd.to_datetime(r["shift_date"]).date(), "weekday": r["weekday"],
                    "actual_start": r["actual_start"], "actual_finish": r["actual_finish"],
                    "contracted_start": (r["contracted_start"] or None),
                    "contracted_finish": None, "kind": r["kind"]})
            for emp in sorted(by_emp):
                pats = V.combine_patterns(by_emp[emp])
                with st.container(border=True):
                    st.markdown(f"**{emp}** — {V.summarise(pats)}")
                    try:
                        docx_bytes = V.render_letter(emp, pats)
                        commence = min(min(p["dates"]) for p in pats)
                        end = max(max(p["dates"]) for p in pats)
                        st.download_button(
                            f"⬇️ Variation letter (.docx)", docx_bytes, key=f"varletter_{emp}",
                            file_name=f"Variation Letter - {emp} - {commence:%d%b}-{end:%d%b%Y}.docx",
                            mime="application/vnd.openxmlformats-officedocument."
                                 "wordprocessingml.document")
                    except Exception as e:
                        st.caption(f"Could not build letter: {e}")
