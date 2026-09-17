#!/usr/bin/env python3
# ============================================================================
# SCOUT55 - REAL BACKTEST: real Rupee P&L for the user's own proposed bracket
# on the 369 ladder - "2% stop loss vachi 50% target vachi build pannunga,
# uptrend and downtrend swing idhula clear-ah irundhale podhum" (2026-09-17).
# ------------------------------------------------------------------------
# WHY THIS SCOUT EXISTS (same day as scout51/52/53/54):
# scout53 found that a plain line-cross reaches the FULL next line only
# 10%-41% of the time before reversing (all 18 rows CONSISTENT between
# train/test - a clean, reliable finding). The user then proposed HALVING
# the target (50% of the distance to the next line instead of the full
# distance). scout54 measured that hit-rate directly: 5 of 6 index/
# timeframe combos stayed BELOW 50% even at half-distance, but ONE combo -
# NIFTY 5-MIN - came back at 64.1% (train 63.8%, test 64.6%, very
# consistent) - the first stable >50% finding across all of today's tests.
#
# scout54 only measured HIT-RATE (does price touch the half-target before
# reversing through the ORIGINAL crossed line) - it did NOT measure real
# money P&L, and its "failure" definition (reversing through the original
# line) is NOT the same as an actual stop-loss. The user has now asked for
# the real thing: build the actual bracket trade - enter on the SAME plain
# line-cross event scout53/54 tested (both CE=uptrend-swing/long-points and
# PE=downtrend-swing/short-points, exactly as he asked), 50% of the
# distance to the next line as the TARGET, and the project's own fixed 2%
# floor (TECHNICAL79_FIXED_SL_PERCENT, re-verified against live app.py) as
# the STOP LOSS - whichever is hit first, or the ladder's own weekly reset,
# closes the trade. This is a genuinely NEW exit combination (a plain
# fixed-fraction target instead of the strong/normal profit tiers used in
# every earlier scout today) - fresh real-data verification before
# anything gets built into Pine/paper/live, same discipline as always.
#
# IMPORTANT SCALE NOTE (flagged honestly, not hidden): the 2% SL is a WIDE
# stop relative to the ladder's own rung spacing - e.g. for NIFTY
# (base_unit=30), the very first rung is only 30 points away, while 2% of
# a ~25000 NIFTY level is ~500 points. So the 2% floor will rarely be the
# thing that actually exits a trade; most non-target trades will instead
# ride out to the ladder's own weekly reset. This is reported honestly via
# the exit-mix breakdown (TARGET / HARD_SL / WEEK_ROLLOVER / END_OF_DATA)
# for every row, exactly like every other scout today.
#
# ENTRY (same plain cross as scout53/54, NOT the v4 confirm-candle rule -
# this stays consistent with the exact test that found the 64% NIFTY
# 5-MIN number, so the two results are directly comparable): a CE cross =
# price closes above ladder line idx having been at/below it the bar
# before (outer-most crossed line wins on a multi-line jump, same
# convention throughout this project). No confirm wait, no entries-per-
# line cap here (scout53/54 counted every real cross with no cap either -
# keeping that unchanged keeps this backtest measuring the exact same
# universe of trades). One open position per direction (CE/PE) at a time;
# a new cross while already in that direction is skipped until the current
# trade closes.
#
# EXIT: TARGET = orig_line + 0.5*(next_line - orig_line) in the crossed
# direction, OR HARD_SL = entry_price*(1-2%) for CE / entry_price*(1+2%)
# for PE, whichever the bar's high/low reaches first (SL checked before
# target within the same bar as the conservative assumption when a bar's
# range could touch both - same discipline used in every prior scout when
# order-within-bar is unknown), OR the ladder's own weekly reset (flattens
# at that bar's own close, matching v4's convention), OR end of data.
# Gap-through fills use the bar's own OPEN (SCOUT22c fill-realism rule,
# reapplied here as in every other scout).
#
# READ-ONLY. No orders placed.
# ============================================================================
import os
import sys
import time
import json
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CACHE_DIR = "/tmp/scout55_cache"
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
    "NIFTY":     {"exchange": "NSE", "token": "99926000", "base_unit": 30.0},
    "BANKNIFTY": {"exchange": "NSE", "token": "99926009", "base_unit": 300.0},
    "SENSEX":    {"exchange": "BSE", "token": "99919000", "base_unit": 300.0},
}

MULT = [1.0, 2.0, 3.0, 4.0, 6.0, 9.0, 10.0, 12.0, 15.0]
NM = len(MULT)
NL = 2 * NM + 1
BASE_IDX = NM
INCLUDE_BASE = True

