#!/usr/bin/env python3
# ============================================================================
# SCOUT40 - BREAKOUT -> POC-PULLBACK ENTRY, *WITHOUT* THE SWEEP PREREQUISITE
#
# WHY THIS EXISTS: scout39 tested the chart-study video's full sequence
# (box -> sweep -> breakout -> POC pullback) on real NIFTY/BANKNIFTY/SENSEX
# data and came back NOT ROBUST (1/48, low-N, exit-fragile). Its funnel showed
# the sequence mostly died at the SWEEP stage: ~1800 "breakdown" cancels vs
# ~470 breakouts per index - the dip below the box usually became a REAL move,
# not a fake-out. This is the ONE agreed follow-up hypothesis (single test,
# not a grid expansion): drop the sweep requirement entirely -
#
#   box (same compression rule + same TPO-POC as scout39)
#   -> first CLOSE outside the box = breakout, direction = breakout side
#      (bias ON still restricts direction via daily EMA20 vs EMA50)
#   -> wait for the pullback into the POC (no pullback = no trade)
#   -> ENTRY at POC; STOP = the OPPOSITE box edge (classic range-retest
#      stop, since there is no sweep extreme any more); TARGET 2R or MM.
#
# Everything else is reused from scout39 (data loaders, warm caches, ATR,
# POC, bias map, stats, verdict, 70/30 split, 48-combo scaffolding, funnel,
# CSV). Run from the same folder as scout39_poc_sweep_pullback.py.
# READ-ONLY. Index points, not option premium. Research only.
# ============================================================================
import os
import sys
import datetime
import scout39_poc_sweep_pullback as s39

CSV_PATH = "/data/scout40_trades.csv"


def run_engine(bars, W, target_kind, bias_on, bias_map, intraday, p):
    n = len(bars)
    atr = s39.wilder_atr(bars)
    win_range = [None] * n
    for i in range(W - 1, n):
        if intraday and bars[i - W + 1]["date"] != bars[i]["date"]:
            continue
        hi = max(b["h"] for b in bars[i - W + 1:i + 1])
        lo = min(b["l"] for b in bars[i - W + 1:i + 1])
        win_range[i] = hi - lo
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
            continue

        if intraday and state != "IDLE" and st.get("date") != b["date"]:
            cancel({"BOX": "cancel_no_break", "BROKE": "cancel_no_pullback"}[state])

        if state == "BOX":
            bl, bh = st["box_lo"], st["box_hi"]
            d = "long" if b["c"] > bh else ("short" if b["c"] < bl else None)
            if d is None:
                if i - st["lock_i"] > p["BOX_WAIT"]:
                    cancel("cancel_no_break")
                continue
            if bias_on:
                need = {"up": "long", "down": "short"}.get(st["bias"])
                if need != d:
                    cancel("cancel_bias_mismatch"); continue
            st["dir"] = d
            st["broke_i"] = i
            state = "BROKE"
            funnel["breakouts"] += 1
            continue

        if state == "BROKE":
            d, poc, bl, bh = st["dir"], st["poc"], st["box_lo"], st["box_hi"]
            touched = (b["l"] <= poc) if d == "long" else (b["h"] >= poc)
            if touched:
                entry = (min(poc, b["o"]) if d == "long" else max(poc, b["o"]))
                stop = bl if d == "long" else bh
                risk = (entry - stop) if d == "long" else (stop - entry)
                if risk <= 0:
                    cancel("cancel_breakdown"); continue
                height = bh - bl
                if target_kind == "2R":
                    target = entry + 2 * risk if d == "long" else entry - 2 * risk
                else:
                    target = bh + height if d == "long" else bl - height
                t = {"date": b["date"], "dir": d, "entry": round(entry, 2), "stop": round(stop, 2),
                     "target": round(target, 2), "risk": risk, "poc": round(poc, 2),
                     "box_lo": round(bl, 2), "box_hi": round(bh, 2), "entry_i": i,
                     "entry_time": b["dt"].strftime("%H:%M") if intraday else ""}
                funnel["entries"] += 1
                hit_stop = (b["l"] <= stop) if d == "long" else (b["h"] >= stop)
                if hit_stop:
                    t.update({"exit": round(stop, 2), "exit_reason": "STOP", "exit_date": b["date"],
                              "pts": round(-risk, 2), "R": -1.0})
                    trades.append(t); state = "IDLE"; st = {}
                    continue
                st["trade"] = t
                state = "IN_TRADE"
                if intraday and day_end:
                    pts = (b["c"] - entry) if d == "long" else (entry - b["c"])
                    t.update({"exit": b["c"], "exit_reason": "EOD", "exit_date": b["date"],
                              "pts": round(pts, 2), "R": round(pts / risk, 2)})
                    trades.append(t); state = "IDLE"; st = {}
                continue
            # before the pullback: a close back through the far edge kills the idea
            if (d == "long" and b["c"] < bl) or (d == "short" and b["c"] > bh):
                cancel("cancel_breakdown"); continue
            if i - st["broke_i"] > p["PULL_MAX"]:
                cancel("cancel_no_pullback")
            continue

        wr = win_range[i]
        if wr is not None:
            if len(hist_ranges) >= 20 and atr[i] is not None and i >= next_lock_ok:
                med = s39.median(hist_ranges[-s39.MED_N:])
                ok_time = True
                if intraday:
                    ok_time = bars_left_in_day(i) >= p["LAST_LOCK_BARS"]
                if ok_time and wr <= s39.COMPRESS * med:
                    win = bars[i - W + 1:i + 1]
                    lo = min(x["l"] for x in win); hi = max(x["h"] for x in win)
                    st = {"box_lo": lo, "box_hi": hi, "atr": atr[i], "lock_i": i, "date": b["date"],
                          "poc": s39.tpo_poc(win, lo, hi, atr[i]), "bias": bias_map.get(b["date"])}
                    if bias_on and st["bias"] is None:
                        st = {}
                    else:
                        state = "BOX"
                        funnel["boxes"] += 1
                        next_lock_ok = i + W
            hist_ranges.append(wr)
    if state in ("BOX", "BROKE"):
        cancel({"BOX": "cancel_no_break", "BROKE": "cancel_no_pullback"}[state])
    return trades, funnel


def main():
    print(f"SCOUT40 - breakout -> POC pullback (NO sweep prerequisite) - {datetime.datetime.now(s39.IST).isoformat()}")
    print("Single follow-up hypothesis to scout39. Stop = opposite box edge. Same 48-combo scaffolding,\n"
          "same data (warm caches), same 70/30 split and ROBUST rule. READ-ONLY, index points.\n")
    _api = {"obj": None}

    def api_maker():
        if _api["obj"] is None:
            if s39.market_hours_now():
                print("SAFETY STOP: a data download is needed during market hours - run after 15:30 IST.")
                sys.exit(1)
            _api["obj"] = s39.get_api()
        return _api["obj"]

    all_rows, all_trades = [], []
    for name, inst in s39.INDEX_INSTRUMENTS.items():
        print(f"=== {name} ===")
        daily = s39.load_daily(api_maker, name, inst)
        m5 = s39.load_5min(api_maker, name, inst)
        bias = s39.daily_bias_map(daily)
        for mode, bars, P in (("INTRADAY", m5, s39.INTRADAY), ("POSITIONAL", daily, s39.POSITIONAL)):
            if len(bars) < 100:
                continue
            cut = s39.split_by_day(bars)
            for W in P["W"]:
                for tk in s39.TARGETS:
                    for bs in s39.BIASES:
                        trades, funnel = run_engine(bars, W, tk, bs == "ON", bias, mode == "INTRADAY", P)
                        tr = [t for t in trades if t["date"] < cut]
                        te = [t for t in trades if t["date"] >= cut]
                        s_tr, s_te = s39.stats(tr), s39.stats(te)
                        row = {"index": name, "mode": mode, "W": W, "target": tk, "bias": bs,
                               "funnel": funnel, "train": s_tr, "test": s_te, "verdict": s39.verdict(s_tr, s_te)}
                        all_rows.append(row)
                        for t in trades:
                            all_trades.append({**t, "index": name, "mode": mode, "W": W, "target": tk,
                                               "bias": bs, "split": "train" if t["date"] < cut else "test"})
                        f = funnel
                        print(f"  {mode:<10} W={W:<3} {tk:<2} bias={bs:<3} | boxes {f['boxes']:>4} breaks {f['breakouts']:>4} "
                              f"entries {f['entries']:>4} | TRAIN n={s_tr['n']:>3} win {s_tr['win']:>5}% avg {s_tr['avg']:>7} pf {s_tr['pf']:>5} | "
                              f"TEST n={s_te['n']:>3} win {s_te['win']:>5}% avg {s_te['avg']:>7} pf {s_te['pf']:>5} | {row['verdict']}")
        print()

    print("=" * 110)
    print("SUMMARY - combos sorted by TEST avg pts (only combos with test trades)")
    print("=" * 110)
    print(f"{'index':<10}{'mode':<11}{'W':>3} {'tgt':<3} {'bias':<4} | {'trainN':>6} {'trAvg':>8} | {'testN':>5} {'teWin%':>6} {'teAvg':>8} {'tePF':>5} | verdict")
    print("-" * 110)
    for r in sorted([x for x in all_rows if x["test"]["n"] > 0], key=lambda x: -x["test"]["avg"]):
        print(f"{r['index']:<10}{r['mode']:<11}{r['W']:>3} {r['target']:<3} {r['bias']:<4} | {r['train']['n']:>6} {r['train']['avg']:>8} | "
              f"{r['test']['n']:>5} {r['test']['win']:>6} {r['test']['avg']:>8} {r['test']['pf']:>5} | {r['verdict']}")
    robust = [r for r in all_rows if r["verdict"].startswith("ROBUST")]
    print(f"\nROBUST combos: {len(robust)} / {len(all_rows)}  (of which LOW-N: {sum(1 for r in robust if 'LOW-N' in r['verdict'])})")

    print("\nFUNNEL - summed over W/target/bias combos:")
    for name in s39.INDEX_INSTRUMENTS:
        for mode in ("INTRADAY", "POSITIONAL"):
            rs = [r for r in all_rows if r["index"] == name and r["mode"] == mode]
            if not rs:
                continue
            f = {k: sum(r["funnel"][k] for r in rs) for k in rs[0]["funnel"]}
            print(f"  {name:<10}{mode:<11} boxes {f['boxes']:>5} -> breakouts {f['breakouts']:>5} -> entries {f['entries']:>5} | "
                  f"cancels: no-break {f['cancel_no_break']}, breakdown {f['cancel_breakdown']}, no-pullback {f['cancel_no_pullback']}, "
                  f"bias-mismatch {f['cancel_bias_mismatch']}")

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
    print("CAVEATS: TPO-POC not volume-POC; 48 combos tried (chance hits possible); points not premium; limit-fill at POC assumed.")


if __name__ == "__main__":
    main()
