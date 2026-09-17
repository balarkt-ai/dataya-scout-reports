#!/usr/bin/env python3
# ============================================================================
# SCOUT48 - REAL BACKTEST OF THE GEMINI-GENERATED "UT Bot Strategy with SL,
# TP and Labels" PINE SCRIPT (2026-09-17) - BEFORE building anything, per
# standing discipline ("never trust a vendor/course/AI-generated formula
# blindly - always verify independently against real Angel data first").
#
# A REAL BUG FOUND IN THE PASTED CODE - FLAGGED TO HIM, AND CORRECTED HERE
# (not silently "fixed" - the backtest below tests the CORRECTED, working
# version, since that is clearly the intended strategy; the bug is
# documented so he can also fix it if he keeps using the Pine version):
#
#   if (strategy.position_size > 0)
#       strategy.exit("Exit Long", "Long", stop=long_stop_price, limit=long_take_profit, when=sell)
#   if (strategy.position_size < 0)
#       strategy.exit("Exit Short", "Short", stop=short_stop_price, limit=short_take_profit, when=buy)
#
#   Both exit() calls are gated by `when=sell` / `when=buy` - the OPPOSITE
#   signal that would already be closing/reversing the position anyway. In
#   Pine, an exit() call that never executes (because its `when` is false)
#   never places or maintains a working stop/limit order. Since `sell` is
#   false for the ENTIRE life of a Long position (sell only ever becomes
#   true at the exact bar the position is about to be reversed anyway),
#   the 1% SL / 2% TP declared in the inputs NEVER ACTUALLY ENGAGE while
#   the trade is open - despite the script's own name and UI implying real
#   risk management, this exits ONLY on the opposite UT Bot signal, exactly
#   like the two earlier scripts he shared, with the SL/TP inputs sitting
#   there unused. This backtest tests the CORRECTED logic instead (SL/TP
#   checked every bar from the moment a position opens, so they can
#   actually fire before an opposite signal ever shows up) - because
#   testing the code exactly as pasted would just be re-testing "no real
#   stop-loss" again with extra unused inputs, telling him nothing new.
#
# FORMULA: same UT Bot trailing-stop recursion as every other UT Bot
# variant in this project (verified byte-identical below) - key_val=1.0,
# ATR period=10 (this script's own defaults, matching the "UT WITH STOP
# LINE" video's settings), single instance driving both buy and sell,
# STOP-AND-REVERSE (real Long AND real Short, like the other Gemini/course
# script he shared, not long-only like the very first SVMKR one).
#
# RISK MANAGEMENT TESTED (the CORRECTED, always-live version): 1% stop
# loss, 2% take profit, both computed from the ACTUAL ENTRY PRICE (not
# recomputed every bar off a moving close, which is what a literal line-
# by-line port of the pasted code would ambiguously suggest) - checked
# against every subsequent bar's real high/low. FILL REALISM (the lesson
# from this project's own SCOUT22c bleed-analysis, 2026-09-06 - "any
# what-if SL simulation must model fill realism"): if a single bar's
# high/low range could have hit BOTH the stop and the target, the STOP is
# assumed to hit first (conservative, never lets a lucky same-bar fill
# flatter the result). Exit is whichever of SL / TP / opposite-signal-flip
# happens first, exactly matching a real bracket order's behavior.
#
# INSTRUMENTS/TIMEFRAMES: same as scout47 (NIFTY + BANKNIFTY spot index
# points-proxy, 1-min/5-min/15-min, real depth discovered not assumed) -
# neither pasted script names an instrument or timeframe, so guessing a
# single one would be guessing at his real intent, not backtesting "this
# strategy." Real FUTIDX lot size fetched fresh from Angel's own live
# scrip master (never trusts a hardcoded number, same discipline as every
# other scout).
#
# READ-ONLY. Run on Render Shell (needs live Angel session - this sandbox
# has none), outside market hours per the guard below. Places no order,
# touches no Pine/webhook/app.py.
# ============================================================================
import os
import sys
import time
import json
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CACHE_DIR = "/tmp/scout48_cache"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_MASTER_CACHE = os.path.join(CACHE_DIR, "scrip_master.json")
SCRIP_MASTER_MAX_AGE_H = 24


