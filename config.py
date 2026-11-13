"""Indicator definitions for the market dashboard.

EDIT THIS FILE to add, remove, or re-classify an indicator. Nothing else needs to change.

Each FRED entry:
  id        short unique key
  name      label shown on the tile
  series    FRED series id
  units     FRED transform: lin (level), pc1 (% change from year ago), chg (change from prior period)
  freq      d | w | m | q   (how often the source publishes; drives staleness + sparkline length)
  section   market | leading | coincident | lagging
  pre/suf   text before / after the number (e.g. "$", "%")
  dec       decimals shown
  chg_kind  how the period change is displayed: pct (% change), bp (basis points), abs (plain difference)
  bad_when  up | down | None  -> which direction of change is adverse (None = no judgement).
            Drives the "improving / worsening" tag. It describes direction only, not a forecast.
  src       who actually produces the number
  desc      one line: what it measures and why it matters
  scale     optional: "usd_b" = convert to $ billions using FRED's own units metadata
"""

SECTIONS = [
    {"id": "market", "title": "Market snapshot",
     "blurb": "Daily prices and rates. Compare these with the headlines; weekly close is the number to weigh."},
    {"id": "leading", "title": "Leading indicators",
     "blurb": "Move before the economy does: yield curve, credit, financial conditions, claims, permits, sentiment, liquidity."},
    {"id": "coincident", "title": "Coincident indicators",
     "blurb": "Describe the economy as it is now: jobs, output, income, spending, real-time recession gauges."},
    {"id": "lagging", "title": "Lagging indicators",
     "blurb": "Confirm what already happened: unemployment, inflation, loan growth. Useful for the regime, poor for timing."},
]

