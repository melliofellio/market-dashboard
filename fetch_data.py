#!/usr/bin/env python3
"""Fetch every indicator and write data/latest.json.

Standard library only (no pip installs). Design rules:
  * One named, authoritative source per number. Nothing is blended or estimated.
  * Every indicator carries its own as-of date.
  * If a source fails, the last good value is carried forward and flagged stale. Never a guess.

Secrets come from environment variables:
  FRED_API_KEY           required (free, fred.stlouisfed.org)
  TWELVE_DATA_API_KEY    optional (gold, equal-weight vs cap-weight, small caps)
"""
import csv
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import datetime as dt
from statistics import pstdev, mean

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
FRED = os.environ.get("FRED_BASE", "https://api.stlouisfed.org/fred")
TD = os.environ.get("TD_BASE", "https://api.twelvedata.com")
CBOE_VIX = os.environ.get("CBOE_VIX_URL", "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv")
COINBASE = os.environ.get("COINBASE_BASE", "https://api.exchange.coinbase.com")
TREASURY = os.environ.get("TREASURY_BASE", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates")
YAHOO = os.environ.get("YAHOO_BASE", "https://query1.finance.yahoo.com")
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

RETRY_SLEEP = float(os.environ.get("RETRY_SLEEP", "2"))
SECRETS = [os.environ.get(k, "") for k in ("FRED_API_KEY", "TWELVE_DATA_API_KEY", "ANTHROPIC_API_KEY")]


def scrub(text):
    for s in SECRETS:
        if s:
            text = text.replace(s, "***")
    return text


def http_get(url, tries=3, timeout=40, ua="market-dashboard/1.0 (personal use)"):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404):  # retrying will not help (429 and 5xx are retried)
                break
            time.sleep(RETRY_SLEEP * (i + 1))
        except Exception as e:  # network hiccup
            last = e
            time.sleep(RETRY_SLEEP * (i + 1))
    raise RuntimeError(scrub(f"{type(last).__name__}: {last}"))


# ---------------------------------------------------------------- sources
def fred_obs(series, units, start, key):
    q = urllib.parse.urlencode({"series_id": series, "api_key": key, "file_type": "json",
                                "observation_start": start, "units": units})
    j = json.loads(http_get(f"{FRED}/series/observations?{q}"))
    if "observations" not in j:
        raise RuntimeError(scrub(str(j.get("error_message", j))[:200]))
    out = []
    for o in j["observations"]:
        v = o.get("value")
        if v in (None, "", "."):
            continue
        out.append((o["date"], float(v)))
    if not out:
        raise RuntimeError("no observations returned")
    return out


def fred_units_factor(series, key):
    """Multiplier that converts the series to $ billions, from FRED's own units text."""
    q = urllib.parse.urlencode({"series_id": series, "api_key": key, "file_type": "json"})
    j = json.loads(http_get(f"{FRED}/series?{q}"))
    u = j["seriess"][0]["units"].lower()
    if "million" in u:
        return 0.001
    if "billion" in u:
        return 1.0
    if "trillion" in u:
        return 1000.0
    raise RuntimeError(f"unrecognised units '{u}' for {series}")


def cboe_vix():
    text = http_get(CBOE_VIX)
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        r = {k.strip().upper(): v for k, v in r.items() if k}
        d, c = r.get("DATE"), r.get("CLOSE")
        if not d or not c:
            continue
        try:
            day = dt.datetime.strptime(d.strip(), "%m/%d/%Y").date() if "/" in d else dt.date.fromisoformat(d.strip())
            out.append((day.isoformat(), float(c)))
        except ValueError:
            continue  # a truncated or malformed row is skipped, never repaired
    if len(out) < 30:
        raise RuntimeError("VIX file returned too few rows")
    return out[-900:]


def twelve_daily(symbol, key, size=420, drop_partial=False):
    q = urllib.parse.urlencode({"symbol": symbol, "interval": "1day", "outputsize": size,
                                "order": "ASC", "apikey": key})
    j = json.loads(http_get(f"{TD}/time_series?{q}"))
    if j.get("status") == "error" or "values" not in j:
        raise RuntimeError(scrub(str(j.get("message", j))[:200]))
    out = [(v["datetime"][:10], float(v["close"])) for v in j["values"]]
    if drop_partial:
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        out = [o for o in out if o[0] < today]
    elif symbol != "XAU/USD":
        # US-listed ETFs: ignore today's bar until the session is over (16:30 New York time)
        from zoneinfo import ZoneInfo
        ny = dt.datetime.now(ZoneInfo("America/New_York"))
        if ny.hour * 60 + ny.minute < 16 * 60 + 30:
            out = [o for o in out if o[0] < ny.date().isoformat()]
    if not out:
        raise RuntimeError("no rows returned")
    return out


