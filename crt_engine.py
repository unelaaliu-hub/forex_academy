"""
CRT (Candle Range Theory) Engine — separate from ICT scanner.
Detects 3-candle patterns: C1 (range) → C2 (sweep) → C3 (distribution).
UPDATED: Fixed 4-value unpacking; structural TPs with real 1:2 R:R gate
         (patterns whose structural target cannot deliver >= 2R are rejected).
         Detects on closed candles only; tz-naive index localized to UTC.
FIXED:   Removed unreachable c1_idx guard; emit rr2/tp2 in output;
         added rejection_reason capture for the R:R gate; live_px now used
         for invalidation/target status; type hints; dedup documented as
         defensive; unified MIN_RR constant (still 2.0 — do not lower).
         Counter-trend downgrade applied in checklist.
FIXED:   itertuples Index-attribute regression in the candle tail — replaced
         with direct zip over index + OHLC arrays so a named index
         (df.index.name set by the fetcher) cannot silently drop every candle.
NOTE:    C2 sweep now requires the close back inside C1's range
         (c1_low < c2_close < c1_high). This rejects wide-outside-bar sweeps
         and will tighten pattern counts vs. prior builds.
NOTE:    C1 range gate and range_atr are now measured against ATR at C1's
         index (the regime that produced the range) rather than C3's, so
         range_atr values may differ from prior builds during vol shifts.
NOTE:    active_signal now includes score_max (checklist length, currently 4)
         so consumers can scale confidence display without hard-coding.
POLISH:  Added top-level schema_version (2) for client-side compatibility
         checks; added logger.debug on the R:R rejection path (fires whether
         or not the rejections out-param is supplied); documented the
         patterns/active_signal overlap; hardened tz guard so a non-
         DatetimeIndex cannot raise AttributeError.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd
from datetime import datetime, timezone

import scanner_engine  # reuse candle fetcher, bias, killzone, swings


logger = logging.getLogger(__name__)


CRT_TF_PARAMS = {
    "15m": {"min_range_atr": 0.8, "displacement_mult": 1.2},
    "1h":  {"min_range_atr": 0.8, "displacement_mult": 1.2},
    "4h":  {"min_range_atr": 0.9, "displacement_mult": 1.2},
    "1d":  {"min_range_atr": 1.0, "displacement_mult": 1.2},
}

# Minimum R:R required on the structural TP1. Keep at 2.0 — lowering this
# defeats the point of the structural-TP gate.
MIN_RR = 2.0


def _atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except Exception:
        return default


def detect_crt_patterns(
    df: pd.DataFrame,
    timeframe: str = "15m",
    lookback: int = 20,
    rejections: Optional[list] = None,
) -> list[dict]:
    params = CRT_TF_PARAMS.get(timeframe, CRT_TF_PARAMS["15m"])
    min_range_atr = params["min_range_atr"]
    disp_mult = params["displacement_mult"]

    if len(df) < 25:
        return []

    atr = _atr_series(df, 14)
    body = (df["close"] - df["open"]).abs()
    avg_body = body.rolling(20).mean()

    # Precompute numpy views for fast scalar access inside the loop, and a
    # positional list of Timestamps so index access is unambiguous regardless
    # of index type.
    highs = df["high"].to_numpy(dtype=np.float64, na_value=np.nan)
    lows = df["low"].to_numpy(dtype=np.float64, na_value=np.nan)
    opens = df["open"].to_numpy(dtype=np.float64, na_value=np.nan)
    closes = df["close"].to_numpy(dtype=np.float64, na_value=np.nan)
    atr_np = atr.to_numpy(dtype=np.float64, na_value=np.nan)
    avg_body_np = avg_body.to_numpy(dtype=np.float64, na_value=np.nan)
    ts_list = df.index.to_list()

    patterns = []
    # Warmup: 20 bars for avg_body, plus 2 bars of C1/C2 history before C3.
    start = max(20 + 2, len(df) - lookback)

    for c3_idx in range(start, len(df)):
        c2_idx = c3_idx - 1
        c1_idx = c3_idx - 2

        # C1 range gate is measured against ATR at C1's time (the regime
        # that produced the range), not C3's.
        atr_c1 = atr_np[c1_idx]
        atr_c3 = atr_np[c3_idx]
        avg_b = avg_body_np[c3_idx]

        if not (
            np.isfinite(atr_c1) and atr_c1 > 0
            and np.isfinite(atr_c3) and atr_c3 > 0
            and np.isfinite(avg_b) and avg_b > 0
        ):
            continue

        c1_high = highs[c1_idx]
        c1_low  = lows[c1_idx]
        c1_range = c1_high - c1_low
        if c1_range < atr_c1 * min_range_atr:
            continue

        c2_high  = highs[c2_idx]
        c2_low   = lows[c2_idx]
        c2_close = closes[c2_idx]

        c3_open  = opens[c3_idx]
        c3_close = closes[c3_idx]
        c3_body  = abs(c3_close - c3_open)

        # ---------------- BULLISH CRT ----------------
        # C2 must sweep C1 low and close back inside C1's range.
        bull_sweep = (c2_low < c1_low) and (c1_low < c2_close < c1_high)
        bull_c3    = (c3_close > c3_open) and (c1_low <= c3_close <= c1_high)
        bull_disp  = c3_body >= avg_b * disp_mult

        if bull_sweep and bull_c3 and bull_disp:
            entry = c3_close
            # SL: below C2 sweep low, floored at 0.5 ATR below entry.
            sl = min(c2_low - atr_c3 * 0.3, entry - atr_c3 * 0.5)
            risk = entry - sl

            if risk > 0:
                tp1 = c1_high
                tp2 = c1_high + c1_range
                rr1 = (tp1 - entry) / risk
                rr2 = (tp2 - entry) / risk

                if rr1 >= MIN_RR:
                    patterns.append({
                        "type": "CRT_BULL",
                        "direction": "BUY",
                        "c1_idx": c1_idx, "c2_idx": c2_idx, "c3_idx": c3_idx,
                        "c1_high": float(c1_high), "c1_low": float(c1_low),
                        "sweep_side": "LOW", "sweep_price": float(c2_low),
                        "entry": float(entry), "sl": float(sl),
                        "tp1": float(tp1), "tp2": float(tp2),
                        "rr1": float(rr1), "rr2": float(rr2),
                        "range_atr": float(c1_range / atr_c1),
                        "displacement": float(c3_body / avg_b),
                        "c3_time": ts_list[c3_idx],
                        "c1_time": ts_list[c1_idx],
                        "c2_time": ts_list[c2_idx],
                    })
                else:
                    logger.debug(
                        "CRT_BULL rejected @ c3_idx=%d c3_time=%s: "
                        "rr1 %.2f < %.1f",
                        c3_idx, ts_list[c3_idx], rr1, MIN_RR,
                    )
                    if rejections is not None:
                        rejections.append({
                            "type": "CRT_BULL",
                            "c3_idx": c3_idx,
                            "c3_time": ts_list[c3_idx],
                            "reason": f"rr1 {rr1:.2f} < {MIN_RR:.1f}",
                            "rr1": float(rr1),
                            "entry": float(entry), "sl": float(sl),
                            "tp1": float(tp1),
                        })

        # ---------------- BEARISH CRT ----------------
        # C2 must sweep C1 high and close back inside C1's range.
        bear_sweep = (c2_high > c1_high) and (c1_low < c2_close < c1_high)
        bear_c3    = (c3_close < c3_open) and (c1_low <= c3_close <= c1_high)
        bear_disp  = c3_body >= avg_b * disp_mult

        if bear_sweep and bear_c3 and bear_disp:
            entry = c3_close
            # SL: above C2 sweep high, floored at 0.5 ATR above entry.
            sl = max(c2_high + atr_c3 * 0.3, entry + atr_c3 * 0.5)
            risk = sl - entry

            if risk > 0:
                tp1 = c1_low
                tp2 = c1_low - c1_range
                rr1 = (entry - tp1) / risk
                rr2 = (entry - tp2) / risk

                if rr1 >= MIN_RR:
                    patterns.append({
                        "type": "CRT_BEAR",
                        "direction": "SELL",
                        "c1_idx": c1_idx, "c2_idx": c2_idx, "c3_idx": c3_idx,
                        "c1_high": float(c1_high), "c1_low": float(c1_low),
                        "sweep_side": "HIGH", "sweep_price": float(c2_high),
                        "entry": float(entry), "sl": float(sl),
                        "tp1": float(tp1), "tp2": float(tp2),
                        "rr1": float(rr1), "rr2": float(rr2),
                        "range_atr": float(c1_range / atr_c1),
                        "displacement": float(c3_body / avg_b),
                        "c3_time": ts_list[c3_idx],
                        "c1_time": ts_list[c1_idx],
                        "c2_time": ts_list[c2_idx],
                    })
                else:
                    logger.debug(
                        "CRT_BEAR rejected @ c3_idx=%d c3_time=%s: "
                        "rr1 %.2f < %.1f",
                        c3_idx, ts_list[c3_idx], rr1, MIN_RR,
                    )
                    if rejections is not None:
                        rejections.append({
                            "type": "CRT_BEAR",
                            "c3_idx": c3_idx,
                            "c3_time": ts_list[c3_idx],
                            "reason": f"rr1 {rr1:.2f} < {MIN_RR:.1f}",
                            "rr1": float(rr1),
                            "entry": float(entry), "sl": float(sl),
                            "tp1": float(tp1),
                        })

    # Defensive dedup — kept for forward-compatibility (if C3 ever stops being
    # at a fixed offset, multiple confirmations of the same C1 could appear).
    # Sorted by C3 recency so the freshest confirmation wins.
    seen = set()
    deduped = []
    for p in sorted(patterns, key=lambda x: x["c3_idx"], reverse=True):
        key = (p["c1_idx"], p["direction"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    return deduped


def _build_checklist(
    pattern: dict,
    bias: dict,
    killzone_ok: bool,
    killzone: str,
) -> tuple[list[dict], int, str]:
    direction = pattern["direction"]

    d1 = bias.get("d1_trend", "NEUTRAL")
    h4 = bias.get("h4_trend", "NEUTRAL")

    if direction == "BUY":
        bias_aligned = d1 == "BULLISH" or h4 == "BULLISH"
    else:
        bias_aligned = d1 == "BEARISH" or h4 == "BEARISH"

    # Note: C2 sweep, C3-inside-C1, and the R:R ≥ MIN_RR gate are enforced
    # during detection and would always pass here, so they are excluded from
    # the checklist and score. The score is driven by the four real
    # conditional items below. The displacement threshold (1.3x) is
    # intentionally stricter than the detection param (1.2x) so borderline
    # patterns score lower rather than being silently dropped.
    checklist = [
        {"label": "HTF Bias Aligned", "pass": bias_aligned,
         "detail": f"D1={d1}, H4={h4}"},
        {"label": "C1 Range Adequate", "pass": pattern["range_atr"] >= 1.0,
         "detail": f"{pattern['range_atr']:.1f}x ATR"},
        {"label": "C3 Displacement", "pass": pattern["displacement"] >= 1.3,
         "detail": f"{pattern['displacement']:.1f}x avg body"},
        {"label": "In Killzone", "pass": killzone_ok,
         "detail": killzone},
    ]

    pts = sum(1 for c in checklist if c["pass"])
    if pts >= 3:
        confidence = "HIGH"
    elif pts >= 2:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # NEW RULE: If HTF Bias fails, never allow HIGH confidence.
    if not bias_aligned:
        if confidence == "HIGH":
            confidence = "MEDIUM"
        elif confidence == "MEDIUM":
            confidence = "LOW"

    return checklist, pts, confidence


def _signal_status(pattern: dict, live_px: float) -> dict:
    """Return live-price status relative to the signal's levels."""
    if live_px <= 0:
        return {
            "status": "UNKNOWN",
            "distance_to_entry": None,
            "distance_to_sl": None,
            "invalidated": False,
        }

    direction = pattern["direction"]
    entry = pattern["entry"]
    sl = pattern["sl"]
    tp1 = pattern["tp1"]

    if direction == "BUY":
        invalidated = live_px <= sl
        past_tp1 = live_px >= tp1
    else:
        invalidated = live_px >= sl
        past_tp1 = live_px <= tp1

    if invalidated:
        status = "INVALIDATED"
    elif past_tp1:
        status = "TARGET_HIT"
    else:
        status = "ACTIVE"

    if status == "ACTIVE":
        distance_to_entry = abs(live_px - entry)
        distance_to_sl = abs(live_px - sl)
    else:
        distance_to_entry = None
        distance_to_sl = None

    return {
        "status": status,
        "distance_to_entry": distance_to_entry,
        "distance_to_sl": distance_to_sl,
        "invalidated": invalidated,
    }


