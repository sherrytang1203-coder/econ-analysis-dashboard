"""Shared design system: color palette, Plotly template, chart config, and CSS.

Single source of truth for chart styling. `register_template()` installs the
"econ_light" Plotly template as the default; every figure picks it up as long
as it is rendered with `theme=None` in `st.plotly_chart` (Streamlit's default
`theme="streamlit"` would override it).
"""

import plotly.graph_objects as go
import plotly.io as pio

PALETTE = {
    "primary": "#2563eb",   # blue — main line/price color
    "up":      "#059669",   # green — gains, oversold, positive bars
    "down":    "#dc2626",   # red — losses, overbought, negative bars
    "amber":   "#d97706",   # secondary series (2Y yield, weekly RSI, forecasts)
    "violet":  "#7c3aed",   # tertiary series (daily RSI, FCF)
    "pink":    "#db2777",   # dividend series
    "text":    "#111827",
    "muted":   "#6b7280",
    "faint":   "#9ca3af",
    "grid":    "#f3f4f6",
    "border":  "#e5e7eb",
    "bg":      "#ffffff",
    "bg_soft": "#f8fafc",
}

COLORWAY = [PALETTE["primary"], PALETTE["amber"], PALETTE["up"],
            PALETTE["violet"], PALETTE["pink"], PALETTE["down"]]

FONT_FAMILY = '"Source Sans Pro", -apple-system, "Segoe UI", Roboto, sans-serif'

CHART_HEIGHT = {"sm": 260, "md": 300, "lg": 340, "heatmap": 420}

# One config for every chart. scrollZoom off + dragmode=False (template) means
# a touch drag scrolls the page instead of panning the chart; a tap shows the
# unified hover; double-tap resets any range-button zoom.
PLOTLY_CONFIG = {
    "displayModeBar": False,
    "scrollZoom": False,
    "doubleClick": "reset",
    "responsive": True,
    "showAxisDragHandles": False,
}


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def register_template() -> None:
    axis = dict(
        gridcolor=PALETTE["grid"], gridwidth=1,
        tickfont=dict(size=11, color=PALETTE["faint"]),
        title=dict(font=dict(size=11, color=PALETTE["faint"])),
        zeroline=False, showline=False,
    )
    pio.templates["econ_light"] = go.layout.Template(layout=go.Layout(
        font=dict(family=FONT_FAMILY, size=12, color=PALETTE["text"]),
        title=dict(font=dict(size=13, color=PALETTE["text"]), y=0.97, yanchor="top"),
        plot_bgcolor=PALETTE["bg"],
        paper_bgcolor="rgba(0,0,0,0)",
        colorway=COLORWAY,
        margin=dict(l=56, r=16, t=48, b=36),
        hovermode="x unified",
        dragmode=False,
        hoverlabel=dict(bgcolor="white", bordercolor=PALETTE["border"],
                        font=dict(size=12, color=PALETTE["text"], family=FONT_FAMILY)),
        legend=dict(orientation="h", x=0, y=1.06, yanchor="bottom",
                    font=dict(size=11, color=PALETTE["muted"]),
                    bgcolor="rgba(0,0,0,0)"),
        showlegend=False,
        xaxis=dict(**axis,
                   rangeslider=dict(visible=False),
                   # styles the vertical line drawn by "x unified" hover
                   showspikes=True, spikemode="across", spikethickness=1,
                   spikedash="dot", spikecolor=PALETTE["border"]),
        yaxis=axis,
    ))
    pio.templates.default = "econ_light"


def pro_layout(title: str, y_title: str = "", height: int = CHART_HEIGHT["md"]) -> dict:
    """Per-chart layout bits; everything else comes from the template."""
    return dict(
        title=dict(text=f"<b>{title}</b>"),
        yaxis_title=y_title,
        height=height,
    )


BASE_CSS = f"""
<style>
.modebar-container {{ opacity: 0.25 !important; transition: opacity .2s; }}
.modebar-container:hover {{ opacity: 1 !important; }}
.sec-label {{
    font-size: 11px; font-weight: 700; color: {PALETTE["faint"]};
    text-transform: uppercase; letter-spacing: 1.2px;
    padding-bottom: 8px; border-bottom: 1px solid {PALETTE["grid"]};
    margin: 14px 0 4px 0;
}}
</style>
"""

MOBILE_CSS = """
<style>
@media (max-width: 1024px) {
    /* Remove padding on sides, add substantial top padding for titles */
    .main .block-container {
        padding: 48px 8px 0 8px !important;
        margin: 0 !important;
        max-width: 100% !important;
    }

    /* Stack all columns vertically */
    div[data-testid="stHorizontalBlock"] {
        flex-direction: column !important;
    }
    div[data-testid="column"] {
        width: 100% !important;
        flex: 1 1 100% !important;
        min-width: 100% !important;
    }

    /* Make tab bar scroll horizontally */
    div[data-testid="stTabBar"] {
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
    }
    div[data-testid="stTabBar"] button {
        white-space: nowrap !important;
        flex-shrink: 0 !important;
    }

    /* Stack metric card groups vertically */
    .metric-grid { flex-direction: column !important; }
    .mgroup      { width: 100% !important; }
    .mpair       { gap: 16px !important; }
    .mval        { font-size: 22px !important; }
    .stock-name  { font-size: 20px !important; }

    /* Charts full width; height stays managed by Plotly (responsive config) */
    .js-plotly-plot {
        width: 100% !important;
        max-height: none !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    .js-plotly-plot .plotly { margin: 0 !important; padding: 0 !important; }
    svg.main-svg {
        width: 100% !important;
        -webkit-tap-highlight-color: transparent !important;
    }
    div[data-testid="stPlotlyChart"] {
        width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    /* Remove container borders and padding on mobile */
    div[data-testid="stContainer"] {
        margin: 0 !important;
        padding: 0 !important;
        border: none !important;
        border-radius: 0 !important;
        background: transparent !important;
    }
    div[class*="stContainer"] { border: none !important; border-radius: 0 !important; }
    div[class*="element-container"] { padding: 0 !important; margin: 0 !important; }
    div[data-testid="stExpanderDetails"] { padding: 0 !important; }

    /* Full-width selectbox and inputs */
    div[data-testid="stSelectbox"],
    div[data-testid="stTextInput"] {
        width: 100% !important;
    }

    h1 { font-size: 1.4rem !important; }

    div[data-testid="stMetric"] {
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
    }
    div[class*="metric"] {
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
    }
    div[data-testid="stHorizontalBlock"] div {
        margin: 0 !important;
        padding: 0 !important;
    }
}
</style>
"""
