"""
Synthetic Indices Engine — Deriv official python-deriv-api
Lazy-import deriv_api so gunicorn startup isn't blocked.
"""
import json
import time
import math
import asyncio
import numpy as np
import pandas as pd
from datetime import datetime, timezone

# NOTE: deriv_api is imported lazily inside the fetch function to avoid
# blocking gunicorn startup on Render's free tier.
DerivAPI = None

# App ID 1089 is Deriv's demo app id (works for public data)
APP_ID = 1089

SYNTHETIC_SYMBOLS = {
    "R_10":     {"name": "Volatility 10",  "kind": "reversion"},
    "R_25":     {"name": "Volatility 25",  "kind": "reversion"},
    "R_50":     {"name": "Volatility 50",  "kind": "reversion"},
    "R_75":     {"name": "Volatility 75",  "kind": "reversion"},
    "R_100":    {"name": "Volatility 100", "kind": "reversion"},
    "JD10":     {"name": "Jump 10",        "kind": "reversion"},
    "JD25":     {"name": "Jump 25",        "kind": "reversion"},
    "JD50":     {"name": "Jump 50",        "kind": "reversion"},
    "JD75":     {"name": "Jump 75",        "kind": "reversion"},
    "JD100":    {"name": "Jump 100",       "kind": "reversion"},
    "BOOM500":  {"name": "Boom 500",       "kind": "spike"},
    "BOOM1000": {"name": "Boom 1000",      "kind": "spike"},
    "CRASH500": {"name": "Crash 500",      "kind": "spike"},
    "CRASH1000":{"name": "Crash 1000",     "kind": "spike"},
    "STPRNG":   {"name": "Step Index",     "kind": "step"},
}

TF_GRANULARITY = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400,
}

_cache = {}
CACHE_TTL = 45


def _cache_get(key):
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return val
    return None


def _cache_set(key, val):
    _cache[key] = (time.time(), val)


def _safe_float(v, default=0.0):
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


async def _fetch_candles_async(symbol, timeframe, count=500):
    """Fetch candles via the official python-deriv-api library."""
    # Lazy import — keeps gunicorn startup fast
    try:
        from deriv_api import DerivAPI as _DerivAPI
    except ImportError:
        return None, "python-deriv-api not installed"
    DerivAPI = _DerivAPI

    if symbol not in SYNTHETIC_SYMBOLS:
        return None, f"Unknown symbol {symbol}"

    gran = TF_GRANULARITY.get(timeframe, 300)

    api = None
    try:
        api = DerivAPI(app_id=APP_ID)
        response = await api.ticks_history({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "start": 1,
            "style": "candles",
            "granularity": gran,
        })

        if response is None:
            return None, "Empty response from Deriv"

        if isinstance(response, dict) and "error" in response:
            err = response["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return None, f"Deriv error: {msg}"

        candles = response.get("candles") if isinstance(response, dict) else None
        if not candles or len(candles) < 50:
            return None, "Not enough candles"

        rows = []
        for c in candles:
            try:
                rows.append({
                    "time": pd.to_datetime(int(c["epoch"]), unit="s", utc=True),
                    "open": _safe_float(c["open"]),
                    "high": _safe_float(c["high"]),
                    "low": _safe_float(c["low"]),
                    "close": _safe_float(c["close"]),
                })
            except Exception:
                continue

        if len(rows) < 50:
            return None, "Parsed rows insufficient"

        df = pd.DataFrame(rows).set_index("time").sort_index()
        df = df[~df.index.duplicated(keep="last")].dropna()
        return df, "DERIV"

    except Exception as e:
        return None, f"API error: {type(e).__name__}: {str(e)[:200]}"
    finally:
        if api is not None:
            try:
                await api.clear()
            except Exception:
                pass


def fetch_candles(symbol, timeframe, count=500):
    """Synchronous wrapper for Flask."""
    key = f"{symbol}_{timeframe}_{count}"
    cached = _cache_get(key)
    if cached is not None:
        return cached, "CACHE"

    loop = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        df, src = loop.run_until_complete(_fetch_candles_async(symbol, timeframe, count))
    except Exception as e:
        return None, f"Loop error: {str(e)[:150]}"
    finally:
        if loop is not None:
            try:
                loop.close()
            except Exception:
                pass

    if df is not None:
        _cache_set(key, df)
    return df, src


def compute_indicators(df, bb_period=20, bb_std=2.0, rsi_period=14, atr_period=14):
    df = df.copy()
    df["ma"] = df["close"].rolling(bb_period).mean()
    df["sd"] = df["close"].rolling(bb_period).std()
    df["bb_up"] = df["ma"] + bb_std * df["sd"]
    df["bb_dn"] = df["ma"] - bb_std * df["sd"]

    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(atr_period).mean()

    return df


def _last(df, col):
    try:
        v = df[col].iloc[-1]
        return _safe_float(v, 0.0)
    except Exception:
        return 0.0


def _efficiency_ratio(closes, n=20):
    if len(closes) < n + 1:
        return 0.0
    window = closes[-(n + 1):]
    net = abs(window[-1] - window[0])
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    return net / path if path > 0 else 0.0


def _consecutive_beyond_band(df, side, max_check=6):
    count = 0
    for i in range(len(df) - 1, max(len(df) - 1 - max_check, 0), -1):
        c = _safe_float(df["close"].iloc[i])
        u = _safe_float(df["bb_up"].iloc[i])
        l = _safe_float(df["bb_dn"].iloc[i])
        if side == "upper" and u > 0 and c > u:
            count += 1
        elif side == "lower" and l > 0 and c < l:
            count += 1
        else:
            break
    return count


def _rejection_candle(df):
    if len(df) < 2:
        return None
    last = df.iloc[-1]
    o = _safe_float(last["open"])
    h = _safe_float(last["high"])
    l = _safe_float(last["low"])
    c = _safe_float(last["close"])
    body = abs(c - o)
    rng = h - l
    if rng <= 0:
        return None
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    if upper_wick > body * 1.5 and upper_wick > rng * 0.5:
        return "bearish"
    if lower_wick > body * 1.5 and lower_wick > rng * 0.5:
        return "bullish"
    return None


def _rsi_divergence(df, direction):
    if len(df) < 4:
        return False
    try:
        h_now = _safe_float(df["high"].iloc[-1])
        h_prev = _safe_float(df["high"].iloc[-2])
        l_now = _safe_float(df["low"].iloc[-1])
        l_prev = _safe_float(df["low"].iloc[-2])
        rsi_now = _safe_float(df["rsi"].iloc[-1])
        rsi_prev = _safe_float(df["rsi"].iloc[-2])
        if direction == "SELL":
            return h_now > h_prev and rsi_now < rsi_prev
        else:
            return l_now < l_prev and rsi_now > rsi_prev
    except Exception:
        return False


def strategy_reversion(df, symbol):
    df = compute_indicators(df)
    if len(df) < 50:
        return None

    close = _last(df, "close")
    ma = _last(df, "ma")
    up = _last(df, "bb_up")
    dn = _last(df, "bb_dn")
    sd = _last(df, "sd")
    rsi = _last(df, "rsi")
    atr = _last(df, "atr")

    if ma <= 0 or sd <= 0 or atr <= 0 or close <= 0:
        return None

    er = _efficiency_ratio(df["close"].values, n=20)
    if er > 0.50:
        return None

    bandwidth_pct = ((up - dn) / ma) * 100 if ma > 0 else 0
    if bandwidth_pct < 0.10:
        return None

    direction = None
    if close > up and rsi >= 68:
        direction = "SELL"
    elif close < dn and rsi <= 32:
        direction = "BUY"
    if not direction:
        return None

    side = "upper" if direction == "SELL" else "lower"
    consec_beyond = _consecutive_beyond_band(df, side, max_check=6)
    if consec_beyond > 3:
        return None

    rejection = _rejection_candle(df)
    rejection_ok = (rejection == "bearish" and direction == "SELL") or \
                   (rejection == "bullish" and direction == "BUY")
    divergence_ok = _rsi_divergence(df, direction)

    rsi_now = _safe_float(df["rsi"].iloc[-1])
    rsi_prev = _safe_float(df["rsi"].iloc[-2])
    rsi_turning = (rsi_now < rsi_prev) if direction == "SELL" else (rsi_now > rsi_prev)

    z = (close - ma) / sd if sd > 0 else 0

    entry = close
    if direction == "BUY":
        sl = entry - 1.8 * atr
        tp1 = ma
        tp2 = up
    else:
        sl = entry + 1.8 * atr
        tp1 = ma
        tp2 = dn

    risk = abs(entry - sl)
    if risk <= 0:
        return None
    reward_1 = abs(tp1 - entry)
    rr = reward_1 / risk
    if rr < 1.5:
        return None

    checklist = []
    if direction == "SELL":
        checklist.append({"label": "Above Upper Band", "pass": True, "detail": f"{close:.2f} > {up:.2f}"})
        checklist.append({"label": "RSI Overbought", "pass": rsi >= 68, "detail": f"RSI {rsi:.1f}"})
        checklist.append({"label": "Z-Score >= 2.0", "pass": z >= 2.0, "detail": f"z = {z:.2f}"})
        checklist.append({"label": "Not Trending", "pass": er <= 0.40, "detail": f"ER = {er:.2f}"})
        checklist.append({"label": "Fresh Band Touch", "pass": consec_beyond <= 2, "detail": f"{consec_beyond} bar(s) beyond"})
        checklist.append({"label": "Reversal Confirmation", "pass": rejection_ok or divergence_ok,
                          "detail": ("Bearish wick" if rejection_ok else ("RSI divergence" if divergence_ok else "None"))})
        checklist.append({"label": "RSI Turning Down", "pass": rsi_turning, "detail": f"{rsi_prev:.1f} -> {rsi_now:.1f}"})
    else:
        checklist.append({"label": "Below Lower Band", "pass": True, "detail": f"{close:.2f} < {dn:.2f}"})
        checklist.append({"label": "RSI Oversold", "pass": rsi <= 32, "detail": f"RSI {rsi:.1f}"})
        checklist.append({"label": "Z-Score <= -2.0", "pass": z <= -2.0, "detail": f"z = {z:.2f}"})
        checklist.append({"label": "Not Trending", "pass": er <= 0.40, "detail": f"ER = {er:.2f}"})
        checklist.append({"label": "Fresh Band Touch", "pass": consec_beyond <= 2, "detail": f"{consec_beyond} bar(s) beyond"})
        checklist.append({"label": "Reversal Confirmation", "pass": rejection_ok or divergence_ok,
                          "detail": ("Bullish wick" if rejection_ok else ("RSI divergence" if divergence_ok else "None"))})
        checklist.append({"label": "RSI Turning Up", "pass": rsi_turning, "detail": f"{rsi_prev:.1f} -> {rsi_now:.1f}"})

    checklist.append({"label": "R:R >= 1.5", "pass": rr >= 1.5, "detail": f"1 : {rr:.2f}"})

    pts = sum(1 for c in checklist if c["pass"])
    total = len(checklist)

    if pts >= 7:
        confidence = "HIGH"
    elif pts >= 5:
        confidence = "MEDIUM"
    else:
        return None

    return {
        "direction": direction,
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr,
        "confidence": confidence,
        "strategy": "Mean Reversion",
        "checklist": checklist,
        "score": pts,
        "total": total,
        "note": f"Fade extreme deviation (Z={z:.2f}, ER={er:.2f}). Target midline at {ma:.2f}.",
    }


def strategy_spike(df, symbol, symbol_type):
    df = compute_indicators(df)
    body = (df["close"] - df["open"]).abs()
    avg_body = body.rolling(30).mean()
    atr = _last(df, "atr")
    if atr <= 0:
        return None

    spike_idx = None
    spike_dir = None
    for i in range(len(df) - 5, len(df)):
        if i < 30:
            continue
        b = _safe_float(body.iloc[i])
        a = _safe_float(avg_body.iloc[i])
        if a <= 0:
            continue
        if b > a * 3.0:
            spike_idx = i
            spike_dir = "UP" if df["close"].iloc[i] > df["open"].iloc[i] else "DOWN"

    if spike_idx is None or spike_dir is None:
        return None

    close = _last(df, "close")
    spike_open = _safe_float(df["open"].iloc[spike_idx])
    spike_close = _safe_float(df["close"].iloc[spike_idx])
    spike_range = abs(spike_close - spike_open)

    direction = "SELL" if spike_dir == "UP" else "BUY"

    entry = close
    if direction == "BUY":
        sl = entry - 1.5 * atr
        tp1 = entry + 1.0 * atr
        tp2 = entry + 2.0 * atr
    else:
        sl = entry + 1.5 * atr
        tp1 = entry - 1.0 * atr
        tp2 = entry - 2.0 * atr

    risk = abs(entry - sl)
    if risk <= 0:
        return None
    rr = abs(tp1 - entry) / risk

    checklist = [
        {"label": "Spike Detected", "pass": True, "detail": f"{spike_dir} spike {spike_range:.2f} pts"},
        {"label": "Fade Direction", "pass": True, "detail": f"{direction} into retracement"},
        {"label": "ATR Reasonable", "pass": atr > 0, "detail": f"ATR {atr:.2f}"},
        {"label": "R:R >= 1.5", "pass": rr >= 1.5, "detail": f"1 : {rr:.2f}"},
    ]

    pts = sum(1 for c in checklist if c["pass"])
    confidence = "HIGH" if pts >= 4 else ("MEDIUM" if pts >= 3 else "LOW")
    if confidence == "LOW":
        return None

    return {
        "direction": direction,
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr,
        "confidence": confidence,
        "strategy": "Spike Fade",
        "checklist": checklist,
        "note": "Fade the spike — retracement is the play",
    }


def strategy_step(df, symbol):
    df = compute_indicators(df)
    close = _last(df, "close")
    atr = _last(df, "atr")
    ma = _last(df, "ma")
    if atr <= 0 or ma <= 0:
        return None

    lookback = min(60, len(df) - 1)
    recent = df.iloc[-lookback:]
    range_high = _safe_float(recent["high"].max())
    range_low = _safe_float(recent["low"].min())
    if range_high <= range_low:
        return None

    direction = None
    if close >= range_high * 0.999:
        direction = "SELL"
    elif close <= range_low * 1.001:
        direction = "BUY"

    if not direction:
        return None

    entry = close
    if direction == "BUY":
        sl = entry - 1.5 * atr
        tp1 = ma
        tp2 = range_high
    else:
        sl = entry + 1.5 * atr
        tp1 = ma
        tp2 = range_low

    risk = abs(entry - sl)
    if risk <= 0:
        return None
    rr = abs(tp1 - entry) / risk

    checklist = [
        {"label": "At Range Boundary", "pass": True,
         "detail": f"{'Top' if direction=='SELL' else 'Bottom'} of {lookback}-bar range"},
        {"label": "Range Width", "pass": (range_high - range_low) / atr >= 3,
         "detail": f"{((range_high-range_low)/atr):.1f}x ATR"},
        {"label": "R:R >= 1.5", "pass": rr >= 1.5, "detail": f"1 : {rr:.2f}"},
    ]

    pts = sum(1 for c in checklist if c["pass"])
    confidence = "HIGH" if pts >= 3 else ("MEDIUM" if pts >= 2 else "LOW")
    if confidence == "LOW":
        return None

    return {
        "direction": direction,
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr,
        "confidence": confidence,
        "strategy": "Range Fade",
        "checklist": checklist,
        "note": "Fade the boundary — target the mean",
    }


def analyze(symbol, timeframe="5m"):
    if symbol not in SYNTHETIC_SYMBOLS:
        return {"error": f"Unknown symbol {symbol}"}

    meta = SYNTHETIC_SYMBOLS[symbol]
    df, src = fetch_candles(symbol, timeframe)
    if df is None:
        return {"error": f"Could not fetch {symbol}: {src}"}

    kind = meta["kind"]

    setup = None
    if kind == "reversion":
        setup = strategy_reversion(df, symbol)
    elif kind == "spike":
        setup = strategy_spike(df, symbol, kind)
    elif kind == "step":
        setup = strategy_step(df, symbol)

    tail = df.tail(300)
    candles = []
    for idx, row in tail.iterrows():
        try:
            t = int(idx.timestamp())
            candles.append({
                "time": t,
                "open": _safe_float(row["open"]),
                "high": _safe_float(row["high"]),
                "low": _safe_float(row["low"]),
                "close": _safe_float(row["close"]),
            })
        except Exception:
            continue

    dfi = compute_indicators(df)
    tail_i = dfi.tail(300)
    ma_line, up_line, dn_line = [], [], []
    for idx, row in tail_i.iterrows():
        try:
            t = int(idx.timestamp())
            if pd.notna(row["ma"]):
                ma_line.append({"time": t, "value": _safe_float(row["ma"])})
            if pd.notna(row["bb_up"]):
                up_line.append({"time": t, "value": _safe_float(row["bb_up"])})
            if pd.notna(row["bb_dn"]):
                dn_line.append({"time": t, "value": _safe_float(row["bb_dn"])})
        except Exception:
            continue

    close = _safe_float(df["close"].iloc[-1])

    return {
        "symbol": symbol,
        "name": meta["name"],
        "kind": kind,
        "timeframe": timeframe,
        "data_source": src,
        "last_price": close,
        "setup": setup,
        "candles": candles,
        "ma_line": ma_line,
        "bb_up_line": up_line,
        "bb_dn_line": dn_line,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }