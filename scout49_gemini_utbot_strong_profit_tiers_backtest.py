#!/usr/bin/env python3
# ============================================================================
# SCOUT49 - REAL BACKTEST: same UT Bot formula scout48 tested (key_val=1.0,
# ATR period=10, single instance, stop-and-reverse), this time with the
# DATAYA ALGO project's own "STRONG PROFIT TIERS" exit method instead of a
# fixed SL/TP bracket - explicit request 2026-09-17, after scout48's plain
# bracket backtest came back weak on 5-MIN for both NIFTY and BANKNIFTY
# (train AND test both negative, the worst of the 6 combos tested).
#
# WHY THIS SCOUT EXISTS: "strong profit tiers" is a DIFFERENT exit
# mechanism than the fixed 1%/2% SL/TP bracket scout48 tested - it's the
# user's own proven manual method (first automated as T74, later refined
# into T79's "STRONG profit tailing" on 2026-09-10 - see
# STRONG_TREND_TIERS_BY_SYMBOL in app.py). Since the ENTRY signal (this
# exact key=1/ATR=10 UT Bot) was shown to be net-losing on 5-MIN with a
# plain bracket, this scout checks whether the tiered exit changes that
# verdict, BEFORE any paper tracker gets built - never assumed, tested.
#
# THE TIER MECHANISM (copied verbatim from the live production code -
# get_effective_sl_and_tiers / get_trail_back_points_for /
# check_live_tick_for_stop in app.py, not re-derived):
#   - Loss side: a fixed hard-floor SL, same 2% every other technical in
#     this project currently uses (TECHNICAL79_FIXED_SL_PERCENT).
#   - Profit side: STRONG_TREND_TIERS_BY_SYMBOL (the WIDE tier table -
#     matches the user's own "strong profit tiers" naming from
#     2026-09-02, later formalized for T79 on 2026-09-10). As the
#     position's peak favorable excursion crosses each points threshold,
#     the trailing stop locks up to (peak - trail_back), moving in the
#     favorable direction only, floored at the hard SL - never looser.
#   - Exit = whichever of (a) price touching that trailing/floor stop or
#     (b) the opposite UT Bot signal appearing, happens FIRST. This
#     matches the user's own description: "exit strong profit tiers-la
#     aayidum irundhalum, sell indication vara varaikum profit tiers
#     follow pannalam" - the tier stop can end a trade early, but absent
#     that, the position rides all the way to the opposite signal.
#
# ENTRY-TIMING NOTE (addressing the user's request directly): he asked
# for entry "at whatever the market price is the instant the signal
# comes, not the candle's close." A minute-bar HISTORICAL backtest has no
# tick-level price feed to reconstruct that from - the finest real data
# available is each bar's own OHLC, so entry here is (as in every other
# scout in this project) the signal bar's own CLOSE, the closest faithful
# proxy this data supports. True intrabar tick entry can only be honestly
# verified in LIVE/paper trading (via the real WebSocket tick feed, same
# mechanism check_live_tick_for_stop already uses for exits) - flagging
# this plainly rather than inventing an intrabar price this data can't
# support.
#
# FILL REALISM (SCOUT22c lesson, reapplied here): if a bar's own OPEN has
# already gapped through the computed stop level, the fill is the OPEN
# (worse than the stop), never the stop level itself - a real stop order
# cannot fill at a price the market already gapped past.
#
# INSTRUMENTS/TIMEFRAMES: same NIFTY + BANKNIFTY, 1-min/5-min/15-min as
# scout47/48 - the user's specific ask was the 5-MIN chart, but every
# timeframe is reported so the honest comparison against scout48's
# bracket result is visible side by side. READ-ONLY, no orders placed.
# ============================================================================
import os
import sys
import time
import json
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CACHE_DIR = "/tmp/scout49_cache"
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

GEMINI_KEY_VAL = 1.0     # same formula scout48 tested - not re-derived
GEMINI_ATR_PERIOD = 10