def market_hours_guard():
    now = datetime.datetime.now(IST)
    if now.weekday() < 5 and (9 <= now.hour < 16):
        print("SAFETY STOP: market hours - run this ONLY after 15:30 IST (or on a weekend). Exiting.")
        sys.exit(1)
    return now


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


SVMKR_INSTRUMENTS = {
    "NIFTY":     {"exchange": "NSE", "token": "99926000"},
    "BANKNIFTY": {"exchange": "NSE", "token": "99926009"},
}

GEMINI_KEY_VAL = 1.0     # this script's own "Key Value (ATR Multiplier)" default
GEMINI_ATR_PERIOD = 10   # this script's own "ATR Period" default
GEMINI_SL_PCT = 0.01     # this script's own "Stop Loss %" default (1.0%)
GEMINI_TP_PCT = 0.02     # this script's own "Take Profit %" default (2.0%)

TIMEFRAMES = [
    ("1-MIN", "ONE_MINUTE", 400, 25),
    ("5-MIN", "FIVE_MINUTE", 700, 60),
    ("15-MIN", "FIFTEEN_MINUTE", 1100, 60),
]


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
    with urllib.request.urlopen(SCRIP_MASTER_URL, timeout=120) as resp:
        data = json.loads(resp.read())
    with open(SCRIP_MASTER_CACHE, "w") as f:
        json.dump(data, f)
    print(f"  (scrip master: downloaded {len(data)} rows)")
    return data


def _expiry_key(r):
    try:
        return datetime.datetime.strptime(r.get("expiry", ""), "%d%b%Y")
    except Exception:
        return datetime.datetime.max


def resolve_fut_lotsize(instruments, name):
    """Real nearest-expiry NFO FUTIDX lotsize - never trusts a hardcoded
    number from anywhere. Returns None (never guesses) if nothing matches."""
    rows = [r for r in instruments if r.get("exch_seg") == "NFO"
            and r.get("instrumenttype") == "FUTIDX" and r.get("expiry")
            and str(r.get("name", "")).upper() == name]
    if not rows:
        print(f"  WARNING: {name}: no FUTIDX contract found in instrument master - cannot confirm a real lot size.")
        return None
    rows.sort(key=_expiry_key)
    nearest = rows[0]
    try:
        lot = int(float(nearest.get("lotsize") or 0))
    except Exception:
        lot = 0
    if lot <= 0:
        print(f"  WARNING: {name}: resolved FUTIDX contract {nearest.get('symbol')} has no usable lotsize ({nearest.get('lotsize')!r}).")
        return None
    print(f"  {name}: real lot size confirmed from {nearest.get('symbol')} (expiry {nearest.get('expiry')}) = {lot}")
    return lot


def _fetch_chunk(api, exchange, token, interval, from_dt, to_dt):
    for attempt in range(3):
        try:
            resp = api.getCandleData({
                "exchange": exchange, "symboltoken": token, "interval": interval,
                "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"), "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
            })
            if not resp or not resp.get("status") or resp.get("data") is None:
                return []
            return resp["data"]
        except Exception as e:
            print(f"    retry {attempt + 1}: {e}")
            time.sleep(2)
    return []


def get_index_candles(api, exchange, token, interval, days_back, chunk_days):
    now = datetime.datetime.now()
    end = now
    start = end - datetime.timedelta(days=days_back)
    raw = []
    cur = start
    empty_streak = 0
    while cur < end:
        nxt = min(cur + datetime.timedelta(days=chunk_days), end)
        rows = _fetch_chunk(api, exchange, token, interval, cur, nxt)
        raw.extend(rows)
        print(f"    chunk {cur.date()} -> {nxt.date()}: {len(rows)} rows")
        empty_streak = empty_streak + 1 if not rows else 0
        time.sleep(0.4)
        cur = nxt
        if empty_streak >= 4:
            print(f"    4 consecutive empty chunks - stopping early, likely reached the real data floor.")
            break
    seen = set()
    bars = []
    for row in raw:
        ts = row[0]
        if ts in seen:
            continue
        seen.add(ts)
        dt = datetime.datetime.fromisoformat(ts).replace(tzinfo=None)
        bars.append((dt, float(row[1]), float(row[2]), float(row[3]), float(row[4])))
    bars.sort(key=lambda x: x[0])
    return bars


# UT Bot ATR trailing-stop engine - byte-identical to every other UT Bot
# variant in this project. Not re-derived.
def true_range(h, l, pc):
    if pc is None:
        return h - l
    return max(h - l, abs(h - pc), abs(l - pc))