FRED_SERIES = [
    # ---------------- Market snapshot ----------------
    dict(id="sp500", name="S&P 500", series="SP500", units="lin", freq="d", section="market", dec=2, chg_kind="pct",
         src="S&P Dow Jones Indices via FRED (1-day lag)", desc="US large-cap equities, cap-weighted."),
    dict(id="nasdaq", name="Nasdaq Composite", series="NASDAQCOM", units="lin", freq="d", section="market", dec=2, chg_kind="pct",
         src="Nasdaq via FRED (1-day lag)", desc="Tech-heavy equities; the growth/duration-sensitive end."),
    dict(id="dow", name="Dow Jones Industrial", series="DJIA", units="lin", freq="d", section="market", dec=2, chg_kind="pct",
         src="S&P Dow Jones Indices via FRED (1-day lag)", desc="30 large industrials; price-weighted."),
    dict(id="us10y", name="10-year Treasury yield", series="DGS10", units="lin", freq="d", section="market", suf="%", dec=2, chg_kind="bp",
         src="Federal Reserve H.15 via FRED", desc="Benchmark long rate; prices mortgages, valuations, and the dollar."),
    dict(id="us2y", name="2-year Treasury yield", series="DGS2", units="lin", freq="d", section="market", suf="%", dec=2, chg_kind="bp",
         src="Federal Reserve H.15 via FRED", desc="Where the market expects the policy rate to average over two years."),
    dict(id="bill4w", name="4-week T-bill yield", series="DTB4WK", units="lin", freq="d", section="market", suf="%", dec=2, chg_kind="bp",
         src="Federal Reserve H.15 via FRED", desc="What cash earns; the opportunity cost of waiting. Bank-discount basis, so it reads below the investment yield brokers like Fidelity quote."),
    dict(id="fedfunds", name="Fed funds (effective)", series="DFF", units="lin", freq="d", section="market", suf="%", dec=2, chg_kind="bp",
         src="Federal Reserve Bank of New York via FRED", desc="Actual overnight policy rate."),
    dict(id="real10", name="10-year real yield (TIPS)", series="DFII10", units="lin", freq="d", section="market", suf="%", dec=2, chg_kind="bp",
         src="Federal Reserve H.15 via FRED", desc="Inflation-adjusted long rate; the main headwind for gold and long-duration assets."),
    dict(id="be10", name="10-year breakeven inflation", series="T10YIE", units="lin", freq="d", section="market", suf="%", dec=2, chg_kind="bp",
         src="Federal Reserve H.15 via FRED", desc="Inflation the bond market is pricing over 10 years."),
    dict(id="mortgage30", name="30-yr mortgage rate", series="MORTGAGE30US", units="lin", freq="w", section="market", suf="%", dec=2, chg_kind="bp",
         src="Freddie Mac PMMS via FRED (weekly, Thursday)", desc="Housing financing cost; tracks the 10-year plus a spread."),
    dict(id="usd", name="US dollar (broad, trade-weighted)", series="DTWEXBGS", units="lin", freq="d", section="market", dec=2, chg_kind="pct", max_age=12,
         src="Federal Reserve Board via FRED (posted weekly, so it runs up to a week behind)", desc="Dollar vs a broad basket; strength tightens global financial conditions."),
    dict(id="wti", name="WTI crude oil", series="DCOILWTICO", units="lin", freq="d", section="market", pre="$", dec=2, chg_kind="pct",
         src="US EIA via FRED (spot, 1-day lag)", desc="Energy input cost and an inflation driver."),

    # ---------------- Leading ----------------
    dict(id="c10y3m", name="Yield curve: 10-yr minus 3-month", series="T10Y3M", units="lin", freq="d", section="leading", suf=" pp", dec=2, chg_kind="bp", bad_when="down",
         src="Federal Reserve H.15 via FRED", desc="The Fed's preferred curve recession gauge. Below zero = inverted."),
    dict(id="c10y2y", name="Yield curve: 10-yr minus 2-year", series="T10Y2Y", units="lin", freq="d", section="leading", suf=" pp", dec=2, chg_kind="bp", bad_when="down",
         src="Federal Reserve H.15 via FRED", desc="Most-quoted curve. Re-steepening after inversion often comes close to the downturn itself."),
    dict(id="hy_oas", name="High-yield credit spread (OAS)", series="BAMLH0A0HYM2", units="lin", freq="d", section="leading", suf="%", dec=2, chg_kind="bp", bad_when="up",
         src="ICE Data Indices via FRED (1-day lag)", desc="Extra yield junk bonds pay. Lags equity stress by days, leads the real economy by months."),
    dict(id="ig_oas", name="Investment-grade credit spread (OAS)", series="BAMLC0A0CM", units="lin", freq="d", section="leading", suf="%", dec=2, chg_kind="bp", bad_when="up",
         src="ICE Data Indices via FRED (1-day lag)", desc="Spread on high-quality corporates; widens later and more slowly than high-yield."),
    dict(id="nfci", name="Financial conditions (Chicago Fed NFCI)", series="NFCI", units="lin", freq="w", section="leading", dec=2, chg_kind="abs", bad_when="up",
         src="Federal Reserve Bank of Chicago via FRED (weekly)", desc="105-measure index. Above 0 = tighter than average; below 0 = looser."),
    dict(id="claims", name="Initial jobless claims", series="ICSA", units="lin", freq="w", section="leading", dec=0, chg_kind="pct", bad_when="up",
         src="US Dept of Labor via FRED (weekly, Thursday)", desc="First layoff signal; the fastest labor-market data we get."),
    dict(id="permits", name="Building permits (thousands, annualized)", series="PERMIT", units="lin", freq="m", section="leading", dec=0, chg_kind="pct", bad_when="down",
         src="US Census Bureau via FRED", desc="Housing is rate-sensitive and turns before the rest of the economy."),
    dict(id="sentiment", name="Consumer sentiment (U. Michigan)", series="UMCSENT", units="lin", freq="m", section="leading", dec=1, chg_kind="abs", bad_when="down",
         src="University of Michigan via FRED", desc="Household mood; noisy, but extremes matter."),
    dict(id="m2yoy", name="M2 money supply, year over year", series="M2SL", units="pc1", freq="m", section="leading", suf="%", dec=1, chg_kind="abs", bad_when="down",
         src="Federal Reserve Board via FRED", desc="Broad money growth; the slow-moving liquidity backdrop."),
    dict(id="reserves", name="Bank reserves at the Fed", series="WRESBAL", units="lin", freq="w", section="leading", pre="$", suf="B", dec=0, chg_kind="pct", bad_when="down", scale="usd_b",
         src="Federal Reserve H.4.1 via FRED (weekly)", desc="Cash banks hold at the Fed; falling reserves are what stressed money markets in 2019."),
    dict(id="rrp", name="Reverse repo facility (overnight)", series="RRPONTSYD", units="lin", freq="d", section="leading", pre="$", suf="B", dec=0, chg_kind="abs", scale="usd_b",
         src="Federal Reserve Bank of New York via FRED", desc="Idle cash parked at the Fed; a drawdown releases liquidity into markets."),

    # ---------------- Coincident ----------------
    dict(id="payrolls", name="Nonfarm payrolls, monthly change (thousands)", series="PAYEMS", units="chg", freq="m", section="coincident", dec=0, chg_kind="abs", bad_when="down",
         src="Bureau of Labor Statistics via FRED", desc="Jobs added in the month; revisions are large, so trend beats any single print."),
    dict(id="indpro", name="Industrial production, year over year", series="INDPRO", units="pc1", freq="m", section="coincident", suf="%", dec=1, chg_kind="abs", bad_when="down",
         src="Federal Reserve Board via FRED", desc="Factory, mining, and utility output."),
    dict(id="realinc", name="Real personal income ex-transfers, YoY", series="W875RX1", units="pc1", freq="m", section="coincident", suf="%", dec=1, chg_kind="abs", bad_when="down",
         src="Bureau of Economic Analysis via FRED", desc="Earned income after inflation; the cycle-dating committee's own income measure."),
    dict(id="retail", name="Retail sales, year over year (nominal)", series="RSAFS", units="pc1", freq="m", section="coincident", suf="%", dec=1, chg_kind="abs", bad_when="down",
         src="US Census Bureau via FRED", desc="Consumer spending in dollars; compare against inflation to judge real strength."),
    dict(id="sahm", name="Sahm rule recession indicator", series="SAHMREALTIME", units="lin", freq="m", section="coincident", suf=" pp", dec=2, chg_kind="abs", bad_when="up",
         src="Federal Reserve Bank of St. Louis via FRED", desc="Unemployment 3-mo avg minus its 12-mo low. At 0.50 or above, a recession has historically begun."),
    dict(id="gdpnow", name="GDPNow, current-quarter estimate", series="GDPNOW", units="lin", freq="q", section="coincident", suf="%", dec=1, chg_kind="abs", bad_when="down", max_age=130,
         src="Federal Reserve Bank of Atlanta via FRED", desc="Model nowcast of real GDP growth, annualized; a model, not a measurement."),
    dict(id="gdp", name="Real GDP growth (annualized, latest quarter)", series="A191RL1Q225SBEA", units="lin", freq="q", section="coincident", suf="%", dec=1, chg_kind="abs", bad_when="down",
         src="Bureau of Economic Analysis via FRED", desc="The official scorecard, published well after the fact."),

    # ---------------- Lagging ----------------
    dict(id="unrate", name="Unemployment rate", series="UNRATE", units="lin", freq="m", section="lagging", suf="%", dec=1, chg_kind="abs", bad_when="up",
         src="Bureau of Labor Statistics via FRED", desc="Peaks after recessions have already started."),
    dict(id="undur", name="Average duration of unemployment (weeks)", series="UEMPMEAN", units="lin", freq="m", section="lagging", dec=1, chg_kind="abs", bad_when="up",
         src="Bureau of Labor Statistics via FRED", desc="How hard it is to get rehired; rises late in a downturn."),
    dict(id="cpi", name="CPI, year over year", series="CPIAUCSL", units="pc1", freq="m", section="lagging", suf="%", dec=1, chg_kind="abs", bad_when="up",
         src="Bureau of Labor Statistics via FRED", desc="Headline consumer inflation."),
    dict(id="corecpi", name="Core CPI, year over year", series="CPILFESL", units="pc1", freq="m", section="lagging", suf="%", dec=1, chg_kind="abs", bad_when="up",
         src="Bureau of Labor Statistics via FRED", desc="Inflation excluding food and energy."),
    dict(id="corepce", name="Core PCE, year over year", series="PCEPILFE", units="pc1", freq="m", section="lagging", suf="%", dec=1, chg_kind="abs", bad_when="up",
         src="Bureau of Economic Analysis via FRED", desc="The Fed's inflation target measure."),
    dict(id="ciloans", name="Commercial & industrial loans, year over year", series="TOTCI", units="pc1", freq="w", section="lagging", suf="%", dec=1, chg_kind="abs", max_age=21,
         src="Federal Reserve H.8 via FRED (weekly)", desc="Business borrowing; lags the cycle, and a contraction confirms a slowdown."),
]

# Series fetched only to build derived indicators (not shown as tiles)
FRED_HELPERS = [
    dict(id="walcl", series="WALCL", units="lin", freq="w", scale="usd_b"),
    dict(id="tga", series="WTREGEN", units="lin", freq="w", scale="usd_b"),
]

# Twelve Data (free key). Prices are an aggregator feed, not the exchange itself.
TD_SYMBOLS = {
    "gold": "XAU/USD",
    "spy": "SPY",
    "rsp": "RSP",
    "iwm": "IWM",
}

# Release calendar: regex on FRED release names -> label shown on the page
CALENDAR_RELEASES = [
    (r"^Employment Situation$", "Jobs report (payrolls, unemployment)"),
    (r"^Consumer Price Index$", "CPI"),
    (r"^Personal Income and Outlays$", "PCE inflation and income"),
    (r"^Gross Domestic Product$", "GDP"),
    (r"^Advance Monthly Sales for Retail", "Retail sales"),
    (r"^G\.17 Industrial Production", "Industrial production"),
    (r"^Job Openings and Labor Turnover", "JOLTS job openings"),
]

# FOMC decision days, entered by hand from federalreserve.gov/monetarypolicy/fomccalendars.htm
# (FRED's FOMC release lists a placeholder every day, so it cannot be used). ADD NEXT YEAR'S DATES EACH DECEMBER.
FOMC_DATES = ["2026-12-09"]

# Staleness thresholds in days (observation date to today), by frequency
MAX_AGE = {"d": 5, "w": 12, "m": 75, "q": 220}
SPARK_N = {"d": 120, "w": 104, "m": 60, "q": 20}
TREND_N = {"d": 20, "w": 4, "m": 3, "q": 1}          # obs back for the improving/worsening tag
TREND_LABEL = {"d": "4 wk", "w": "4 wk", "m": "3 mo", "q": "1 qtr"}
PCTILE_N = {"d": 756, "w": 260, "m": 120, "q": 40}
LOOKBACK_DAYS = {"d": 1150, "w": 1900, "m": 3800, "q": 5200}