TARGET_FRACTION = 0.5   # user's ask: 50% of the distance to the next line
SL_PERCENT = 0.02       # TECHNICAL79_FIXED_SL_PERCENT, re-verified against live app.py (2026-09-17, same day)

TIMEFRAMES = [
    ("1-MIN", "ONE_MINUTE", 400, 25),
    ("5-MIN", "FIVE_MINUTE", 700, 60),
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


def week_key(dt):
    y, w, _ = dt.isocalendar()
    return (y, w)


def build_weekly_bases(bars):
    last_close_of_week = {}
    for dt, o, h, l, c in bars:
        last_close_of_week[week_key(dt)] = c
    weeks_sorted = sorted(last_close_of_week.keys())
    base_for_week = {}
    for i in range(1, len(weeks_sorted)):
        base_for_week[weeks_sorted[i]] = last_close_of_week[weeks_sorted[i - 1]]
    return base_for_week


def build_ladder(base_price, base_unit):
    levels = [None] * NL
    for i in range(NM):
        levels[BASE_IDX + 1 + i] = base_price + base_unit * MULT[i]
        levels[BASE_IDX - 1 - i] = base_price - base_unit * MULT[i]
    levels[BASE_IDX] = base_price
    return levels


# ------------------------------------------------------------ BACKTEST -----
class Instance:
    def __init__(self, is_ce):
        self.is_ce = is_ce
        self.in_position = False
        self.entry_price = None
        self.entry_i = None
        self.entry_day = None
        self.target = None
        self.hard_sl = None


def run_variant(bars, base_unit, target_fraction, sl_pct):
    base_for_week = build_weekly_bases(bars)
    trades = []  # (entry_day, pts, hold_bars, exit_reason, option_type)

    ce = Instance(is_ce=True)
    pe = Instance(is_ce=False)

    cur_week = None
    levels = None

    def close_position(inst, i, fill, reason, cur_i):
        pts = (fill - inst.entry_price) if inst.is_ce else (inst.entry_price - fill)
        opt = "CE" if inst.is_ce else "PE"
        trades.append((inst.entry_day, pts, cur_i - inst.entry_i, reason, opt))
        inst.in_position = False
        inst.entry_price = None
        inst.entry_i = None
        inst.entry_day = None
        inst.target = None
        inst.hard_sl = None

    for i, (dt, o, h, l, c) in enumerate(bars):
        prev_c = bars[i - 1][4] if i > 0 else None
        wk = week_key(dt)
        day = dt.date()

        if wk != cur_week:
            cur_week = wk
            base_price = base_for_week.get(wk)
            levels = build_ladder(base_price, base_unit) if base_price is not None else None
            for inst in (ce, pe):
                if inst.in_position:
                    close_position(inst, i, c, "WEEK_ROLLOVER", i)

        if levels is None or prev_c is None:
            continue

        # ---- 1) EXITS first (2% SL checked before target - conservative
        # assumption when a bar's range could reach both) ----
        for inst in (ce, pe):
            if not inst.in_position:
                continue
            if inst.is_ce:
                if l <= inst.hard_sl:
                    fill = o if o <= inst.hard_sl else inst.hard_sl
                    close_position(inst, i, fill, "HARD_SL", i)
                elif h >= inst.target:
                    fill = o if o >= inst.target else inst.target
                    close_position(inst, i, fill, "TARGET", i)
            else:
                if h >= inst.hard_sl:
                    fill = o if o >= inst.hard_sl else inst.hard_sl
                    close_position(inst, i, fill, "HARD_SL", i)
                elif l <= inst.target:
                    fill = o if o <= inst.target else inst.target
                    close_position(inst, i, fill, "TARGET", i)

        # ---- 2) CROSS detection (outer line wins), same convention as scout51-54 ----
        ce_cross_idx = None
        for idx in range(NL - 1, -1, -1):
            if not INCLUDE_BASE and idx == BASE_IDX:
                continue
            lvl = levels[idx]
            if c > lvl and prev_c <= lvl:
                ce_cross_idx = idx
                break
        pe_cross_idx = None
        for idx in range(0, NL):
            if not INCLUDE_BASE and idx == BASE_IDX:
                continue
            lvl = levels[idx]
            if c < lvl and prev_c >= lvl:
                pe_cross_idx = idx
                break

        # ---- 3) ENTRY - plain cross, no confirm wait, no per-line cap
        # (matches scout53/54's exact test universe) ----
        if not ce.in_position and ce_cross_idx is not None and ce_cross_idx < NL - 1:
            orig_line = levels[ce_cross_idx]
            next_line = levels[ce_cross_idx + 1]
            ce.in_position = True
            ce.entry_price = c
            ce.entry_i = i
            ce.entry_day = day
            ce.target = orig_line + target_fraction * (next_line - orig_line)
            ce.hard_sl = c * (1 - sl_pct)

        if not pe.in_position and pe_cross_idx is not None and pe_cross_idx > 0:
            orig_line = levels[pe_cross_idx]
            next_line = levels[pe_cross_idx - 1]
            pe.in_position = True
            pe.entry_price = c
            pe.entry_i = i
            pe.entry_day = day
            pe.target = orig_line - target_fraction * (orig_line - next_line)
            pe.hard_sl = c * (1 + sl_pct)

    if bars:
        last_i = len(bars) - 1
        last_c = bars[-1][4]
        for inst in (ce, pe):
            if inst.in_position:
                close_position(inst, last_i, last_c, "END_OF_DATA", last_i)

    return trades


def split_weeks(trades, frac=0.7):
    weeks_sorted = sorted({week_key(datetime.datetime.combine(t[0], datetime.time())) for t in trades})
    s = int(len(weeks_sorted) * frac)
    return set(weeks_sorted[:s]), set(weeks_sorted[s:])


def verdict_of(tr, te, tr_avg, te_avg):
    if not tr or not te:
        return "UNEVALUABLE"
    if tr_avg > 0 and te_avg > 0 and te_avg >= 0.3 * tr_avg:
        return "ROBUST"
    return "NOT ROBUST"


def run_one(name, tf_label, bars, lotsize, base_unit):
    trades = run_variant(bars, base_unit, TARGET_FRACTION, SL_PERCENT)
    if not trades:
        return {"index": name, "tf": tf_label, "train_n": 0, "train_avg": 0.0, "test_n": 0,
                "test_avg": 0.0, "trades_per_week": 0.0, "avg_hold_bars": 0.0, "verdict": "UNEVALUABLE",
                "rupee_note": "no trades fired", "exit_mix": "n/a"}

    def wk_of(d):
        return week_key(datetime.datetime.combine(d, datetime.time()))

    train_weeks, test_weeks = split_weeks(trades, frac=0.7)
    tr = [t for t in trades if wk_of(t[0]) in train_weeks]
    te = [t for t in trades if wk_of(t[0]) in test_weeks]
    tr_pts = [t[1] for t in tr]
    te_pts = [t[1] for t in te]
    tra = sum(tr_pts) / len(tr_pts) if tr_pts else 0.0
    tea = sum(te_pts) / len(te_pts) if te_pts else 0.0
    hold = sum(t[2] for t in trades) / len(trades)
    all_weeks = train_weeks | test_weeks
    per_week = len(trades) / len(all_weeks) if all_weeks else 0.0
    n_target = sum(1 for t in trades if t[3] == "TARGET")
    n_hardsl = sum(1 for t in trades if t[3] == "HARD_SL")
    n_rollover = sum(1 for t in trades if t[3] == "WEEK_ROLLOVER")
    n_eod = sum(1 for t in trades if t[3] == "END_OF_DATA")
    n_ce = sum(1 for t in trades if t[4] == "CE")
    n_pe = sum(1 for t in trades if t[4] == "PE")
    verdict = verdict_of(tr_pts, te_pts, tra, tea)
    rupee_note = f"{tra * lotsize:+.0f} / {tea * lotsize:+.0f} Rs per trade (train/test, x{lotsize} lot)" if lotsize else "lot size unresolved - points only"

    return {
        "index": name, "tf": tf_label, "train_n": len(tr), "train_avg": tra,
        "test_n": len(te), "test_avg": tea, "trades_per_week": per_week,
        "avg_hold_bars": hold, "verdict": verdict, "rupee_note": rupee_note,
        "exit_mix": f"TARGET={n_target} HARD_SL={n_hardsl} ROLLOVER={n_rollover} EOD={n_eod} (CE={n_ce}/PE={n_pe})",
    }


# ------------------------------------------------------------ SMOKE TEST ---
def run_synthetic_smoke_test():
    import math
    bars = []
    d0 = datetime.datetime(2024, 1, 1, 9, 15)
    i = 0
    for day_offset in range(56):
        day = d0 + datetime.timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for m in range(120):
            center = 25000.0 + 60.0 * day_offset
            price = center + 380.0 * math.sin(i / 18.0) + 40.0 * math.sin(i / 4.0)
            o, h, l, c = price - 3, price + 6, price - 6, price
            dt = day.replace(hour=9, minute=15) + datetime.timedelta(minutes=m)
            bars.append((dt, o, h, l, c))
            i += 1
    trades = run_variant(bars, 30.0, TARGET_FRACTION, SL_PERCENT)
    assert trades, "SMOKE TEST FAILED: zero trades fired"
    reasons = {}
    for t in trades:
        reasons[t[3]] = reasons.get(t[3], 0) + 1
    assert "TARGET" in reasons, "SMOKE TEST FAILED: TARGET exit never fired even once on smooth oscillating data"
    avg = sum(t[1] for t in trades) / len(trades)
    print(f"  smoke test: {len(trades)} trades, avg {avg:+.2f} pts, exit mix {reasons}")
    # sanity: with a target this close (half the first rung) and an SL this
    # far (2% of ~25000 = ~500pts, many times the ladder's own span), HARD_SL
    # should be rare or absent on this bounded synthetic data
    assert reasons.get("HARD_SL", 0) <= reasons.get("TARGET", 0), \
        "SANITY FAILED: HARD_SL fired more than TARGET on data that never approaches a 2% move - check the SL math"
    print("SMOKE TEST OK - 50%-target/2%-SL bracket engine verified.\n")


# ------------------------------------------------------------------ MAIN ---
def main():
    run_synthetic_smoke_test()
    market_hours_guard()
    api = get_api()
    print(f"SCOUT55 - 369 Ladder: plain cross entry + 50% partial target + 2% hard SL (real Rupee P&L) - "
          f"{datetime.datetime.now().isoformat()}")
    print(f"Entry: same plain line-cross scout53/54 measured (no confirm wait, no per-line cap). "
          f"Exit: {TARGET_FRACTION*100:.0f}% of the distance to the next line, OR {SL_PERCENT*100:.0f}% hard SL, "
          f"OR week rollover - whichever first.\n")

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
            print(f"\n--- {name} / {tf_label} (base_unit={inst['base_unit']}) ---")
            bars = get_index_candles(api, inst["exchange"], inst["token"], interval, days_back, chunk_days)
            if len(bars) < 2000:
                print(f"  SKIP: only {len(bars)} bars - not enough for a meaningful multi-week sample.")
                continue
            days_sorted = sorted({dt.date() for dt, o, h, l, c in bars})
            print(f"  {len(bars)} real bars across {len(days_sorted)} days, {days_sorted[0]} -> {days_sorted[-1]}")
            all_results.append(run_one(name, tf_label, bars, lotsizes.get(name), inst["base_unit"]))

    print("\n" + "=" * 130)
    print(f"SCOUT55 SUMMARY - Plain cross entry + {TARGET_FRACTION*100:.0f}% partial target + {SL_PERCENT*100:.0f}% hard SL")
    print("=" * 130)
    print(f"{'index':<12} {'tf':<8} {'trN':>4} {'train_avg_pts':>13} {'teN':>4} {'test_avg_pts':>12} "
          f"{'tr/wk':>6} {'hold':>6}  verdict     exit_mix")
    print("-" * 130)
    robust = []
    for r in all_results:
        print(f"{r['index']:<12} {r['tf']:<8} {r['train_n']:>4} {r['train_avg']:>13.2f} "
              f"{r['test_n']:>4} {r['test_avg']:>12.2f} {r['trades_per_week']:>6.2f} "
              f"{r['avg_hold_bars']:>6.1f}  {r['verdict']:<10}  {r['exit_mix']}   [{r['rupee_note']}]")
        if r["verdict"] == "ROBUST":
            robust.append(r)

    print(f"\nSUMMARY: {len(robust)}/{len(all_results)} combos ROBUST.")
    for r in sorted(robust, key=lambda x: -(x["train_avg"] + x["test_avg"])):
        print(f"  {r['index']} / {r['tf']} -> train {r['train_avg']:+.2f} pts (N={r['train_n']}), "
              f"test {r['test_avg']:+.2f} pts (N={r['test_n']}), {r['trades_per_week']:.2f} trades/week, "
              f"hold {r['avg_hold_bars']:.1f} bars, exit mix {r['exit_mix']}, {r['rupee_note']}")

    print("\nDone. READ-ONLY run complete - nothing modified, no orders placed.")
    print("REMINDER: this is real points-proxy P&L (index points via CE=Long/PE=Short convention), not real option")
    print("premium/theta/slippage. Watch the exit_mix column - if ROLLOVER dominates over TARGET/HARD_SL, most of")
    print("the P&L is coming from wherever price happened to be at the week's own close, not from the bracket you asked for.")


if __name__ == "__main__":
    main()
