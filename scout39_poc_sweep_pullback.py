#!/usr/bin/env python3
# ============================================================================
# SCOUT39 - POC / LIQUIDITY-SWEEP / PULLBACK STRATEGY BACKTEST (INDEX, TPO-POC)
#
# WHY THIS EXISTS: user shared a chart-study video whose method is:
#   strong trend -> consolidation box -> volume profile inside the box ->
#   Point of Control (POC = the price where the most trading happened) ->
#   a "manipulation" dip below the box (sweeps stops resting under the range)
#   -> breakout above the box in the higher-timeframe direction -> DO NOT
#   chase; wait for price to PULL BACK into the POC -> enter on that reaction
#   for the continuation. Mirror image for bearish setups.
#
# DATA REALITY (verified 2026-09-14 on the user's Render Shell): Angel returns
# volume = 0 for every INDEX SPOT candle (NIFTY/BANKNIFTY/SENSEX, 5-min and
# daily) - the index is a calculated number, not a traded instrument. So a
# true volume profile on the spot series is impossible. This script therefore
# uses the TPO / "time-at-price" POC (Market Profile style): inside the
# consolidation box, the price bin covered by the MOST candles is the POC.
# That is an APPROXIMATION of the video's volume POC. Per the user's decision,
# a second build on index FUTURES real volume comes later for comparison.
#
# TWO MODES, SAME ENGINE:
#   INTRADAY   - 5-min bars, box/sweep/breakout/pullback/trade must all
#                resolve inside one trading day; open trade force-closed at
#                the day's last bar.
#   POSITIONAL - daily bars, box forms over W days, setup plays out over
#                days/weeks, max hold HOLD_MAX days.
#
# MECHANICAL RULES (no discretion, no lookahead - every decision at bar i
# uses only bars <= i):
#   BOX      : rolling window of W bars whose range (max high - min low) is
#              <= COMPRESS x the median range of the trailing MED_N windows
#              (same "relative compression" idea as the narrow-CPR gate
#              already validated in this project). Box = [min low, max high].
#              POC = centre of the price bin (width ATR/10) touched by the
#              most bars in the window.
#   SWEEP    : (bullish) a bar trades BELOW box low, and within SWEEP_MAX
#              bars a bar CLOSES back inside (>= box low). sweep_low = lowest
#              low during the excursion. A close below box_low - 1 ATR is a
#              real breakdown -> setup cancelled ("no chase" discipline).
#   BREAKOUT : (bullish) within BREAK_MAX bars after the sweep, a bar CLOSES
#              above box high. A close below sweep_low first -> cancelled.
#   PULLBACK : (bullish) within PULL_MAX bars after the breakout, a bar's
#              low touches the POC -> ENTRY at the POC (limit-fill
#              assumption; if the bar OPENS below the POC we take the open).
#              No pullback in time -> no trade (the video's "don't chase").
#   STOP     : sweep_low (the manipulation low - if that goes, the idea is
#              wrong). R = entry - stop.
#   TARGET   : variant "2R" = entry + 2R ; variant "MM" (measured move) =
#              box high + box height. Stop checked BEFORE target on the same
#              bar (conservative).
#   BIAS     : variant ON = only bullish setups when daily EMA20 > EMA50 (as
#              of the PRIOR completed daily bar), only bearish when EMA20 <
#              EMA50 - the video's "overall sentiment". Variant OFF = the
#              first excursion out of the box decides the direction.
#   Bearish setups are the exact mirror (sweep above, break below, pull up
#   to POC, stop at sweep_high, targets mirrored).
#
# GRID (kept small on purpose - multiple-comparison discipline):
#   INTRADAY : W in {12, 18} bars x target {2R, MM} x bias {ON, OFF} = 8
#   POSITIONAL: W in {10, 15} days x target {2R, MM} x bias {ON, OFF} = 8
# per index, 3 indices -> 48 combos total. 70/30 chronological split BY DAY,
# ROBUST verdict = same rule as scout_common (train avg > 0, test avg > 0,
# test avg >= 0.3 x train avg) plus a LOW-N flag when test N < 15.
#
# P&L is in underlying INDEX POINTS (same convention as every other scout
# here) - not option premium. This is a research report, not a live signal.
# READ-ONLY: fetches historical candles only, never places an order.
# ============================================================================
import os
import sys
import json
import time
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# ------------------------------------------------------------------ CONFIG --
INDEX_INSTRUMENTS = {   # reused verbatim from app.py / scout29 / scout37
    "NIFTY":     {"exchange": "NSE", "token": "99926000"},
    "BANKNIFTY": {"exchange": "NSE", "token": "99926009"},
    "SENSEX":    {"exchange": "BSE", "token": "99919000"},
}
DAYS_BACK_5M = 730          # ~2 years of 5-min bars
CHUNK_5M = 60               # Angel 5-min history: keep chunks small
DAYS_BACK_1D = 1100         # ~3 years daily (scout37 already cached this)
CHUNK_1D = 700
CACHE_DIR = "/data/scout39_cache"
SCOUT37_DAILY_CACHE = "/data/scout_sideways_cache"   # reuse if present
CSV_PATH = "/data/scout39_trades.csv"

