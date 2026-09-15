#!/usr/bin/env python3
# ============================================================================
# SCOUT42 - "T55 ON ALL STOCKS": SUPERTREND 1-HOUR (2.0 / 7), LONG-ONLY,
#           ACROSS THE NIFTY 50 EQUITY UNIVERSE                    (READ-ONLY)
#
# WHY THIS SCRIPT EXISTS (his 2026-09-15 request "t55 madhiri all stocks.la
# build pannanum"): T55 (LIVE, real money, index OPTIONS) trades a classic
# Supertrend colour flip on the 1-HOUR chart - factor 2.0, ATR period 7 -
# as a stop-and-reverse CE/PE system. A bearish flip there just buys a PUT,
# never a real short sale.
#
# Individual STOCK equity is different: Indian cash-market delivery CANNOT
# be sold short overnight, so on a stock a bearish flip can only mean "exit
# the long". That is a forced, real market-structure change to the traded
# logic - so, exactly like scout32 did for T78, T55's edge on NIFTY points is
# NOT inherited by assumption; it is re-tested here from scratch, stock by
# stock, with the same three hold policies scout32 used (so results are
# directly comparable to the UT Bot equity findings that produced the
# Equity UT Bot paper tracker):
#   A1_LONG_OPEN     - long on a bullish flip, exit only on a bearish flip
#                      (multi-day swing, never forced closed)
#   A2_LONG_INTRADAY - forced exit at every day's own last 1-hour bar
#   A4_LONG_BTST     - hold at most ONE night, forced exit at the NEXT day's
#                      last bar if no bearish flip has fired by then
#
# ENGINE: Supertrend exactly as TradingView's built-in ta.supertrend()
# computes it (the same thing T55's Pine alert fires from) - Wilder/RMA ATR
# seeded with an SMA, hl2 +/- factor*ATR bands, the band "ratchet"
# (lowerBand only rises while price stays above it, upperBand only falls
# while price stays below it), and the direction test against the CURRENT
# bar's ratcheted band. This is deliberately NOT the looser SupertrendState
# from scout21_one_good_ball.py (that one compared against the PREVIOUS
# bar's band) - a faithful T55 test needs the Pine-faithful engine, and the
# verify script checks it bar-by-bar against a hand-worked reference.
#
# DATA: Angel's native ONE_HOUR candles (09:15, 10:15 ... 14:15, 15:15 - the
# last one is the 15-minute stub, same as TradingView shows on NSE), ~2 years
# back, cached per stock under /data so a re-run is cheap. Real rupee P&L at
# a fixed Rs20,000 notional per trade (qty = floor(20000/entry)), fills at
# the signal bar's close - same convention as scout32. STT/brokerage/DP
# charges and slippage are NOT modelled - flagged, not hidden.
#
# SPLIT / VERDICT: 70/30 chronological by trading day. ROBUST only if
# train_avg>0 AND test_avg>0 AND test_avg >= 0.3 x train_avg; LOW-N flag
# when either side has fewer than 15 trades. A ROBUST tag is a candidate for
# a PAPER tracker (the same path T78 -> Equity UT Bot tracker took), never a
# live-ready proof by itself.
#
# TOKEN RESOLUTION: never hardcoded - each name is looked up fresh against
# Angel's published scrip master (same resolve_token() as scout31/scout32).
# One bad name is SKIPPED with a printed reason, never crashes the other 49.
#
# Run on the Render Shell after 15:30 IST (needs the live Angel session):
#   curl -sL https://raw.githubusercontent.com/balarkt-ai/dataya-scout-reports/main/scout42_st1h_equity_longonly.py -o scout42_st1h_equity_longonly.py
#   sha256sum scout42_st1h_equity_longonly.py
#   python3 scout42_st1h_equity_longonly.py
# Optional env: SCOUT42_DAYS_BACK=1100  SCOUT42_ALLOW_MARKET_HOURS=1
#               SCOUT42_ONLY=RELIANCE,TCS   (quick run on a few names)
# ============================================================================
import os
import sys
import csv
import json
import time
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# ----------------------------------------------------------------- CONFIG ---
NIFTY50_NAMES = [   # same list as scout31/scout32 (sourced 2026-09-13) - kept identical on purpose
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

ST_FACTOR = 2.0             # T55: Supertrend factor 2.0
ST_PERIOD = 7               # T55: ATR period 7
INTERVAL = "ONE_HOUR"       # T55 is a 1-HOUR chart technical
DAYS_BACK = int(os.environ.get("SCOUT42_DAYS_BACK", "730"))   # ~2yr, same window as scout32
CHUNK_DAYS = 300            # Angel allows up to 400 days per ONE_HOUR request
CAPITAL_PER_TRADE = 20000   # fixed notional per trade, rupees (GFS / scout32 convention)
MIN_DAYS = 200              # skip a name with less history than this
LOW_N = 15                  # LOW-N flag threshold (project standard)
TRAIN_FRAC = 0.7
CACHE_DIR = "/data/scout42_st1h_cache"
RESULTS_CSV = "/data/scout42_results.csv"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_MASTER_CACHE = os.path.join(CACHE_DIR, "scrip_master.json")
SCRIP_MASTER_MAX_AGE_H = 24

POLICIES = (("A1_LONG_OPEN", "open"), ("A2_LONG_INTRADAY", "intraday"), ("A4_LONG_BTST", "btst"))


def market_hours_guard():
    now = datetime.datetime.now(IST)
    if os.environ.get("SCOUT42_ALLOW_MARKET_HOURS") == "1":
        return now
    if now.weekday() < 5 and (9 <= now.hour < 16):
        print("SAFETY STOP: market hours - run this ONLY after 15:30 IST "
              "(or set SCOUT42_ALLOW_MARKET_HOURS=1). Exiting.")
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


def resolve_token(master, name):
    """NSE equity token for `name` via symbol == '{name}-EQ'. Raises
    ValueError (never a silent guess) if not found or ambiguous."""
    target = f"{name}-EQ".upper()
    matches = [
        row for row in master
        if row.get("exch_seg") == "NSE" and str(row.get("symbol", "")).upper() == target
    ]
    if not matches:
        raise ValueError(f"{name}: no NSE row with symbol '{target}' in scrip master - "
                          f"possibly renamed/delisted/demerged. Skipping, not guessing.")
    if len(matches) > 1:
        raise ValueError(f"{name}: {len(matches)} ambiguous matches for '{target}' - skipping, not guessing.")
    return matches[0]["token"]


# ------------------------------------------------------------ DATA FETCH ---
def _fetch_chunk(api, token, from_dt, to_dt):
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": INTERVAL,
        "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
        "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
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


def get_stock_1h_days(api, name, token, days_back=DAYS_BACK, chunk_days=CHUNK_DAYS):
    """Returns dict: date -> sorted list of (dt, o, h, l, c). Cached per stock."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{name}_1h_{days_back}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            raw = json.load(f)
        print(f"  (using cache: {len(raw)} rows)")
    else:
        now = datetime.datetime.now()
        end = now.replace(hour=15, minute=30, second=0, microsecond=0)
        start = end - datetime.timedelta(days=days_back)
        raw = []
        cur = start
        while cur < end:
            nxt = min(cur + datetime.timedelta(days=chunk_days), end)
            rows = _fetch_chunk(api, token, cur, nxt)
            raw.extend(rows)
            print(f"    chunk {cur.date()} -> {nxt.date()}: {len(rows)} rows")
            time.sleep(0.5)
            cur = nxt
        with open(cache_path, "w") as f:
            json.dump(raw, f)
    return rows_to_days(raw)


def rows_to_days(raw):
    seen = set()
    days = {}
    for row in raw:
        ts, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
        if ts in seen:
            continue
        seen.add(ts)
        dt = datetime.datetime.fromisoformat(ts)
        days.setdefault(dt.date(), []).append((dt, float(o), float(h), float(l), float(c)))
    for d in days:
        days[d].sort(key=lambda x: x[0])
    return days


# ------------------------------------------------- SUPERTREND (PINE-FAITHFUL)
def true_range(h, l, pc):
    if pc is None:
        return h - l
    return max(h - l, abs(h - pc), abs(l - pc))


class SupertrendPine:
    """TradingView ta.supertrend(factor, atrPeriod), bar by bar.

    step(h, l, c) returns trend: +1 bullish (price above the lower band,
    Pine direction -1 / green), -1 bearish (Pine direction +1 / red), or
    None while the ATR is still warming up. A change of trend is the flip
    T55 trades on.

    Pine reference (built-in):
        atr = ta.atr(atrPeriod)                      # RMA of TR, SMA-seeded
        upperBand = hl2 + factor*atr ; lowerBand = hl2 - factor*atr
        lowerBand := lowerBand > prevLowerBand or close[1] < prevLowerBand ? lowerBand : prevLowerBand
        upperBand := upperBand < prevUpperBand or close[1] > prevUpperBand ? upperBand : prevUpperBand
        if na(atr[1])                      direction := 1
        else if prevSuperTrend == prevUpperBand  direction := close > upperBand ? -1 : 1
        else                                     direction := close < lowerBand ? 1 : -1
        superTrend := direction == -1 ? lowerBand : upperBand
    """

    def __init__(self, factor=ST_FACTOR, period=ST_PERIOD):
        self.factor = float(factor)
        self.period = int(period)
        self.rma = None
        self.seed = []
        self.prev_close = None
        self.prev_lower = None
        self.prev_upper = None
        self.prev_st = None
        self.direction = None      # Pine sign convention (-1 up, +1 down)
        self.lower = None
        self.upper = None

    def step(self, h, l, c):
        tr = true_range(h, l, self.prev_close)
        if self.rma is None:
            self.seed.append(tr)
            if len(self.seed) >= self.period:
                self.rma = sum(self.seed) / self.period
        else:
            self.rma = (self.rma * (self.period - 1) + tr) / self.period
        pc = self.prev_close
        self.prev_close = c
        if self.rma is None:
            return None

        hl2 = (h + l) / 2.0
        upper = hl2 + self.factor * self.rma
        lower = hl2 - self.factor * self.rma
        if self.prev_lower is not None:
            if not (lower > self.prev_lower or pc < self.prev_lower):
                lower = self.prev_lower
            if not (upper < self.prev_upper or pc > self.prev_upper):
                upper = self.prev_upper

        if self.prev_st is None:                 # na(atr[1]) -> first warmed bar
            d = 1
        elif self.prev_st == self.prev_upper:    # was in downtrend
            d = -1 if c > upper else 1
        else:                                    # was in uptrend
            d = 1 if c < lower else -1
        st = lower if d == -1 else upper

        self.prev_lower, self.prev_upper, self.prev_st = lower, upper, st
        self.lower, self.upper, self.direction = lower, upper, d
        return -d      # +1 bullish, -1 bearish


# --------------------------------------------------- LONG-ONLY BACKTEST ----
def run_variant_longonly(bars, mode, capital=CAPITAL_PER_TRADE, factor=ST_FACTOR, period=ST_PERIOD):
    """bars: (dt,o,h,l,c,day,is_last_bar_of_day) chronological, ONE stock.
    mode: 'open' (A1) | 'intraday' (A2) | 'btst' (A4). A bearish flip only
    closes an existing long - it never opens a short (real equity delivery
    constraint). Returns list of (entry_date, pnl_rupees, hold_bars, qty)."""
    st = SupertrendPine(factor, period)
    trades = []
    side = None
    entry = e_i = e_d = qty = None
    prev_trend = None

    for i, (dt, o, h, l, c, day, last) in enumerate(bars):
        trend = st.step(h, l, c)
        flip_up = prev_trend == -1 and trend == 1
        flip_dn = prev_trend == 1 and trend == -1
        if trend is not None:
            prev_trend = trend

        if flip_up and side is None:
            entry, e_i, e_d = c, i, day
            qty = max(1, int(capital // entry))
            side = 'LONG'
        elif flip_dn and side == 'LONG':
            trades.append((e_d, (c - entry) * qty, i - e_i, qty))
            side = None

        if last and side == 'LONG':
            if mode == 'intraday':
                trades.append((e_d, (c - entry) * qty, i - e_i, qty))
                side = None
            elif mode == 'btst' and day > e_d:
                trades.append((e_d, (c - entry) * qty, i - e_i, qty))
                side = None
            # mode == 'open': no forced exit

    if side == 'LONG':
        last_c = bars[-1][4]
        trades.append((e_d, (last_c - entry) * qty, len(bars) - 1 - e_i, qty))
    return trades


def split_days(day_keys, frac=TRAIN_FRAC):
    s = int(len(day_keys) * frac)
    return day_keys[:s], day_keys[s:]


def verdict_of(tr, te, tr_avg, te_avg):
    if not tr or not te:
        return "UNEVALUABLE"
    v = "ROBUST" if (tr_avg > 0 and te_avg > 0 and te_avg >= 0.3 * tr_avg) else "NOT ROBUST"
    if len(tr) < LOW_N or len(te) < LOW_N:
        v += " (LOW-N)"
    return v


def days_to_bars(days_dict):
    days_sorted = sorted(days_dict.keys())
    bars = []
    for day in days_sorted:
        db = days_dict[day]
        for idx, (dt, o, h, l, c) in enumerate(db):
            bars.append((dt, o, h, l, c, day, idx == len(db) - 1))
    return days_sorted, bars


def run_one(name, days_dict):
    days_sorted, bars = days_to_bars(days_dict)
    train_days, test_days = split_days(days_sorted)
    train_set, test_set = set(train_days), set(test_days)

    rows_out = []
    for label, mode in POLICIES:
        trades = run_variant_longonly(bars, mode)
        tr = [p for d, p, hb, q in trades if d in train_set]
        te = [p for d, p, hb, q in trades if d in test_set]
        tra = sum(tr) / len(tr) if tr else 0.0
        tea = sum(te) / len(te) if te else 0.0
        wins = sum(1 for d, p, hb, q in trades if p > 0)
        hold = sum(hb for d, p, hb, q in trades) / len(trades) if trades else 0.0
        rows_out.append({
            "stock": name, "policy": label,
            "train_n": len(tr), "train_avg": tra, "train_sum": sum(tr),
            "test_n": len(te), "test_avg": tea, "test_sum": sum(te),
            "trades": len(trades), "win_rate": (wins / len(trades)) if trades else 0.0,
            "avg_hold_bars": hold,
            "verdict": verdict_of(tr, te, tra, tea),
        })
    return rows_out


# ------------------------------------------------------------------ MAIN ---
def main():
    market_hours_guard()
    only = os.environ.get("SCOUT42_ONLY")
    names = [n.strip().upper() for n in only.split(",") if n.strip()] if only else NIFTY50_NAMES

    api = get_api()
    print(f"SCOUT42 - Supertrend 1-HOUR ({ST_FACTOR}/{ST_PERIOD}) LONG-ONLY equity, NIFTY 50 - "
          f"{datetime.datetime.now(IST).isoformat()}")
    print(f"Testing {len(names)} names, {DAYS_BACK} days back, {INTERVAL} bars, "
          f"capital/trade = Rs.{CAPITAL_PER_TRADE:,}\n")
    print("REMINDER: a bearish flip only EXITS a long here - never opens a short (real")
    print("equity delivery constraint). T55's options edge is NOT inherited - re-tested here.\n")

    master = _load_scrip_master()

    all_results, skipped = [], []
    for idx, name in enumerate(names, 1):
        print(f"[{idx}/{len(names)}] {name} ...")
        try:
            token = resolve_token(master, name)
        except ValueError as e:
            print(f"  SKIPPED (token): {e}")
            skipped.append((name, str(e)))
            continue
        try:
            days_dict = get_stock_1h_days(api, name, token)
        except Exception as e:
            print(f"  SKIPPED (data fetch error): {e}")
            skipped.append((name, f"data fetch error: {e}"))
            continue
        if len(days_dict) < MIN_DAYS:
            print(f"  SKIPPED: only {len(days_dict)} days of 1-hour data - not enough history.")
            skipped.append((name, f"only {len(days_dict)} days"))
            continue
        n_bars = sum(len(v) for v in days_dict.values())
        print(f"  {len(days_dict)} days / {n_bars} one-hour bars")
        all_results.extend(run_one(name, days_dict))

    print("\n" + "=" * 118)
    print("SCOUT42 RESULTS - Supertrend 1H (2.0/7) long-only, every hold policy, every evaluated NIFTY 50 stock")
    print("=" * 118)
    print(f"{'stock':<12} {'policy':<18} {'trN':>4} {'train_avg_Rs':>13} {'teN':>4} {'test_avg_Rs':>12} "
          f"{'win%':>5} {'hold':>6}  verdict")
    print("-" * 118)
    robust = []
    for r in all_results:
        print(f"{r['stock']:<12} {r['policy']:<18} {r['train_n']:>4} {r['train_avg']:>13.2f} "
              f"{r['test_n']:>4} {r['test_avg']:>12.2f} {100 * r['win_rate']:>5.1f} "
              f"{r['avg_hold_bars']:>6.1f}  {r['verdict']}")
        if r["verdict"].startswith("ROBUST"):
            robust.append(r)

    # Pooled "all stocks as one portfolio" view per policy - answers the
    # question actually asked ("does T55 work on ALL stocks?") without
    # cherry-picking single names.
    print("\n" + "-" * 118)
    print("POOLED (all evaluated stocks together, per policy)")
    print(f"{'policy':<18} {'trN':>5} {'train_avg_Rs':>13} {'train_sum_Rs':>14} {'teN':>5} {'test_avg_Rs':>12} "
          f"{'test_sum_Rs':>13}  verdict")
    for label, _mode in POLICIES:
        rows = [r for r in all_results if r["policy"] == label]
        trn = sum(r["train_n"] for r in rows)
        ten = sum(r["test_n"] for r in rows)
        trs = sum(r["train_sum"] for r in rows)
        tes = sum(r["test_sum"] for r in rows)
        tra = trs / trn if trn else 0.0
        tea = tes / ten if ten else 0.0
        v = verdict_of([1] * trn, [1] * ten, tra, tea) if (trn and ten) else "UNEVALUABLE"
        print(f"{label:<18} {trn:>5} {tra:>13.2f} {trs:>14.0f} {ten:>5} {tea:>12.2f} {tes:>13.0f}  {v}")

    evaluated = len(names) - len(skipped)
    print(f"\nSUMMARY: {len(robust)}/{len(all_results)} combos ROBUST across {evaluated} evaluated stocks "
          f"({sum(1 for r in robust if 'LOW-N' not in r['verdict'])} of them with N>={LOW_N} on both sides).")
    for r in sorted(robust, key=lambda x: -(x["train_avg"] + x["test_avg"])):
        print(f"  {r['stock']} / {r['policy']} -> train Rs.{r['train_avg']:+.2f} (N={r['train_n']}), "
              f"test Rs.{r['test_avg']:+.2f} (N={r['test_n']}), win {100 * r['win_rate']:.0f}%, "
              f"hold {r['avg_hold_bars']:.1f} bars  [{r['verdict']}]")

    if skipped:
        print(f"\nSKIPPED {len(skipped)} name(s) (did not crash the run):")
        for name, reason in skipped:
            print(f"  {name}: {reason}")

    try:
        os.makedirs(os.path.dirname(RESULTS_CSV), exist_ok=True)
        with open(RESULTS_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()) if all_results else ["stock"])
            w.writeheader()
            for r in all_results:
                w.writerow(r)
        print(f"\nResults CSV: {RESULTS_CSV}")
    except Exception as e:
        print(f"\n(could not write results CSV: {e})")

    print("\nDone. READ-ONLY run complete - nothing was modified, no orders placed.")
    print("Caveats not modelled: STT/brokerage/DP charges, slippage beyond signal-bar close")
    print("fill. ROBUST here = candidate for a PAPER tracker (same path as T78 -> Equity UT")
    print("Bot tracker), never a live-ready proof by itself.")


if __name__ == "__main__":
    main()
