"""
Realistic backtest engine for the scanner.
- Runs the scanner analysis on PAST data only (no lookahead)
- Only enters when price actually reaches the entry level
- Walks forward bar-by-bar to resolve SL / TP1 / TP2
- One trade at a time (no overlapping positions)
- Minimum R:R filter — TP1 must be at least 1.5R away
- Skips same-bar exits (trade must resolve from the next candle)
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
import scanner_engine as se
from datetime import datetime, timezone


def run_backtest(symbol, timeframe, starting_balance=1000.0, risk_per_trade=0.01,
                 window_size=200, entry_wait_bars=40, max_hold_bars=200,
                 analysis_step=4, min_rr=1.5):
    """
    analysis_step: run the scanner every N bars. 1 = every bar (slow),
    4 = every 4th bar (fast).
    min_rr: minimum reward-to-risk ratio for TP1. Trades below this are skipped.
    """
    try:
        df, src = se.fetch_data(symbol, timeframe)
        if df is None or len(df) < 100:
            return {"error": f"Not enough data from {src}"}

        df = se.find_swings(df, timeframe)

        trades = []
        balance = starting_balance
        equity_curve = [{
            "time": int(df.index[0].timestamp()),
            "balance": round(balance, 2)
        }]

        start_i = max(60, window_size)
        end_i = len(df) - 1
        i = start_i
        skipped_no_entry = 0
        skipped_low_rr = 0
        analyzed = 0

        while i < end_i:
            analyzed += 1

            # Analysis window: only data up to and including bar i
            window = df.iloc[max(0, i - window_size):i + 1].copy()
            if len(window) < 60:
                i += analysis_step
                continue

            try:
                structure = se.detect_structure(window)
                mss = se.detect_mss(window, structure)
                pools = se.find_liquidity_pools(window, structure, timeframe)
                obs = se.find_order_blocks(window, timeframe)
                fvgs = se.find_fvgs(window, timeframe, lookback=100)
                last_close = float(window["close"].values[-1])
                pd_zones = se.premium_discount(window, structure, last_close)
                killzone_ok = True  # backtest ignores session filter
                bias = {
                    "d1_trend": "BULLISH", "h4_trend": "BULLISH",
                    "allow_buy": True, "allow_sell": True,
                    "reason": "backtest",
                }
                buy, sell = se.generate_scenarios(
                    window, structure, obs, fvgs, pools, pd_zones,
                    timeframe, mss, symbol=symbol, bias=bias,
                    killzone_ok=killzone_ok
                )
            except Exception:
                i += analysis_step
                continue

            # Choose the setup (prefer the one with better R:R if both exist)
            setup = None
            if buy and sell:
                setup = buy if buy["rr"] >= sell["rr"] else sell
            elif buy:
                setup = buy
            elif sell:
                setup = sell

            if not setup:
                i += analysis_step
                continue

            direction = setup["direction"]
            entry = float(setup["entry"])
            sl = float(setup["sl"])
            tp1 = float(setup["tp1"])
            tp2 = float(setup["tp2"])
            risk_points = abs(entry - sl)
            if risk_points <= 0:
                i += analysis_step
                continue

            # Minimum R:R filter — TP1 must be at least min_rr × risk
            reward_1 = abs(tp1 - entry)
            if reward_1 < risk_points * min_rr:
                skipped_low_rr += 1
                i += analysis_step
                continue

            # Look for entry trigger in the next N bars
            trigger_idx = None
            for j in range(i + 1, min(i + 1 + entry_wait_bars, end_i)):
                bar = df.iloc[j]
                if direction == "BUY":
                    if float(bar["low"]) <= entry:
                        trigger_idx = j
                        break
                else:
                    if float(bar["high"]) >= entry:
                        trigger_idx = j
                        break

            if trigger_idx is None:
                skipped_no_entry += 1
                i += analysis_step
                continue

            # Walk forward to resolve — start checking from the bar AFTER entry
            outcome = None
            exit_price = entry
            exit_idx = trigger_idx
            for k in range(trigger_idx + 1, min(trigger_idx + max_hold_bars, end_i)):
                bar = df.iloc[k]
                high = float(bar["high"])
                low = float(bar["low"])

                if direction == "BUY":
                    if low <= sl:
                        outcome = "SL"
                        exit_price = sl
                        exit_idx = k
                        break
                    if high >= tp2:
                        outcome = "TP2"
                        exit_price = tp2
                        exit_idx = k
                        break
                    if high >= tp1:
                        outcome = "TP1"
                        exit_price = tp1
                        exit_idx = k
                        break
                else:
                    if high >= sl:
                        outcome = "SL"
                        exit_price = sl
                        exit_idx = k
                        break
                    if low <= tp2:
                        outcome = "TP2"
                        exit_price = tp2
                        exit_idx = k
                        break
                    if low <= tp1:
                        outcome = "TP1"
                        exit_price = tp1
                        exit_idx = k
                        break

            if outcome is None:
                # Trade still open at the end of data — skip
                i = exit_idx + 1
                continue

            # P&L in R multiples
            if outcome == "SL":
                r_multiple = -1.0
            elif outcome == "TP1":
                r_multiple = abs(tp1 - entry) / risk_points
            else:  # TP2
                r_multiple = abs(tp2 - entry) / risk_points

            risk_dollars = balance * risk_per_trade
            pnl_dollars = r_multiple * risk_dollars
            balance += pnl_dollars

            entry_time = int(df.index[trigger_idx].timestamp())
            exit_time = int(df.index[exit_idx].timestamp())

            trades.append({
                "entry_time": entry_time,
                "exit_time": exit_time,
                "direction": direction,
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "tp1": round(tp1, 2),
                "tp2": round(tp2, 2),
                "outcome": outcome,
                "r_multiple": round(r_multiple, 2),
                "pnl": round(pnl_dollars, 2),
                "balance": round(balance, 2),
                "confidence": setup.get("confidence", "MEDIUM"),
                "zone_type": setup.get("zone_type", ""),
                "bars_held": exit_idx - trigger_idx,
            })

            equity_curve.append({
                "time": exit_time,
                "balance": round(balance, 2),
            })

            # Resume after the trade closed
            i = exit_idx + 1

        # ----- Stats -----
        total = len(trades)
        if total == 0:
            return {
                "symbol": symbol, "timeframe": timeframe, "data_source": src,
                "total_trades": 0,
                "skipped_no_entry": skipped_no_entry,
                "skipped_low_rr": skipped_low_rr,
                "win_rate": 0, "profit_factor": 0,
                "final_balance": round(balance, 2),
                "starting_balance": starting_balance,
                "net_pnl": round(balance - starting_balance, 2),
                "trades": [], "equity_curve": equity_curve,
                "bars_analyzed": analyzed,
                "note": "No setups triggered and completed during the backtest window"
            }

        wins = [t for t in trades if t["outcome"] in ("TP1", "TP2")]
        losses = [t for t in trades if t["outcome"] == "SL"]
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))

        win_rate = len(wins) / total * 100
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 999
        avg_r = sum(t["r_multiple"] for t in trades) / total

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "data_source": src,
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "skipped_no_entry": skipped_no_entry,
            "skipped_low_rr": skipped_low_rr,
            "bars_analyzed": analyzed,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_r_multiple": round(avg_r, 2),
            "final_balance": round(balance, 2),
            "starting_balance": starting_balance,
            "net_pnl": round(balance - starting_balance, 2),
            "trades": trades[-100:],
            "equity_curve": equity_curve,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        return {"error": f"Backtest failed: {str(e)}"}


def backtest(symbol, timeframe, **kwargs):
    return run_backtest(symbol, timeframe, **kwargs)