# Copied verbatim from app.py - NOT re-derived, so a future divergence in
# the live tier tables can be spotted by re-diffing against the source of
# truth rather than silently drifting.
TIER_SL_PERCENT = 0.02   # TECHNICAL79_FIXED_SL_PERCENT (2.0%) - current global floor
STRONG_TREND_TIERS_BY_SYMBOL = {
    "NIFTY":     [(10, 4), (40, 16), (70, 28), (120, 42), (200, 60), (300, 80), (500, 120), (1000, 180)],
    "BANKNIFTY": [(24, 10), (100, 35), (150, 50), (200, 65), (300, 85), (400, 105), (600, 140), (1000, 200)],
}

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
def _trail_stop_long(entry_price, peak_price, sl_pct, tiers):
    profit_points = round(peak_price - entry_price, 2)
    trail_back = None
    for threshold, tb in tiers:
        if profit_points >= threshold:
            trail_back = tb
    hard_floor = entry_price * (1 - sl_pct)
    if trail_back is None:
        return hard_floor, False
    return max(hard_floor, peak_price - trail_back), True


def _trail_stop_short(entry_price, trough_price, sl_pct, tiers):
    profit_points = round(entry_price - trough_price, 2)
    trail_back = None
    for threshold, tb in tiers:
        if profit_points >= threshold:
            trail_back = tb
    hard_ceiling = entry_price * (1 + sl_pct)
    if trail_back is None:
        return hard_ceiling, False
    return min(hard_ceiling, trough_price + trail_back), True


def run_variant_tiered(bars, key_val, atr_period, sl_pct, tiers):
    """Stop-and-reverse UT Bot with the project's own STRONG PROFIT TIERS
    trailing exit (see header) instead of a fixed SL/TP bracket. Never
    checks a position's own entry bar (no look-ahead - matches scout48's
    bracket logic exactly). Returns list of
    (entry_date, points_pnl, hold_bars, exit_reason) where exit_reason is
    one of "HARD_SL" (2% floor, tier never reached), "TIER_STOP" (a
    reached tier's trailing lock was touched), "SIGNAL_FLIP", or
    "END_OF_DATA"."""
    eng = UTBotState(key_val, atr_period)
    trades = []
    side = None       # None | 'LONG' | 'SHORT'
    entry = entry_i = entry_day = None
    extreme = None    # LONG: running max high since entry; SHORT: running min low since entry

    for i, (dt, o, h, l, c) in enumerate(bars):
        b, s = eng.step(h, l, c)
        day = dt.date()

        if side is not None:
            if side == 'LONG':
                extreme = h if extreme is None else max(extreme, h)
                stop_level, tiered = _trail_stop_long(entry, extreme, sl_pct, tiers)
                if l <= stop_level:
                    fill = o if o <= stop_level else stop_level  # gap-through fill realism
                    reason = "TIER_STOP" if tiered else "HARD_SL"
                    trades.append((entry_day, fill - entry, i - entry_i, reason))
                    side = None
            else:  # SHORT
                extreme = l if extreme is None else min(extreme, l)
                stop_level, tiered = _trail_stop_short(entry, extreme, sl_pct, tiers)
                if h >= stop_level:
                    fill = o if o >= stop_level else stop_level  # gap-through fill realism
                    reason = "TIER_STOP" if tiered else "HARD_SL"
                    trades.append((entry_day, entry - fill, i - entry_i, reason))
                    side = None

        if b and side != 'LONG':
            if side == 'SHORT':
                trades.append((entry_day, entry - c, i - entry_i, "SIGNAL_FLIP"))
            entry, entry_i, entry_day = c, i, day
            side = 'LONG'
            extreme = None
        elif s and side != 'SHORT':
            if side == 'LONG':
                trades.append((entry_day, c - entry, i - entry_i, "SIGNAL_FLIP"))
            entry, entry_i, entry_day = c, i, day
            side = 'SHORT'
            extreme = None

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
    tiers = STRONG_TREND_TIERS_BY_SYMBOL[name]
    trades = run_variant_tiered(bars, GEMINI_KEY_VAL, GEMINI_ATR_PERIOD, TIER_SL_PERCENT, tiers)
    days_sorted = sorted({dt.date() for dt, o, h, l, c in bars})
    train_days, test_days = split_days(days_sorted, frac=0.7)
    train_set, test_set = set(train_days), set(test_days)

    tr = [p for d, p, hb, rs in trades if d in train_set]
    te = [p for d, p, hb, rs in trades if d in test_set]
    tra = sum(tr) / len(tr) if tr else 0.0
    tea = sum(te) / len(te) if te else 0.0
    hold = sum(hb for d, p, hb, rs in trades) / len(trades) if trades else 0.0
    per_day = len(trades) / len(days_sorted) if days_sorted else 0.0
    n_hardsl = sum(1 for d, p, hb, rs in trades if rs == "HARD_SL")
    n_tier = sum(1 for d, p, hb, rs in trades if rs == "TIER_STOP")
    n_flip = sum(1 for d, p, hb, rs in trades if rs == "SIGNAL_FLIP")
    verdict = verdict_of(tr, te, tra, tea)
    rupee_note = f"{tra * lotsize:+.0f} / {tea * lotsize:+.0f} Rs per trade (train/test, x{lotsize} lot)" if lotsize else "lot size unresolved - points only"
    return {
        "index": name, "tf": tf_label, "train_n": len(tr), "train_avg": tra,
        "test_n": len(te), "test_avg": tea, "trades_per_day": per_day,
        "avg_hold_bars": hold, "verdict": verdict, "rupee_note": rupee_note,
        "exit_mix": f"SL={n_hardsl} TIER={n_tier} FLIP={n_flip}",
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
    trades = run_variant_tiered(bars, GEMINI_KEY_VAL, GEMINI_ATR_PERIOD, TIER_SL_PERCENT,
                                 STRONG_TREND_TIERS_BY_SYMBOL["NIFTY"])
    assert trades, "SMOKE TEST FAILED: the tiered engine never fired a single trade"
    reasons = {}
    for d, p, hb, rs in trades:
        reasons[rs] = reasons.get(rs, 0) + 1
    assert "HARD_SL" in reasons or "TIER_STOP" in reasons, \
        "SMOKE TEST FAILED: neither the hard floor nor a tier stop ever triggered once - exit logic may not be wired correctly"
    avg = sum(p for _, p, _, _ in trades) / len(trades)
    print(f"  smoke test: {len(trades)} trades, avg {avg:+.2f} pts, exit mix {reasons}")
    print("SMOKE TEST OK - strong profit tiers engine (trailing lock + opposite-flip) verified.\n")


# ------------------------------------------------------------------ MAIN ---
def main():
    run_synthetic_smoke_test()
    market_hours_guard()
    api = get_api()
    print(f"SCOUT49 - Gemini UT Bot + STRONG PROFIT TIERS backtest - {datetime.datetime.now().isoformat()}")
    print(f"Formula: single UT Bot instance key_val={GEMINI_KEY_VAL}/ATR={GEMINI_ATR_PERIOD}, stop-and-reverse "
          f"(real Long+Short), exit = {TIER_SL_PERCENT*100:.0f}% hard floor + STRONG_TREND_TIERS_BY_SYMBOL trailing "
          f"lock (copied verbatim from app.py) OR the opposite signal, whichever first.\n")

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
    print("SCOUT49 SUMMARY - Gemini UT Bot (key=1/ATR=10) + STRONG PROFIT TIERS exit, vs scout48's plain SL/TP bracket")
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
    print("REMINDER: entry price = signal bar's own close (finest data this historical backtest can support -")
    print("true intrabar tick entry can only be verified in live/paper trading). Points-proxy P&L (spot index,")
    print("not real futures tick value or slippage/charges). Compare this table against scout48's bracket-SL/TP")
    print("table directly - same instruments/timeframes/formula, only the exit method differs.")


if __name__ == "__main__":
    main()