ATR_PERIOD = 14
COMPRESS = 0.6              # box range <= 0.6 x trailing median window range
MED_N = 100                 # trailing windows used for that median
POC_BIN_DIV = 10            # bin width = ATR / 10
SWEEP_MAX = 6               # bars allowed to reclaim the box after the sweep

INTRADAY = {
    "W": [12, 18],          # 1h / 1.5h boxes on 5-min bars
    "BOX_WAIT": 24,         # bars to wait for a sweep after the box locks
    "BREAK_MAX": 12,        # bars after sweep for the breakout
    "PULL_MAX": 24,         # bars after breakout for the POC pullback
    "LAST_LOCK_BARS": 15,   # never lock a box in the last 15 bars of a day
}
POSITIONAL = {
    "W": [10, 15],          # 10 / 15 day boxes on daily bars
    "BOX_WAIT": 15,
    "BREAK_MAX": 10,
    "PULL_MAX": 15,
    "HOLD_MAX": 15,         # days; then exit at close
}
TARGETS = ["2R", "MM"]
BIASES = ["ON", "OFF"]
TRAIN_FRAC = 0.7


# -------------------------------------------------------------------- AUTH --
def get_api():
    from SmartApi import SmartConnect
    import pyotp
    api = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
    session = api.generateSession(os.environ["ANGEL_CLIENT_CODE"], os.environ["ANGEL_MPIN"], totp)
    if not session or not session.get("status"):
        raise RuntimeError(f"Angel login failed: {session}")
    return api


def market_hours_now():
    now = datetime.datetime.now(IST)
    return now.weekday() < 5 and (9 <= now.hour < 16)


# -------------------------------------------------------------------- DATA --
def _fetch_chunk(api, exchange, token, interval, from_dt, to_dt):
    params = {"exchange": exchange, "symboltoken": token, "interval": interval,
              "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"), "todate": to_dt.strftime("%Y-%m-%d %H:%M")}
    for attempt in range(4):
        try:
            resp = api.getCandleData(params)
            if not resp or not resp.get("status") or resp.get("data") is None:
                return []
            return resp["data"]
        except Exception as e:
            wait = 2 * (2 ** attempt)      # 2, 4, 8, 16 s - Angel rate limiter backoff
            print(f"    retry {attempt + 1} in {wait}s: {str(e)[:90]}")
            time.sleep(wait)
    return []


def _raw_rows(api, exchange, token, interval, days_back, chunk_days):
    now = datetime.datetime.now()
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    start = end - datetime.timedelta(days=days_back)
    raw = {}
    cur = start
    while cur < end:
        nxt = min(cur + datetime.timedelta(days=chunk_days), end)
        rows = _fetch_chunk(api, exchange, token, interval, cur, nxt)
        for c in rows:
            raw[c[0]] = c
        print(f"    {interval} {cur.date()} -> {nxt.date()}: {len(rows)} bars")
        time.sleep(0.8)
        cur = nxt
    return [raw[k] for k in sorted(raw.keys())]


def load_5min(api_maker, name, inst):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{name}_5min.json")
    if os.path.exists(path):
        with open(path) as f:
            rows = json.load(f)
        print(f"  5-min cache hit: {len(rows)} bars")
    else:
        rows = _raw_rows(api_maker(), inst["exchange"], inst["token"], "FIVE_MINUTE", DAYS_BACK_5M, CHUNK_5M)
        with open(path, "w") as f:
            json.dump(rows, f)
    bars = []
    for c in rows:
        dt = datetime.datetime.fromisoformat(c[0]).replace(tzinfo=None)
        bars.append({"dt": dt, "date": dt.date().isoformat(), "o": float(c[1]), "h": float(c[2]),
                     "l": float(c[3]), "c": float(c[4])})
    bars.sort(key=lambda b: b["dt"])
    return bars


