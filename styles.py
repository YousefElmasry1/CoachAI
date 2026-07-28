"""
CoachAI – CSS Styles & Theme Engine
=====================================

Injects custom CSS into Streamlit for a premium, polished appearance.
Supports dark and light mode via CSS custom properties.
"""

from __future__ import annotations

import streamlit as st


def inject_styles(dark_mode: bool = True) -> None:
    """Inject the complete CSS theme into the Streamlit page."""
    mode = "dark" if dark_mode else "light"
    st.markdown(_build_css(mode), unsafe_allow_html=True)


def _build_css(mode: str) -> str:
    """Build the full CSS string with variables for the given mode."""
    if mode == "dark":
        vars_block = """
            --bg-primary: #0a0a0f;
            --bg-secondary: #12121a;
            --bg-card: #1a1a2e;
            --bg-card-hover: #1f1f35;
            --bg-elevated: #16213e;
            --border: rgba(255,255,255,0.06);
            --border-hover: rgba(255,255,255,0.12);
            --text-primary: #f0f0f5;
            --text-secondary: #8888a0;
            --text-muted: #55556a;
            --accent: #6c63ff;
            --accent-light: #a78bfa;
            --accent-glow: rgba(108,99,255,0.15);
            --success: #10b981;
            --success-bg: rgba(16,185,129,0.10);
            --warning: #f59e0b;
            --warning-bg: rgba(245,158,11,0.10);
            --danger: #ef4444;
            --danger-bg: rgba(239,68,68,0.10);
            --info: #3b82f6;
            --info-bg: rgba(59,130,246,0.10);
            --shadow: 0 4px 24px rgba(0,0,0,0.25);
            --shadow-sm: 0 2px 8px rgba(0,0,0,0.15);
            --glass: rgba(26,26,46,0.75);
            --glass-border: rgba(255,255,255,0.08);
        """
    else:
        vars_block = """
            --bg-primary: #f8f9fc;
            --bg-secondary: #ffffff;
            --bg-card: #ffffff;
            --bg-card-hover: #f3f4f8;
            --bg-elevated: #f0f2f8;
            --border: rgba(0,0,0,0.06);
            --border-hover: rgba(0,0,0,0.12);
            --text-primary: #1a1a2e;
            --text-secondary: #64748b;
            --text-muted: #94a3b8;
            --accent: #6c63ff;
            --accent-light: #8b7cf7;
            --accent-glow: rgba(108,99,255,0.10);
            --success: #059669;
            --success-bg: rgba(5,150,105,0.08);
            --warning: #d97706;
            --warning-bg: rgba(217,119,6,0.08);
            --danger: #dc2626;
            --danger-bg: rgba(220,38,38,0.08);
            --info: #2563eb;
            --info-bg: rgba(37,99,235,0.08);
            --shadow: 0 4px 24px rgba(0,0,0,0.06);
            --shadow-sm: 0 2px 8px rgba(0,0,0,0.04);
            --glass: rgba(255,255,255,0.85);
            --glass-border: rgba(0,0,0,0.06);
        """

    return f"""
<style>
    /* ── Import Premium Font ──────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    /* ── CSS Variables ────────────────────────────────── */
    :root {{
        {vars_block}
    }}

    /* ── Global Reset ─────────────────────────────────── */
    .stApp {{
        background-color: var(--bg-primary) !important;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
        color: var(--text-primary) !important;
    }}

    /* ── Sidebar ──────────────────────────────────────── */
    section[data-testid="stSidebar"] {{
        background: var(--bg-secondary) !important;
        border-right: 1px solid var(--border) !important;
    }}
    section[data-testid="stSidebar"] .stMarkdown p,
    section[data-testid="stSidebar"] .stMarkdown span {{
        color: var(--text-secondary) !important;
    }}
    section[data-testid="stSidebar"] .stRadio label span {{
        color: var(--text-primary) !important;
    }}

    /* ── Main Content ─────────────────────────────────── */
    .block-container {{
        padding: 1.5rem 2rem 4rem 2rem !important;
        max-width: 1400px !important;
    }}

    /* ── Typography ───────────────────────────────────── */
    h1, h2, h3, h4, h5, h6 {{
        font-family: 'Inter', sans-serif !important;
        color: var(--text-primary) !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em !important;
    }}
    h1 {{ font-size: 2rem !important; }}
    h2 {{ font-size: 1.5rem !important; }}
    h3 {{ font-size: 1.2rem !important; font-weight: 600 !important; }}

    p, span, li, div {{
        font-family: 'Inter', sans-serif !important;
    }}

    /* Never override Streamlit's own icon font here - doing so breaks
       icon ligatures (expander arrows, sidebar collapse arrow, etc.)
       and shows literal text like "keyboard_arrow_down" instead of
       the icon glyph. */
    [data-testid="stIconMaterial"] {{
        font-family: 'Material Symbols Rounded' !important;
    }}

    /* ── Metrics ──────────────────────────────────────── */
    [data-testid="stMetric"] {{
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 16px !important;
        padding: 1.2rem 1.4rem !important;
        transition: all 0.3s cubic-bezier(0.4,0,0.2,1) !important;
        box-shadow: var(--shadow-sm) !important;
    }}
    [data-testid="stMetric"]:hover {{
        border-color: var(--border-hover) !important;
        transform: translateY(-2px) !important;
        box-shadow: var(--shadow) !important;
    }}
    [data-testid="stMetricLabel"] {{
        color: var(--text-secondary) !important;
        font-weight: 500 !important;
        font-size: 0.82rem !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
    }}
    [data-testid="stMetricValue"] {{
        color: var(--text-primary) !important;
        font-weight: 700 !important;
        font-size: 1.8rem !important;
    }}
    [data-testid="stMetricDelta"] {{
        font-weight: 600 !important;
        font-size: 0.8rem !important;
    }}

    /* ── Tabs ─────────────────────────────────────────── */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px !important;
        background: var(--bg-card) !important;
        border-radius: 12px !important;
        padding: 4px !important;
        border: 1px solid var(--border) !important;
    }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 8px !important;
        padding: 8px 16px !important;
        font-weight: 500 !important;
        font-size: 0.85rem !important;
        color: var(--text-secondary) !important;
        background: transparent !important;
        border: none !important;
        transition: all 0.2s ease !important;
    }}
    .stTabs [data-baseweb="tab"]:hover {{
        background: var(--bg-card-hover) !important;
        color: var(--text-primary) !important;
    }}
    .stTabs [aria-selected="true"] {{
        background: var(--accent) !important;
        color: white !important;
    }}
    .stTabs [data-baseweb="tab-highlight"] {{
        display: none !important;
    }}
    .stTabs [data-baseweb="tab-border"] {{
        display: none !important;
    }}

    /* ── Expanders ────────────────────────────────────── */
    .streamlit-expanderHeader {{
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 12px !important;
        font-weight: 600 !important;
        color: var(--text-primary) !important;
        transition: all 0.2s ease !important;
    }}
    .streamlit-expanderHeader:hover {{
        border-color: var(--accent) !important;
    }}
    .streamlit-expanderContent {{
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-top: none !important;
        border-radius: 0 0 12px 12px !important;
    }}

    /* ── Buttons ──────────────────────────────────────── */
    .stButton > button {{
        border-radius: 10px !important;
        font-weight: 600 !important;
        font-size: 0.85rem !important;
        padding: 0.5rem 1.2rem !important;
        border: 1px solid var(--border) !important;
        background: var(--bg-card) !important;
        color: var(--text-primary) !important;
        transition: all 0.25s cubic-bezier(0.4,0,0.2,1) !important;
        letter-spacing: 0.01em !important;
    }}
    .stButton > button:hover {{
        border-color: var(--accent) !important;
        background: var(--accent-glow) !important;
        color: var(--accent) !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 4px 12px rgba(108,99,255,0.15) !important;
    }}
    .stButton > button[kind="primary"] {{
        background: linear-gradient(135deg, var(--accent), var(--accent-light)) !important;
        color: white !important;
        border: none !important;
    }}
    .stButton > button[kind="primary"]:hover {{
        box-shadow: 0 6px 20px rgba(108,99,255,0.3) !important;
        transform: translateY(-2px) !important;
    }}

    /* ── Select / Input ───────────────────────────────── */
    .stSelectbox [data-baseweb="select"],
    .stMultiSelect [data-baseweb="select"],
    .stTextInput input,
    .stNumberInput input {{
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 10px !important;
        color: var(--text-primary) !important;
    }}

    /* ── Divider ──────────────────────────────────────── */
    hr {{
        border-color: var(--border) !important;
        opacity: 0.5 !important;
    }}

    /* ── Scrollbar ────────────────────────────────────── */
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{
        background: var(--text-muted);
        border-radius: 3px;
    }}
    ::-webkit-scrollbar-thumb:hover {{ background: var(--text-secondary); }}

    /* ── Custom Card Component ────────────────────────── */
    .coach-card {{
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 1.4rem;
        margin-bottom: 1rem;
        transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
        box-shadow: var(--shadow-sm);
    }}
    .coach-card:hover {{
        border-color: var(--border-hover);
        transform: translateY(-2px);
        box-shadow: var(--shadow);
    }}

    /* ── Glass Card ───────────────────────────────────── */
    .glass-card {{
        background: var(--glass);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 1px solid var(--glass-border);
        border-radius: 20px;
        padding: 1.6rem;
        box-shadow: var(--shadow);
    }}

    /* ── Gradient Hero ────────────────────────────────── */
    .hero-gradient {{
        background: linear-gradient(135deg, var(--accent) 0%, var(--accent-light) 50%, #ec4899 100%);
        border-radius: 20px;
        padding: 2rem 2.4rem;
        color: white !important;
        position: relative;
        overflow: hidden;
        margin-bottom: 1.5rem;
    }}
    .hero-gradient::before {{
        content: '';
        position: absolute;
        top: -50%;
        right: -50%;
        width: 100%;
        height: 100%;
        background: radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 70%);
        pointer-events: none;
    }}
    .hero-gradient h1 {{
        color: white !important;
        font-size: 1.8rem !important;
        margin-bottom: 0.3rem !important;
    }}
    .hero-gradient p {{
        color: rgba(255,255,255,0.85) !important;
        font-size: 1rem !important;
    }}

    /* ── Status Badges ────────────────────────────────── */
    .badge {{
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px 10px;
        border-radius: 20px;
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.03em;
        text-transform: uppercase;
    }}
    .badge-success {{ background: var(--success-bg); color: var(--success); }}
    .badge-warning {{ background: var(--warning-bg); color: var(--warning); }}
    .badge-danger  {{ background: var(--danger-bg);  color: var(--danger); }}
    .badge-info    {{ background: var(--info-bg);    color: var(--info); }}
    .badge-muted   {{ background: var(--bg-elevated); color: var(--text-secondary); }}

    /* ── KPI Card ─────────────────────────────────────── */
    .kpi-card {{
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 1.3rem 1.5rem;
        transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
        box-shadow: var(--shadow-sm);
        position: relative;
        overflow: hidden;
    }}
    .kpi-card:hover {{
        transform: translateY(-3px);
        box-shadow: var(--shadow);
        border-color: var(--border-hover);
    }}
    .kpi-card::after {{
        content: '';
        position: absolute;
        top: 0; left: 0;
        width: 100%; height: 3px;
        border-radius: 16px 16px 0 0;
    }}
    .kpi-card .kpi-label {{
        font-size: 0.75rem;
        font-weight: 500;
        color: var(--text-secondary);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 0.5rem;
    }}
    .kpi-card .kpi-value {{
        font-size: 1.8rem;
        font-weight: 800;
        color: var(--text-primary);
        line-height: 1.1;
        margin-bottom: 0.3rem;
    }}
    .kpi-card .kpi-subtitle {{
        font-size: 0.78rem;
        color: var(--text-muted);
    }}

    /* ── Accent top strip colors ──────────────────────── */
    .kpi-accent-purple::after {{ background: linear-gradient(90deg, #6c63ff, #a78bfa); }}
    .kpi-accent-green::after  {{ background: linear-gradient(90deg, #10b981, #34d399); }}
    .kpi-accent-orange::after {{ background: linear-gradient(90deg, #f59e0b, #fbbf24); }}
    .kpi-accent-red::after    {{ background: linear-gradient(90deg, #ef4444, #f87171); }}
    .kpi-accent-blue::after   {{ background: linear-gradient(90deg, #3b82f6, #60a5fa); }}
    .kpi-accent-pink::after   {{ background: linear-gradient(90deg, #ec4899, #f472b6); }}
    .kpi-accent-teal::after   {{ background: linear-gradient(90deg, #14b8a6, #2dd4bf); }}

    /* ── Task Card ────────────────────────────────────── */
    .task-card {{
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.6rem;
        display: flex;
        align-items: center;
        gap: 1rem;
        transition: all 0.25s ease;
    }}
    .task-card:hover {{
        border-color: var(--border-hover);
        transform: translateX(4px);
    }}
    .task-card .priority-dot {{
        width: 10px;
        height: 10px;
        border-radius: 50%;
        flex-shrink: 0;
    }}
    .task-card .task-info {{
        flex: 1;
    }}
    .task-card .task-title {{
        font-weight: 600;
        font-size: 0.92rem;
        color: var(--text-primary);
        margin-bottom: 2px;
    }}
    .task-card .task-meta {{
        font-size: 0.78rem;
        color: var(--text-secondary);
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
    }}
    .task-card .task-time {{
        font-weight: 600;
        color: var(--accent);
        font-size: 0.85rem;
        white-space: nowrap;
    }}

    /* ── Section Header ───────────────────────────────── */
    .section-header {{
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 1.5rem 0 1rem 0;
    }}
    .section-header h3 {{
        margin: 0 !important;
        font-size: 1.1rem !important;
    }}

    /* ── Insight Card ─────────────────────────────────── */
    .insight-card {{
        background: var(--bg-card);
        border-left: 3px solid var(--accent);
        border-radius: 0 12px 12px 0;
        padding: 1rem 1.2rem;
        margin-bottom: 0.6rem;
    }}
    .insight-card p {{
        margin: 0 !important;
        color: var(--text-primary) !important;
        font-size: 0.9rem !important;
    }}
    .insight-card .evidence {{
        color: var(--text-muted) !important;
        font-size: 0.8rem !important;
        margin-top: 4px !important;
    }}

    /* ── Strength / Weakness Cards ─────────────────────── */
    .strength-card {{
        background: var(--success-bg);
        border: 1px solid rgba(16,185,129,0.15);
        border-radius: 12px;
        padding: 0.9rem 1.1rem;
        margin-bottom: 0.5rem;
        color: var(--text-primary);
        font-size: 0.9rem;
    }}
    .weakness-card {{
        background: var(--warning-bg);
        border: 1px solid rgba(245,158,11,0.15);
        border-radius: 12px;
        padding: 0.9rem 1.1rem;
        margin-bottom: 0.5rem;
        color: var(--text-primary);
        font-size: 0.9rem;
    }}
    .rec-card {{
        background: var(--info-bg);
        border: 1px solid rgba(59,130,246,0.15);
        border-radius: 12px;
        padding: 0.9rem 1.1rem;
        margin-bottom: 0.5rem;
        color: var(--text-primary);
        font-size: 0.9rem;
    }}

    /* ── Quote Card ────────────────────────────────────── */
    .quote-card {{
        background: linear-gradient(135deg, var(--accent-glow), transparent);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 1.5rem;
        font-style: italic;
    }}
    .quote-card .quote-text {{
        font-size: 1rem;
        color: var(--text-primary);
        line-height: 1.6;
    }}
    .quote-card .quote-author {{
        font-size: 0.82rem;
        color: var(--text-secondary);
        font-style: normal;
        margin-top: 0.5rem;
        font-weight: 600;
    }}

    /* ── Progress Bar ──────────────────────────────────── */
    .custom-progress {{
        width: 100%;
        height: 8px;
        background: var(--bg-elevated);
        border-radius: 4px;
        overflow: hidden;
    }}
    .custom-progress .bar {{
        height: 100%;
        border-radius: 4px;
        transition: width 1s cubic-bezier(0.4,0,0.2,1);
    }}

    /* ── Status Indicator ─────────────────────────────── */
    .status-dot {{
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-right: 6px;
        animation: pulse-dot 2s infinite;
    }}
    @keyframes pulse-dot {{
        0%, 100% {{ opacity: 1; }}
        50% {{ opacity: 0.5; }}
    }}

    /* ── Animations ────────────────────────────────────── */
    @keyframes fadeInUp {{
        from {{ opacity: 0; transform: translateY(12px); }}
        to   {{ opacity: 1; transform: translateY(0); }}
    }}
    .animate-in {{
        animation: fadeInUp 0.5s ease forwards;
    }}

    @keyframes slideInLeft {{
        from {{ opacity: 0; transform: translateX(-16px); }}
        to   {{ opacity: 1; transform: translateX(0); }}
    }}

    /* ── Info/Alert Boxes ─────────────────────────────── */
    .stAlert {{
        border-radius: 12px !important;
        border: 1px solid var(--border) !important;
    }}

    /* ── Download button ──────────────────────────────── */
    .stDownloadButton > button {{
        border-radius: 10px !important;
        font-weight: 600 !important;
    }}

    /* ── Plotly charts ────────────────────────────────── */
    .stPlotlyChart {{
        border-radius: 16px;
        overflow: hidden;
    }}

    /* ── Hide Streamlit Branding (but keep the sidebar toggle usable!) ──
       NOTE: `header {{ visibility: hidden }}` used to hide the sidebar's
       collapse/expand arrow too, since that control lives inside
       Streamlit's <header>. visibility is inherited by children, so once
       a user collapsed the sidebar there was no way to reopen it.

       Streamlit has also renamed this control's data-testid across
       versions (collapsedControl -> stSidebarCollapseButton -> ...), so
       every known variant is force-shown below rather than guessing one
       name. The header/toolbar are only made transparent, never hidden
       with display/visibility, so the toggle can never disappear again. ── */
    #MainMenu {{ visibility: hidden; }}
    footer {{ visibility: hidden; }}

    header[data-testid="stHeader"],
    .stAppHeader {{
        background: transparent !important;
        box-shadow: none !important;
        visibility: visible !important;
    }}

    /* Hide only the "Deploy" / options toolbar icons inside the header */
    [data-testid="stToolbar"],
    [data-testid="stToolbarActions"] {{
        visibility: hidden !important;
    }}

    /* Force the sidebar re-open control visible under every known
       data-testid Streamlit has used for it, across versions. */
    [data-testid="collapsedControl"],
    [data-testid="stSidebarCollapseButton"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapseIcon"],
    button[kind="header"] {{
        visibility: visible !important;
        display: flex !important;
        opacity: 1 !important;
        z-index: 999999 !important;
    }}

    /* ── Toast styling ────────────────────────────────── */
    .stToast {{
        border-radius: 12px !important;
        font-family: 'Inter', sans-serif !important;
    }}
</style>
"""