class UTBotState:
    def __init__(self, key_val, atr_period):
        self.key_val = key_val
        self.atr_period = atr_period
        self.rma = None
        self.tr_seed = []
        self.stop = None
        self.prev_close = None

    def _update_atr(self, tr):
        if self.rma is None:
            self.tr_seed.append(tr)
            if len(self.tr_seed) < self.atr_period:
                return None
            self.rma = sum(self.tr_seed) / self.atr_period
            return self.rma
        self.rma = (self.rma * (self.atr_period - 1) + tr) / self.atr_period
        return self.rma

    def step(self, h, l, c):
        tr = true_range(h, l, self.prev_close)
        atr = self._update_atr(tr)
        if atr is None:
            self.prev_close = c
            return False, False
        n_loss = self.key_val * atr
        prev_stop = self.stop if self.stop is not None else 0.0
        prev_close = self.prev_close if self.prev_close is not None else c
        iff1 = (c - n_loss) if c > prev_stop else (c + n_loss)
        iff2 = min(prev_stop, c + n_loss) if (c < prev_stop and prev_close < prev_stop) else iff1
        new_stop = max(prev_stop, c - n_loss) if (c > prev_stop and prev_close > prev_stop) else iff2
        above = (prev_close <= prev_stop) and (c > prev_stop)
        below = (prev_close >= prev_stop) and (c < prev_stop)
        buy = (c > new_stop) and above
        sell = (c < new_stop) and below
        self.stop = new_stop
        self.prev_close = c
        return buy, sell


# --------------------------------------------------------------- BACKTEST --
def run_variant_bracket(bars, key_val, atr_period, sl_pct, tp_pct):
    """Stop-and-reverse (real Long AND Short) with a REAL, always-live
    bracket SL/TP from the moment a position opens - the CORRECTED version
    of the pasted script's intent (see header comment for the bug in the
    original). Exit is whichever of SL / TP / opposite-signal-flip happens
    first. FILL REALISM: if one bar's high/low could have hit both the
    stop and the target, the STOP is assumed to hit first (conservative,
    matches this project's own SCOUT22c fill-realism lesson). Returns list
    of (entry_date, points_pnl, hold_bars, exit_reason)."""
    eng = UTBotState(key_val, atr_period)
    trades = []
    side = None   # None | 'LONG' | 'SHORT'
    entry = entry_i = entry_day = None
    sl = tp = None

    for i, (dt, o, h, l, c) in enumerate(bars):
        b, s = eng.step(h, l, c)
        day = dt.date()

        # 1) Check the ALREADY-open position's bracket (opened on a prior
        # bar - never checked against the same bar it was opened on, which
        # would be look-ahead bias since it opens AT that bar's close).
        if side is not None:
            if side == 'LONG':
                hit_sl, hit_tp = (l <= sl), (h >= tp)
                if hit_sl:
                    trades.append((entry_day, sl - entry, i - entry_i, "SL"))
                    side = None
                elif hit_tp:
                    trades.append((entry_day, tp - entry, i - entry_i, "TP"))
                    side = None
            else:  # SHORT
                hit_sl, hit_tp = (h >= sl), (l <= tp)
                if hit_sl:
                    trades.append((entry_day, entry - sl, i - entry_i, "SL"))
                    side = None
                elif hit_tp:
                    trades.append((entry_day, entry - tp, i - entry_i, "TP"))
                    side = None

        # 2) Signal-based flip (only if not already exited via bracket above).
        if b and side != 'LONG':
            if side == 'SHORT':
                trades.append((entry_day, entry - c, i - entry_i, "SIGNAL_FLIP"))
            entry, entry_i, entry_day = c, i, day
            side = 'LONG'
            sl, tp = entry * (1 - sl_pct), entry * (1 + tp_pct)
        elif s and side != 'SHORT':
            if side == 'LONG':
                trades.append((entry_day, c - entry, i - entry_i, "SIGNAL_FLIP"))
            entry, entry_i, entry_day = c, i, day
            side = 'SHORT'
            sl, tp = entry * (1 + sl_pct), entry * (1 - tp_pct)

    if side is not None:
        last_c = bars[-1][4]
        pnl = (last_c - entry) if side == 'LONG' else (entry - last_c)
        trades.append((entry_day, pnl, len(bars) - 1 - entry_i, "END_OF_DATA"))
    return trades


