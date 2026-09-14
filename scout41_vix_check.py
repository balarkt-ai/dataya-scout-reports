#!/usr/bin/env python3
"""
scout41_vix_check.py
---------------------
READ-ONLY diagnostic. Places NO orders. Touches nothing except historical
candle data (same as scout37/scout39's data-check scripts).

WHY THIS EXISTS
Before building a full "VIX fear-spike reversal + VIX-regime gate" backtest
(a genuinely new strategy family - it uses India VIX, an option-market
fear gauge, as the core signal, instead of pure OHLC price patterns like
every other technic in this project), we must first confirm - with REAL
Angel SmartAPI data, not assumption - that:

  1. India VIX historical DAILY data is actually retrievable at all.
  2. The values look sane (no zero/garbage days, like index-spot volume
     turned out to be zero in the earlier POC/sweep track).
  3. Roughly how many "fear spike" candidate events exist over ~3 years,
     so we know up front whether there is even enough sample size to
     honestly train/test-split later (same discipline as scout37/39/40).

TOKEN NOTE (2026-09-14): the live run of v1 of this script proved that
Angel's order/v1/searchScrip endpoint does NOT index pure quote-only
indices (it returned "AB4047: Scrip not found in scrip master cache" for
INDIAVIX, the same way it would for NIFTY/BANKNIFTY/SENSEX - searchScrip
is for TRADABLE instruments only). So, exactly like scout37 already does
for NIFTY/BANKNIFTY/SENSEX (see INDEX_INSTRUMENTS there), we now use
India VIX's own published NSE symboltoken directly: 99926017 (confirmed
against Angel's own SmartAPI forum announcement of index token coverage,
same "998269xx"-style new-format numbering as NIFTY=99926000 and
BANKNIFTY=99926009). searchScrip is still called first, purely as an
informational cross-check that is allowed to fail without stopping the
script - the hardcoded token is what actually drives the data fetch.
"""
import sys, os, datetime, statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scout37_sideways_regime_report as s37   # reuse get_api() / auth only

VIX_EXCHANGE = "NSE"
VIX_TOKEN = "99926017"   # India VIX - same numbering family as NIFTY 99926000 / BANKNIFTY 99926009


def try_search_scrip_crosscheck(api):
    """Informational only: try the Search Scrip endpoint and print whatever
    it says, but NEVER fail the script over this - it's known to not cover
    pure quote-only indices (confirmed live: AB4047 for INDIAVIX)."""
    try:
        resp = api.searchScrip(exchange=VIX_EXCHANGE, searchscrip="INDIAVIX")
    except Exception as e:
        print(f"  (searchScrip cross-check raised {e!r} - ignoring, not fatal)")
        return
    if resp and resp.get("status") and resp.get("data"):
        print(f"  (searchScrip cross-check found: {resp['data']})")
    else:
        print(f"  (searchScrip cross-check: no match - expected for quote-only "
              f"indices, continuing with hardcoded token {VIX_TOKEN})")


def fetch_daily(api, token, from_dt, to_dt):
    params = {
        "exchange": VIX_EXCHANGE,
        "symboltoken": token,
        "interval": "ONE_DAY",
        "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
        "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
    }
    resp = api.getCandleData(params)
    if not resp or not resp.get("status") or not resp.get("data"):
        print(f"  [FAIL] getCandleData returned nothing usable: {resp}")
        return []
    rows = []
    for r in resp["data"]:
        dt = datetime.datetime.fromisoformat(r[0])
        rows.append({"date": dt.date().isoformat(), "o": float(r[1]), "h": float(r[2]),
                     "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
    return rows


def scan_fear_spikes(rows, pct=15.0, window=3):
    """Crude first-look scan: VIX rising by >=pct% over `window` trading days."""
    closes = [r["c"] for r in rows]
    spikes = []
    for i in range(window, len(rows)):
        base = closes[i - window]
        if base <= 0:
            continue
        chg = (closes[i] - base) / base * 100.0
        if chg >= pct:
            spikes.append((rows[i]["date"], round(chg, 1), round(closes[i], 2)))
    return spikes


def main():
    print("=== scout41_vix_check: India VIX data-availability + fear-spike scan ===\n")
    api = s37.get_api()

    print("Step 1: informational searchScrip cross-check (not fatal if it fails) ...")
    try_search_scrip_crosscheck(api)
    print(f"\nUsing hardcoded India VIX token: exchange={VIX_EXCHANGE} symboltoken={VIX_TOKEN}\n")

    to_dt = datetime.datetime.now()
    from_dt = to_dt - datetime.timedelta(days=3 * 365 + 10)
    print(f"Step 2: fetch daily candles {from_dt.date()} -> {to_dt.date()} ...")
    rows = fetch_daily(api, VIX_TOKEN, from_dt, to_dt)
    print(f"  got {len(rows)} daily bars\n")
    if not rows:
        print("STOP: no VIX candle data returned at all - token may be wrong, "
              "need to re-verify against Angel's scrip master.")
        return

    closes = [r["c"] for r in rows]
    zero_days = sum(1 for c in closes if c <= 0)
    print("Step 3: sanity-check the values")
    print(f"  min={min(closes):.2f}  max={max(closes):.2f}  median={statistics.median(closes):.2f}")
    print(f"  zero/garbage-value days: {zero_days} / {len(rows)}\n")

    print("Step 4: first-look fear-spike scan (VIX +15% or more over 3 trading days)")
    spikes = scan_fear_spikes(rows, pct=15.0, window=3)
    print(f"  occurrences over ~3 years: {len(spikes)}")
    for d, chg, lvl in spikes[:25]:
        print(f"    {d}   +{chg}%   (VIX={lvl})")
    if len(spikes) > 25:
        print(f"    ... and {len(spikes) - 25} more")

    print("\n=== SUMMARY ===")
    usable = zero_days == 0 and len(rows) > 500
    print(f"  data usable for a real backtest: {'YES' if usable else 'NEEDS REVIEW'}")
    print(f"  candidate fear-spike events found: {len(spikes)} "
          f"(want ~40+ spread across the 3 years for an honest 70/30 train/test split)")
    print("\nNo orders were placed. This script only read historical candle data.")


if __name__ == "__main__":
    main()