def analyze(symbol: str, timeframe: str = "15m") -> dict:
    # 4-value unpacking
    df, live_px, src, _ = scanner_engine.fetch_data_with_live(symbol, timeframe)

    if df is None:
        return {"error": f"Could not fetch {symbol} {timeframe}: {src}"}

    # Ensure tz-aware index so .timestamp() yields correct UTC epoch.
    # Guarded by isinstance so a non-DatetimeIndex cannot raise AttributeError
    # here (it will simply be left as-is and the downstream int(...timestamp())
    # conversions will drop unusable rows via their existing try/except).
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")

    last_close = _safe_float(df["close"].values[-1])

    bias = scanner_engine.get_bias(symbol)
    killzone = scanner_engine.current_killzone(symbol)
    killzone_ok = scanner_engine.in_killzone(symbol)

    # Drop the currently-forming candle before detection so signals
    # never fire on an unclosed bar.
    df_confirmed = df.iloc[:-1]

    rejections: list = []
    patterns = detect_crt_patterns(
        df_confirmed, timeframe, lookback=20, rejections=rejections
    )

    # Note: active_signal is a dict(p) copy of one of the patterns entries.
    # It shares c3_time / entry / sl / tp levels with the corresponding
    # item in patterns_out — clients rendering both lists should expect a
    # single-signal overlap, not two independent signals.
    active_signal = None
    for p in patterns:
        candles_since = (len(df_confirmed) - 1) - p["c3_idx"]
        if candles_since <= 2:
            checklist, pts, confidence = _build_checklist(
                p, bias, killzone_ok, killzone
            )
            p_copy = dict(p)
            p_copy["checklist"] = checklist
            p_copy["score"] = pts
            p_copy["score_max"] = len(checklist)
            p_copy["confidence"] = confidence
            p_copy["live"] = _signal_status(p, _safe_float(live_px))
            active_signal = p_copy
            break

    tail = df.tail(400)
    candles = []
    # Zip index + OHLC arrays directly. Do NOT use itertuples(index=True):
    # pandas names the index field after df.index.name when set, so
    # row.Index would raise AttributeError and silently drop every candle.
    for t_val, o, h, l, c in zip(
        tail.index,
        tail["open"].to_numpy(),
        tail["high"].to_numpy(),
        tail["low"].to_numpy(),
        tail["close"].to_numpy(),
    ):
        try:
            t = int(t_val.timestamp())
            if t <= 0:
                continue
            candles.append({
                "time": t,
                "open": _safe_float(o),
                "high": _safe_float(h),
                "low": _safe_float(l),
                "close": _safe_float(c),
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

    rejections_out = []
    for r in rejections[:5]:
        r_out = dict(r)
        r_out.pop("c3_idx", None)
        try:
            r_out["c3_time"] = int(r["c3_time"].timestamp())
        except Exception:
            r_out["c3_time"] = 0
        rejections_out.append(r_out)

    return {
        "schema_version": 2,
        "symbol": symbol,
        "timeframe": timeframe,
        "data_source": src,
        "last_price": last_close,
        "live_price": live_px,
        "candles": candles,
        "patterns": patterns_out,
        "active_signal": active_out,
        "rejections": rejections_out,
        "bias": bias,
        "killzone": killzone,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }