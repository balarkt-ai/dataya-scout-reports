#!/usr/bin/env python3
# ============================================================================
# SCOUT37 - SIDEWAYS/CHOP REGIME REPORT, LAST ~3 YEARS, ALL SEGMENTS
#
# WHY THIS EXISTS: user asked for a report of WHEN sideways/range-bound
# conditions happened over the last 3 years, segment-wise, across everything
# this project touches - the 3 index-options segments he trades
# (NIFTY/BANKNIFTY/SENSEX), the 50 NIFTY50 equity names (CPR equity/UT Bot
# equity trackers), and the 4 MCX commodities (GFS commodity track).
#
# THIS IS A FRESH, INDEPENDENT PRICE-ACTION CLASSIFICATION - not the same
# thing as the project's existing live trend_regime TAGS (scout23/27/28,
# [[dataya-regime]]), which come from Pine at entry time on intraday bars
# for the OPTIONS fleet's own trades. This script instead looks at plain
# DAILY candles going back 3 years for every segment, independent of any
# trade log, using two separate sideways definitions - per his explicit
# choice, shown SEPARATELY rather than merged into one verdict:
#
#   METHOD A - ADX(14) < 20 (Wilder DMI/ADX). Ported BYTE-IDENTICAL from
#   app.py's own already-live _postmarket_adx() (the exact formula behind
#   T76's ADX no-trade filter and scout20's filter face-off) - not
#   re-derived, just fed DAILY bars instead of 1-min ones.
#
#   METHOD B - "narrow CPR" day: width (TC-BC, built from the PREVIOUS day's
#   H/L/C) < 0.5x the 20-day rolling median width. This is the SAME
#   narrow-CPR gate already validated for T72/T73 and reused in the CPR
#   Equity Day-Trade Paper Tracker - not a new invention, just applied here
#   as a second, independent "is today range-bound" signal instead of as an
#   entry filter.
#
# Both methods only ever look at PAST bars for each day's own classification
# (no lookahead) - same discipline as every other scout script.
#
# SEGMENTS (57 total):
#   - 3 index-options segments: NIFTY, BANKNIFTY, SENSEX spot (token dict
#     reused verbatim from app.py's INDEX_SPOT_TOKENS / scout29).
#   - 50 NIFTY50 equity names (same NIFTY50_NAMES list as scout31/32/34,
#     resolve_token() same NSE-EQ scrip-master match, never hardcoded).
#   - 4 MCX commodities (GOLD/SILVER/CRUDEOIL/NATURALGAS - front-month
#     FUTCOM resolution reused from scout26/27, confirmed there to carry
#     10+ years of continuous history under whichever token is currently
#     nearest to expiry).
#
# DATA: ~1100 daily bars (~3yr) per segment, own cache dir - one fetch each,
# cached on retry (daily bars are light; this should be much faster than any
# of the 5-min-bar scout runs).
#
# OUTPUT:
#   (1) an OVERALL 3-year sideways% table for all 57 segments under BOTH
#       methods, sorted so the choppiest segments are easy to spot at a
#       glance;
#   (2) a full MONTH-BY-MONTH table (~36 months) for NIFTY/BANKNIFTY/SENSEX
#       specifically under both methods, since those are the segments he
#       actually trades options on;
#   (3) the COMPLETE month-by-month breakdown for ALL 57 segments x BOTH
#       methods is written to a CSV on the Render persistent disk
#       (/data/sideways_regime_report.csv) so nothing is lost even though
#       the terminal only prints the index detail + the 57-segment overall
#       summary - any specific stock's/commodity's month-by-month can be
#       pulled up separately later from that file if wanted.
#
# Run this on your Render Shell (needs live Angel session + real network -
# this sandbox has neither). READ-ONLY - fetches historical data only, never
# places an order, never modifies anything.
# ============================================================================
import os
import sys
import json
import time
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def market_hours_guard():
    now = datetime.datetime.now(IST)
    if now.weekday() < 5 and (9 <= now.hour < 16):
        print("SAFETY STOP: market hours - run this ONLY after 15:30 IST. Exiting.")
        sys.exit(1)
    return now


# ------------------------------------------------------------------ AUTH ---
def get_api():
    from SmartApi import SmartConnect
    import pyotp

    api_key = os.environ["ANGEL_API_KEY"]
    client_code = os.environ["ANGEL_CLIENT_CODE"]
    mpin = os.environ["ANGEL_MPIN"]
    totp_secret = os.environ["ANGEL_TOTP_SECRET"]

    api = SmartConnect(api_key=api_key)
    totp = pyotp.TOTP(totp_secret).now()
    session = api.generateSession(client_code, mpin, totp)
    if not session or not session.get("status"):
        raise RuntimeError(f"Angel login failed: {session}")
    return api


# ------------------------------------------------------------- SEGMENTS ----
# Index tokens reused verbatim from app.py's INDEX_SPOT_TOKENS / scout29 -
# NOT re-guessed.
INDEX_INSTRUMENTS = {
    "NIFTY":     {"exchange": "NSE", "token": "99926000"},
    "BANKNIFTY": {"exchange": "NSE", "token": "99926009"},
    "SENSEX":    {"exchange": "BSE", "token": "99919000"},
}

# Same NIFTY 50 list as scout31/scout32/scout34 - kept identical on purpose.
NIFTY50_NAMES = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TMPV", "TATASTEEL",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

COMMODITIES = ["GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"]

DAYS_BACK = 1100        # ~3yr, matches T72/T73's own re-verify window
CHUNK_DAYS = 700
ADX_PERIOD = 14         # same as app.py's POSTMARKET_ADX_LEN
CACHE_DIR = "/data/scout_sideways_cache"
CSV_PATH = "/data/sideways_regime_report.csv"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_MASTER_CACHE = os.path.join(CACHE_DIR, "scrip_master.json")
SCRIP_MASTER_MAX_AGE_H = 24


# --------------------------------------------------------- TOKEN LOOKUP ----
def _load_scrip_master():
    import urllib.request
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(SCRIP_MASTER_CACHE):
        age_h = (time.time() - os.path.getmtime(SCRIP_MASTER_CACHE)) / 3600
        if age_h < SCRIP_MASTER_MAX_AGE_H:
            with open(SCRIP_MASTER_CACHE) as f:
                data = json.load(f)
            print(f"  (scrip master: using cache, {len(data)} rows, {age_h:.1f}h old)")
            return data
    print("  (scrip master: downloading fresh copy - this file is large, may take a bit)")
    with urllib.request.urlopen(SCRIP_MASTER_URL, timeout=120) as resp:
        data = json.loads(resp.read())
    with open(SCRIP_MASTER_CACHE, "w") as f:
        json.dump(data, f)
    print(f"  (scrip master: downloaded {len(data)} rows)")
    return data


def resolve_equity_token(master, name):
    target = f"{name}-EQ".upper()
    matches = [r for r in master if r.get("exch_seg") == "NSE" and str(r.get("symbol", "")).upper() == target]
    if len(matches) != 1:
        raise ValueError(f"{name}: {len(matches)} match(es) for '{target}' - skipping, not guessing.")
    return matches[0]["token"]


def _expiry_key(r):
    e = r.get("expiry", "")
    try:
        return datetime.datetime.strptime(e, "%d%b%Y")
    except Exception:
        return datetime.datetime.max


def resolve_commodity_token(master, commodity):
    """Current front-month plain-name FUTCOM contract - confirmed (scout26)
    to carry the full multi-year continuous history under whichever token is
    nearest to expiry right now. Never hardcoded."""
    rows = [r for r in master if r.get("exch_seg") == "MCX" and r.get("instrumenttype") == "FUTCOM"
            and r.get("expiry") and str(r.get("name", "")).upper() == commodity]
    if not rows:
        raise ValueError(f"{commodity}: no dated FUTCOM contract found")
    rows.sort(key=_expiry_key)
    return rows[0]["token"]