def load_daily(api_maker, name, inst):
    # reuse scout37's daily cache when present (identical source/token)
    p37 = os.path.join(SCOUT37_DAILY_CACHE, f"{name}_daily.json")
    if os.path.exists(p37):
        with open(p37) as f:
            rows = json.load(f)
        print(f"  daily cache hit (scout37): {len(rows)} bars")
        bars = [{"dt": datetime.datetime.fromisoformat(r["date"]), "date": r["date"], "o": r["open"],
                 "h": r["high"], "l": r["low"], "c": r["close"]} for r in rows]
    else:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, f"{name}_daily.json")
        if os.path.exists(path):
            with open(path) as f:
                rows = json.load(f)
        else:
            rows = _raw_rows(api_maker(), inst["exchange"], inst["token"], "ONE_DAY", DAYS_BACK_1D, CHUNK_1D)
            with open(path, "w") as f:
                json.dump(rows, f)
        bars = []
        for c in rows:
            ts = c[0][:10]
            bars.append({"dt": datetime.datetime.fromisoformat(ts), "date": ts, "o": float(c[1]),
                         "h": float(c[2]), "l": float(c[3]), "c": float(c[4])})
    bars.sort(key=lambda b: b["dt"])
    return bars


# --------------------------------------------------------------- INDICATORS --
def wilder_atr(bars, period=ATR_PERIOD):
    """ATR aligned to bars; None until enough history. Uses only past bars."""
    n = len(bars)
    out = [None] * n
    trs = []
    atr = None
    for i in range(n):
        if i == 0:
            tr = bars[i]["h"] - bars[i]["l"]
        else:
            pc = bars[i - 1]["c"]
            tr = max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - pc), abs(bars[i]["l"] - pc))
        if atr is None:
            trs.append(tr)
            if len(trs) == period:
                atr = sum(trs) / period
                out[i] = atr
        else:
            atr = (atr * (period - 1) + tr) / period
            out[i] = atr
    return out


def ema_series(values, period):
    out = [None] * len(values)
    k = 2.0 / (period + 1)
    e = None
    for i, v in enumerate(values):
        if e is None:
            if i + 1 >= period:
                e = sum(values[i - period + 1:i + 1]) / period
                out[i] = e
        else:
            e = v * k + e * (1 - k)
            out[i] = e
    return out


def daily_bias_map(daily_bars):
    """date -> 'up'/'down'/None using EMA20 vs EMA50 of the PRIOR completed
    daily bar (so intraday bars of day D only see bars < D)."""
    closes = [b["c"] for b in daily_bars]
    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)
    out = {}
    for i in range(1, len(daily_bars)):
        a, b = e20[i - 1], e50[i - 1]
        out[daily_bars[i]["date"]] = None if (a is None or b is None) else ("up" if a > b else "down")
    return out


def median(xs):
    s = sorted(xs)
    m = len(s)
    return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2


def tpo_poc(bars, lo, hi, atr):
    """Time-at-price POC: bin width = ATR/POC_BIN_DIV; count bars covering each
    bin; POC = centre of the most-covered bin (tie -> nearest box midpoint)."""
    width = max(atr / POC_BIN_DIV, 0.05)
    nb = max(1, int((hi - lo) / width) + 1)
    counts = [0] * nb
    for b in bars:
        i0 = max(0, int((b["l"] - lo) / width))
        i1 = min(nb - 1, int((b["h"] - lo) / width))
        for k in range(i0, i1 + 1):
            counts[k] += 1
    mid = (lo + hi) / 2
    best = max(range(nb), key=lambda k: (counts[k], -abs(lo + (k + 0.5) * width - mid)))
    return lo + (best + 0.5) * width


