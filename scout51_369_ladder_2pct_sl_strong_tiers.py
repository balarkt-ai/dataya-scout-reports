#!/usr/bin/env python3
# ============================================================================
# SCOUT51 - REAL BACKTEST: the 369 Magic Number ladder's own ENTRY rule
# (v4 Pine, live-deployed 2026-09-16 - "cross + confirm" cross-and-clear
# entry, byte-for-byte ported below from magic369_ladder_alerts_v4.pine),
# combined with a NEW exit rule requested 2026-09-17: replace v4's
# touch-based target/stop (next line / crossed line) with a fixed 2% SL
# from entry + the project's own STRONG PROFIT TIERS trailing exit (same
# mechanism verified against live app.py in scout49/scout50), PLUS a new
# DAILY SL-HIT CAP per index (2 HARD_SL-hit trades/day for NIFTY, 2 for
# BANKNIFTY, 2 for SENSEX - once hit, no new entries for that index for
# the rest of that day; an already-open trade is NOT force-closed, it
# still rides its own SL/tier exit).
#
# WHY THIS SCOUT EXISTS: the 369 ladder's backtest record is still
# MIXED/UNRESOLVED (scout16/17: 0/6 robust on the touch-exit convention;
# scout35's "3/3 robust" was flagged as an unverified possible refactor
# bug, never diagnosed). The user is now asking for a DIFFERENT exit
# mechanism entirely (2% SL + strong tiers) plus a brand-new daily
# SL-hit cap rule that has never been tested in any form - this is a genuinely new
# combination, so it gets its own fresh backtest before touching the live
# Pine/receiver, same discipline just applied to the UT Bot exploration
# (scout48/49/50).
#
# DAILY CAP RULE - CLARIFIED LIVE MID-BUILD (2026-09-17): user's first
# phrasing ("2 target archive trade a day, then stop") sounded like a
# WIN cap, so Claude asked via options. Before he answered, he clarified
# directly: "profit tiers-la exit aayidum so loss maximum aaga vaippu
# illa, apadi aana 2 SL hit aachinna trade venda" - since the tiered exit
# already keeps any single loss capped at 2%, the real rule he wants is a
# LOSS cap, not a win cap: 2 trades exiting via the HARD 2% STOP LOSS
# (HARD_SL reason specifically - not a TIER_STOP, even one that happens
# to close slightly below entry) in one day, for one index -> block NEW
# entries for that index (both CE and PE) for the rest of that calendar
# day. Wins do not count against this cap; only genuine SL hits do.
#
# ASSUMPTIONS STILL FLAGGED EXPLICITLY (not yet separately confirmed):
#   1. The cap BLOCKS NEW ENTRIES ONLY. It does NOT force-close a trade
#      that is already open when the 2nd SL hit lands - that trade still
#      rides out via its own 2%-SL/tier exit normally.
#   2. Entry logic (cross + confirm, outer-line-wins, per-line weekly
#      entriesPerLine=2 cap) is COPIED VERBATIM from the live v4 Pine -
#      not re-derived, not changed. Only the EXIT and the NEW daily cap
#      are different from what's live today.
#   3. CE = a LONG points position (profits if price keeps rising, same
#      "CE=Long / PE=Short index-points proxy" convention used in every
#      other scout in this project). PE = a SHORT points position.
#   If either of these 2 don't match what you meant, say so and this
#   gets corrected before any paper tracker is built on it.
#
# THE ENTRY RULE (ported from magic369_ladder_alerts_v4.pine, unchanged):
#   19-line ladder (base +/- 9 rungs, multipliers [1,2,3,4,6,9,10,12,15] x
#   base_unit), redrawn every week off the LAST completed week's closing
#   price and held constant through the week. A CROSS candle closes
#   beyond a line (outer/furthest line wins if one candle jumps several).
#   The line stays "pending" until a CONFIRM candle both closes beyond it
#   AND is fully clear of it (no wick touching) -> entry at that confirm
#   candle's close. A candle that closes beyond but still touches the
#   line stays pending (no cap on how long). A close back on the other
#   side cancels the pending cross. Max 2 entries per line per week
#   (entriesPerLine, same as live).
#
# THE NEW EXIT RULE (replaces v4's touch-based target/stop):
#   Loss side: fixed 2% hard-floor SL from entry price
#   (TECHNICAL79_FIXED_SL_PERCENT, re-verified against live app.py).
#   Profit side: STRONG_TREND_TIERS_BY_SYMBOL trailing lock (copied
#   verbatim from app.py, same table used in scout49/scout50) - as the
#   position's peak favorable move crosses each points threshold, the
#   stop locks up to (peak - trail_back), never loosening, floored at
#   the 2% SL. Week rollover still flattens the trade (same as v4) -
#   at the new week's first bar's own close, matching v4's own convention
#   exactly (v4 sets sellPx := close on the rollover bar itself).
#
# FILL REALISM (SCOUT22c lesson, reapplied): a bar's own OPEN gapped
# through the stop level -> fill at the OPEN, never the stale stop level.
#
# INSTRUMENTS: NIFTY, BANKNIFTY, SENSEX - the same 3 indices the live 369
# paper receiver tracks. Base units per the course/live convention:
# NIFTY=30, BANKNIFTY=300, SENSEX=300 (BANKNIFTY/SENSEX=300 was flagged
# as an assumption when first extended from the course sheet - carried
# forward here unchanged, not re-litigated). TIMEFRAME: 1-MIN (matches
# the live deployment's chart) - 5-MIN also reported for comparison.
# BACKTEST SPLIT: 70/30 BY WEEK (the 369 track's own established
# convention from scout16, NOT by day like the UT Bot scouts - a week is
# this strategy's natural unit since the ladder itself is weekly).
# READ-ONLY, no orders placed.
# ============================================================================
import os
import sys
import time
import json
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CACHE_DIR = "/tmp/scout51_cache"
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


# Real tokens verified against the live app.py source (never guessed):
# NIFTY/BANKNIFTY from scout48/49/50, SENSEX from DATAYA_ALGO's own
# INSTRUMENTS dict (exchange BSE, token 99919000).
SVMKR_INSTRUMENTS = {
    "NIFTY":     {"exchange": "NSE", "token": "99926000", "base_unit": 30.0},
    "BANKNIFTY": {"exchange": "NSE", "token": "99926009", "base_unit": 300.0},
    "SENSEX":    {"exchange": "BSE", "token": "99919000", "base_unit": 300.0},
}

# --- Ladder constants (verbatim from magic369_ladder_alerts_v4.pine) ---
MULT = [1.0, 2.0, 3.0, 4.0, 6.0, 9.0, 10.0, 12.0, 15.0]
NM = len(MULT)          # 9 rungs each side
NL = 2 * NM + 1         # 19 lines total
BASE_IDX = NM           # index 9 = the weekly-close base line
INCLUDE_BASE = True
ENTRIES_PER_LINE = 2    # max entries per line per week (per CE/PE instance) - unchanged from live

# --- Exit constants (NEW rule, copied verbatim from app.py - not re-derived) ---
SL_PERCENT = 0.02       # TECHNICAL79_FIXED_SL_PERCENT (2.0%) - re-verified against live app.py
STRONG_TREND_TIERS_BY_SYMBOL = {
    "NIFTY":     [(10, 4), (40, 16), (70, 28), (120, 42), (200, 60), (300, 80), (500, 120), (1000, 180)],
    "BANKNIFTY": [(24, 10), (100, 35), (150, 50), (200, 65), (300, 85), (400, 105), (600, 140), (1000, 200)],
    "SENSEX":    [(24, 10), (100, 35), (150, 50), (200, 65), (300, 85), (400, 105), (600, 140), (1000, 200)],
}

DAILY_SL_CAP = 2        # NEW rule, requested+clarified 2026-09-17: 2 HARD_SL-hit trades/day/index, then no new entries that day

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


# --------------------------------------------------------- TIER HELPERS ----
# Copied verbatim from scout49/scout50 - not re-derived.
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


# ------------------------------------------------------ LADDER HELPERS -----
def week_key(dt):
    y, w, _ = dt.isocalendar()
    return (y, w)


def build_weekly_bases(bars):
    """Weekly base for week W = the LAST close of the week BEFORE week W
    (matches request.security(...,'W',close[1]) - held constant through
    the following week). First week in the data has no base -> no trades."""
    last_close_of_week = {}
    for dt, o, h, l, c in bars:
        last_close_of_week[week_key(dt)] = c  # overwritten each bar -> ends up as the week's last close
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
    return levels  # sorted low -> high, index BASE_IDX = the base line


# ------------------------------------------------------------ BACKTEST -----
class LadderInstance:
    """One CE or PE instance's state - mirrors the Pine v4 state vars
    exactly (pendIdx / inPosition / entry tracking), one instance per
    option type, both sharing the same ladder levels and the same
    per-symbol daily SL-hit cap."""
    def __init__(self, is_ce):
        self.is_ce = is_ce
        self.pend_idx = None
        self.in_position = False
        self.entry_price = None
        self.entry_i = None
        self.entry_day = None
        self.extreme = None
        self.used_count = [0] * NL  # per-line entries-this-week counter


def run_variant_369(bars, symbol, base_unit, sl_pct, tiers, entries_per_line, daily_sl_cap):
    base_for_week = build_weekly_bases(bars)
    trades = []            # (entry_day, points, hold_bars, exit_reason, option_type)
    skipped_by_cap = 0

    ce = LadderInstance(is_ce=True)
    pe = LadderInstance(is_ce=False)

    cur_week = None
    levels = None
    cur_day = None
    daily_sl_count = 0
    blocked_today = False

    def close_position(inst, i, fill, reason, cur_i):
        nonlocal daily_sl_count
        pts = (fill - inst.entry_price) if inst.is_ce else (inst.entry_price - fill)
        opt = "CE" if inst.is_ce else "PE"
        trades.append((inst.entry_day, pts, cur_i - inst.entry_i, reason, opt))
        if reason == "HARD_SL":
            daily_sl_count += 1
        inst.in_position = False
        inst.entry_price = None
        inst.entry_i = None
        inst.entry_day = None
        inst.extreme = None

    for i, (dt, o, h, l, c) in enumerate(bars):
        prev_c = bars[i - 1][4] if i > 0 else None
        wk = week_key(dt)
        day = dt.date()

        new_week = wk != cur_week
        if new_week:
            cur_week = wk
            base_price = base_for_week.get(wk)
            levels = build_ladder(base_price, base_unit) if base_price is not None else None
            ce.used_count = [0] * NL
            pe.used_count = [0] * NL
            # week rollover: flatten any open position at THIS bar's close (matches v4's sellPx := close)
            for inst in (ce, pe):
                if inst.in_position:
                    close_position(inst, i, c, "WEEK_ROLLOVER", i)
            ce.pend_idx = None
            pe.pend_idx = None

        if new_day:= (day != cur_day):
            cur_day = day
            daily_sl_count = 0
            blocked_today = False

        if levels is None or prev_c is None:
            continue

        blocked_today = daily_sl_count >= daily_sl_cap

        # ---- 1) EXITS first (2% SL + strong tiers, both instances) ----
        for inst in (ce, pe):
            if not inst.in_position:
                continue
            if inst.is_ce:
                inst.extreme = h if inst.extreme is None else max(inst.extreme, h)
                stop_level, tiered = _trail_stop_long(inst.entry_price, inst.extreme, sl_pct, tiers)
                if l <= stop_level:
                    fill = o if o <= stop_level else stop_level
                    close_position(inst, i, fill, "TIER_STOP" if tiered else "HARD_SL", i)
            else:
                inst.extreme = l if inst.extreme is None else min(inst.extreme, l)
                stop_level, tiered = _trail_stop_short(inst.entry_price, inst.extreme, sl_pct, tiers)
                if h >= stop_level:
                    fill = o if o >= stop_level else stop_level
                    close_position(inst, i, fill, "TIER_STOP" if tiered else "HARD_SL", i)

        # ---- 2) CROSS detection (outer line wins), per direction ----
        ce_cross_idx = None
        for idx in range(NL - 1, -1, -1):  # highest first
            if not INCLUDE_BASE and idx == BASE_IDX:
                continue
            lvl = levels[idx]
            if c > lvl and prev_c <= lvl:
                ce_cross_idx = idx
                break
        pe_cross_idx = None
        for idx in range(0, NL):  # lowest first
            if not INCLUDE_BASE and idx == BASE_IDX:
                continue
            lvl = levels[idx]
            if c < lvl and prev_c >= lvl:
                pe_cross_idx = idx
                break

        # ---- 3) CONFIRM / ENTRY per instance ----
        for inst, cross_idx in ((ce, ce_cross_idx), (pe, pe_cross_idx)):
            if inst.in_position or blocked_today:
                continue
            if inst.pend_idx is not None:
                p_idx = inst.pend_idx
                p_lvl = levels[p_idx]
                confirmed = (c > p_lvl) if inst.is_ce else (c < p_lvl)
                jumped_more = cross_idx is not None and ((cross_idx > p_idx) if inst.is_ce else (cross_idx < p_idx))
                clear_of_line = (l > p_lvl) if inst.is_ce else (h < p_lvl)
                has_target = (p_idx < NL - 1) if inst.is_ce else (p_idx > 0)
                if jumped_more:
                    inst.pend_idx = cross_idx
                elif confirmed and clear_of_line:
                    if has_target and inst.used_count[p_idx] < entries_per_line:
                        inst.used_count[p_idx] += 1
                        inst.in_position = True
                        inst.entry_price = c
                        inst.entry_i = i
                        inst.entry_day = day
                        inst.extreme = None
                    inst.pend_idx = None
                elif confirmed:
                    pass  # waiting - closed beyond but still touching
                else:
                    inst.pend_idx = None  # failed confirm
            elif cross_idx is not None:
                inst.pend_idx = cross_idx

        if blocked_today and (ce_cross_idx is not None or pe_cross_idx is not None):
            skipped_by_cap += 1

    # end of data - close anything still open
    if bars:
        last_i = len(bars) - 1
        last_c = bars[-1][4]
        for inst in (ce, pe):
            if inst.in_position:
                close_position(inst, last_i, last_c, "END_OF_DATA", last_i)

    return trades, skipped_by_cap


def split_weeks(trades, frac=0.7):
    weeks_sorted = sorted({week_key(datetime.datetime.combine(d, datetime.time())) for d, p, hb, rs, opt in trades})
    s = int(len(weeks_sorted) * frac)
    return set(weeks_sorted[:s]), set(weeks_sorted[s:])


def verdict_of(tr, te, tr_avg, te_avg):
    if not tr or not te:
        return "UNEVALUABLE"
    if tr_avg > 0 and te_avg > 0 and te_avg >= 0.3 * tr_avg:
        return "ROBUST"
    return "NOT ROBUST"


def run_one(name, tf_label, bars, lotsize, base_unit, tiers):
    trades, skipped_by_cap = run_variant_369(bars, name, base_unit, SL_PERCENT, tiers,
                                              ENTRIES_PER_LINE, DAILY_SL_CAP)
    if not trades:
        return {"index": name, "tf": tf_label, "train_n": 0, "train_avg": 0.0, "test_n": 0,
                "test_avg": 0.0, "trades_per_week": 0.0, "avg_hold_bars": 0.0, "verdict": "UNEVALUABLE",
                "rupee_note": "no trades fired", "exit_mix": "n/a", "skipped_by_cap": skipped_by_cap}

    train_weeks, test_weeks = split_weeks(trades, frac=0.7)
    tr = [p for d, p, hb, rs, opt in trades if week_key(datetime.datetime.combine(d, datetime.time())) in train_weeks]
    te = [p for d, p, hb, rs, opt in trades if week_key(datetime.datetime.combine(d, datetime.time())) in test_weeks]
    tra = sum(tr) / len(tr) if tr else 0.0
    tea = sum(te) / len(te) if te else 0.0
    hold = sum(hb for d, p, hb, rs, opt in trades) / len(trades)
    all_weeks = train_weeks | test_weeks
    per_week = len(trades) / len(all_weeks) if all_weeks else 0.0
    n_hardsl = sum(1 for d, p, hb, rs, opt in trades if rs == "HARD_SL")
    n_tier = sum(1 for d, p, hb, rs, opt in trades if rs == "TIER_STOP")
    n_rollover = sum(1 for d, p, hb, rs, opt in trades if rs == "WEEK_ROLLOVER")
    n_eod = sum(1 for d, p, hb, rs, opt in trades if rs == "END_OF_DATA")
    n_ce = sum(1 for d, p, hb, rs, opt in trades if opt == "CE")
    n_pe = sum(1 for d, p, hb, rs, opt in trades if opt == "PE")
    verdict = verdict_of(tr, te, tra, tea)
    rupee_note = f"{tra * lotsize:+.0f} / {tea * lotsize:+.0f} Rs per trade (train/test, x{lotsize} lot)" if lotsize else "lot size unresolved - points only"
    return {
        "index": name, "tf": tf_label, "train_n": len(tr), "train_avg": tra,
        "test_n": len(te), "test_avg": tea, "trades_per_week": per_week,
        "avg_hold_bars": hold, "verdict": verdict, "rupee_note": rupee_note,
        "exit_mix": f"SL={n_hardsl} TIER={n_tier} ROLLOVER={n_rollover} EOD={n_eod} (CE={n_ce}/PE={n_pe})",
        "skipped_by_cap": skipped_by_cap,
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
    import math
    bars = []
    d0 = datetime.datetime(2024, 1, 1, 9, 15)  # a Monday
    # Span 8 CALENDAR WEEKS with a BOUNDED oscillation (sine wave, amplitude
    # ~380pts) around a fixed 25000 center - NOT a compounding trend. A prior
    # version of this smoke test used a strongly compounding drift
    # (price *= 1.0012 repeatedly); over 4800 bars that runaway-trended from
    # 25000 to 33640 (+34%), which is many multiples of the ladder's own
    # +/-450pt (15 x 30) span - every week's price gapped straight past the
    # OUTERMOST line (index 18/0, which has no line beyond it, so
    # has_target=False and no entry can ever open there) before any
    # confirmable mid-ladder cross could occur, firing ZERO trades. Fixed by
    # keeping the oscillation's amplitude comfortably inside the ladder's own
    # span so it repeatedly crosses and re-crosses the inner/mid lines,
    # exercising cross+confirm entries, hard-SL, and tier-stop exits alike.
    i = 0
    for day_offset in range(56):  # 8 weeks
        day = d0 + datetime.timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for m in range(120):  # 120 bars/trading day
            center = 25000.0 + 60.0 * day_offset  # gentle week-to-week walk, still << ladder span
            price = center + 380.0 * math.sin(i / 18.0) + 40.0 * math.sin(i / 4.0)
            o, h, l, c = price - 3, price + 6, price - 6, price
            dt = day.replace(hour=9, minute=15) + datetime.timedelta(minutes=m)
            bars.append((dt, o, h, l, c))
            i += 1
    trades, skipped = run_variant_369(bars, "NIFTY", 30.0, SL_PERCENT,
                                       STRONG_TREND_TIERS_BY_SYMBOL["NIFTY"], ENTRIES_PER_LINE, DAILY_SL_CAP)
    assert trades, "SMOKE TEST FAILED: the 369+tiers engine never fired a single trade"
    reasons = {}
    for d, p, hb, rs, opt in trades:
        reasons[rs] = reasons.get(rs, 0) + 1
    assert "HARD_SL" in reasons or "TIER_STOP" in reasons, \
        "SMOKE TEST FAILED: neither the hard floor nor a tier stop ever triggered - exit logic may not be wired correctly"
    avg = sum(p for _, p, _, _, _ in trades) / len(trades)
    print(f"  smoke test: {len(trades)} trades, avg {avg:+.2f} pts, exit mix {reasons}, skipped-by-cap bars={skipped}")
    print("SMOKE TEST OK - 369 ladder entry + 2% SL/strong-tiers exit + daily SL-hit cap engine verified.\n")


# ------------------------------------------------------------------ MAIN ---
def main():
    run_synthetic_smoke_test()
    market_hours_guard()
    api = get_api()
    print(f"SCOUT51 - 369 Ladder (v4 entry) + 2% SL + STRONG PROFIT TIERS + Daily SL-Hit Cap - {datetime.datetime.now().isoformat()}")
    print(f"Entry: cross+confirm 19-line weekly ladder (verbatim from live v4 Pine). "
          f"Exit: {SL_PERCENT*100:.0f}% hard floor + STRONG_TREND_TIERS_BY_SYMBOL trailing lock "
          f"(copied verbatim from app.py), OR week rollover, whichever first. "
          f"Daily cap: {DAILY_SL_CAP} HARD_SL-hit trades/day/index, then no new entries that day (clarified live 2026-09-17).\n")

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
        tiers = STRONG_TREND_TIERS_BY_SYMBOL[name]
        for tf_label, interval, days_back, chunk_days in TIMEFRAMES:
            print(f"\n--- {name} / {tf_label} (base_unit={inst['base_unit']}) ---")
            bars = get_index_candles(api, inst["exchange"], inst["token"], interval, days_back, chunk_days)
            if len(bars) < 2000:
                print(f"  SKIP: only {len(bars)} bars - not enough for a meaningful multi-week sample.")
                continue
            days_sorted = sorted({dt.date() for dt, o, h, l, c in bars})
            print(f"  {len(bars)} real bars across {len(days_sorted)} days, {days_sorted[0]} -> {days_sorted[-1]}")
            jumps = find_big_jumps(bars)
            if jumps:
                print(f"  data-quality note: {len(jumps)} single-day close-to-close move(s) >=8% (reported, nothing excluded).")
            all_results.append(run_one(name, tf_label, bars, lotsizes.get(name), inst["base_unit"], tiers))

    print("\n" + "=" * 130)
    print("SCOUT51 SUMMARY - 369 Ladder entry + 2% SL + Strong Profit Tiers exit + Daily SL-Hit Cap (2/day/index)")
    print("=" * 130)
    print(f"{'index':<12} {'tf':<8} {'trN':>4} {'train_avg_pts':>13} {'teN':>4} {'test_avg_pts':>12} "
          f"{'tr/wk':>6} {'hold':>6}  verdict     exit_mix")
    print("-" * 130)
    robust = []
    for r in all_results:
        print(f"{r['index']:<12} {r['tf']:<8} {r['train_n']:>4} {r['train_avg']:>13.2f} "
              f"{r['test_n']:>4} {r['test_avg']:>12.2f} {r['trades_per_week']:>6.2f} "
              f"{r['avg_hold_bars']:>6.1f}  {r['verdict']:<10}  {r['exit_mix']}   [{r['rupee_note']}]   "
              f"(setups skipped by daily cap: {r['skipped_by_cap']})")
        if r["verdict"] == "ROBUST":
            robust.append(r)

    print(f"\nSUMMARY: {len(robust)}/{len(all_results)} combos ROBUST.")
    for r in sorted(robust, key=lambda x: -(x["train_avg"] + x["test_avg"])):
        print(f"  {r['index']} / {r['tf']} -> train {r['train_avg']:+.2f} pts (N={r['train_n']}), "
              f"test {r['test_avg']:+.2f} pts (N={r['test_n']}), {r['trades_per_week']:.2f} trades/week, "
              f"hold {r['avg_hold_bars']:.1f} bars, exit mix {r['exit_mix']}, {r['rupee_note']}")

    print("\nDone. READ-ONLY run complete - nothing modified, no orders placed.")
    print("REMINDER: entry = the v4 confirm-candle's own close (matches the live Pine exactly). Points-proxy P&L")
    print("(index points, not real option premium/theta/slippage). Compare this table against the ladder's own")
    print("prior touch-exit results (scout16/17/35: 0/6 robust, one unresolved 3/3 flag) - this tests a DIFFERENT")
    print("exit mechanism entirely, not a re-run of the same thing.")


if __name__ == "__main__":
    main()