# ------------------------------------------------------------ DATA FETCH ---
def _fetch_chunk(api, exchange, token, from_dt, to_dt):
    params = {
        "exchange": exchange, "symboltoken": token, "interval": "ONE_DAY",
        "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"), "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
    }
    for attempt in range(3):
        try:
            resp = api.getCandleData(params)
            if not resp or not resp.get("status") or resp.get("data") is None:
                return []
            return resp["data"]
        except Exception as e:
            print(f"    retry {attempt + 1}: {e}")
            time.sleep(2)
    return []


def get_daily_rows(api, cache_key, exchange, token, days_back=DAYS_BACK, chunk_days=CHUNK_DAYS):
    """Returns a sorted list of {"date","open","high","low","close"} dicts,
    one per trading day, own cache file per segment."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}_daily.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            rows = json.load(f)
        print(f"  (using cache: {len(rows)} rows)")
        return rows

    now = datetime.datetime.now()
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    start = end - datetime.timedelta(days=days_back)
    raw = {}
    cur = start
    while cur < end:
        nxt = min(cur + datetime.timedelta(days=chunk_days), end)
        for c in _fetch_chunk(api, exchange, token, cur, nxt):
            ts = c[0][:10]
            raw[ts] = {"date": ts, "open": float(c[1]), "high": float(c[2]),
                       "low": float(c[3]), "close": float(c[4])}
        time.sleep(0.4)
        cur = nxt
    rows = [raw[d] for d in sorted(raw.keys())]
    with open(cache_path, "w") as f:
        json.dump(rows, f)
    return rows


# ------------------------------------------------------ SIDEWAYS METHODS ---
def sideways_adx(daily_rows, period=ADX_PERIOD, threshold=20.0):
    """Wilder DMI/ADX - byte-identical formula to app.py's own
    _postmarket_adx() (T76's live ADX filter), just fed DAILY candles here
    instead of 1-min ones. Returns a list aligned to daily_rows:
    True (ADX<threshold, sideways) / False (trending) / None (not enough
    history yet to compute ADX)."""
    candles = [(r["date"], r["open"], r["high"], r["low"], r["close"]) for r in daily_rows]
    n = len(candles)
    out = [None] * n
    if n < period * 2 + 1:
        return out
    trs, pdms, ndms = [], [], []
    for i in range(1, n):
        _, o, h, l, c = candles[i]
        ph, pl, pc = candles[i - 1][2], candles[i - 1][3], candles[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, dn = h - ph, pl - l
        pdms.append(up if (up > dn and up > 0) else 0.0)
        ndms.append(dn if (dn > up and dn > 0) else 0.0)
    atr = sum(trs[:period]); pdi_s = sum(pdms[:period]); ndi_s = sum(ndms[:period])
    dxs = []
    for i in range(period, len(trs)):
        atr = atr - atr / period + trs[i]
        pdi_s = pdi_s - pdi_s / period + pdms[i]
        ndi_s = ndi_s - ndi_s / period + ndms[i]
        if atr <= 0:
            dxs.append(0.0); continue
        pdi = 100.0 * pdi_s / atr
        ndi = 100.0 * ndi_s / atr
        dxs.append(100.0 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0.0)
    if len(dxs) < period:
        return out
    adx = sum(dxs[:period]) / period
    adx_vals = [None] * n
    adx_vals[period * 2] = adx
    for j in range(period, len(dxs)):
        adx = (adx * (period - 1) + dxs[j]) / period
        idx = j + period + 1
        if idx < n:
            adx_vals[idx] = adx
    return [(v < threshold) if v is not None else None for v in adx_vals]


def sideways_narrow_cpr(daily_rows):
    """Narrow-CPR day: width (TC-BC from the PREVIOUS day's H/L/C) < 0.5x
    the 20-day rolling median width - the SAME construction as
    build_levels()/run_cpr_equity()'s narrow gate (scout34, CPR Equity Paper
    Tracker), just applied here as a standalone classifier. Returns a list
    aligned to daily_rows: True/False/None (None until the median has
    enough history, same >=5-widths warmup as the original)."""
    n = len(daily_rows)
    out = [None] * n
    widths = []
    for i in range(1, n):
        ph, pl, pc = daily_rows[i - 1]["high"], daily_rows[i - 1]["low"], daily_rows[i - 1]["close"]
        cp = (ph + pl + pc) / 3
        bc = (ph + pl) / 2
        tc = 2 * cp - bc
        if tc < bc:
            tc, bc = bc, tc
        w = tc - bc
        if len(widths) >= 5:
            sorted_w = sorted(widths[-20:])
            m = len(sorted_w)
            med20 = sorted_w[m // 2] if m % 2 else (sorted_w[m // 2 - 1] + sorted_w[m // 2]) / 2
            out[i] = (w < 0.5 * med20)
        widths.append(w)
    return out


def monthly_breakdown(daily_rows, flags):
    out = {}
    for row, flag in zip(daily_rows, flags):
        if flag is None:
            continue
        ym = row["date"][:7]
        e = out.setdefault(ym, {"sideways": 0, "total": 0})
        e["total"] += 1
        if flag:
            e["sideways"] += 1
    return out


def overall_pct(flags):
    valid = [f for f in flags if f is not None]
    if not valid:
        return None, 0
    return round(100.0 * sum(valid) / len(valid), 1), len(valid)


# ------------------------------------------------------------------ MAIN ---
def main():
    # NOTE: unlike other scout scripts, this one is safe to run ANY time,
    # including market hours - it only reads historical daily candles
    # (never intraday, never live ticks) and never places or touches any
    # order. market_hours_guard() is intentionally NOT called here.
    print("(read-only, historical-daily-data-only script - safe to run anytime, no market-hours wait needed)\n")
    api = get_api()
    print(f"SCOUT37 - Sideways/Chop Regime Report, ~{DAYS_BACK} days (~3yr), "
          f"all segments - {datetime.datetime.now().isoformat()}\n")
    print("METHOD A = daily ADX(14) < 20 (same formula as app.py's live ADX filter)")
    print("METHOD B = narrow-CPR day (same gate already validated for T72/T73)")
    print("Both are independent, price-action-only classifications - NOT the same")
    print("as your live trade-log regime tags (scout23/27/28).\n")

    master = _load_scrip_master()

    segments = []  # (name, kind, exchange, token)
    for name, inst in INDEX_INSTRUMENTS.items():
        segments.append((name, "INDEX", inst["exchange"], inst["token"]))
    for name in NIFTY50_NAMES:
        segments.append((name, "EQUITY", "NSE", None))
    for name in COMMODITIES:
        segments.append((name, "COMMODITY", "MCX", None))

    results = []  # dicts with name/kind/adx_pct/adx_n/narrow_pct/narrow_n/monthly_adx/monthly_narrow
    skipped = []
    for idx, (name, kind, exch, token) in enumerate(segments, 1):
        print(f"[{idx}/{len(segments)}] {name} ({kind}) ...")
        try:
            if token is None:
                if kind == "EQUITY":
                    token = resolve_equity_token(master, name)
                else:
                    token = resolve_commodity_token(master, name)
        except ValueError as e:
            print(f"  SKIPPED (token): {e}")
            skipped.append((name, str(e)))
            continue
        try:
            daily_rows = get_daily_rows(api, name, exch, token)
        except Exception as e:
            print(f"  SKIPPED (data fetch error): {e}")
            skipped.append((name, f"data fetch error: {e}"))
            continue
        if len(daily_rows) < 60:
            print(f"  SKIPPED: only {len(daily_rows)} daily rows - not enough history.")
            skipped.append((name, f"only {len(daily_rows)} daily rows"))
            continue

        adx_flags = sideways_adx(daily_rows)
        narrow_flags = sideways_narrow_cpr(daily_rows)
        adx_pct, adx_n = overall_pct(adx_flags)
        narrow_pct, narrow_n = overall_pct(narrow_flags)
        print(f"  {len(daily_rows)} daily rows - ADX-sideways {adx_pct}% (N={adx_n}), "
              f"narrow-CPR-sideways {narrow_pct}% (N={narrow_n})")

        results.append({
            "name": name, "kind": kind,
            "adx_pct": adx_pct, "adx_n": adx_n, "monthly_adx": monthly_breakdown(daily_rows, adx_flags),
            "narrow_pct": narrow_pct, "narrow_n": narrow_n, "monthly_narrow": monthly_breakdown(daily_rows, narrow_flags),
        })

    # ---------------------------------------------------- OVERALL SUMMARY --
    print("\n" + "=" * 100)
    print("OVERALL 3-YEAR SIDEWAYS%% - ALL SEGMENTS (sorted by ADX-sideways%% descending)")
    print("=" * 100)
    print(f"{'segment':<14} {'kind':<10} {'ADX<20 %':>10} {'N':>6} {'narrowCPR %':>13} {'N':>6}")
    print("-" * 100)
    for r in sorted(results, key=lambda x: -(x["adx_pct"] or 0)):
        print(f"{r['name']:<14} {r['kind']:<10} {r['adx_pct']:>9}%  {r['adx_n']:>6} "
              f"{r['narrow_pct']:>12}%  {r['narrow_n']:>6}")

    # ------------------------------------------- INDEX MONTH-BY-MONTH DETAIL
    print("\n" + "=" * 100)
    print("MONTH-BY-MONTH DETAIL - NIFTY / BANKNIFTY / SENSEX (the segments you trade options on)")
    print("=" * 100)
    for r in results:
        if r["kind"] != "INDEX":
            continue
        print(f"\n--- {r['name']} ---")
        months = sorted(set(r["monthly_adx"]) | set(r["monthly_narrow"]))
        print(f"{'month':<10} {'ADX<20 sideways':>18} {'narrowCPR sideways':>20}")
        for ym in months:
            a = r["monthly_adx"].get(ym, {"sideways": 0, "total": 0})
            b = r["monthly_narrow"].get(ym, {"sideways": 0, "total": 0})
            a_str = f"{a['sideways']}/{a['total']}" if a["total"] else "-"
            b_str = f"{b['sideways']}/{b['total']}" if b["total"] else "-"
            print(f"{ym:<10} {a_str:>18} {b_str:>20}")

    # ------------------------------------------------------------- CSV DUMP
    with open(CSV_PATH, "w") as f:
        f.write("segment,kind,month,method,sideways_days,total_days,sideways_pct\n")
        for r in results:
            for method, monthly in (("ADX<20", r["monthly_adx"]), ("narrowCPR", r["monthly_narrow"])):
                for ym, e in sorted(monthly.items()):
                    pct = round(100.0 * e["sideways"] / e["total"], 1) if e["total"] else ""
                    f.write(f"{r['name']},{r['kind']},{ym},{method},{e['sideways']},{e['total']},{pct}\n")

    print(f"\nFull month-by-month detail for ALL {len(results)} segments x both methods written to:")
    print(f"  {CSV_PATH}")
    print("(the terminal above only showed the overall summary + the 3 index segments in full detail -")
    print(" ask if you want any specific stock's or commodity's month-by-month pulled up separately)")

    if skipped:
        print(f"\nSKIPPED {len(skipped)} segment(s) (did not crash the run):")
        for name, reason in skipped:
            print(f"  {name}: {reason}")

    print("\nDone. READ-ONLY run complete - nothing was modified, no orders placed.")
    print("CAVEAT: both methods classify each day using only PAST bars (no lookahead), but they are")
    print("descriptive regime labels, not a proven trading edge by themselves - same discipline as")
    print("every other scout report in this project.")


if __name__ == "__main__":
    main()