# ------------------------------------------------------------------ ENGINE --
def run_engine(bars, W, target_kind, bias_on, bias_map, intraday, p):
    """Walks bars once with a state machine. Returns (trades, funnel).
    trades: dicts with date/dir/entry/stop/target/exit/exit_reason/pts/R.
    funnel: counts of boxes, sweeps, breakouts, entries, cancels per stage."""
    n = len(bars)
    atr = wilder_atr(bars)
    # per-bar window range (None if the window would cross a day in intraday mode)
    win_range = [None] * n
    for i in range(W - 1, n):
        if intraday and bars[i - W + 1]["date"] != bars[i]["date"]:
            continue
        hi = max(b["h"] for b in bars[i - W + 1:i + 1])
        lo = min(b["l"] for b in bars[i - W + 1:i + 1])
        win_range[i] = hi - lo
    # day end (intraday): by CLOCK (last 5-min slot starts 15:25) so no
    # data-derived lookahead; data-driven fallback for early-close days.
    session_end = datetime.time(15, 30)
    def bars_left_in_day(i):
        t = bars[i]["dt"]
        end = datetime.datetime.combine(t.date(), session_end)
        return max(0, int((end - t).total_seconds() // 300) - 1)
    is_last = [(i == n - 1) or bars[i + 1]["date"] != bars[i]["date"] for i in range(n)]

    funnel = {"boxes": 0, "sweeps": 0, "breakouts": 0, "entries": 0,
              "cancel_no_sweep": 0, "cancel_breakdown": 0, "cancel_no_break": 0,
              "cancel_no_pullback": 0, "cancel_bias_mismatch": 0}
    trades = []
    state = "IDLE"
    st = {}
    next_lock_ok = 0
    hist_ranges = []

    def cancel(reason):
        nonlocal state, st
        funnel[reason] += 1
        state = "IDLE"
        st = {}

    for i in range(n):
        b = bars[i]
        day_end = intraday and (b["dt"].time() >= datetime.time(15, 25) or is_last[i])

        # ---------- manage open trade first ----------
        if state == "IN_TRADE":
            t = st["trade"]
            exit_px = exit_reason = None
            if t["dir"] == "long":
                if b["l"] <= t["stop"]:
                    exit_px, exit_reason = t["stop"], "STOP"
                elif b["h"] >= t["target"]:
                    exit_px, exit_reason = t["target"], "TARGET"
            else:
                if b["h"] >= t["stop"]:
                    exit_px, exit_reason = t["stop"], "STOP"
                elif b["l"] <= t["target"]:
                    exit_px, exit_reason = t["target"], "TARGET"
            if exit_px is None:
                if intraday and day_end:
                    exit_px, exit_reason = b["c"], "EOD"
                elif (not intraday) and (i - t["entry_i"]) >= p["HOLD_MAX"]:
                    exit_px, exit_reason = b["c"], "TIME"
            if exit_px is not None:
                pts = (exit_px - t["entry"]) if t["dir"] == "long" else (t["entry"] - exit_px)
                t.update({"exit": exit_px, "exit_reason": exit_reason, "exit_date": b["date"],
                          "pts": round(pts, 2), "R": round(pts / t["risk"], 2)})
                trades.append(t)
                state = "IDLE"
                st = {}
            # a bar that closes a trade cannot also lock a new box - move on
            if state == "IN_TRADE" or exit_px is not None:
                if intraday and day_end and state != "IDLE":
                    state = "IDLE"; st = {}
                continue

        # ---------- intraday: everything resets at the day boundary ----------
        if intraday and state != "IDLE" and st.get("date") != b["date"]:
            cancel({"BOX": "cancel_no_sweep", "SWEPT": "cancel_no_break",
                    "BROKE": "cancel_no_pullback"}[state])

        if state == "BOX":
            bl, bh, a = st["box_lo"], st["box_hi"], st["atr"]
            if st.get("sweeping") is None:
                below, above = b["l"] < bl, b["h"] > bh
                if not below and not above:
                    if i - st["lock_i"] > p["BOX_WAIT"]:
                        cancel("cancel_no_sweep")
                    continue
                # first excursion decides (or must match the bias)
                d = "long" if (below and not above) else ("short" if (above and not below) else None)
                if d is None:                      # both sides in one bar - messy, skip
                    cancel("cancel_breakdown"); continue
                if bias_on:
                    need = {"up": "long", "down": "short"}.get(st["bias"])
                    if need != d:
                        cancel("cancel_bias_mismatch"); continue
                st["dir"] = d
                st["sweeping"] = i
                st["ext"] = b["l"] if d == "long" else b["h"]
            # in an excursion: track extreme, look for reclaim / breakdown
            d = st["dir"]
            if d == "long":
                st["ext"] = min(st["ext"], b["l"])
                if b["c"] < bl - a:
                    cancel("cancel_breakdown"); continue
                if b["c"] >= bl:
                    st["sweep_x"] = st["ext"]; st["swept_i"] = i
                    state = "SWEPT"; funnel["sweeps"] += 1
                    continue
            else:
                st["ext"] = max(st["ext"], b["h"])
                if b["c"] > bh + a:
                    cancel("cancel_breakdown"); continue
                if b["c"] <= bh:
                    st["sweep_x"] = st["ext"]; st["swept_i"] = i
                    state = "SWEPT"; funnel["sweeps"] += 1
                    continue
            if i - st["sweeping"] >= SWEEP_MAX:
                cancel("cancel_breakdown")
            continue

        if state == "SWEPT":
            d, bl, bh = st["dir"], st["box_lo"], st["box_hi"]
            if d == "long":
                if b["c"] < st["sweep_x"]:
                    cancel("cancel_breakdown"); continue
                if b["c"] > bh:
                    state = "BROKE"; st["broke_i"] = i; funnel["breakouts"] += 1; continue
            else:
                if b["c"] > st["sweep_x"]:
                    cancel("cancel_breakdown"); continue
                if b["c"] < bl:
                    state = "BROKE"; st["broke_i"] = i; funnel["breakouts"] += 1; continue
            if i - st["swept_i"] > p["BREAK_MAX"]:
                cancel("cancel_no_break")
            continue

        if state == "BROKE":
            d, poc, sx = st["dir"], st["poc"], st["sweep_x"]
            bl, bh = st["box_lo"], st["box_hi"]
            touched = (b["l"] <= poc) if d == "long" else (b["h"] >= poc)
            if touched:
                entry = (min(poc, b["o"]) if d == "long" else max(poc, b["o"]))
                risk = (entry - sx) if d == "long" else (sx - entry)
                if risk <= 0:
                    cancel("cancel_breakdown"); continue
                height = bh - bl
                if target_kind == "2R":
                    target = entry + 2 * risk if d == "long" else entry - 2 * risk
                else:  # measured move
                    target = bh + height if d == "long" else bl - height
                t = {"date": b["date"], "dir": d, "entry": round(entry, 2), "stop": round(sx, 2),
                     "target": round(target, 2), "risk": risk, "poc": round(poc, 2),
                     "box_lo": round(bl, 2), "box_hi": round(bh, 2), "entry_i": i,
                     "entry_time": b["dt"].strftime("%H:%M") if intraday else ""}
                funnel["entries"] += 1
                # same-bar stop check (conservative): stop before target
                hit_stop = (b["l"] <= sx) if d == "long" else (b["h"] >= sx)
                if hit_stop:
                    pts = (sx - entry) if d == "long" else (entry - sx)
                    t.update({"exit": round(sx, 2), "exit_reason": "STOP", "exit_date": b["date"],
                              "pts": round(pts, 2), "R": -1.0})
                    trades.append(t); state = "IDLE"; st = {}
                    continue
                st["trade"] = t
                state = "IN_TRADE"
                if intraday and day_end:   # entered on the last bar - flat at close
                    pts = (b["c"] - entry) if d == "long" else (entry - b["c"])
                    t.update({"exit": b["c"], "exit_reason": "EOD", "exit_date": b["date"],
                              "pts": round(pts, 2), "R": round(pts / risk, 2)})
                    trades.append(t); state = "IDLE"; st = {}
                continue
            # not touched: invalidation / timeout
            if (d == "long" and b["c"] < sx) or (d == "short" and b["c"] > sx):
                cancel("cancel_breakdown"); continue
            if i - st["broke_i"] > p["PULL_MAX"]:
                cancel("cancel_no_pullback")
            continue

        # ---------- IDLE: look for a new compressed box ----------
        wr = win_range[i]
        if wr is not None:
            # trailing median uses ONLY windows that ended before this bar
            if len(hist_ranges) >= 20 and atr[i] is not None and i >= next_lock_ok:
                med = median(hist_ranges[-MED_N:])
                ok_time = True
                if intraday:
                    ok_time = bars_left_in_day(i) >= p["LAST_LOCK_BARS"]
                if ok_time and wr <= COMPRESS * med:
                    win = bars[i - W + 1:i + 1]
                    lo = min(x["l"] for x in win); hi = max(x["h"] for x in win)
                    st = {"box_lo": lo, "box_hi": hi, "atr": atr[i], "lock_i": i, "date": b["date"],
                          "poc": tpo_poc(win, lo, hi, atr[i]), "bias": bias_map.get(b["date"])}
                    if bias_on and st["bias"] is None:
                        st = {}
                    else:
                        state = "BOX"
                        funnel["boxes"] += 1
                        next_lock_ok = i + W
            hist_ranges.append(wr)
    # a setup still pending when the data ends is a cancel, not a trade
    if state in ("BOX", "SWEPT", "BROKE"):
        cancel({"BOX": "cancel_no_sweep", "SWEPT": "cancel_no_break", "BROKE": "cancel_no_pullback"}[state])
    return trades, funnel


# ---------------------------------------------------------------- REPORTING --
def stats(trades):
    if not trades:
        return {"n": 0, "win": 0.0, "avg": 0.0, "total": 0.0, "pf": 0.0}
    wins = [t["pts"] for t in trades if t["pts"] > 0]
    losses = [-t["pts"] for t in trades if t["pts"] <= 0]
    gp, gl = sum(wins), sum(losses)
    return {"n": len(trades), "win": round(100.0 * len(wins) / len(trades), 1),
            "avg": round(sum(t["pts"] for t in trades) / len(trades), 2),
            "total": round(sum(t["pts"] for t in trades), 1),
            "pf": round(gp / gl, 2) if gl > 0 else (9.99 if gp > 0 else 0.0)}


def verdict(tr, te):
    if tr["n"] == 0 or te["n"] == 0:
        return "no-trades"
    v = "ROBUST" if (tr["avg"] > 0 and te["avg"] > 0 and te["avg"] >= 0.3 * tr["avg"]) else "not robust"
    if te["n"] < 15:
        v += " (LOW-N)"
    return v


def split_by_day(bars, frac=TRAIN_FRAC):
    days = sorted(set(b["date"] for b in bars))
    cut = days[int(len(days) * frac)]
    return cut


def main():
    print(f"SCOUT39 - POC / sweep / pullback backtest (TPO-POC on index spot) - {datetime.datetime.now(IST).isoformat()}")
    print("READ-ONLY research report. P&L in index points. TPO-POC is an APPROXIMATION of the video's volume POC\n"
          "(index spot has no volume - verified). Futures-volume POC comparison is a separate later step.\n")

    _api = {"obj": None}

    def api_maker():
        if _api["obj"] is None:
            if market_hours_now():
                print("SAFETY STOP: a data download is needed and it is market hours. Heavy historical\n"
                      "fetches share the live bot's Angel rate limit - run this after 15:30 IST (or on a\n"
                      "weekend) the FIRST time. Once cached, it runs anytime.")
                sys.exit(1)
            _api["obj"] = get_api()
        return _api["obj"]

    all_rows = []
    all_trades = []
    for name, inst in INDEX_INSTRUMENTS.items():
        print(f"=== {name} ===")
        daily = load_daily(api_maker, name, inst)
        m5 = load_5min(api_maker, name, inst)
        bias = daily_bias_map(daily)
        print(f"  daily bars {len(daily)} | 5-min bars {len(m5)} over {len(set(b['date'] for b in m5))} days")

        for mode, bars, P in (("INTRADAY", m5, INTRADAY), ("POSITIONAL", daily, POSITIONAL)):
            if len(bars) < 100:
                print(f"  {mode}: not enough bars - skipped")
                continue
            cut = split_by_day(bars)
            for W in P["W"]:
                for tk in TARGETS:
                    for bs in BIASES:
                        trades, funnel = run_engine(bars, W, tk, bs == "ON", bias, mode == "INTRADAY", P)
                        tr = [t for t in trades if t["date"] < cut]
                        te = [t for t in trades if t["date"] >= cut]
                        s_tr, s_te = stats(tr), stats(te)
                        row = {"index": name, "mode": mode, "W": W, "target": tk, "bias": bs,
                               "funnel": funnel, "train": s_tr, "test": s_te, "verdict": verdict(s_tr, s_te)}
                        all_rows.append(row)
                        for t in trades:
                            all_trades.append({**t, "index": name, "mode": mode, "W": W, "target": tk,
                                               "bias": bs, "split": "train" if t["date"] < cut else "test"})
                        f = funnel
                        print(f"  {mode:<10} W={W:<3} {tk:<2} bias={bs:<3} | boxes {f['boxes']:>4} sweeps {f['sweeps']:>4} "
                              f"breaks {f['breakouts']:>4} entries {f['entries']:>4} | "
                              f"TRAIN n={s_tr['n']:>3} win {s_tr['win']:>5}% avg {s_tr['avg']:>7} pf {s_tr['pf']:>5} | "
                              f"TEST n={s_te['n']:>3} win {s_te['win']:>5}% avg {s_te['avg']:>7} pf {s_te['pf']:>5} | {row['verdict']}")
        print()

    # ------------------------------------------------ summary ------------
    print("=" * 110)
    print("SUMMARY - combos sorted by TEST avg pts (only combos with test trades)")
    print("=" * 110)
    print(f"{'index':<10}{'mode':<11}{'W':>3} {'tgt':<3} {'bias':<4} | {'trainN':>6} {'trAvg':>8} | {'testN':>5} {'teWin%':>6} {'teAvg':>8} {'tePF':>5} | verdict")
    print("-" * 110)
    for r in sorted([x for x in all_rows if x["test"]["n"] > 0], key=lambda x: -x["test"]["avg"]):
        print(f"{r['index']:<10}{r['mode']:<11}{r['W']:>3} {r['target']:<3} {r['bias']:<4} | {r['train']['n']:>6} {r['train']['avg']:>8} | "
              f"{r['test']['n']:>5} {r['test']['win']:>6} {r['test']['avg']:>8} {r['test']['pf']:>5} | {r['verdict']}")
    robust = [r for r in all_rows if r["verdict"].startswith("ROBUST")]
    print(f"\nROBUST combos: {len(robust)} / {len(all_rows)}  "
          f"(of which LOW-N: {sum(1 for r in robust if 'LOW-N' in r['verdict'])})")

    # funnel overview: is the pattern rare?
    print("\nFUNNEL (how often the full video sequence actually completes) - summed over W/target/bias combos:")
    for name in INDEX_INSTRUMENTS:
        for mode in ("INTRADAY", "POSITIONAL"):
            rs = [r for r in all_rows if r["index"] == name and r["mode"] == mode]
            if not rs:
                continue
            f = {k: sum(r["funnel"][k] for r in rs) for k in rs[0]["funnel"]}
            print(f"  {name:<10}{mode:<11} boxes {f['boxes']:>5} -> sweeps {f['sweeps']:>5} -> breakouts {f['breakouts']:>5} "
                  f"-> entries {f['entries']:>5} | cancels: no-sweep {f['cancel_no_sweep']}, breakdown {f['cancel_breakdown']}, "
                  f"no-break {f['cancel_no_break']}, no-pullback {f['cancel_no_pullback']}, bias-mismatch {f['cancel_bias_mismatch']}")

    # trade dump
    try:
        with open(CSV_PATH, "w") as fh:
            fh.write("index,mode,W,target,bias,split,date,entry_time,dir,box_lo,box_hi,poc,entry,stop,target_px,exit,exit_reason,exit_date,pts,R\n")
            for t in all_trades:
                fh.write(f"{t['index']},{t['mode']},{t['W']},{t['target']},{t['bias']},{t['split']},{t['date']},{t['entry_time']},"
                         f"{t['dir']},{t['box_lo']},{t['box_hi']},{t['poc']},{t['entry']},{t['stop']},{t['target']},"
                         f"{t['exit']},{t['exit_reason']},{t['exit_date']},{t['pts']},{t['R']}\n")
        print(f"\nAll {len(all_trades)} simulated trades written to {CSV_PATH}")
    except Exception as e:
        print(f"\n(csv write skipped: {e})")

    print("\nDone. READ-ONLY run complete - nothing modified, no orders placed.")
    print("CAVEATS: (1) TPO-POC, not volume-POC (index spot has no volume). (2) 48 combos were tried - a few\n"
          "'ROBUST' hits can appear by chance; only trust ones with healthy test N and a sensible funnel.\n"
          "(3) Points, not option premium. (4) Limit-fill at POC assumed; real fills can be worse.")


if __name__ == "__main__":
    main()