def coinbase_daily(product, days=600):
    """Daily candles bucketed at 00:00 UTC. The in-progress day is dropped."""
    now = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = {}
    end = now + dt.timedelta(days=1)
    left = days
    while left > 0:
        span = min(299, left)
        start = end - dt.timedelta(days=span)
        q = urllib.parse.urlencode({"granularity": 86400, "start": start.isoformat(), "end": end.isoformat()})
        data = json.loads(http_get(f"{COINBASE}/products/{product}/candles?{q}"))
        if not isinstance(data, list):
            raise RuntimeError(str(data)[:200])
        for t, low, high, op, close, vol in data:
            day = dt.datetime.fromtimestamp(t, dt.timezone.utc).date()
            rows[day.isoformat()] = float(close)
        end = start
        left -= span
        time.sleep(0.4)
    today = now.date().isoformat()
    out = sorted((d, v) for d, v in rows.items() if d < today)
    if not out:
        raise RuntimeError("no candles returned")
    return out


def treasury_curve(kind, years_back=3):
    """US Treasury daily par yield curve (kind='nominal') or real yield curve (kind='real') from treasury.gov.

    Returns {COLUMN: [(iso_date, pct), ...]} ascending by date, with column names normalised
    (e.g. '10 Yr' and '10 YR' both become '10YR'). The CSV arrives newest-first, so it is sorted here.
    Treasury publishes the same afternoon, so these are a day fresher than the FRED copies of the same data.
    The current year must load; older years only extend the history used for percentiles.
    """
    page = {"nominal": "daily_treasury_yield_curve", "real": "daily_treasury_real_yield_curve"}[kind]
    this_year = dt.date.today().year
    cols = {}
    for y in range(this_year - years_back, this_year + 1):
        q = f"type={page}&field_tdr_date_value={y}&page&_format=csv"
        try:
            text = http_get(f"{TREASURY}/daily-treasury-rates.csv/{y}/all?{q}")
        except RuntimeError:
            if y == this_year:
                raise
            continue
        for r in csv.DictReader(io.StringIO(text)):
            r = {re.sub(r"\s+", "", (k or "")).upper(): (v or "").strip() for k, v in r.items()}
            d = r.get("DATE")
            if not d:
                continue
            try:
                day = dt.datetime.strptime(d, "%m/%d/%Y").date().isoformat()
            except ValueError:
                continue  # a malformed row is skipped, never repaired
            for k, v in r.items():
                if k == "DATE" or not v:
                    continue
                try:
                    cols.setdefault(k, {})[day] = float(v)
                except ValueError:
                    continue
    out = {k: sorted(v.items()) for k, v in cols.items()}
    if not out or max(len(v) for v in out.values()) < 30:
        raise RuntimeError("Treasury file returned too few rows")
    return out


