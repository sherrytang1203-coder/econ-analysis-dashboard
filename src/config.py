MACRO_INDICATORS = {
    "leading": {
        "PERMIT":   {"name": "Building Permits",         "unit": "Thousands of Units",         "frequency": "Monthly"},
        "UMCSENT":  {"name": "Consumer Sentiment",       "unit": "Index 1966:Q1=100",          "frequency": "Monthly"},
        "AWHMAN":   {"name": "Avg Weekly Mfg Hours",     "unit": "Hours",                      "frequency": "Monthly"},
        "ICSA":     {"name": "Initial Jobless Claims",   "unit": "Number",                     "frequency": "Weekly"},
        "NEWORDER": {"name": "Mfg New Orders",           "unit": "Millions of Dollars",        "frequency": "Monthly"},
        "M2SL":     {"name": "M2 Money Supply",          "unit": "Billions of Dollars",        "frequency": "Monthly"},
    },
    "coincident": {
        "GDPC1":    {"name": "Real GDP",                 "unit": "Billions of Chained 2017 $", "frequency": "Quarterly"},
        "INDPRO":   {"name": "Industrial Production",    "unit": "Index 2017=100",             "frequency": "Monthly"},
        "PAYEMS":   {"name": "Nonfarm Payrolls",         "unit": "Thousands of Persons",       "frequency": "Monthly"},
        "PI":       {"name": "Personal Income",          "unit": "Billions of Dollars",        "frequency": "Monthly"},
    },
    "lagging": {
        "UNRATE":   {"name": "Unemployment Rate",        "unit": "Percent",                    "frequency": "Monthly"},
        "CPIAUCSL": {"name": "CPI",                      "unit": "Index 1982-84=100",          "frequency": "Monthly"},
        "CPILFESL": {"name": "Core CPI",                 "unit": "Index 1982-84=100",          "frequency": "Monthly"},
        "PCEPI":    {"name": "PCE Price Index",          "unit": "Index 2017=100",             "frequency": "Monthly"},
        "FEDFUNDS": {"name": "Fed Funds Rate",           "unit": "Percent",                    "frequency": "Monthly"},
        "BUSLOANS": {"name": "C&I Loans",                "unit": "Billions of Dollars",        "frequency": "Monthly"},
        "UEMPMEAN": {"name": "Avg Unemployment Duration","unit": "Weeks",                      "frequency": "Monthly"},
    },
}

ALL_SERIES_IDS = [sid for group in MACRO_INDICATORS.values() for sid in group]

MACRO_GROUP_LABELS = {
    "leading":    "Leading Indicators",
    "coincident": "Coincident Indicators",
    "lagging":    "Lagging Indicators",
}

# ── Capital Markets — yfinance tickers ───────────────────────────────────────

SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Health Care",
    "XLE":  "Energy",
    "XLY":  "Consumer Discr.",
    "XLP":  "Consumer Staples",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
    "XLC":  "Comm. Services",
}

INDEX_TICKERS = {
    "^GSPC": "S&P 500",
    "^DJI":  "Dow Jones",
    "^IXIC": "NASDAQ",
    "^RUT":  "Russell 2000",
}

COMMODITY_TICKERS = {
    "GC=F":     "Gold",
    "CL=F":     "WTI Oil",
    "HG=F":     "Copper",
    "DX-Y.NYB": "US Dollar",
}

RATE_TICKERS = {
    "^TNX": "10-Year Treasury",
    "^FVX": "5-Year Treasury",
    "^IRX": "3-Month T-Bill",
    "^VIX": "VIX",
}

# ── Misc ──────────────────────────────────────────────────────────────────────

# Rising value is bad — invert delta color in UI
INVERSE_DELTA_SERIES = {"UNRATE", "ICSA", "UEMPMEAN", "^VIX"}

# yfinance fallback when FRED is unavailable
YFINANCE_FALLBACK = {
    "SP500":  "^GSPC",
    "VIXCLS": "^VIX",
    "DGS10":  "^TNX",
    "DGS2":   "^FVX",
}

# BLS fallbacks when FRED is unavailable
BLS_FALLBACK = {
    "UNRATE":        "LNS14000000",
    "CPIAUCSL":      "CUUR0000SA0",
    "CPILFESL":      "CUUR0000SA0L1E",
    "PAYEMS":        "CES0000000001",
    "CES0500000003": "CES0500000003",
    "CIVPART":       "LNS11300000",
}

TRACKED_STOCKS = {
    "COF":   "Capital One Financial",
    "JRVR":  "James River Group",
    "MU":    "Micron Technology",
    "GOOGL": "Alphabet (Google)",
    "MKL":   "Markel Group",
    "DEO":   "Diageo",
    "BAM":   "Brookfield Asset Management",
    "BN":    "Brookfield Corporation",
    "PM":    "Philip Morris International",
    "ULTA":  "Ulta Beauty",
    "MO":    "Altria Group",
    "PYPL":  "PayPal Holdings",
}

GROQ_MODEL = "llama-3.3-70b-versatile"

NEWS_FEEDS = [
    {"name": "Reuters Business",  "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "CNBC Economy",      "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
    {"name": "MarketWatch",       "url": "https://feeds.marketwatch.com/marketwatch/economy-politics/"},
    {"name": "Yahoo Finance",     "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "Federal Reserve",   "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"name": "BLS",               "url": "https://www.bls.gov/feed/bls_latest.rss"},
]