def split_days(day_keys, frac=0.7):
    s = int(len(day_keys) * frac)
    return day_keys[:s], day_keys[s:]


def verdict_of(tr, te, tr_avg, te_avg):
    if not tr or not te:
        return "UNEVALUABLE"
    if tr_avg > 0 and te_avg > 0 and te_avg >= 0.3 * tr_avg:
        return "ROBUST"
    return "NOT ROBUST"


def run_one(name, tf_label, bars, lotsize):
    trades = run_variant_bracket(bars, GEMINI_KEY_VAL, GEMINI_ATR_PERIOD, GEMINI_SL_PCT, GEMINI_TP_PCT)
    days_sorted = sorted({dt.date() for dt, o, h, l, c in bars})
    train_days, test_days = split_days(days_sorted, frac=0.7)
    train_set, test_set = set(train_days), set(test_days)

    tr = [p for d, p, hb, rs in trades if d in train_set]
    te = [p for d, p, hb, rs in trades if d in test_set]
    tra = sum(tr) / len(tr) if tr else 0.0
    tea = sum(te) / len(te) if te else 0.0
    hold = sum(hb for d, p, hb, rs in trades) / len(trades) if trades else 0.0
    per_day = len(trades) / len(days_sorted) if days_sorted else 0.0
    n_sl = sum(1 for d, p, hb, rs in trades if rs == "SL")
    n_tp = sum(1 for d, p, hb, rs in trades if rs == "TP")
    n_flip = sum(1 for d, p, hb, rs in trades if rs == "SIGNAL_FLIP")
    verdict = verdict_of(tr, te, tra, tea)
    rupee_note = f"{tra * lotsize:+.0f} / {tea * lotsize:+.0f} Rs per trade (train/test, x{lotsize} lot)" if lotsize else "lot size unresolved - points only"
    return {
        "index": name, "tf": tf_label, "train_n": len(tr), "train_avg": tra,
        "test_n": len(te), "test_avg": tea, "trades_per_day": per_day,
        "avg_hold_bars": hold, "verdict": verdict, "rupee_note": rupee_note,
        "exit_mix": f"SL={n_sl} TP={n_tp} FLIP={n_flip}",
    }


def find_big_jumps(bars, threshold_pct=8.0):
    days = {}
    for dt, o, h, l, c in bars:
        days[dt.date()] = c
    days_sorted = sorted(days.keys())
    jumps = []
    prev = None
    for d in days_sorted:
        if prev is not None and prev != 0:
            pct = (days[d] - prev) / prev * 100
            if abs(pct) >= threshold_pct:
                jumps.append((d, pct))
        prev = days[d]
    return jumps


# ------------------------------------------------------------ SMOKE TEST ---
def run_synthetic_smoke_test():
    bars = []
    price = 25000.0
    d0 = datetime.datetime(2024, 1, 1, 9, 15)
    for i in range(15):
        price *= 0.995
        bars.append((d0 + datetime.timedelta(minutes=i), price * 1.001, price * 1.003, price * 0.997, price))
    for i in range(15, 4000):
        cyc = i % 60
        if cyc < 35:
            price *= 1.0015
        elif cyc < 45:
            price *= 0.996
        else:
            price *= 1.001
        o, h, l, c = price * 0.999, price * 1.003, price * 0.997, price
        bars.append((d0 + datetime.timedelta(minutes=i), o, h, l, c))
    trades = run_variant_bracket(bars, GEMINI_KEY_VAL, GEMINI_ATR_PERIOD, GEMINI_SL_PCT, GEMINI_TP_PCT)
    assert trades, "SMOKE TEST FAILED: the corrected bracket engine never fired a single trade"
    reasons = {}
    for d, p, hb, rs in trades:
        reasons[rs] = reasons.get(rs, 0) + 1
    assert "SL" in reasons or "TP" in reasons, "SMOKE TEST FAILED: bracket SL/TP never once triggered - the bracket logic may not be wired correctly"
    avg = sum(p for _, p, _, _ in trades) / len(trades)
    print(f"  smoke test: {len(trades)} trades, avg {avg:+.2f} pts, exit mix {reasons}")
    print("SMOKE TEST OK - corrected bracket engine (real SL/TP from entry) verified.\n")


# ------------------------------------------------------------------ MAIN ---
def main():
    run_synthetic_smoke_test()
    market_hours_guard()
    api = get_api()
    print(f"SCOUT48 - Gemini UT Bot SL/TP backtest (CORRECTED bracket logic) - {datetime.datetime.now().isoformat()}")
    print(f"Formula: single UT Bot instance key_val={GEMINI_KEY_VAL}/ATR={GEMINI_ATR_PERIOD}, stop-and-reverse "
          f"(real Long+Short), SL={GEMINI_SL_PCT*100:.1f}% / TP={GEMINI_TP_PCT*100:.1f}% live from entry "
          f"(the pasted script's own SL/TP never actually engage - see header comment - this tests the "
          f"corrected, working version).\n")

    print("Fetching real instrument master for lot-size confirmation...")
    try:
        instruments = _load_scrip_master()
    except Exception as e:
        print(f"  WARNING: could not download instrument master ({e}) - lot sizes will be unresolved, points-only report.")
        instruments = []

    lotsizes = {}
    for name in SVMKR_INSTRUMENTS:
        lotsizes[name] = resolve_fut_lotsize(instruments, name)

    all_results = []
    for name, inst in SVMKR_INSTRUMENTS.items():
        for tf_label, interval, days_back, chunk_days in TIMEFRAMES:
            print(f"\n--- {name} / {tf_label} ---")
            bars = get_index_candles(api, inst["exchange"], inst["token"], interval, days_back, chunk_days)
            if len(bars) < 305:
                print(f"  SKIP: only {len(bars)} bars - not enough for a meaningful ATR({GEMINI_ATR_PERIOD}) sample.")
                continue
            days_sorted = sorted({dt.date() for dt, o, h, l, c in bars})
            print(f"  {len(bars)} real bars across {len(days_sorted)} days, {days_sorted[0]} -> {days_sorted[-1]}")
            jumps = find_big_jumps(bars)
            if jumps:
                print(f"  data-quality note: {len(jumps)} single-day close-to-close move(s) >=8% (reported, nothing excluded).")
            all_results.append(run_one(name, tf_label, bars, lotsizes.get(name)))

    print("\n" + "=" * 122)
    print("SCOUT48 SUMMARY - Gemini UT Bot (key=1/ATR=10, stop-and-reverse, CORRECTED SL/TP live from entry)")
    print("=" * 122)
    print(f"{'index':<12} {'tf':<8} {'trN':>4} {'train_avg_pts':>13} {'teN':>4} {'test_avg_pts':>12} "
          f"{'tr/day':>7} {'hold':>6}  verdict     exit_mix")
    print("-" * 122)
    robust = []
    for r in all_results:
        print(f"{r['index']:<12} {r['tf']:<8} {r['train_n']:>4} {r['train_avg']:>13.2f} "
              f"{r['test_n']:>4} {r['test_avg']:>12.2f} {r['trades_per_day']:>7.2f} "
              f"{r['avg_hold_bars']:>6.1f}  {r['verdict']:<10}  {r['exit_mix']}   [{r['rupee_note']}]")
        if r["verdict"] == "ROBUST":
            robust.append(r)

    print(f"\nSUMMARY: {len(robust)}/{len(all_results)} combos ROBUST.")
    for r in sorted(robust, key=lambda x: -(x["train_avg"] + x["test_avg"])):
        print(f"  {r['index']} / {r['tf']} -> train {r['train_avg']:+.2f} pts (N={r['train_n']}), "
              f"test {r['test_avg']:+.2f} pts (N={r['test_n']}), {r['trades_per_day']:.2f} trades/day, "
              f"hold {r['avg_hold_bars']:.1f} bars, exit mix {r['exit_mix']}, {r['rupee_note']}")

    print("\nDone. READ-ONLY run complete - nothing modified, no orders placed.")
    print("REMINDER: this tests the CORRECTED bracket logic (real SL/TP live from entry), NOT the pasted Pine")
    print("code exactly as written (which has the when=sell/when=buy gating bug documented at the top of this")
    print("file - its SL/TP never actually engage). Points-proxy P&L (spot index, not real futures tick value")
    print("or slippage/charges). A ROBUST tag here is a candidate for a discussion, not a live-ready proof -")
    print("same skepticism as every scout in this project.")


if __name__ == "__main__":
    main()
