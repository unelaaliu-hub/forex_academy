"""
CRT (Candle Range Theory) Engine — separate from ICT scanner.
Detects 3-candle patterns: C1 (range) → C2 (sweep) → C3 (distribution).
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from datetime import datetime, timezone

import scanner_engine  # reuse candle fetcher, bias, killzone, swings


CRT_TF_PARAMS = {
    "15m": {"min_range_atr": 0.8, "displacement_mult": 1.2},
    "1h":  {"min_range_atr": 0.8, "displacement_mult": 1.2},
    "4h":  {"min_range_atr": 0.9, "displacement_mult": 1.2},
    "1d":  {"min_range_atr": 1.0, "displacement_mult": 1.2},
}


def _atr_series(df, period=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _safe_float(v, default=0.0):
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except Exception:
        return default


def detect_crt_patterns(df, timeframe="15m", lookback=20):
    params = CRT_TF_PARAMS.get(timeframe, CRT_TF_PARAMS["15m"])
    min_range_atr = params["min_range_atr"]
    disp_mult = params["displacement_mult"]

    if len(df) < 25:
        return []

    atr = _atr_series(df, 14)
    body = (df["close"] - df["open"]).abs()
    avg_body = body.rolling(20).mean()

    patterns = []
    start = max(2, len(df) - lookback)

    for c3_idx in range(start, len(df)):
        c2_idx = c3_idx - 1
        c1_idx = c3_idx - 2
        if c1_idx < 20:
            continue

        c1 = df.iloc[c1_idx]
        c2 = df.iloc[c2_idx]
        c3 = df.iloc[c3_idx]

        atr_v = _safe_float(atr.iloc[c3_idx], 0)
        avg_b = _safe_float(avg_body.iloc[c3_idx], 0)
        if atr_v <= 0 or avg_b <= 0:
            continue

        c1_high = _safe_float(c1["high"])
        c1_low  = _safe_float(c1["low"])
        c1_range = c1_high - c1_low
        if c1_range < atr_v * min_range_atr:
            continue

        c2_high  = _safe_float(c2["high"])
        c2_low   = _safe_float(c2["low"])
        c2_close = _safe_float(c2["close"])

        c3_open  = _safe_float(c3["open"])
        c3_close = _safe_float(c3["close"])
        c3_body  = abs(c3_close - c3_open)

        # BULLISH CRT
        bull_sweep = (c2_low < c1_low) and (c2_close > c1_low)
        bull_c3    = (c3_close > c3_open) and (c1_low <= c3_close <= c1_high)
        bull_disp  = c3_body >= avg_b * disp_mult

        if bull_sweep and bull_c3 and bull_disp:
            entry = c3_close
            sl    = c2_low - atr_v * 0.3
            tp1   = c1_high
            tp2   = c1_high + c1_range * 0.5
            risk  = entry - sl
            rr1   = (tp1 - entry) / risk if risk > 0 else 0
            if rr1 >= 1.5:
                patterns.append({
                    "type": "CRT_BULL",
                    "direction": "BUY",
                    "c1_idx": c1_idx, "c2_idx": c2_idx, "c3_idx": c3_idx,
                    "c1_high": c1_high, "c1_low": c1_low,
                    "sweep_side": "LOW", "sweep_price": c2_low,
                    "entry": entry, "sl": sl,
                    "tp1": tp1, "tp2": tp2, "rr1": rr1,
                    "range_atr": c1_range / atr_v,
                    "displacement": c3_body / avg_b,
                    "c3_time": df.index[c3_idx],
                    "c1_time": df.index[c1_idx],
                    "c2_time": df.index[c2_idx],
                })

        # BEARISH CRT
        bear_sweep = (c2_high > c1_high) and (c2_close < c1_high)
        bear_c3    = (c3_close < c3_open) and (c1_low <= c3_close <= c1_high)
        bear_disp  = c3_body >= avg_b * disp_mult

        if bear_sweep and bear_c3 and bear_disp:
            entry = c3_close
            sl    = c2_high + atr_v * 0.3
            tp1   = c1_low
            tp2   = c1_low - c1_range * 0.5
            risk  = sl - entry
            rr1   = (entry - tp1) / risk if risk > 0 else 0
            if rr1 >= 1.5:
                patterns.append({
                    "type": "CRT_BEAR",
                    "direction": "SELL",
                    "c1_idx": c1_idx, "c2_idx": c2_idx, "c3_idx": c3_idx,
                    "c1_high": c1_high, "c1_low": c1_low,
                    "sweep_side": "HIGH", "sweep_price": c2_high,
                    "entry": entry, "sl": sl,
                    "tp1": tp1, "tp2": tp2, "rr1": rr1,
                    "range_atr": c1_range / atr_v,
                    "displacement": c3_body / avg_b,
                    "c3_time": df.index[c3_idx],
                    "c1_time": df.index[c1_idx],
                    "c2_time": df.index[c2_idx],
                })

    patterns.sort(key=lambda p: p["c3_idx"], reverse=True)
    return patterns


def _build_checklist(pattern, bias, killzone_ok, killzone):
    direction = pattern["direction"]

    if direction == "BUY":
        bias_aligned = bias["d1_trend"] == "BULLISH" or bias["h4_trend"] == "BULLISH"
    else:
        bias_aligned = bias["d1_trend"] == "BEARISH" or bias["h4_trend"] == "BEARISH"

    checklist = [
        {"label": "HTF Bias Aligned", "pass": bias_aligned,
         "detail": f"D1={bias['d1_trend']}, H4={bias['h4_trend']}"},
        {"label": "C1 Range Adequate", "pass": pattern["range_atr"] >= 1.0,
         "detail": f"{pattern['range_atr']:.1f}x ATR"},
        {"label": f"C2 Swept {pattern['sweep_side']}", "pass": True,
         "detail": f"@ {pattern['sweep_price']:.2f}"},
        {"label": "C3 Closed Inside C1", "pass": True,
         "detail": f"Range {pattern['c1_low']:.2f} – {pattern['c1_high']:.2f}"},
        {"label": "C3 Displacement", "pass": pattern["displacement"] >= 1.3,
         "detail": f"{pattern['displacement']:.1f}x avg body"},
        {"label": "R:R ≥ 2.0", "pass": pattern["rr1"] >= 2.0,
         "detail": f"1 : {pattern['rr1']:.2f}"},
        {"label": "In Killzone", "pass": killzone_ok,
         "detail": killzone},
    ]

    pts = sum(1 for c in checklist if c["pass"])
    if pts >= 6:
        confidence = "HIGH"
    elif pts >= 4:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return checklist, pts, confidence


def analyze(symbol, timeframe="15m"):
    df, live_px, src = scanner_engine.fetch_data_with_live(symbol, timeframe)
    if df is None:
        return {"error": f"Could not fetch {symbol} {timeframe}: {src}"}

    last_close = _safe_float(df["close"].values[-1])
    current_px = live_px if (live_px and live_px > 0) else last_close

    bias = scanner_engine.get_bias(symbol)
    killzone = scanner_engine.current_killzone(symbol)
    killzone_ok = scanner_engine.in_killzone(symbol)

    patterns = detect_crt_patterns(df, timeframe, lookback=20)

    active_signal = None
    for p in patterns:
        candles_since = (len(df) - 1) - p["c3_idx"]
        if candles_since <= 2:
            checklist, pts, confidence = _build_checklist(p, bias, killzone_ok, killzone)
            p_copy = dict(p)
            p_copy["checklist"] = checklist
            p_copy["score"] = pts
            p_copy["confidence"] = confidence
            active_signal = p_copy
            break

    tail = df.tail(400)
    candles = []
    for idx, row in tail.iterrows():
        try:
            t = int(idx.timestamp())
            if t <= 0:
                continue
            candles.append({
                "time": t,
                "open": _safe_float(row["open"]),
                "high": _safe_float(row["high"]),
                "low": _safe_float(row["low"]),
                "close": _safe_float(row["close"]),
            })
        except Exception:
            continue

    patterns_out = []
    for p in patterns[:5]:
        p_out = dict(p)
        p_out.pop("c1_idx", None)
        p_out.pop("c2_idx", None)
        p_out.pop("c3_idx", None)
        for k in ("c1_time", "c2_time", "c3_time"):
            try:
                p_out[k] = int(p[k].timestamp())
            except Exception:
                p_out[k] = 0
        patterns_out.append(p_out)

    active_out = None
    if active_signal:
        active_out = dict(active_signal)
        for k in ("c1_idx", "c2_idx", "c3_idx"):
            active_out.pop(k, None)
        for k in ("c1_time", "c2_time", "c3_time"):
            try:
                active_out[k] = int(active_signal[k].timestamp())
            except Exception:
                active_out[k] = 0

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "data_source": src,
        "last_price": last_close,
        "live_price": live_px,
        "candles": candles,
        "patterns": patterns_out,
        "active_signal": active_out,
        "bias": bias,
        "killzone": killzone,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }