"""
Live Chart Scanner — OANDA with yfinance fallback
Adds: Sweep detection, PDH/PDL, Session H/L, Displacement, Dealing Range
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OANDA_API_KEY = "7601c3626d13d6a4cbcc8a96a91b01aa-9e0a98d2f5cba0b6e3d2a5905c61e91a"
OANDA_BASE_URL = "https://api-fxpractice.oanda.com"

INSTRUMENT_MAP = {
    "XAUUSD": "XAU_USD", "EURUSD": "EUR_USD", "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY", "AUDUSD": "AUD_USD", "BTCUSD": "BTC_USD", "ETHUSD": "ETH_USD",
}
GRANULARITY_MAP = {"15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}

YF_FALLBACK = {
    "XAUUSD": "GC=F", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD",
}
YF_PERIOD = {"15m": "60d", "1h": "180d", "4h": "365d", "1d": "5y"}

CRYPTO_SYMBOLS = {"BTCUSD", "ETHUSD"}

_df_cache = {}
_bias_cache = {}
_forming_cache = {}

def _cache_get(cache, key, ttl):
    if key in cache:
        ts, val = cache[key]
        if time.time() - ts < ttl:
            return val
    return None

def _cache_set(cache, key, val):
    cache[key] = (time.time(), val)


TF_PARAMS = {
    "15m": {"swing_left": 3, "swing_right": 3, "ob_impulse_mult": 1.2, "ob_range_mult": 1.0,
            "min_ob_quality": 1.1, "min_fvg_quality": 0.8, "max_pools": 6,
            "pool_min_range_pct": 0.05, "ob_max_size_atr_mult": 2.5},
    "1h": {"swing_left": 4, "swing_right": 4, "ob_impulse_mult": 1.3, "ob_range_mult": 1.1,
           "min_ob_quality": 1.1, "min_fvg_quality": 0.9, "max_pools": 5,
           "pool_min_range_pct": 0.10, "ob_max_size_atr_mult": 2.5},
    "4h": {"swing_left": 5, "swing_right": 5, "ob_impulse_mult": 1.5, "ob_range_mult": 1.2,
           "min_ob_quality": 1.2, "min_fvg_quality": 1.0, "max_pools": 4,
           "pool_min_range_pct": 0.20, "ob_max_size_atr_mult": 2.5},
    "1d": {"swing_left": 7, "swing_right": 7, "ob_impulse_mult": 1.7, "ob_range_mult": 1.4,
           "min_ob_quality": 1.4, "min_fvg_quality": 1.2, "max_pools": 3,
           "pool_min_range_pct": 0.30, "ob_max_size_atr_mult": 2.5},
}

def tf_params(timeframe):
    return TF_PARAMS.get(timeframe, TF_PARAMS["15m"])

def safe_float(v, default=0.0):
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except Exception:
        return default

def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def oanda_request(url, headers, params=None, retries=2):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=8, verify=True)
            r.raise_for_status()
            return r
        except (requests.exceptions.ConnectionError,
                requests.exceptions.SSLError,
                requests.exceptions.Timeout):
            if attempt == 0:
                try:
                    r = requests.get(url, headers=headers, params=params, timeout=8, verify=False)
                    r.raise_for_status()
                    return r
                except Exception:
                    pass
            time.sleep(0.5)
        except requests.exceptions.HTTPError:
            raise
        except Exception:
            time.sleep(0.5)
    return None


def fetch_from_oanda(symbol, timeframe):
    instrument = INSTRUMENT_MAP.get(symbol.upper())
    granularity = GRANULARITY_MAP.get(timeframe, "H1")
    if not instrument:
        return None, None, "Unknown symbol"

    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    url = f"{OANDA_BASE_URL}/v3/instruments/{instrument}/candles"
    params = {"granularity": granularity, "count": 500, "price": "M"}

    r = oanda_request(url, headers, params)
    if r is None:
        return None, None, "OANDA connection failed"

    try:
        candles_raw = r.json().get("candles", [])
    except Exception as e:
        return None, None, f"Invalid JSON: {e}"

    if not candles_raw:
        return None, None, "No candles returned"

    rows = []
    live_price = None
    for c in candles_raw:
        mid = c.get("mid", {})
        if not mid:
            continue
        complete = c.get("complete", True)
        if not complete:
            p = safe_float(mid.get("c", 0))
            if p > 0:
                live_price = p
                try:
                    _forming_cache[(symbol.upper(), timeframe)] = {
                        "time": pd.to_datetime(c["time"]),
                        "open": safe_float(mid["o"]),
                        "high": safe_float(mid["h"]),
                        "low": safe_float(mid["l"]),
                        "close": safe_float(mid["c"]),
                    }
                except Exception:
                    pass
            continue
        try:
            rows.append({
                "time": pd.to_datetime(c["time"]),
                "open": safe_float(mid["o"]),
                "high": safe_float(mid["h"]),
                "low": safe_float(mid["l"]),
                "close": safe_float(mid["c"]),
            })
        except Exception:
            continue

    if len(rows) < 50:
        return None, None, "Not enough OANDA candles"

    df = pd.DataFrame(rows).set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="last")].dropna()
    return df, live_price, "OANDA"


def fetch_from_yahoo(symbol, timeframe):
    try:
        import yfinance as yf
    except ImportError:
        return None, None, "yfinance not installed"

    ticker = YF_FALLBACK.get(symbol.upper())
    period = YF_PERIOD.get(timeframe, "180d")
    if not ticker:
        return None, None, "No fallback ticker"

    try:
        df = yf.download(ticker, period=period, interval=timeframe,
                         progress=False, auto_adjust=False)
        if df is None or df.empty or len(df) < 50:
            return None, None, "Yahoo returned no data"
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close"]].dropna()
        if len(df) < 50:
            return None, None, "Yahoo not enough data"
        return df, None, "YAHOO (fallback)"
    except Exception as e:
        return None, None, f"Yahoo error: {str(e)[:100]}"


def fetch_data_with_live(symbol, timeframe):
    df, live_px, src = fetch_from_oanda(symbol, timeframe)
    if df is not None:
        return df, live_px, src
    df2, _, src2 = fetch_from_yahoo(symbol, timeframe)
    if df2 is not None:
        last_close = safe_float(df2["close"].values[-1])
        return df2, last_close, src2
    return None, None, f"{src} / {src2}"


def fetch_data(symbol, timeframe):
    df, _, src = fetch_data_with_live(symbol, timeframe)
    return df, src

def fetch_live_price(symbol, timeframe):
    _, live_px, _ = fetch_data_with_live(symbol, timeframe)
    return live_px

def fetch_data_cached(symbol, timeframe, ttl=300):
    key = f"{symbol}_{timeframe}"
    cached = _cache_get(_df_cache, key, ttl)
    if cached is not None:
        return cached, "CACHE"
    df, _, src = fetch_data_with_live(symbol, timeframe)
    if df is not None:
        _cache_set(_df_cache, key, df)
    return df, src


def find_swings(df, timeframe):
    params = tf_params(timeframe)
    left = params["swing_left"]
    right = params["swing_right"]

    df = df.copy()
    sh = np.zeros(len(df), dtype=bool)
    sl = np.zeros(len(df), dtype=bool)
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    for i in range(left, n - right):
        window_h = highs[i - left:i + right + 1]
        window_l = lows[i - left:i + right + 1]
        if highs[i] == window_h.max() and highs[i] > highs[i - 1]:
            sh[i] = True
        if lows[i] == window_l.min() and lows[i] < lows[i - 1]:
            sl[i] = True

    df["sh"] = sh
    df["sl"] = sl
    return df


def detect_structure(df):
    sh_idx = [i for i, v in enumerate(df["sh"].values) if v]
    sl_idx = [i for i, v in enumerate(df["sl"].values) if v]
    sh_prices = [safe_float(df["high"].values[i]) for i in sh_idx]
    sl_prices = [safe_float(df["low"].values[i]) for i in sl_idx]

    if len(sh_prices) < 2 or len(sl_prices) < 2:
        return {"trend": "RANGING", "swing_highs": [], "swing_lows": [],
                "bos": None, "choch": None, "last_high": None,
                "last_low": None, "prev_high": None, "prev_low": None}

    highs = sh_prices[-3:]
    lows = sl_prices[-3:]
    hh = len(highs) >= 2 and highs[-1] > highs[-2]
    hl = len(lows) >= 2 and lows[-1] > lows[-2]
    lh = len(highs) >= 2 and highs[-1] < highs[-2]
    ll = len(lows) >= 2 and lows[-1] < lows[-2]

    if hl and not ll:
        trend = "BULLISH"
    elif lh and not hh:
        trend = "BEARISH"
    elif hh and hl:
        trend = "BULLISH"
    elif lh and ll:
        trend = "BEARISH"
    else:
        trend = "RANGING"

    last_high = safe_float(highs[-1]) if highs else None
    prev_high = safe_float(highs[-2]) if len(highs) >= 2 else None
    last_low = safe_float(lows[-1]) if lows else None
    prev_low = safe_float(lows[-2]) if len(lows) >= 2 else None
    last_close = safe_float(df["close"].values[-1])

    bos = None
    choch = None
    if trend == "BULLISH" and last_high and last_close > last_high:
        bos = {"type": "BOS_BULL", "level": last_high}
    elif trend == "BEARISH" and last_low and last_close < last_low:
        bos = {"type": "BOS_BEAR", "level": last_low}
    elif trend == "BULLISH" and prev_low and last_close < prev_low:
        choch = {"type": "CHOCH_BEAR", "level": prev_low}
    elif trend == "BEARISH" and prev_high and last_close > prev_high:
        choch = {"type": "CHOCH_BULL", "level": prev_high}

    sh_out = [{"index": safe_int(sh_idx[i]), "price": safe_float(sh_prices[i])}
              for i in range(max(0, len(sh_idx) - 10), len(sh_idx))]
    sl_out = [{"index": safe_int(sl_idx[i]), "price": safe_float(sl_prices[i])}
              for i in range(max(0, len(sl_idx) - 10), len(sl_idx))]

    return {
        "trend": trend, "last_high": last_high, "prev_high": prev_high,
        "last_low": last_low, "prev_low": prev_low,
        "bos": bos, "choch": choch,
        "swing_highs": sh_out, "swing_lows": sl_out,
    }


def detect_mss(df, structure):
    sh = structure["swing_highs"]
    sl = structure["swing_lows"]
    if not sh or not sl:
        return None

    last_close = safe_float(df["close"].values[-1])
    last_high = sh[-1]
    prev_high_price = structure.get("prev_high")
    last_low = sl[-1]
    prev_low_price = structure.get("prev_low")

    mss = None

    if last_low["index"] > last_high["index"]:
        if last_close > last_high["price"]:
            mss = {"type": "MSS_BULL", "level": safe_float(last_high["price"]),
                   "description": f"Price closed above swing high at {last_high['price']:.2f} — bullish reversal confirmed"}
        elif prev_high_price and last_close > prev_high_price:
            mss = {"type": "MSS_BULL", "level": safe_float(prev_high_price),
                   "description": f"Price closed above prior swing high at {prev_high_price:.2f} — early bullish shift"}
    elif last_high["index"] > last_low["index"]:
        if last_close < last_low["price"]:
            mss = {"type": "MSS_BEAR", "level": safe_float(last_low["price"]),
                   "description": f"Price closed below swing low at {last_low['price']:.2f} — bearish reversal confirmed"}
        elif prev_low_price and last_close < prev_low_price:
            mss = {"type": "MSS_BEAR", "level": safe_float(prev_low_price),
                   "description": f"Price closed below prior swing low at {prev_low_price:.2f} — early bearish shift"}

    return mss


def detect_recent_sweep(df, structure, lookback=40):
    sh = structure.get("swing_highs", [])
    sl = structure.get("swing_lows", [])
    if not sh and not sl:
        return None

    n = len(df)
    if n < 5:
        return None
    start = max(1, n - lookback)

    recent_sweeps = []

    for i in range(start, n):
        row = df.iloc[i]
        close = safe_float(row["close"])
        high = safe_float(row["high"])
        low = safe_float(row["low"])
        if close <= 0:
            continue

        for h in sh:
            hi_idx = safe_int(h["index"])
            if hi_idx >= i:
                continue
            swept_price = safe_float(h["price"])
            if swept_price <= 0:
                continue
            if high > swept_price and close < swept_price:
                recent_sweeps.append({
                    "type": "BSL_SWEEP", "index": i,
                    "swept_price": swept_price, "swing_index": hi_idx,
                })
                break

        for l in sl:
            li_idx = safe_int(l["index"])
            if li_idx >= i:
                continue
            swept_price = safe_float(l["price"])
            if swept_price <= 0:
                continue
            if low < swept_price and close > swept_price:
                recent_sweeps.append({
                    "type": "SSL_SWEEP", "index": i,
                    "swept_price": swept_price, "swing_index": li_idx,
                })
                break

    if not recent_sweeps:
        return None
    return recent_sweeps[-1]


def get_previous_day_hl(symbol):
    try:
        d1_df, _ = fetch_data_cached(symbol, "1d", ttl=1800)
        if d1_df is None or len(d1_df) < 2:
            return None, None
        now_utc_date = datetime.now(timezone.utc).date()
        for i in range(len(d1_df) - 1, -1, -1):
            ts = d1_df.index[i]
            ts_date = None
            try:
                ts_date = ts.date() if hasattr(ts, "date") else None
            except Exception:
                ts_date = None
            if ts_date is not None and ts_date < now_utc_date:
                row = d1_df.iloc[i]
                return safe_float(row["high"]), safe_float(row["low"])
    except Exception:
        pass
    return None, None


def get_session_ranges(df, symbol="", timeframe="15m"):
    """
    Today's session ranges (Asia / London / NY) in UTC.
    Returns dict: { "ASIA": {high, low, start, end, ongoing}, ... }
    Only for 15m and 1h (not meaningful on higher TFs).
    """
    if symbol.upper() in CRYPTO_SYMBOLS:
        return {}
    if timeframe in ("4h", "1d"):
        return {}

    try:
        ts0 = df.index[0]
        if getattr(ts0, "tzinfo", None) is None:
            idx_ts = np.array(
                [int(t.replace(tzinfo=timezone.utc).timestamp()) for t in df.index],
                dtype=np.int64,
            )
        else:
            idx_ts = np.array([int(t.timestamp()) for t in df.index], dtype=np.int64)
    except Exception:
        return {}

    now_utc = datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ts = int(today_start.timestamp())

    sessions_def = {
        "ASIA":   (0, 8),
        "LONDON": (7, 16),
        "NY":     (12, 21),
    }

    highs = df["high"].values
    lows = df["low"].values

    result = {}
    for name, (start_h, end_h) in sessions_def.items():
        start_ts = today_start_ts + start_h * 3600
        end_ts = today_start_ts + end_h * 3600

        if now_ts < start_ts:
            continue

        effective_end_ts = min(end_ts, now_ts)
        mask = (idx_ts >= start_ts) & (idx_ts < effective_end_ts)
        if not mask.any():
            continue

        result[name] = {
            "high": safe_float(np.max(highs[mask])),
            "low":  safe_float(np.min(lows[mask])),
            "start": int(start_ts),
            "end":   int(effective_end_ts),
            "ongoing": bool(now_ts < end_ts),
        }

    return result


def compute_dealing_range(df, structure, timeframe):
    """
    Find the most recent significant impulse leg — the dealing range.
    Walks backward through swings, finds first alternating pair >= 3 ATR apart.
    Falls back to last 100-candle H/L if no such pair exists.
    """
    atr = safe_float((df["high"] - df["low"]).rolling(14).mean().iloc[-1], 0.5)
    if atr <= 0:
        return None

    sh = structure.get("swing_highs", [])
    sl = structure.get("swing_lows", [])

    swings = []
    for h in sh:
        swings.append({"index": safe_int(h["index"]), "price": safe_float(h["price"]), "type": "H"})
    for l in sl:
        swings.append({"index": safe_int(l["index"]), "price": safe_float(l["price"]), "type": "L"})
    swings.sort(key=lambda s: s["index"])

    if len(swings) >= 2:
        for i in range(len(swings) - 1, 0, -1):
            a = swings[i]
            for j in range(i - 1, -1, -1):
                b = swings[j]
                if b["type"] == a["type"]:
                    continue
                distance = abs(a["price"] - b["price"])
                if distance >= atr * 3.0:
                    high = max(a["price"], b["price"])
                    low = min(a["price"], b["price"])
                    return {
                        "high": high, "low": low, "mid": (high + low) / 2,
                        "start_index": b["index"], "end_index": a["index"],
                        "size_atr": distance / atr,
                    }

    n = len(df)
    sub = df.iloc[max(0, n - 100):]
    high = safe_float(sub["high"].max())
    low = safe_float(sub["low"].min())
    if high > low:
        return {"high": high, "low": low, "mid": (high + low) / 2,
                "start_index": max(0, n - 100), "end_index": n - 1,
                "size_atr": (high - low) / atr}
    return None


def count_ob_touches(df, impulse_index, ob_top, ob_bottom):
    touches = 0
    in_zone = False
    start = min(impulse_index + 1, len(df) - 1)
    for i in range(start, len(df)):
        row = df.iloc[i]
        touched = (row["low"] <= ob_top) and (row["high"] >= ob_bottom)
        if touched and not in_zone:
            touches += 1
            in_zone = True
        elif not touched:
            in_zone = False
    return touches


def find_liquidity_pools(df, structure, timeframe):
    params = tf_params(timeframe)
    min_range_pct = params["pool_min_range_pct"]
    max_pools = params["max_pools"]

    pools = []
    sh = structure["swing_highs"]
    sl = structure["swing_lows"]
    last_price = safe_float(df["close"].values[-1])

    def swing_qualifies(price_a, price_b):
        if price_a == 0:
            return False
        diff_pct = abs(price_a - price_b) / price_a * 100
        return diff_pct < 0.15

    def swing_is_significant(price):
        if price == 0 or last_price == 0:
            return False
        return abs(price - last_price) / last_price * 100 >= min_range_pct

    bsl_candidates = [s for s in sh if s["price"] > last_price and swing_is_significant(s["price"])]
    for i in range(len(bsl_candidates)):
        for j in range(i + 1, len(bsl_candidates)):
            if swing_qualifies(bsl_candidates[i]["price"], bsl_candidates[j]["price"]):
                pools.append({"type": "BSL",
                              "price": max(bsl_candidates[i]["price"], bsl_candidates[j]["price"]),
                              "index": max(bsl_candidates[i]["index"], bsl_candidates[j]["index"]),
                              "label": "Buy-side Liquidity"})
                break

    ssl_candidates = [s for s in sl if s["price"] < last_price and swing_is_significant(s["price"])]
    for i in range(len(ssl_candidates)):
        for j in range(i + 1, len(ssl_candidates)):
            if swing_qualifies(ssl_candidates[i]["price"], ssl_candidates[j]["price"]):
                pools.append({"type": "SSL",
                              "price": min(ssl_candidates[i]["price"], ssl_candidates[j]["price"]),
                              "index": max(ssl_candidates[i]["index"], ssl_candidates[j]["index"]),
                              "label": "Sell-side Liquidity"})
                break

    bsl_out = sorted([p for p in pools if p["type"] == "BSL"],
                     key=lambda p: p["price"])[:max_pools]
    ssl_out = sorted([p for p in pools if p["type"] == "SSL"],
                     key=lambda p: p["price"], reverse=True)[:max_pools]

    return bsl_out + ssl_out


def _ranges_overlap(a_top, a_bottom, b_top, b_bottom, threshold=0.5):
    a_hi, a_lo = max(a_top, a_bottom), min(a_top, a_bottom)
    b_hi, b_lo = max(b_top, b_bottom), min(b_top, b_bottom)
    overlap_hi = min(a_hi, b_hi)
    overlap_lo = max(a_lo, b_lo)
    if overlap_hi <= overlap_lo:
        return False
    overlap_size = overlap_hi - overlap_lo
    a_size = a_hi - a_lo
    b_size = b_hi - b_lo
    min_size = min(a_size, b_size)
    if min_size <= 0:
        return True
    return (overlap_size / min_size) >= threshold


def find_order_blocks(df, timeframe, lookback=300):
    params = tf_params(timeframe)
    impulse_mult = params["ob_impulse_mult"]
    range_mult = params["ob_range_mult"]
    min_quality = params["min_ob_quality"]
    max_ob_atr = params["ob_max_size_atr_mult"]

    obs = []
    body = (df["close"] - df["open"]).abs()
    avg_body = body.rolling(30).mean()
    atr = (df["high"] - df["low"]).rolling(14).mean()
    start = max(4, len(df) - lookback)

    for i in range(start, len(df)):
        c = df.iloc[i]
        a = avg_body.iloc[i]
        atr_v = atr.iloc[i]
        if pd.isna(a) or pd.isna(atr_v) or a == 0:
            continue
        cur_body = abs(c["close"] - c["open"])
        cur_range = c["high"] - c["low"]

        if not (cur_body > a * impulse_mult and cur_range > atr_v * range_mult):
            continue

        impulse_bullish = c["close"] > c["open"]

        ob_idx = None
        for k in range(1, 4):
            j = i - k
            if j < 0:
                break
            candidate = df.iloc[j]
            candidate_bullish = candidate["close"] > candidate["open"]
            if impulse_bullish and not candidate_bullish:
                ob_idx = j
                break
            elif not impulse_bullish and candidate_bullish:
                ob_idx = j
                break

        if ob_idx is None:
            continue

        ob_candle = df.iloc[ob_idx]
        impulse_ratio = safe_float(cur_body / a)

        if impulse_ratio < min_quality:
            continue

        ob_top = safe_float(ob_candle["high"])
        ob_bottom = safe_float(ob_candle["low"])

        if (ob_top - ob_bottom) > atr_v * max_ob_atr:
            continue

        touches = count_ob_touches(df, i, ob_top, ob_bottom)

        if impulse_bullish:
            obs.append({"type": "OB_BULL", "index": ob_idx, "impulse_index": i,
                        "top": ob_top, "bottom": ob_bottom,
                        "label": "Bullish OB", "impulse": impulse_ratio,
                        "quality": impulse_ratio, "touches": touches})
        else:
            obs.append({"type": "OB_BEAR", "index": ob_idx, "impulse_index": i,
                        "top": ob_top, "bottom": ob_bottom,
                        "label": "Bearish OB", "impulse": impulse_ratio,
                        "quality": impulse_ratio, "touches": touches})

    kept = []
    bull_count = 0
    bear_count = 0

    for ob in reversed(obs):
        if ob["type"] == "OB_BULL" and bull_count >= 2:
            continue
        if ob["type"] == "OB_BEAR" and bear_count >= 2:
            continue

        overlaps_any = False
        for k in kept:
            if _ranges_overlap(ob["top"], ob["bottom"], k["top"], k["bottom"]):
                overlaps_any = True
                break
        if overlaps_any:
            continue

        kept.append(ob)
        if ob["type"] == "OB_BULL":
            bull_count += 1
        else:
            bear_count += 1

        if bull_count >= 2 and bear_count >= 2:
            break

    return kept


def find_fvgs(df, timeframe, lookback=300):
    params = tf_params(timeframe)
    min_quality = params["min_fvg_quality"]

    fvgs = []
    atr_series = (df["high"] - df["low"]).rolling(14).mean()
    start = max(2, len(df) - lookback)
    for i in range(start, len(df)):
        c1 = df.iloc[i - 2]
        c3 = df.iloc[i]
        atr_v = safe_float(atr_series.iloc[i], 1.0)
        if c1["high"] < c3["low"]:
            size = safe_float(c3["low"] - c1["high"])
            quality = size / atr_v if atr_v > 0 else 0
            if quality >= min_quality and quality <= 3.0:
                fvgs.append({"type": "FVG_BULL", "index": i - 1,
                             "top": safe_float(c3["low"]), "bottom": safe_float(c1["high"]),
                             "label": "Bullish FVG", "size": size, "quality": quality,
                             "touches": 0})
        if c1["low"] > c3["high"]:
            size = safe_float(c1["low"] - c3["high"])
            quality = size / atr_v if atr_v > 0 else 0
            if quality >= min_quality and quality <= 3.0:
                fvgs.append({"type": "FVG_BEAR", "index": i - 1,
                             "top": safe_float(c1["low"]), "bottom": safe_float(c3["high"]),
                             "label": "Bearish FVG", "size": size, "quality": quality,
                             "touches": 0})

    fresh = []
    for fvg in reversed(fvgs):
        filled = False
        for j in range(fvg["index"] + 2, len(df)):
            row = df.iloc[j]
            if row["low"] <= fvg["bottom"] and row["high"] >= fvg["top"]:
                filled = True
                break
        if not filled:
            fresh.append(fvg)
        if len(fresh) >= 2:
            break
    return fresh


def premium_discount(df, structure, current_price=None, dealing_range=None):
    if dealing_range:
        high = safe_float(dealing_range["high"])
        low = safe_float(dealing_range["low"])
    else:
        sh = structure["swing_highs"]
        sl = structure["swing_lows"]
        if not sh or not sl:
            return None
        high = safe_float(sh[-1]["price"])
        low = safe_float(sl[-1]["price"])

    if high <= low:
        return None
    mid = (high + low) / 2
    px = safe_float(current_price) if (current_price and current_price > 0) else safe_float(df["close"].values[-1])
    return {"high": high, "low": low, "mid": mid,
            "position": "PREMIUM" if px > mid else "DISCOUNT",
            "current_price": px}


def current_killzone(symbol=""):
    if symbol.upper() in CRYPTO_SYMBOLS:
        return "CRYPTO 24/7 🔥"
    now_utc = datetime.now(timezone.utc)
    now_est = now_utc - timedelta(hours=5)
    hour = now_est.hour
    if 2 <= hour < 5:
        return "LONDON KILLZONE 🔥"
    elif 8 <= hour < 11:
        return "NY AM KILLZONE 🔥"
    elif 13 <= hour < 16:
        return "NY PM KILLZONE"
    elif 20 <= hour < 24 or hour < 1:
        return "ASIAN SESSION"
    else:
        return "OFF-SESSION"


def in_killzone(symbol=""):
    if symbol.upper() in CRYPTO_SYMBOLS:
        return True
    kz = current_killzone(symbol)
    return "KILLZONE" in kz


def get_bias(symbol):
    cached = _cache_get(_bias_cache, symbol, 300)
    if cached is not None:
        return cached

    d1_trend = "RANGING"
    h4_trend = "RANGING"
    try:
        d1_df, _ = fetch_data_cached(symbol, "1d", ttl=1800)
        if d1_df is not None and len(d1_df) >= 50:
            d1_df = find_swings(d1_df, "1d")
            d1_trend = detect_structure(d1_df)["trend"]
    except Exception:
        pass

    try:
        h4_df, _ = fetch_data_cached(symbol, "4h", ttl=300)
        if h4_df is not None and len(h4_df) >= 50:
            h4_df = find_swings(h4_df, "4h")
            h4_trend = detect_structure(h4_df)["trend"]
    except Exception:
        pass

    strong_buy  = (d1_trend == "BULLISH" and h4_trend == "BULLISH")
    strong_sell = (d1_trend == "BEARISH" and h4_trend == "BEARISH")
    weak_buy    = (d1_trend == "BULLISH" and h4_trend == "RANGING") or \
                  (d1_trend == "RANGING" and h4_trend == "BULLISH")
    weak_sell   = (d1_trend == "BEARISH" and h4_trend == "RANGING") or \
                  (d1_trend == "RANGING" and h4_trend == "BEARISH")
    conflict    = (d1_trend == "BULLISH" and h4_trend == "BEARISH") or \
                  (d1_trend == "BEARISH" and h4_trend == "BULLISH")
    neutral     = (d1_trend == "RANGING" and h4_trend == "RANGING")

    allow_buy  = strong_buy  or weak_buy  or neutral
    allow_sell = strong_sell or weak_sell or neutral

    if strong_buy:
        tier, reason = "STRONG", "D1 + H4 both BULLISH — strong BUY bias"
    elif strong_sell:
        tier, reason = "STRONG", "D1 + H4 both BEARISH — strong SELL bias"
    elif weak_buy:
        tier, reason = "WEAK", f"Partial BULLISH (D1={d1_trend}, H4={h4_trend}) — BUY at reduced size"
    elif weak_sell:
        tier, reason = "WEAK", f"Partial BEARISH (D1={d1_trend}, H4={h4_trend}) — SELL at reduced size"
    elif conflict:
        tier, reason = "CONFLICT", f"HTF CONFLICT (D1={d1_trend}, H4={h4_trend}) — no trade"
    else:
        tier, reason = "NEUTRAL", "D1 + H4 both RANGING — range conditions, MSS required"

    result = {"d1_trend": d1_trend, "h4_trend": h4_trend,
              "allow_buy": allow_buy, "allow_sell": allow_sell,
              "tier": tier, "reason": reason}
    _cache_set(_bias_cache, symbol, result)
    return result


def pick_tp_swing(structure, entry, direction):
    highs = sorted([h["price"] for h in structure.get("swing_highs", []) if h["price"] > 0])
    lows = sorted([l["price"] for l in structure.get("swing_lows", []) if l["price"] > 0])

    if direction == "BUY":
        targets = [p for p in highs if p > entry]
        if targets:
            tp1 = targets[0]
            tp2 = targets[1] if len(targets) >= 2 else entry + (tp1 - entry) * 1.5
            return tp1, tp2
        return None, None
    else:
        targets = sorted([p for p in lows if p < entry], reverse=True)
        if targets:
            tp1 = targets[0]
            tp2 = targets[1] if len(targets) >= 2 else entry - (entry - tp1) * 1.5
            return tp1, tp2
        return None, None


def build_checklist(direction, mss, bias, pd, killzone_ok, zone, rr, sweep=None):
    checklist = []

    # HTF Bias
    bias_tier = bias.get("tier", "NEUTRAL") if bias else "UNKNOWN"
    d1 = bias.get("d1_trend") if bias else "RANGING"
    h4 = bias.get("h4_trend") if bias else "RANGING"
    aligned = (direction == "BUY" and d1 == "BULLISH") or \
              (direction == "SELL" and d1 == "BEARISH")
    if bias_tier == "STRONG" and aligned:
        checklist.append({"label": "HTF Bias", "pass": True, "detail": f"STRONG — D1+H4 {d1}"})
    elif bias_tier == "WEAK" and aligned:
        checklist.append({"label": "HTF Bias", "pass": True, "detail": f"WEAK — {d1}/{h4}"})
    else:
        checklist.append({"label": "HTF Bias", "pass": False, "detail": f"{bias_tier} — {d1}/{h4}"})

    # MSS
    mss_ok = bool(mss) and (
        (mss["type"] == "MSS_BULL" and direction == "BUY") or
        (mss["type"] == "MSS_BEAR" and direction == "SELL")
    )
    checklist.append({
        "label": "MSS Confirmed", "pass": mss_ok,
        "detail": (mss["type"].replace("MSS_", "") if mss else "No MSS")
    })

    # Sweep
    wanted_sweep = "SSL_SWEEP" if direction == "BUY" else "BSL_SWEEP"
    sweep_ok = bool(sweep) and sweep.get("type") == wanted_sweep
    sweep_label = "Sweep of SSL" if direction == "BUY" else "Sweep of BSL"
    if sweep:
        side = "SSL" if sweep["type"] == "SSL_SWEEP" else "BSL"
        sweep_detail = f"{side} @ {sweep['swept_price']:.2f}"
        if not sweep_ok:
            sweep_detail += f" (wrong side for {direction})"
    else:
        sweep_detail = "No recent sweep"
    checklist.append({
        "label": sweep_label, "pass": sweep_ok, "detail": sweep_detail
    })

    # PD
    want = "DISCOUNT" if direction == "BUY" else "PREMIUM"
    pd_ok = bool(pd) and pd["position"] == want
    checklist.append({
        "label": f"Price in {want}", "pass": pd_ok,
        "detail": (pd["position"] if pd else "Unknown")
    })

    # Killzone
    checklist.append({
        "label": "In Killzone", "pass": bool(killzone_ok),
        "detail": "Active" if killzone_ok else "Outside session"
    })

    # Displacement (renamed from Zone Quality, stricter threshold)
    q = safe_float(zone.get("quality", 0))
    checklist.append({
        "label": "Displacement", "pass": q >= 2.0,
        "detail": f"{q:.1f}x avg body"
    })

    # Freshness
    touches = safe_int(zone.get("touches", 0))
    checklist.append({
        "label": "OB Fresh", "pass": touches <= 1,
        "detail": "Untapped" if touches == 0 else f"Tapped {touches}x"
    })

    # R:R
    checklist.append({
        "label": "R:R >= 2.0", "pass": rr >= 2.0,
        "detail": f"1 : {rr:.2f}"
    })

    return checklist


def generate_scenarios(df, structure, obs, fvgs, pools, pd, timeframe, mss=None, symbol="",
                       bias=None, killzone_ok=True, sweep=None):
    atr = safe_float((df["high"] - df["low"]).rolling(14).mean().iloc[-1], 0.5)
    current_price = safe_float(df["close"].values[-1])

    tolerance = atr * 0.5
    min_risk = atr * 0.5
    max_risk = atr * 5.0

    if not bias:
        return None, None

    bias_tier = bias.get("tier", "NEUTRAL")

    if bias_tier == "CONFLICT":
        return None, None

    if bias_tier == "NEUTRAL" and not mss:
        return None, None

    allow_buy = bias["allow_buy"]
    allow_sell = bias["allow_sell"]

    def build_setup(direction, zone, entry, sl, tp1, tp2):
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        reward_1 = abs(tp1 - entry)
        rr = reward_1 / risk

        if rr < 2.0:
            return None

        if bias_tier == "NEUTRAL":
            mss_ok = mss and (
                (mss["type"] == "MSS_BULL" and direction == "BUY") or
                (mss["type"] == "MSS_BEAR" and direction == "SELL")
            )
            if not mss_ok:
                return None

        checklist = build_checklist(direction, mss, bias, pd, killzone_ok, zone, rr, sweep=sweep)

        pts = 0
        for c in checklist:
            if not c["pass"]:
                continue
            lbl = c["label"]
            if lbl == "MSS Confirmed":
                pts += 3
            elif lbl == "HTF Bias":
                pts += 2
            elif lbl.startswith("Sweep of"):
                pts += 2
            elif lbl == "In Killzone":
                pts += 2
            elif lbl.startswith("Price in"):
                pts += 2
            elif lbl == "Displacement":
                pts += 2
            elif lbl == "OB Fresh":
                pts += 2
            elif lbl.startswith("R:R"):
                pts += 1

        if pts >= 10:
            confidence = "HIGH"
        elif pts >= 6:
            confidence = "MEDIUM"
        elif pts >= 4:
            confidence = "LOW"
        else:
            return None

        if bias_tier in ("NEUTRAL", "WEAK") and confidence == "HIGH":
            confidence = "MEDIUM"

        reasons = [f"{'✅' if c['pass'] else '⚠️'} {c['label']} — {c['detail']}" for c in checklist]

        return {
            "direction": direction,
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr,
            "zone_type": zone["label"], "confidence": confidence,
            "zone_quality": safe_float(zone.get("quality", 0)),
            "checklist": checklist,
            "score": pts,
            "reasons": reasons,
            "sl_basis": "zone edge", "tp_basis": "next swing"
        }

    buy_setup = None
    sell_setup = None

    if allow_buy:
        bull_zones = sorted(
            [o for o in obs if o["type"] == "OB_BULL"] +
            [f for f in fvgs if f["type"] == "FVG_BULL"],
            key=lambda z: (z.get("quality", 0), z["top"]), reverse=True
        )
        for zone in bull_zones:
            if safe_int(zone.get("touches", 0)) > 1:
                continue

            entry = safe_float(zone["top"])
            zone_bottom = safe_float(zone["bottom"])

            if not ((zone_bottom - tolerance) <= current_price <= (entry + tolerance)):
                continue

            sl = zone_bottom - atr * 0.15
            risk = entry - sl
            if risk < min_risk:
                sl = entry - min_risk
                risk = min_risk
            if risk > max_risk:
                continue

            tp1, tp2 = pick_tp_swing(structure, entry, "BUY")
            if tp1 is None:
                continue

            setup = build_setup("BUY", zone, entry, sl, tp1, tp2)
            if setup:
                buy_setup = setup
                break

    if allow_sell:
        bear_zones = sorted(
            [o for o in obs if o["type"] == "OB_BEAR"] +
            [f for f in fvgs if f["type"] == "FVG_BEAR"],
            key=lambda z: (z.get("quality", 0), -z["bottom"]), reverse=True
        )
        for zone in bear_zones:
            if safe_int(zone.get("touches", 0)) > 1:
                continue

            entry = safe_float(zone["bottom"])
            zone_top = safe_float(zone["top"])

            if not ((entry - tolerance) <= current_price <= (zone_top + tolerance)):
                continue

            sl = zone_top + atr * 0.15
            risk = sl - entry
            if risk < min_risk:
                sl = entry + min_risk
                risk = min_risk
            if risk > max_risk:
                continue

            tp1, tp2 = pick_tp_swing(structure, entry, "SELL")
            if tp1 is None:
                continue

            setup = build_setup("SELL", zone, entry, sl, tp1, tp2)
            if setup:
                sell_setup = setup
                break

    return buy_setup, sell_setup


def compute_correlations(symbols, timeframe='1h', lookback=200):
    series = {}
    for sym in symbols:
        df, _ = fetch_data(sym, timeframe)
        if df is not None and len(df) >= 50:
            closes = df['close'].tail(lookback).values
            series[sym] = closes

    if len(series) < 2:
        return {'error': 'Not enough data to compute correlations'}

    min_len = min(len(v) for v in series.values())
    if min_len < 30:
        return {'error': 'Not enough overlapping data'}

    for k in series:
        series[k] = series[k][-min_len:]

    syms = list(series.keys())
    matrix = []
    for a in syms:
        row = []
        for b in syms:
            try:
                c = np.corrcoef(series[a], series[b])[0, 1]
                row.append(round(float(c), 2))
            except Exception:
                row.append(0.0)
        matrix.append(row)

    return {'symbols': syms, 'matrix': matrix, 'timeframe': timeframe}


def analyze(symbol, timeframe):
    df, live_px, used_source = fetch_data_with_live(symbol, timeframe)
    if df is None:
        return {"error": f"Could not fetch {symbol} {timeframe}: {used_source}"}

    last_closed = safe_float(df["close"].values[-1])
    current_px = live_px if (live_px and live_px > 0) else last_closed

    df = find_swings(df, timeframe)
    structure = detect_structure(df)
    mss = detect_mss(df, structure)
    sweep = detect_recent_sweep(df, structure, lookback=40)
    pools = find_liquidity_pools(df, structure, timeframe)
    obs = find_order_blocks(df, timeframe)
    fvgs = find_fvgs(df, timeframe)

    dealing_range = compute_dealing_range(df, structure, timeframe)
    pd_zones = premium_discount(df, structure, current_px, dealing_range=dealing_range)

    pdh, pdl = get_previous_day_hl(symbol)
    sessions = get_session_ranges(df, symbol, timeframe)

    killzone_ok = in_killzone(symbol)
    bias = get_bias(symbol)

    buy_setup, sell_setup = generate_scenarios(
        df, structure, obs, fvgs, pools, pd_zones, timeframe, mss,
        symbol=symbol, bias=bias, killzone_ok=killzone_ok, sweep=sweep
    )
    killzone = current_killzone(symbol)

    df_tail = df.tail(400)
    candles = []
    for idx, row in df_tail.iterrows():
        try:
            t = safe_int(idx.timestamp())
            if t <= 0:
                continue
            o, h, l, c = (safe_float(row["open"]), safe_float(row["high"]),
                          safe_float(row["low"]), safe_float(row["close"]))
            if o <= 0 or c <= 0:
                continue
            candles.append({"time": t, "open": o, "high": h, "low": l, "close": c})
        except Exception:
            continue

    fc = _forming_cache.get((symbol.upper(), timeframe))
    if fc is not None:
        try:
            t = safe_int(fc["time"].timestamp())
            if t > 0:
                candles.append({
                    "time": t,
                    "open": safe_float(fc["open"]),
                    "high": safe_float(fc["high"]),
                    "low": safe_float(fc["low"]),
                    "close": safe_float(fc["close"]),
                })
        except Exception:
            pass

    sh_markers = []
    for h in structure["swing_highs"]:
        i = safe_int(h["index"])
        if 0 <= i < len(df):
            try:
                t = safe_int(df.index[i].timestamp())
                if t > 0:
                    sh_markers.append({"time": t, "price": safe_float(h["price"])})
            except Exception:
                continue

    sl_markers = []
    for l in structure["swing_lows"]:
        i = safe_int(l["index"])
        if 0 <= i < len(df):
            try:
                t = safe_int(df.index[i].timestamp())
                if t > 0:
                    sl_markers.append({"time": t, "price": safe_float(l["price"])})
            except Exception:
                continue

    def idx_to_ts(i):
        i = max(0, min(safe_int(i), len(df) - 1))
        try:
            t = safe_int(df.index[i].timestamp())
            return t if t > 0 else None
        except Exception:
            return None

    ob_out = []
    for z in obs:
        t = idx_to_ts(z["index"])
        if t is None:
            continue
        ob_out.append({"type": z["type"], "top": safe_float(z["top"]),
                       "bottom": safe_float(z["bottom"]), "time": t,
                       "label": z["label"], "strength": safe_float(z.get("impulse", 0)),
                       "touches": safe_int(z.get("touches", 0)),
                       "kind": "OB"})

    fvg_out = []
    for z in fvgs:
        t = idx_to_ts(z["index"])
        if t is None:
            continue
        fvg_out.append({"type": z["type"], "top": safe_float(z["top"]),
                        "bottom": safe_float(z["bottom"]), "time": t,
                        "label": z["label"], "size": safe_float(z.get("size", 0)),
                        "kind": "FVG"})

    pools_out = []
    for p in pools:
        t = idx_to_ts(p["index"])
        if t is None:
            continue
        pools_out.append({"type": p["type"], "price": safe_float(p["price"]),
                          "time": t, "label": p["label"]})

    mss_out = None
    if mss:
        mss_out = {
            "type": mss["type"],
            "level": safe_float(mss["level"]),
            "description": mss["description"],
            "time": safe_int(df.index[-1].timestamp())
        }

    sweep_out = None
    if sweep:
        sweep_out = {
            "type": sweep["type"],
            "time": idx_to_ts(sweep["index"]),
            "swept_price": safe_float(sweep["swept_price"]),
            "index": safe_int(sweep["index"]),
        }

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "ticker": used_source,
        "last_price": last_closed,
        "live_price": live_px,
        "structure": structure,
        "bias": bias,
        "mss": mss_out,
        "sweep": sweep_out,
        "pdh": pdh,
        "pdl": pdl,
        "sessions": sessions,
        "dealing_range": dealing_range,
        "pools": pools_out,
        "obs": ob_out,
        "fvgs": fvg_out,
        "premium_discount": pd_zones,
        "buy_setup": buy_setup,
        "sell_setup": sell_setup,
        "killzone": killzone,
        "candles": candles,
        "swing_high_markers": sh_markers,
        "swing_low_markers": sl_markers,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": used_source
    }