def yahoo_daily(symbol, range_="3y"):
    """Daily closes from Yahoo Finance's chart API (unofficial relay; no key). Today's bar is dropped until the
    US session is over (17:30 New York), so a half-finished day is never shown as a close."""
    from zoneinfo import ZoneInfo
    sym = urllib.parse.quote(symbol, safe="")
    j = json.loads(http_get(f"{YAHOO}/v8/finance/chart/{sym}?range={range_}&interval=1d", ua=BROWSER_UA))
    res = (j.get("chart") or {}).get("result")
    if not res:
        raise RuntimeError(scrub(str((j.get("chart") or {}).get("error") or j)[:200]))
    res = res[0]
    off = dt.timezone(dt.timedelta(seconds=int((res.get("meta") or {}).get("gmtoffset") or 0)))
    closes = (((res.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    out = {}
    for t, c in zip(res.get("timestamp") or [], closes):
        if c is None:
            continue
        out[dt.datetime.fromtimestamp(t, off).date().isoformat()] = float(c)
    ny = dt.datetime.now(ZoneInfo("America/New_York"))
    if ny.hour * 60 + ny.minute < 17 * 60 + 30:
        out.pop(ny.date().isoformat(), None)
    out = sorted(out.items())
    if len(out) < 30:
        raise RuntimeError("too few rows returned")
    return out


def fred_calendar(key, horizon_days=30):
    q = urllib.parse.urlencode({"api_key": key, "file_type": "json", "limit": 1000})
    rels = json.loads(http_get(f"{FRED}/releases?{q}")).get("releases", [])
    today = dt.date.today()
    events = []
    for pat, label in C.CALENDAR_RELEASES:
        match = next((r for r in rels if re.search(pat, r["name"])), None)
        if not match:
            continue
        q2 = urllib.parse.urlencode({
            "api_key": key, "file_type": "json", "release_id": match["id"],
            "realtime_start": today.isoformat(), "realtime_end": "9999-12-31",
            "include_release_dates_with_no_data": "true", "sort_order": "asc", "limit": 12})
        try:
            dates = json.loads(http_get(f"{FRED}/release/dates?{q2}")).get("release_dates", [])
        except RuntimeError:
            continue
        for d in dates:
            day = dt.date.fromisoformat(d["date"])
            if today <= day <= today + dt.timedelta(days=horizon_days):
                events.append({"date": d["date"], "event": label})
    for d in getattr(C, "FOMC_DATES", []):
        if today <= dt.date.fromisoformat(d) <= today + dt.timedelta(days=horizon_days):
            events.append({"date": d, "event": "FOMC decision"})
    # one entry per (date, event)
    uniq = {(e["date"], e["event"]): e for e in events}
    return sorted(uniq.values(), key=lambda e: (e["date"], e["event"]))


# ---------------------------------------------------------------- math helpers
def pct(a, b):
    return None if (b in (0, None) or a is None) else (a / b - 1.0) * 100.0


def rnd(x, n=4):
    return None if x is None else round(x, n)


def percentile_rank(vals, x):
    if not vals:
        return None
    return round(100.0 * sum(1 for v in vals if v <= x) / len(vals), 0)


def iso_week(d):
    y, w, _ = dt.date.fromisoformat(d).isocalendar()
    return (y, w)


def align_last_at_or_before(series, date):
    """series: ascending [(date, value)]. Value at the last date <= `date`."""
    lo, hi, ans = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= date:
            ans, lo = series[mid], mid + 1
        else:
            hi = mid - 1
    return ans


def make_record(cfg, obs):
    freq = cfg["freq"]
    n = C.SPARK_N[freq]
    dates = [o[0] for o in obs]
    vals = [o[1] for o in obs]
    last_d, last_v = obs[-1]
    prev_d, prev_v = (obs[-2] if len(obs) > 1 else (None, None))
    rec = {
        "id": cfg["id"], "name": cfg["name"], "section": cfg["section"], "freq": freq,
        "pre": cfg.get("pre", ""), "suf": cfg.get("suf", ""), "dec": cfg.get("dec", 2),
        "chg_kind": cfg.get("chg_kind", "pct"), "bad_when": cfg.get("bad_when"),
        "src": cfg["src"], "desc": cfg["desc"],
        "asof": last_d, "value": rnd(last_v), "prev_asof": prev_d, "prev": rnd(prev_v),
        "chg": rnd(last_v - prev_v) if prev_v is not None else None,
        "chg_pct": rnd(pct(last_v, prev_v)) if prev_v is not None and last_v > 0 and prev_v > 0 else None,
        "d": dates[-n:], "v": [rnd(v) for v in vals[-n:]],
        "extra": [],
    }
    win = vals[-C.PCTILE_N[freq]:]
    rec["pctile"] = percentile_rank(win, last_v) if len(win) >= 20 else None
    rec["pctile_n"] = len(win)

    # improving / worsening tag over a fixed window (direction only; no forecast)
    tn = C.TREND_N[freq]
    rec["trend_label"] = C.TREND_LABEL[freq]
    rec["trend_chg"] = None
    rec["trend"] = None
    if len(vals) > tn:
        tchg = last_v - vals[-1 - tn]
        rec["trend_chg"] = rnd(tchg)
        if cfg.get("bad_when"):
            sd = pstdev(vals[-n:]) if len(vals[-n:]) > 2 else 0
            if abs(tchg) <= 0.25 * sd:
                rec["trend"] = "flat"
            else:
                adverse = (tchg > 0) == (cfg["bad_when"] == "up")
                rec["trend"] = "worse" if adverse else "better"

    if freq == "d":
        yr = vals[-252:]
        lo, hi = min(yr), max(yr)
        rec["range52"] = {"lo": rnd(lo), "hi": rnd(hi), "pos": rnd((last_v - lo) / (hi - lo), 3) if hi > lo else None}
        wk = iso_week(last_d)
        prior = [o for o in obs if iso_week(o[0]) < wk]
        if prior:
            pd_, pv = prior[-1]
            rec["wtd"] = {"from": pd_, "chg": rnd(last_v - pv),
                          "pct": rnd(pct(last_v, pv)) if last_v > 0 and pv > 0 else None,
                          "complete": dt.date.fromisoformat(last_d).weekday() >= 4}
    return rec


def derived_record(base_cfg, obs, extra=None):
    cfg = dict(base_cfg)
    rec = make_record(cfg, obs)
    if extra:
        rec["extra"] = extra
    return rec


def is_stale(rec):
    cfg_age = rec.get("max_age") or C.MAX_AGE[rec["freq"]]
    age = (dt.date.today() - dt.date.fromisoformat(rec["asof"])).days
    return age > cfg_age, age


# ---------------------------------------------------------------- main
def main():
    os.makedirs(DATA, exist_ok=True)
    latest_path = os.path.join(DATA, "latest.json")
    prior = {}
    if os.path.exists(latest_path):
        try:
            prior = json.load(open(latest_path))
        except Exception:
            prior = {}
    prior_by_id = {i["id"]: i for i in prior.get("indicators", [])}

    fred_key = os.environ.get("FRED_API_KEY", "")
    td_key = os.environ.get("TWELVE_DATA_API_KEY", "")
    today = dt.date.today()

    records, failures, raw = [], [], {}

    def start_for(freq, days=None):
        return (today - dt.timedelta(days=days or C.LOOKBACK_DAYS[freq])).isoformat()

    def fail(cfg_id, name, msg):
        failures.append({"id": cfg_id, "name": name, "error": scrub(msg)[:240]})
        old = prior_by_id.get(cfg_id)
        if old:
            old = dict(old)
            old["carried_forward"] = True
            old["fetch_error"] = scrub(msg)[:240]
            records.append(old)

    def add_derived(cfg, builder):
        try:
            builder()
        except KeyError as e:  # an input series failed earlier in this run
            fail(cfg["id"], cfg["name"], f"derived value skipped: input {e} unavailable")
        except Exception as e:
            fail(cfg["id"], cfg["name"], f"derived: {e}")

    # ---- US Treasury curves (same-day source for 2y, 10y, TIPS and the curve spreads)
    tsy, tsy_err = {}, {}
    for kind in ("nominal", "real"):
        try:
            tsy[kind] = treasury_curve(kind, C.TREASURY_YEARS_BACK)
        except Exception as e:
            tsy_err[kind] = str(e)

    def tsy_obs(spec):
        kind = spec[0]
        if kind == "real" or kind == "nominal":
            if spec[0] not in tsy:
                raise RuntimeError(f"Treasury {spec[0]} curve unavailable: {tsy_err.get(spec[0], 'not loaded')}")
            col = tsy[spec[0]].get(spec[1])
            if not col:
                raise RuntimeError(f"Treasury column {spec[1]} missing")
            return col
        if "nominal" not in tsy:
            raise RuntimeError(f"Treasury nominal curve unavailable: {tsy_err.get('nominal', 'not loaded')}")
        a, b = tsy["nominal"].get(spec[1]), tsy["nominal"].get(spec[2])
        if not a or not b:
            raise RuntimeError(f"Treasury column {spec[1]} or {spec[2]} missing")
        bd = dict(b)
        return [(d, round(v - bd[d], 4)) for d, v in a if d in bd]

    # ---- FRED tiles (Treasury-sourced entries are routed to treasury.gov instead)
    for cfg in C.FRED_SERIES:
        try:
            if cfg.get("tsy"):
                obs = tsy_obs(cfg["tsy"])
                keep = dt.date.today() - dt.timedelta(days=C.LOOKBACK_DAYS[cfg["freq"]])
                obs = [o for o in obs if o[0] >= keep.isoformat()]
                if not obs:
                    raise RuntimeError("no observations returned")
                raw[cfg["id"]] = obs
                records.append(make_record(cfg, obs))
                continue
            if not fred_key:
                raise RuntimeError("FRED_API_KEY is not set")
            days = 3800 if cfg["id"] == "sp500" else None
            obs = fred_obs(cfg["series"], cfg["units"], start_for(cfg["freq"], days), fred_key)
            if cfg.get("scale") == "usd_b":
                f = fred_units_factor(cfg["series"], fred_key)
                obs = [(d, v * f) for d, v in obs]
            raw[cfg["id"]] = obs
            rec = make_record(cfg, obs)
            if cfg.get("max_age"):
                rec["max_age"] = cfg["max_age"]
            if cfg["id"] == "sp500":
                hi_d, hi_v = max(obs, key=lambda o: o[1])
                rec["extra"] = [{"k": "From record high", "v": f"{pct(obs[-1][1], hi_v):+.1f}%"},
                                {"k": "Record close", "v": f"{hi_v:,.2f} ({hi_d})"}]
            records.append(rec)
        except Exception as e:
            fail(cfg["id"], cfg["name"], str(e))
        time.sleep(0.15)

    # ---- helper FRED series for derived liquidity
    for h in C.FRED_HELPERS:
        try:
            obs = fred_obs(h["series"], h["units"], start_for(h["freq"]), fred_key)
            f = fred_units_factor(h["series"], fred_key)
            raw[h["id"]] = [(d, v * f) for d, v in obs]
        except Exception as e:
            failures.append({"id": h["id"], "name": h["series"], "error": scrub(str(e))[:240]})

    # ---- VIX (Cboe, the index's own publisher)
    vix_cfg = dict(id="vix", name="VIX (S&P 500 implied volatility)", section="market", freq="d", dec=2, chg_kind="abs",
                   src="Cboe Global Markets (index publisher)", desc="Cost of S&P 500 downside protection; the market's fear gauge.")
    try:
        vix = cboe_vix()
        raw["vix"] = vix
        records.append(make_record(vix_cfg, vix))
    except Exception as e:
        fail("vix", vix_cfg["name"], str(e))

    # ---- DXY and MOVE (ICE indexes, via Yahoo Finance relay; unofficial, so failure is expected sometimes)
    for y in C.YAHOO_SYMBOLS:
        ycfg = dict(id=y["id"], name=y["name"], section="market", freq="d", dec=y["dec"], chg_kind=y["chg_kind"],
                    bad_when=y.get("bad_when"), src=y["src"], desc=y["desc"])
        try:
            obs = yahoo_daily(y["symbol"])
            raw[y["id"]] = obs
            records.append(make_record(ycfg, obs))
        except Exception as e:
            fail(y["id"], y["name"], str(e))
        time.sleep(0.5)

    # ---- Crypto (Coinbase, the exchange itself)
    for pid, cid, nm, dec in (("BTC-USD", "btc", "Bitcoin (USD)", 0), ("ETH-USD", "eth", "Ethereum (USD)", 0)):
        cfg = dict(id=cid, name=nm, section="market", freq="d", pre="$", dec=dec, chg_kind="pct",
                   src="Coinbase Exchange daily close, 00:00 UTC (last complete day)",
                   desc="Trades 24/7. The weekly close is Sunday 00:00 UTC.")
        try:
            obs = coinbase_daily(pid)
            raw[cid] = obs
            rec = make_record(cfg, obs)
            records.append(rec)
        except Exception as e:
            fail(cid, nm, str(e))
        time.sleep(0.3)

    # BTC vs its 50-week moving average, from Saturday closes (= Sunday 00:00 UTC weekly close)
    def btc_ma():
        obs = raw["btc"]
        sat = [(d, v) for d, v in obs if dt.date.fromisoformat(d).weekday() == 5]
        if len(sat) < 50:
            raise RuntimeError("fewer than 50 weekly closes available")
        ma_series = []
        for i in range(49, len(sat)):
            ma_series.append((sat[i][0], mean(v for _, v in sat[i - 49:i + 1])))
        ma_now = ma_series[-1][1]
        last_px = obs[-1][1]
        px_by_date = dict(obs)
        rel_series = [(d, (px_by_date[d] / ma - 1) * 100) for d, ma in ma_series if px_by_date.get(d)]
        cfg = dict(id="btc_ma", name="Bitcoin vs 50-week MA, at the last weekly close", section="market", freq="w", suf="%", dec=1, chg_kind="abs",
                   src="Derived from Coinbase weekly closes", desc="Long-term trend line the market watches; weekly close above or below matters most.")
        extra = [{"k": f"Latest daily close ({obs[-1][0]}) vs MA", "v": f"{(last_px / ma_now - 1) * 100:+.1f}% (${last_px:,.0f})"},
                 {"k": "50-week MA", "v": f"${ma_now:,.0f}"},
                 {"k": "Last weekly close", "v": f"${sat[-1][1]:,.0f} ({sat[-1][0]})"}]
        rec = make_record(cfg, rel_series)
        rec["extra"] = extra
        records.append(rec)
    add_derived(dict(id="btc_ma", name="Bitcoin vs 50-week MA"), btc_ma)

    # ---- Twelve Data (gold + ETF ratios)
    td_raw, td_err = {}, {}
    for name, sym in C.TD_SYMBOLS.items():
        try:
            if not td_key:
                raise RuntimeError("TWELVE_DATA_API_KEY is not set")
            td_raw[name] = twelve_daily(sym, td_key, drop_partial=(name == "gold"))
        except Exception as e:
            td_err[name] = f"{sym}: {e}"
        if td_key:
            time.sleep(float(os.environ.get("TD_SLEEP", "8")))  # free plan allows 8 requests/minute

    if "gold" in td_raw:
        records.append(make_record(
            dict(id="gold", name="Gold (XAU/USD spot)", section="market", freq="d", pre="$", dec=2, chg_kind="pct",
                 src="Twelve Data XAU/USD spot, last complete day (aggregator; not the LBMA fix)",
                 desc="Monetary hedge; reacts to real yields and the dollar."), td_raw["gold"]))
    else:
        fail("gold", "Gold (XAU/USD spot)", td_err.get("gold", "source unavailable"))

    def ratio(num, den, cid, name, desc):
        missing = [k for k in (num, den) if k not in td_raw]
        if missing:
            raise RuntimeError("; ".join(td_err.get(k, f"{k} unavailable") for k in missing))
        a, b = dict(td_raw[num]), dict(td_raw[den])
        series = [(d, a[d] / b[d]) for d in sorted(a) if d in b and b[d]]
        cfg = dict(id=cid, name=name, section="market", freq="d", dec=4, chg_kind="pct",
                   src=f"Derived from Twelve Data {num.upper()} / {den.upper()} closes (ETF prices)", desc=desc)
        records.append(make_record(cfg, series))

    add_derived(dict(id="rsp_spy", name="Equal-weight vs cap-weight (RSP/SPY)"),
                lambda: ratio("rsp", "spy", "rsp_spy", "Breadth proxy: equal-weight vs cap-weight (RSP/SPY)",
                              "Rising = the average stock is keeping up; falling = a few mega-caps are carrying the index."))
    add_derived(dict(id="iwm_spy", name="Small caps vs large caps (IWM/SPY)"),
                lambda: ratio("iwm", "spy", "iwm_spy", "Small caps vs large caps (IWM/SPY)",
                              "Small caps are more rate- and credit-sensitive; a falling ratio is risk-off."))

    # ---- Derived from FRED data
    def net_liquidity():
        walcl, tga, rrp = raw["walcl"], raw["tga"], raw.get("rrp")
        if not rrp:
            raise RuntimeError("reverse repo series unavailable")
        series = []
        for d, w in walcl:
            t = align_last_at_or_before(tga, d)
            r = align_last_at_or_before(rrp, d)
            if t and r:
                series.append((d, w - t[1] - r[1]))
        cfg = dict(id="netliq", name="Net liquidity (Fed assets - Treasury cash - reverse repo)", section="leading",
                   freq="w", pre="$", suf="B", dec=0, chg_kind="pct", bad_when="down",
                   src="Derived: Fed H.4.1 total assets, Treasury General Account, NY Fed reverse repo (all via FRED)",
                   desc="Dollars actually available to the financial system. Widely used, but a convention, not an official statistic.")
        records.append(make_record(cfg, series))
    add_derived(dict(id="netliq", name="Net liquidity"), net_liquidity)

    def policy_expectations():
        two, ffs = raw["us2y"], raw["fedfunds"]
        series = []
        for d, v in two:
            f = align_last_at_or_before(ffs, d)   # policy rate only moves at meetings, so the latest print stands in for a day not yet posted
            if f and (dt.date.fromisoformat(d) - dt.date.fromisoformat(f[0])).days <= 5:
                series.append((d, v - f[1]))
        if not series:
            raise RuntimeError("no overlapping dates between 2-yr yield and Fed funds")
        cfg = dict(id="polexp", name="Policy expectations: 2-yr yield minus Fed funds", section="leading", freq="d",
                   suf=" pp", dec=2, chg_kind="bp",
                   src="Derived: 2-yr yield (US Treasury) minus effective Fed funds rate (NY Fed via FRED)",
                   desc="Above 0 = market prices hikes; below 0 = prices cuts. Free stand-in for FedWatch odds.")
        records.append(make_record(cfg, series))
    add_derived(dict(id="polexp", name="Policy expectations"), policy_expectations)

    def rate_vol():
        s = raw["us10y"]
        diffs = [(s[i][0], (s[i][1] - s[i - 1][1]) * 100) for i in range(1, len(s))]
        series = []
        for i in range(19, len(diffs)):
            window = [x[1] for x in diffs[i - 19:i + 1]]
            series.append((diffs[i][0], pstdev(window)))
        cfg = dict(id="ratevol", name="Treasury volatility: 20-day std of 10-yr daily moves (bp)", section="leading",
                   freq="d", dec=1, chg_kind="abs", bad_when="up",
                   src="Derived from US Treasury daily 10-yr yields",
                   desc="Realized volatility of the 10-yr yield (what already happened). The MOVE index above is the implied counterpart; when they diverge, options traders expect something the recent past does not show.")
        records.append(make_record(cfg, series))
    add_derived(dict(id="ratevol", name="Treasury volatility"), rate_vol)

    # ---- calendar
    try:
        calendar = fred_calendar(fred_key) if fred_key else []
    except Exception as e:
        calendar = prior.get("calendar", [])
        failures.append({"id": "calendar", "name": "Release calendar", "error": scrub(str(e))[:240]})

    # ---- health + output
    stale = []
    for r in records:
        s, age = is_stale(r)
        r["stale"] = bool(s or r.get("carried_forward"))
        r["age_days"] = age
        if r["stale"]:
            stale.append({"id": r["id"], "name": r["name"], "asof": r["asof"]})

    now_utc = dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        now_ct = now_utc.astimezone(ZoneInfo("America/Chicago"))
    except Exception:
        now_ct = now_utc
    session = "morning catch-up" if now_ct.hour < 12 else "after close"

    order = {s["id"]: i for i, s in enumerate(C.SECTIONS)}
    records.sort(key=lambda r: order.get(r["section"], 99))

    out = {
        "generated_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_ct": now_ct.strftime("%a %b %d, %Y %I:%M %p %Z"),
        "session": session,
        "sections": C.SECTIONS,
        "indicators": records,
        "calendar": calendar,
        "health": {"total": len(records), "stale": stale, "failed": failures},
    }
    tmp = latest_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, latest_path)

    # Compact plain-text digest (small enough for any tool or chat to read in full)
    try:
        import assess
        head = (f"MARKET DASHBOARD DIGEST. Data generated {out['generated_ct']} ({session} run). "
                f"{len(records)} indicators; {len(stale)} stale; {len(failures)} source failures.\n"
                "Each indicator has its own as-of date. Week-to-date is measured from the prior week's last observation.\n")
        if stale:
            head += "Stale: " + "; ".join(f"{x['name']} (last {x['asof']})" for x in stale) + "\n"
        with open(os.path.join(DATA, "digest.txt"), "w") as f:
            f.write(head + assess.summarize(out) + "\n")
    except Exception as e:
        print("digest not written:", scrub(str(e)))

    print(f"{len(records)} indicators written; {len(stale)} stale; {len(failures)} failures")
    for f in failures:
        print(f"  FAILED {f['id']}: {f['error']}")
    # Exit non-zero only if nothing useful was fetched at all
    if len(records) == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
