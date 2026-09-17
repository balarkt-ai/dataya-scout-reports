#!/usr/bin/env python3
# ============================================================================
# ONE-TIME DATA CORRECTION - 2026-09-17, explicit request ("enaku original
# record venum" - after confirming the get_exact_fill_price / Trade Book
# fix, the user asked for today's 2 already-wrong T99 manual trade records
# to be corrected to match Angel's real broker numbers).
#
# WHAT THIS FIXES: two T99 (manual) trades logged today with a WRONG exit
# price (the bug fixed in the app.py update - Order Book didn't confirm the
# fill in time, so it fell back to a stale LTP snapshot instead of the real
# fill). This script corrects ONLY those two exact trade records in
# /data/angel_trades.json to the REAL Angel broker exit price, and
# recomputes points/pnl/charges/net_pnl from that real price using the
# EXACT same formula as calculate_trade_charges() in app.py (copied here
# verbatim, not re-derived).
#
#   id 4144: NIFTY 23250 CE, T99 - dashboard exit 150.95 -> REAL exit 160.00
#            (Angel position book: B 148.00 / S 160.00, G&L +Rs 7,020.00)
#   id 4181: NIFTY 23300 CE, T99 - dashboard exit 127.35 -> REAL exit 130.00
#            (Angel position book: B 122.78 / S 130.00, G&L +Rs 4,223.70)
#
# SAFETY - NEVER TRUSTS BLINDLY, EVEN ITS OWN HARDCODED NUMBERS ABOVE:
#   - Before touching anything, re-reads BOTH records by id and verifies
#     symbol/option_type/technical/entry_price/OLD exit_price match EXACTLY
#     what is documented above. Any mismatch -> aborts with no write at all
#     (the trade log may have already changed since this was written).
#   - DRY-RUN BY DEFAULT. Only prints the before/after and does not write
#     anything unless run with --apply.
#   - On --apply: backs up the whole file first (angel_trades.json.bak_<ts>)
#     next to the original, then writes via the same atomic temp-file +
#     os.replace() pattern app.py's own save_trades() uses (never a partial
#     write visible to a concurrent reader).
#   - Touches ONLY these 2 records, in this 1 file. Never touches
#     active_positions.json, trading_modes.json, or any other file. Does
#     not import or run any part of app.py itself - no live order, no
#     Angel API call, no network access needed at all.
# ============================================================================
import json
import os
import sys
import shutil
import datetime

TRADES_FILE = "/data/angel_trades.json"

BROKERAGE_PER_ORDER = 20
STT_SELL_PCT = 0.15
EXCHANGE_CHARGE_PCT = 0.0355299
SEBI_CHARGE_PCT = 0.0001
STAMP_DUTY_BUY_PCT = 0.003
GST_PCT = 18


def calculate_trade_charges(entry_price, exit_price, quantity):
    """Byte-identical to calculate_trade_charges() in app.py - not
    re-derived, copied verbatim so the corrected numbers are computed
    exactly the way the live system computes every other trade's."""
    buy_turnover = entry_price * quantity
    sell_turnover = exit_price * quantity
    total_turnover = buy_turnover + sell_turnover
    brokerage = BROKERAGE_PER_ORDER * 2
    stt = round(sell_turnover * STT_SELL_PCT / 100, 2)
    exchange_charge = round(total_turnover * EXCHANGE_CHARGE_PCT / 100, 2)
    sebi_charge = round(total_turnover * SEBI_CHARGE_PCT / 100, 2)
    stamp_duty = round(buy_turnover * STAMP_DUTY_BUY_PCT / 100, 2)
    gst = round((brokerage + exchange_charge + sebi_charge) * GST_PCT / 100, 2)
    total_charges = round(brokerage + stt + exchange_charge + sebi_charge + stamp_duty + gst, 2)
    gross_pnl = round((exit_price - entry_price) * quantity, 2)
    net_pnl = round(gross_pnl - total_charges, 2)
    return {
        "brokerage": brokerage, "stt": stt, "exchange_charge": exchange_charge,
        "sebi_charge": sebi_charge, "stamp_duty": stamp_duty, "gst": gst,
        "total_charges": total_charges, "gross_pnl": gross_pnl, "net_pnl": net_pnl,
    }


# The 2 exact corrections, with the expected BEFORE state as a guard.
CORRECTIONS = [
    {
        "id": 4144, "symbol": "NIFTY", "option_type": "CE", "technical": "99",
        "expect_entry_price": 148.0, "expect_old_exit_price": 150.95,
        "real_exit_price": 160.00,
        "note": "Angel position book: B 148.00 / S 160.00, G&L +Rs 7,020.00",
    },
    {
        "id": 4181, "symbol": "NIFTY", "option_type": "CE", "technical": "99",
        "expect_entry_price": 122.78, "expect_old_exit_price": 127.35,
        "real_exit_price": 130.00,
        "note": "Angel position book: B 122.78 / S 130.00, G&L +Rs 4,223.70",
    },
]


def main():
    apply = "--apply" in sys.argv
    if not os.path.exists(TRADES_FILE):
        print(f"ABORT: {TRADES_FILE} not found.")
        sys.exit(1)

    with open(TRADES_FILE) as f:
        trades = json.load(f)
    by_id = {t.get("id"): t for t in trades}

    print("=" * 100)
    print(f"{'DRY RUN' if not apply else 'APPLY'} - correcting {len(CORRECTIONS)} T99 manual trade record(s) "
          f"in {TRADES_FILE}")
    print("=" * 100)

    planned = []
    for c in CORRECTIONS:
        t = by_id.get(c["id"])
        if t is None:
            print(f"\nABORT: trade id {c['id']} not found in the file - nothing will be written.")
            sys.exit(1)
        mismatches = []
        if t.get("symbol") != c["symbol"]:
            mismatches.append(f"symbol {t.get('symbol')!r} != expected {c['symbol']!r}")
        if t.get("option_type") != c["option_type"]:
            mismatches.append(f"option_type {t.get('option_type')!r} != expected {c['option_type']!r}")
        if str(t.get("technical")) != c["technical"]:
            mismatches.append(f"technical {t.get('technical')!r} != expected {c['technical']!r}")
        if abs(float(t.get("entry_price", -1e9)) - c["expect_entry_price"]) > 0.001:
            mismatches.append(f"entry_price {t.get('entry_price')!r} != expected {c['expect_entry_price']!r}")
        if t.get("exit_price") is None or abs(float(t["exit_price"]) - c["expect_old_exit_price"]) > 0.001:
            mismatches.append(f"exit_price {t.get('exit_price')!r} != expected old value {c['expect_old_exit_price']!r}")
        if mismatches:
            print(f"\nABORT: trade id {c['id']} does not match what this script expects to find - "
                  f"NOTHING will be written. Mismatches:")
            for m in mismatches:
                print(f"    - {m}")
            print("  (the trade log may already have changed since this script was written - re-check by hand.)")
            sys.exit(1)
        planned.append((t, c))

    for t, c in planned:
        old = {k: t.get(k) for k in ("exit_price", "points", "pnl", "charges", "net_pnl")}
        quantity = t["quantity"]
        entry_price = t["entry_price"]
        new_exit = c["real_exit_price"]
        new_points = round(new_exit - entry_price, 2)
        new_gross_pnl = round(new_points * quantity, 2)
        charges = calculate_trade_charges(entry_price, new_exit, quantity)

        peak_price = t.get("peak_price")
        new_capture_pct = None
        if peak_price is not None:
            peak_points = round(peak_price - entry_price, 2)
            if peak_points > 0:
                new_capture_pct = round((new_points / peak_points) * 100, 1)
                if new_points > peak_points + 0.01:
                    print(f"\n  NOTE: id {c['id']} - corrected points ({new_points}) exceed the recorded "
                          f"peak ({peak_points}) by more than rounding - flagging for your own eyes, not blocking.")

        print(f"\n--- id {c['id']}  ({c['symbol']} {t.get('strike')} {c['option_type']}, T{c['technical']}) ---")
        print(f"  {c['note']}")
        print(f"  BEFORE: exit_price={old['exit_price']}  points={old['points']}  pnl={old['pnl']}  "
              f"charges={old['charges']}  net_pnl={old['net_pnl']}")
        print(f"  AFTER:  exit_price={new_exit}  points={new_points}  pnl={new_gross_pnl}  "
              f"charges={charges['total_charges']}  net_pnl={charges['net_pnl']}"
              + (f"  capture_pct={new_capture_pct}" if new_capture_pct is not None else ""))

        if apply:
            t["exit_price"] = new_exit
            t["points"] = new_points
            t["pnl"] = new_gross_pnl
            t["charges"] = charges["total_charges"]
            t["net_pnl"] = charges["net_pnl"]
            if new_capture_pct is not None:
                t["capture_pct"] = new_capture_pct
            t["corrected_2026_09_17"] = {
                "reason": "get_exact_fill_price did not confirm the real fill in time for this manual exit; "
                          "corrected to Angel's real broker exit price after the Trade Book cross-check fix.",
                "old_exit_price": old["exit_price"],
            }

    if not apply:
        print("\nDRY RUN ONLY - nothing written. Re-run with --apply to actually save these 2 corrections.")
        return

    backup_path = f"{TRADES_FILE}.bak_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(TRADES_FILE, backup_path)
    print(f"\nBackup of the original file saved to: {backup_path}")

    tmp_path = TRADES_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(trades, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, TRADES_FILE)
    print(f"APPLIED - {len(planned)} record(s) corrected in {TRADES_FILE}.")
    print("Refresh the dashboard - Trade History for these 2 rows should now show the real broker numbers.")


if __name__ == "__main__":
    main()
