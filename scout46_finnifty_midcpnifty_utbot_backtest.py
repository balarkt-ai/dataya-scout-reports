#!/usr/bin/env python3
# ============================================================================
# SCOUT46 - UT BOT ON FINNIFTY / MIDCPNIFTY (2026-09-16)
#
# THE REAL BACKTEST, built only after scout45 (spot-token + OPTIDX-contract
# discovery) and scout45b (real 15-min depth confirmation) - never built on
# guessed tokens or a guessed data floor:
#   - FINNIFTY spot token 99926037, MIDCPNIFTY spot token 99926074 - both
#     resolved via a real LTP cross-check against their own nearest-expiry
#     OPTIDX strike ranges (25262.4 fell inside FINNIFTY's 22600-30000
#     range; 14288.45 fell inside MIDCPNIFTY's 12800-16600 range), and both
#     follow the exact same "999-prefix AMXIDX" token family already proven
#     live for NIFTY(99926000)/BANKNIFTY(99926009)/SENSEX(99919000) in this
#     project's own MANUAL_INDEX_LOOKUP.
#   - Both indices are confirmed MONTHLY-ONLY now (SEBI's Nov 2024 rule -
#     NSE kept only NIFTY weekly; this SUPERSEDES an earlier-session memory
#     assumption of "FINNIFTY Tuesday / MIDCPNIFTY Monday weekly expiry",
#     which was stale). FINNIFTY lot size 60, MIDCPNIFTY lot size 120 (both
#     from the 2026-01 NSE lot-size revision, confirmed against Angel's own
#     live scrip master, not just the web).
#   - scout45b confirmed real 15-min candles exist at least 1000 days back
#     for both (this script fetches the FULL available depth below,
#     chunked, stopping on a real empty-streak rather than assuming either
#     the 1000-day floor or a bigger number).
#
# WHY NO LONGONLY/BIDIR SPLIT (unlike scout44's commodity-futures backtest):
# this project's real-money deployment path for index signals is ALWAYS
# via OPTIONS (T78 buys a CE on a Buy signal, a PE on a Sell signal) - a
# "Sell" signal is never a short sale of the index itself, it just means
# "buy a PUT", which is still a LONG options position. So the single,
# always-in-a-position engine (byte-identical to scout18/21/21b/29's own
# UTBotState-driven backtest) is the right shape here, exactly as it was
# for NIFTY/BANKNIFTY/SENSEX - no futures-style short leg applies to index
# options the way it did to MCX commodity futures.
#
# POINTS-BASED P&L (same disclosed proxy/limitation as every prior UT Bot
# index scout - scout18/21/21b/29 all used raw index points, NOT real
# options-premium P&L, since a full historical options-chain rebuild is a
# much bigger undertaking and this project's own established discipline is
# to validate the SIGNAL first this way, then let real premium behavior
# show up honestly once a PAPER tracker runs on the real option). Overnight
# gap and theta are NOT modeled here, same disclosed caveat as T78's own
# original validation.
#
# HOLD POLICIES (same A1/A2/A4 decomposition already used for equity/
# commodity, and originally for NIFTY itself in scout21b - THAT is where
# T78's real edge was found to live specifically OVERNIGHT, not intraday,
# so this is repeated fresh here rather than assumed to transfer):
#   A1_OPEN     - never forced flat; ride until the opposite signal fires
#                 or data ends. Can hold indefinitely.
#   A2_INTRADAY - forced exit at every day's own last bar - never carries
#                 overnight at all.
#   A4_BTST     - T78's actual deployed policy on NIFTY: hold at most ONE
#                 night, forced exit at the next day's last bar if no
#                 opposite signal has fired by then.
#
# READ-ONLY. Run on Render Shell (needs live Angel session + network - this
# sandbox has neither), outside market hours per the guard below. Nothing
# here places an order, writes to app.py, or touches Pine/webhooks.
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


# Resolved by scout45 (real LTP cross-check) + confirmed real by scout45b
# (candle depth probe) - NOT guessed. See header comment for the full trail.
INDEX_INSTRUMENTS = {
    "FINNIFTY": {"exchange": "NSE", "token": "99926037", "lot_size": 60},
    "MIDCPNIFTY": {"exchange": "NSE", "token": "99926074", "lot_size": 120},
}

DAYS_BACK = 1100          # a bit past scout45b's confirmed 1000-day floor - the fetch loop below
                          # stops early on a real empty-streak rather than assuming this exact number
CHUNK_DAYS = 60           # conservative chunk size for FIFTEEN_MINUTE on a still-somewhat-untested index feed
CACHE_DIR = "/data/scout46_cache"


def _fetch_15min_chunk(api, exchange, token, from_dt, to_dt):
    for attempt in range(3):
        try:
            resp = api.getCandleData({
                "exchange": exchange, "symboltoken": str(token), "interval": "FIFTEEN_MINUTE",
                "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"), "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
            })
            if not resp or not resp.get("status") or resp.get("data") is None:
                return []
            return resp["data"]
        except Exception as e:
            print(f"    retry {attempt + 1}: {e}")
            time.sleep(2)
    return []


def get_index_15min_days(api, name, inst, days_back=DAYS_BACK, chunk_days=CHUNK_DAYS):
    """Returns dict: date -> sorted list of (dt, o, h, l, c). Cached per-
    index. Stops early on 4 consecutive empty chunks (same "found the real
    floor" pattern as scout44's commodity fetcher) rather than hammering
    Angel for a window with no data."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{name}_15min.json")
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
        empty_streak = 0
        while cur < end:
            nxt = min(cur + datetime.timedelta(days=chunk_days), end)
            rows = _fetch_15min_chunk(api, inst["exchange"], inst["token"], cur, nxt)
            raw.extend(rows)
            print(f"    chunk {cur.date()} -> {nxt.date()}: {len(rows)} rows")
            empty_streak = empty_streak + 1 if not rows else 0
            time.sleep(0.5)
            cur = nxt
            if empty_streak >= 4:
                print(f"    4 consecutive empty chunks - stopping early, likely reached the real data floor.")
                break
        with open(cache_path, "w") as f:
            json.dump(raw, f)

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


# UT Bot ATR trailing-stop engine - byte-identical to scout18/21/21b/29/32/
# 44's UTBotState (Buy key=6/ATR=1, Sell key=4/ATR=300 - the exact formula
# T78/T79 trade live). Not re-derived, not modified.
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
# Always-in-a-position engine (flip on signal, not "long-only") - matches
# scout21/21b/29's ORIGINAL NIFTY/BANKNIFTY/SENSEX approach exactly, since a
# Sell signal here just means "buy a PUT", never a real short sale.
def run_variant(bars, mode):
    """bars: (dt,o,h,l,c,day,is_last_bar_of_day) chronological, ONE index.
    mode: 'open' (A1) | 'intraday' (A2) | 'btst' (A4). Returns list of
    (entry_date, points_pnl, hold_bars)."""
    buy_src = UTBotState(6.0, 1)
    sell_src = UTBotState(4.0, 300)
    trades = []
    side = None    # None / 'CE' / 'PE' (mirrors T78's real CE/PE mapping)
    entry = e_i = e_d = None

    for i, (dt, o, h, l, c, day, last) in enumerate(bars):
        b, _ = buy_src.step(h, l, c)
        _, s = sell_src.step(h, l, c)

        if b and side != 'CE':
            if side == 'PE':
                trades.append((e_d, entry - c, i - e_i))   # PE profits when price falls
            entry, e_i, e_d = c, i, day
            side = 'CE'
        elif s and side != 'PE':
            if side == 'CE':
                trades.append((e_d, c - entry, i - e_i))   # CE profits when price rises
            entry, e_i, e_d = c, i, day
            side = 'PE'

        if last and side is not None:
            if mode == 'intraday':
                pnl = (c - entry) if side == 'CE' else (entry - c)
                trades.append((e_d, pnl, i - e_i))
                side = None
            elif mode == 'btst' and day > e_d:
                pnl = (c - entry) if side == 'CE' else (entry - c)
                trades.append((e_d, pnl, i - e_i))
                side = None
            # mode == 'open': no forced exit, ride continues

    if side is not None:
        last_c = bars[-1][4]
        pnl = (last_c - entry) if side == 'CE' else (entry - last_c)
        trades.append((e_d, pnl, len(bars) - 1 - e_i))
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


def run_one(name, days_dict):
    days_sorted = sorted(days_dict.keys())
    train_days, test_days = split_days(days_sorted, frac=0.7)
    train_set, test_set = set(train_days), set(test_days)

    bars = []
    for day in days_sorted:
        db = days_dict[day]
        for idx, (dt, o, h, l, c) in enumerate(db):
            bars.append((dt, o, h, l, c, day, idx == len(db) - 1))

    rows_out = []
    for label, mode in (("A1_OPEN", 'open'), ("A2_INTRADAY", 'intraday'), ("A4_BTST", 'btst')):
        trades = run_variant(bars, mode)
        tr = [p for d, p, hb in trades if d in train_set]
        te = [p for d, p, hb in trades if d in test_set]
        tra = sum(tr) / len(tr) if tr else 0.0
        tea = sum(te) / len(te) if te else 0.0
        hold = sum(hb for d, p, hb in trades) / len(trades) if trades else 0.0
        per_day = len(trades) / len(days_sorted) if days_sorted else 0.0
        rows_out.append({
            "index": name, "policy": label,
            "train_n": len(tr), "train_avg": tra,
            "test_n": len(te), "test_avg": tea,
            "trades_per_day": per_day, "avg_hold_bars": hold,
            "verdict": verdict_of(tr, te, tra, tea),
        })
    return rows_out


def find_big_jumps(days_sorted, days_dict, threshold_pct=8.0):
    jumps = []
    prev_close = None
    for d in days_sorted:
        day_close = days_dict[d][-1][4]
        if prev_close is not None and prev_close != 0:
            pct = (day_close - prev_close) / prev_close * 100
            if abs(pct) >= threshold_pct:
                jumps.append((d, pct))
        prev_close = day_close
    return jumps


# ------------------------------------------------------------ SMOKE TEST ---
def run_synthetic_smoke_test():
    import random
    random.seed(46)
    bars = []
    price = 25000.0
    d = datetime.date(2024, 1, 1)
    for i in range(3000):
        if i % 20 == 0 and i > 0:
            d += datetime.timedelta(days=1)
        cyc = i % 80
        if cyc < 45:
            price *= 1.0025
        elif cyc < 60:
            price *= 0.994
        else:
            price *= 1.002
        o, h, l, c = price * 0.999, price * 1.004, price * 0.996, price
        last = (i % 20 == 19)
        bars.append((datetime.datetime(d.year, d.month, d.day, 9, 0), o, h, l, c, d, last))

    fired_any = False
    for label, mode in [("A1_OPEN", 'open'), ("A2_INTRADAY", 'intraday'), ("A4_BTST", 'btst')]:
        trades = run_variant(bars, mode)
        if trades:
            fired_any = True
        avg = sum(p for _, p, _ in trades) / len(trades) if trades else 0.0
        print(f"  smoke {label}: {len(trades)} trades, avg {avg:+.2f} pts")
    assert fired_any, "SMOKE TEST FAILED: no policy fired even once on a favorable synthetic series"
    print("SMOKE TEST OK - unmodified UTBotState engine + CE/PE always-in-market backtest verified.\n")


# ------------------------------------------------------------------ MAIN ---
def main():
    run_synthetic_smoke_test()
    market_hours_guard()
    api = get_api()
    print(f"SCOUT46 - UT Bot on FINNIFTY/MIDCPNIFTY - {datetime.datetime.now().isoformat()}")
    print(f"Testing {list(INDEX_INSTRUMENTS.keys())}, {DAYS_BACK} days back requested, FIFTEEN_MINUTE bars.\n")

    all_results = []
    skipped = []
    for name, inst in INDEX_INSTRUMENTS.items():
        print(f"\n--- {name} ---")
        try:
            days_dict = get_index_15min_days(api, name, inst)
        except Exception as e:
            print(f"  SKIP {name}: {e}")
            skipped.append((name, str(e)))
            continue
        n_days = len(days_dict)
        days_sorted = sorted(days_dict.keys())
        if n_days < 150:
            print(f"  SKIP {name}: only {n_days} days - not enough history.")
            skipped.append((name, f"only {n_days} days"))
            continue
        print(f"  {n_days} days of real 15-min data, {days_sorted[0]} -> {days_sorted[-1]}")

        jumps = find_big_jumps(days_sorted, days_dict)
        if jumps:
            print(f"  data-quality note: {len(jumps)} single-day close-to-close move(s) >=8% "
                  f"(reported, nothing excluded because of it).")

        all_results.extend(run_one(name, days_dict))

    print("\n" + "=" * 112)
    print("SCOUT46 SUMMARY - UT Bot hold-policy face-off, FINNIFTY/MIDCPNIFTY (index points, proxy P&L)")
    print("=" * 112)
    print(f"{'index':<12} {'policy':<14} {'trN':>4} {'train_avg_pts':>13} {'teN':>4} {'test_avg_pts':>12} "
          f"{'tr/day':>7} {'hold':>6}  verdict")
    print("-" * 112)
    robust = []
    for r in all_results:
        print(f"{r['index']:<12} {r['policy']:<14} {r['train_n']:>4} {r['train_avg']:>13.2f} "
              f"{r['test_n']:>4} {r['test_avg']:>12.2f} {r['trades_per_day']:>7.2f} "
              f"{r['avg_hold_bars']:>6.1f}  {r['verdict']}")
        if r["verdict"] == "ROBUST":
            robust.append(r)

    print(f"\nSUMMARY: {len(robust)}/{len(all_results)} combos ROBUST.")
    for r in sorted(robust, key=lambda x: -(x["train_avg"] + x["test_avg"])):
        print(f"  {r['index']} / {r['policy']} -> train {r['train_avg']:+.2f} pts (N={r['train_n']}), "
              f"test {r['test_avg']:+.2f} pts (N={r['test_n']}), {r['trades_per_day']:.2f} trades/day, "
              f"hold {r['avg_hold_bars']:.1f} bars")

    if skipped:
        print(f"\nSKIPPED {len(skipped)}:")
        for name, reason in skipped:
            print(f"  {name}: {reason}")

    print("\nDone. READ-ONLY run complete - nothing modified, no orders placed.")
    print("Caveats not modeled: real options premium/theta/overnight gap (points proxy only, same")
    print("disclosed limitation as T78's own original NIFTY validation), STT/brokerage/DP charges,")
    print("entry slippage beyond signal-bar close fill. A ROBUST tag here is a candidate for a PAPER")
    print("tracker discussion, not a live-ready proof - same skepticism as every scout in this project.")


if __name__ == "__main__":
    main()
