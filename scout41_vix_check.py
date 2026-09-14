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

We do NOT hardcode India VIX's symboltoken (Angel occasionally changes/
reassigns tokens). Instead we resolve it live via the official searchScrip
endpoint, exactly like Angel's own docs describe, and only fail loudly if
that lookup itself fails - never guess a token number.
"""
import sys, os, datetime, statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scout37_sideways_regime_report as s37   # reuse get_api() / auth only


def resolve_vix_token(api):
    """Look up India VIX's NSE symboltoken via the official Search Scrip
    endpoint instead of hardcoding a token number."""
    resp = api.searchScrip(exchange="NSE", searchscrip="INDIAVIX")
    if not resp or not resp.get("status") or not resp.get("data"):
        print(f"  [FAIL] searchScrip('INDIAVIX') returned nothing usable: {resp}")
        return None
    for row in resp["data"]:
        sym = (row.get("tradingsymbol") or "").upper()
        if sym in ("INDIAVIX", "INDIA VIX"):
            print(f"  resolved: tradingsymbol={row.get('tradingsymbol')} "
                  f"symboltoken={row.get('symboltoken')} exchange={row.get('exchange')}")
            return row.get("symboltoken")
    # fall back to first result if exact name match isn't found
    row = resp["data"][0]
    print(f"  [WARN] no exact 'INDIAVIX' match, using first result: {row}")
    return row.get("symboltoken")


def fetch_daily(api, token, from_dt, to_dt):
    params = {
        "exchange": "NSE",
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

    print("Step 1: resolve INDIAVIX symboltoken via searchScrip ...")
    token = resolve_vix_token(api)
    if not token:
        print("\nSTOP: cannot resolve India VIX token - this data source is not "
              "usable via SmartAPI, need a different approach.")
        return
    print()

    to_dt = datetime.datetime.now()
    from_dt = to_dt - datetime.timedelta(days=3 * 365 + 10)
    print(f"Step 2: fetch daily candles {from_dt.date()} -> {to_dt.date()} ...")
    rows = fetch_daily(api, token, from_dt, to_dt)
    print(f"  got {len(rows)} daily bars\n")
    if not rows:
        print("STOP: no VIX candle data returned at all.")
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
