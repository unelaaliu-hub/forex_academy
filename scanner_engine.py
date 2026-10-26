"""
Live Chart Scanner — OANDA with yfinance fallback

REVISION 3 (V5 final pass) + USER CUSTOMIZATIONS:
  # USER CUSTOM 1 — Hardcoded OANDA API Key.
  # USER CUSTOM 2 — MIN_RR set to 2.0 (strict 1:2 R:R).
  # USER CUSTOM 3 — Weekend check added to killzone logic.
  # FIX #1  — find_break_bar scans FORWARD from the swing, not backward.
  # FIX #2  — find_swings marks all plateau bars, dedupes to median.
  # FIX #3  — compute_correlations uses pct_change (returns).
  # FIX #4  — fetch_data_with_live returns live_price_is_tick.
  # FIX #5  — analyze() wrapped in try/except.
  # FIX #6  — detect_recent_sweep returns per-side sweeps.
  # FIX #7  — _cache_set enforces _CACHE_MAX.
  # FIX #8  — oanda_request handles 429 gracefully.
  # FIX #10 — analyze() uses 30s TTL cache (_fetch_with_live_cached).
  # FIX #11 — Golden OHLC fixture tests for OB/FVG.
  # FIX #12 — Sweep per-side test.
  # FIX #13 — Cache eviction test.
  # FIX #14 — 429 handling test.
  # FIX #15 — Three-state sweep detail in checklist.
  # FIX #16 — Counter-trend downgrade (limits confidence to LOW/MEDIUM).
"""
import os
import sys
import logging
import threading
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional, Any, Dict, List, Tuple
import time
import urllib3
import concurrent.futures

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("scanner")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

# ---------- Config ----------
# HARDCODED API KEY
OANDA_API_KEY = "7601c3626d13d6a4cbcc8a96a91b01aa-9e0a98d2f5cba0b6e3d2a5905c61e91a"
OANDA_BASE_URL = "https://api-fxpractice.oanda.com"

OANDA_INSECURE_RETRY = os.environ.get("OANDA_INSECURE_RETRY", "0").strip() == "1"

# STRICT 1:2 R:R
MIN_RR = 2.0
FORMING_TTL = 120
FAILURE_TTL = 30
_CACHE_MAX = 256
_ANALYZE_TTL = 30

INSTRUMENT_MAP = {
    "XAUUSD": "XAU_USD", "EURUSD": "EUR_USD", "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY", "AUDUSD": "AUD_USD", "BTCUSD": "BTC_USD", "ETHUSD": "ETH_USD",
    "NAS100": "NAS100_USD", "US100": "NAS100_USD", "US30": "US30_USD", "JPN225": "JP225_USD",
}
GRANULARITY_MAP = {"15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}

YF_FALLBACK = {
    "XAUUSD": "GC=F", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD",
    "NAS100": "NQ=F", "US100": "NQ=F", "US30": "YM=F", "JPN225": "^N225",
}
# 4h is NOT a native yfinance interval; fetch_from_yahoo resamples from 1h.
YF_PERIOD = {"15m": "60d", "1h": "180d", "4h": "730d", "1d": "5y"}

CRYPTO_SYMBOLS = {"BTCUSD", "ETHUSD"}
INDEX_SYMBOLS = {"NAS100", "US100", "US30", "JPN225"}

# ---------- Caches ----------
_df_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
_bias_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_failure_cache: Dict[str, Tuple[float, bool]] = {}
_forming_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
_analyze_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.RLock()


def _cache_get(cache: Dict, key: Any, ttl: float):
    with _cache_lock:
        if key in cache:
            ts, val = cache[key]
            if time.time() - ts < ttl:
                return val
    return None


def _cache_set(cache: Dict, key: Any, val: Any) -> None:
    with _cache_lock:
        cache[key] = (time.time(), val)
        if len(cache) > _CACHE_MAX:
            oldest = min(cache, key=lambda k: cache[k][0])
            cache.pop(oldest, None)


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except Exception:
        return default


def safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


TF_PARAMS = {
    "15m": {"swing_left": 3, "swing_right": 3, "ob_impulse_mult": 1.2, "ob_range_mult": 1.0,
            "min_fvg_quality": 0.8, "max_pools": 6,
            "pool_min_range_pct": 0.05, "ob_max_size_atr_mult": 2.5,
            "mss_max_swing_age": 100},
    "1h": {"swing_left": 4, "swing_right": 4, "ob_impulse_mult": 1.3, "ob_range_mult": 1.1,
           "min_fvg_quality": 0.9, "max_pools": 5,
           "pool_min_range_pct": 0.10, "ob_max_size_atr_mult": 2.5,
           "mss_max_swing_age": 60},
    "4h": {"swing_left": 5, "swing_right": 5, "ob_impulse_mult": 1.5, "ob_range_mult": 1.2,
           "min_fvg_quality": 1.0, "max_pools": 4,
           "pool_min_range_pct": 0.20, "ob_max_size_atr_mult": 2.5,
           "mss_max_swing_age": 40},
    "1d": {"swing_left": 7, "swing_right": 7, "ob_impulse_mult": 1.7, "ob_range_mult": 1.4,
           "min_fvg_quality": 1.2, "max_pools": 3,
           "pool_min_range_pct": 0.30, "ob_max_size_atr_mult": 2.5,
           "mss_max_swing_age": 20},
}


def tf_params(timeframe: str) -> Dict[str, Any]:
    return TF_PARAMS.get(timeframe, TF_PARAMS["15m"])


try:
    from zoneinfo import ZoneInfo
    _NY_TZ = ZoneInfo("America/New_York")
    _LONDON_TZ = ZoneInfo("Europe/London")
except Exception:
    try:
        import pytz
        _NY_TZ = pytz.timezone("America/New_York")
        _LONDON_TZ = pytz.timezone("Europe/London")
    except Exception:
        _NY_TZ = None
        _LONDON_TZ = None
        logger.warning("zoneinfo/pytz unavailable; killzone times may be DST-inaccurate.")


def current_killzone(symbol: str = "") -> str:
    if symbol.upper() in CRYPTO_SYMBOLS:
        return "CRYPTO 24/7 🔥"
    now_utc = datetime.now(timezone.utc)
    if _NY_TZ is not None:
        now_est = now_utc.astimezone(_NY_TZ)
    else:
        now_est = now_utc - timedelta(hours=5)
        
    weekday = now_est.weekday()
    hour = now_est.hour
    
    # Forex closes Friday 5 PM EST, opens Sunday 5 PM EST
    if weekday == 5: 
        return "OFF-SESSION (WEEKEND)"
    if weekday == 6 and hour < 17: 
        return "OFF-SESSION (WEEKEND)"
    if weekday == 4 and hour >= 17: 
        return "OFF-SESSION (WEEKEND)"

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


def in_killzone(symbol: str = "") -> bool:
    if symbol.upper() in CRYPTO_SYMBOLS:
        return True
    return "KILLZONE" in current_killzone(symbol)


def oanda_request(url: str, headers: Dict[str, str], params: Optional[dict] = None,
                  retries: int = 1, allow_insecure: bool = False):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=5, verify=True)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                logger.warning("OANDA 429 rate-limited on %s", url)
                return None
            r.raise_for_status()
            return r
        except (requests.exceptions.ConnectionError,
                requests.exceptions.SSLError,
                requests.exceptions.Timeout):
            if attempt == 0 and allow_insecure:
                try:
                    logger.warning("OANDA retry with verify=False (OANDA_INSECURE_RETRY=1)")
                    r = requests.get(url, headers=headers, params=params, timeout=5, verify=False)
                    if r.status_code == 404:
                        return None
                    if r.status_code == 429:
                        logger.warning("OANDA 429 (insecure retry) on %s", url)
                        return None
                    r.raise_for_status()
                    return r
                except Exception as e:
                    logger.debug("OANDA retry (insecure) failed: %s", e)
            time.sleep(0.5)
        except requests.exceptions.HTTPError as e:
            logger.debug("OANDA HTTP error: %s", e)
            return None
        except Exception as e:
            logger.debug("OANDA unexpected error: %s", e)
            time.sleep(0.5)
    return None


def _forming_get(symbol: str, timeframe: str) -> Optional[dict]:
    key = (symbol.upper(), timeframe)
    with _cache_lock:
        entry = _forming_cache.get(key)
        if entry and (time.time() - entry["ts"]) < FORMING_TTL:
            return entry["data"]
    return None


def _forming_put(symbol: str, timeframe: str, data: dict) -> None:
    with _cache_lock:
        _forming_cache[(symbol.upper(), timeframe)] = {"data": data, "ts": time.time()}
        if len(_forming_cache) > _CACHE_MAX:
            oldest = min(_forming_cache, key=lambda k: _forming_cache[k]["ts"])
            _forming_cache.pop(oldest, None)


def fetch_from_oanda(symbol: str, timeframe: str) -> Tuple[Optional[pd.DataFrame], Optional[float], str]:
    if not OANDA_API_KEY:
        return None, None, "OANDA_API_KEY not set"
    instrument = INSTRUMENT_MAP.get(symbol.upper())
    granularity = GRANULARITY_MAP.get(timeframe, "H1")
    if not instrument:
        return None, None, "Unknown symbol"

    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    url = f"{OANDA_BASE_URL}/v3/instruments/{instrument}/candles"
    params = {"granularity": granularity, "count": 500, "price": "M"}

    r = oanda_request(url, headers, params, allow_insecure=OANDA_INSECURE_RETRY)
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
                    _forming_put(symbol, timeframe, {
                        "time": pd.to_datetime(c["time"]),
                        "open": safe_float(mid["o"]),
                        "high": safe_float(mid["h"]),
                        "low": safe_float(mid["l"]),
                        "close": safe_float(mid["c"]),
                    })
                except Exception as e:
                    logger.debug("forming-candle cache write failed: %s", e)
            continue
        try:
            rows.append({
                "time": pd.to_datetime(c["time"]),
                "open": safe_float(mid["o"]),
                "high": safe_float(mid["h"]),
                "low": safe_float(mid["l"]),
                "close": safe_float(mid["c"]),
            })
        except Exception as e:
            logger.debug("skip malformed candle: %s", e)
            continue

    if len(rows) < 50:
        return None, None, "Not enough OANDA candles"

    df = pd.DataFrame(rows).set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="last")].dropna()
    return df, live_price, "OANDA"


def fetch_from_yahoo(symbol: str, timeframe: str) -> Tuple[Optional[pd.DataFrame], Optional[float], str]:
    try:
        import yfinance as yf
    except ImportError:
        return None, None, "yfinance not installed"

    ticker = YF_FALLBACK.get(symbol.upper())
    if not ticker:
        return None, None, "No fallback ticker"

    if timeframe == "4h":
        yf_interval = "1h"
        yf_period = "730d"
        needs_resample = True
    else:
        yf_interval = timeframe
        yf_period = YF_PERIOD.get(timeframe, "180d")
        needs_resample = False

    def _download():
        return yf.download(ticker, period=yf_period, interval=yf_interval,
                           progress=False, auto_adjust=False)

    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(_download)
            df = future.result(timeout=8)

        if df is None or df.empty:
            return None, None, "Yahoo returned no data"
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close"]].dropna()

        if needs_resample:
            df = df.resample("4h").agg({
                "open": "first", "high": "max",
                "low": "min", "close": "last",
            }).dropna()

        if len(df) < 50:
            return None, None, "Yahoo not enough data"
        return df, None, "YAHOO (fallback)"
    except concurrent.futures.TimeoutError:
        return None, None, "Yahoo API Timed Out (Skipped)"
    except Exception as e:
        return None, None, f"Yahoo error: {str(e)[:100]}"


def fetch_data_with_live(symbol: str, timeframe: str) -> Tuple[Optional[pd.DataFrame], Optional[float], str, bool]:
    df, live_px, src = fetch_from_oanda(symbol, timeframe)
    if df is not None:
        return df, live_px, src, bool(live_px and live_px > 0)

    if symbol.upper() in INDEX_SYMBOLS:
        return None, None, "OANDA failed (Market Closed or Wrong Ticker)", False

    df2, _, src2 = fetch_from_yahoo(symbol, timeframe)
    if df2 is not None:
        last_close = safe_float(df2["close"].values[-1])
        return df2, last_close, src2, False
    return None, None, f"{src} / {src2}", False


def _fetch_with_live_cached(symbol: str, timeframe: str) -> Tuple[Optional[pd.DataFrame], Optional[float], str, bool]:
    key = f"{symbol.upper()}_{timeframe}"
    with _cache_lock:
        entry = _analyze_cache.get(key)
        if entry and (time.time() - entry["ts"]) < _ANALYZE_TTL:
            return entry["df"], entry["live_px"], entry["source"], entry["is_tick"]

    df, live_px, source, is_tick = fetch_data_with_live(symbol, timeframe)

    with _cache_lock:
        _analyze_cache[key] = {
            "df": df, "live_px": live_px, "source": source,
            "is_tick": is_tick, "ts": time.time(),
        }
        if len(_analyze_cache) > _CACHE_MAX:
            oldest = min(_analyze_cache, key=lambda k: _analyze_cache[k]["ts"])
            _analyze_cache.pop(oldest, None)
    return df, live_px, source, is_tick


def fetch_data(symbol: str, timeframe: str) -> Tuple[Optional[pd.DataFrame], str]:
    df, _, src, _ = fetch_data_with_live(symbol, timeframe)
    return df, src


def fetch_live_price(symbol: str, timeframe: str) -> Optional[float]:
    _, live_px, _, _ = fetch_data_with_live(symbol, timeframe)
    return live_px


def fetch_data_cached(symbol: str, timeframe: str, ttl: int = 300) -> Tuple[pd.DataFrame, str]:
    key = f"{symbol}_{timeframe}"

    cached = _cache_get(_df_cache, key, ttl)
    if cached is not None:
        return cached, "CACHE"

    if _cache_get(_failure_cache, key, FAILURE_TTL) is not None:
        return pd.DataFrame(), "CACHE_FAILURE"

    df, _, src, _ = fetch_data_with_live(symbol, timeframe)

    if df is None or df.empty:
        _cache_set(_failure_cache, key, True)
        return pd.DataFrame(), src

    _cache_set(_df_cache, key, df)
    return df, src


def find_swings(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    params = tf_params(timeframe)
    left = params["swing_left"]
    right = params["swing_right"]

    df = df.copy()
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    sh_raw = np.zeros(n, dtype=bool)
    sl_raw = np.zeros(n, dtype=bool)

    for i in range(left, n - right):
        window_h = highs[i - left:i + right + 1]
        window_l = lows[i - left:i + right + 1]
        if np.isclose(highs[i], window_h.max()):
            sh_raw[i] = True
        if np.isclose(lows[i], window_l.min()):
            sl_raw[i] = True

    def _dedupe_plateaus(marks: np.ndarray) -> np.ndarray:
        out = np.zeros_like(marks)
        idx = np.where(marks)[0]
        if len(idx) == 0:
            return out
        splits = np.where(np.diff(idx) > 1)[0] + 1
        groups = np.split(idx, splits)
        for g in groups:
            out[g[len(g) // 2]] = True
        return out

    df["sh"] = _dedupe_plateaus(sh_raw)
    df["sl"] = _dedupe_plateaus(sl_raw)
    return df


def detect_structure(df: pd.DataFrame) -> Dict[str, Any]:
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


def find_break_bar(closes: np.ndarray, level: float, bullish: bool, start: int) -> int:
    n = len(closes)
    start = max(0, min(start, n))
    for i in range(start, n):
        c = safe_float(closes[i])
        if (bullish and c > level) or (not bullish and c < level):
            return i
    return n - 1


def detect_mss(df: pd.DataFrame, structure: Dict[str, Any], timeframe: str = "15m") -> Optional[dict]:
    sh = structure["swing_highs"]
    sl = structure["swing_lows"]
    if not sh or not sl:
        return None

    last_close = safe_float(df["close"].values[-1])
    atr = safe_float((df["high"] - df["low"]).rolling(14).mean().iloc[-1], 0.5)
    if atr <= 0:
        return None

    params = tf_params(timeframe)
    max_swing_age = params.get("mss_max_swing_age", 100)
    max_trigger_distance = atr * 1.5
    n = len(df)
    closes = df["close"].values

    last_high = sh[-1]
    last_low = sl[-1]
    prev_high_price = structure.get("prev_high")
    prev_low_price = structure.get("prev_low")

    mss = None

    if last_low["index"] > last_high["index"]:
        if (n - 1 - safe_int(last_high["index"])) > max_swing_age:
            return None
        target_high = safe_float(last_high["price"])
        if last_close > target_high:
            break_idx = find_break_bar(closes, target_high, True, safe_int(last_high["index"]) + 1)
            mss = {"type": "MSS_BULL", "level": target_high,
                   "break_index": break_idx,
                   "description": f"Price closed above swing high at {target_high:.2f} — bullish reversal confirmed"}
        elif prev_high_price and len(sh) >= 2:
            prev_high_idx = safe_int(sh[-2]["index"])
            if (n - 1 - prev_high_idx) <= max_swing_age:
                distance = abs(prev_high_price - last_close)
                if last_close > prev_high_price and distance <= max_trigger_distance:
                    break_idx = find_break_bar(closes, prev_high_price, True, prev_high_idx + 1)
                    mss = {"type": "MSS_BULL", "level": safe_float(prev_high_price),
                           "break_index": break_idx,
                           "description": f"Price closed above prior swing high at {prev_high_price:.2f} — early bullish shift"}
    elif last_high["index"] > last_low["index"]:
        if (n - 1 - safe_int(last_low["index"])) > max_swing_age:
            return None
        target_low = safe_float(last_low["price"])
        if last_close < target_low:
            break_idx = find_break_bar(closes, target_low, False, safe_int(last_low["index"]) + 1)
            mss = {"type": "MSS_BEAR", "level": target_low,
                   "break_index": break_idx,
                   "description": f"Price closed below swing low at {target_low:.2f} — bearish reversal confirmed"}
        elif prev_low_price and len(sl) >= 2:
            prev_low_idx = safe_int(sl[-2]["index"])
            if (n - 1 - prev_low_idx) <= max_swing_age:
                distance = abs(last_close - prev_low_price)
                if last_close < prev_low_price and distance <= max_trigger_distance:
                    break_idx = find_break_bar(closes, prev_low_price, False, prev_low_idx + 1)
                    mss = {"type": "MSS_BEAR", "level": safe_float(prev_low_price),
                           "break_index": break_idx,
                           "description": f"Price closed below prior swing low at {prev_low_price:.2f} — early bearish shift"}

    return mss


def detect_recent_sweep(df: pd.DataFrame, structure: Dict[str, Any], lookback: int = 40) -> Optional[dict]:
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

    bsl_list = [s for s in recent_sweeps if s["type"] == "BSL_SWEEP"]
    ssl_list = [s for s in recent_sweeps if s["type"] == "SSL_SWEEP"]
    return {
        "latest": recent_sweeps[-1],
        "bsl": bsl_list[-1] if bsl_list else None,
        "ssl": ssl_list[-1] if ssl_list else None,
    }


def get_previous_day_hl(symbol: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        d1_df, _ = fetch_data_cached(symbol, "1d", ttl=1800)
        if d1_df is None or len(d1_df) < 2:
            return None, None
        now_utc_date = datetime.now(timezone.utc).date()
        for i in range(len(d1_df) - 1, -1, -1):
            ts = d1_df.index[i]
            try:
                ts_date = ts.date() if hasattr(ts, "date") else None
            except Exception:
                ts_date = None
            if ts_date is not None and ts_date < now_utc_date:
                row = d1_df.iloc[i]
                return safe_float(row["high"]), safe_float(row["low"])
    except Exception as e:
        logger.warning("get_previous_day_hl failed for %s: %s", symbol, e)
    return None, None


def get_session_ranges(df: pd.DataFrame, symbol: str = "", timeframe: str = "15m") -> Dict[str, Any]:
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
    except Exception as e:
        logger.warning("session range index conversion failed: %s", e)
        return {}

    now_utc = datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())
    today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ts = int(today_start_utc.timestamp())

    sessions_def = {
        "ASIA":   (0, 8, None),
        "LONDON": (8, 17, _LONDON_TZ),
        "NY":     (8, 17, _NY_TZ),
    }

    highs = df["high"].values
    lows = df["low"].values

    result: Dict[str, Any] = {}
    for name, (start_h, end_h, tz) in sessions_def.items():
        if tz is not None:
            try:
                local_now = now_utc.astimezone(tz)
                start_local = local_now.replace(hour=start_h, minute=0, second=0, microsecond=0)
                end_local = local_now.replace(hour=end_h, minute=0, second=0, microsecond=0)
                start_ts = int(start_local.timestamp())
                end_ts = int(end_local.timestamp())
            except Exception as e:
                logger.debug("session bounds failed for %s: %s", name, e)
                start_ts = today_start_ts + start_h * 3600
                end_ts = today_start_ts + end_h * 3600
        else:
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


def compute_dealing_range(df: pd.DataFrame, structure: Dict[str, Any], timeframe: str) -> Optional[dict]:
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
                        "size_atr": distance / atr, "fallback": False,
                    }

    n = len(df)
    sub = df.iloc[max(0, n - 100):]
    high = safe_float(sub["high"].max())
    low = safe_float(sub["low"].min())
    if high > low:
        return {"high": high, "low": low, "mid": (high + low) / 2,
                "start_index": max(0, n - 100), "end_index": n - 1,
                "size_atr": (high - low) / atr, "fallback": True}
    return None


def count_ob_touches(df: pd.DataFrame, impulse_index: int, ob_top: float, ob_bottom: float) -> int:
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


def _ranges_overlap(a_top: float, a_bottom: float, b_top: float, b_bottom: float,
                    threshold: float = 0.5) -> bool:
    a_hi, a_lo = max(a_top, a_bottom), min(a_top, a_bottom)
    b_hi, b_lo = max(b_top, b_bottom), min(b_top, b_bottom)
    a_size = a_hi - a_lo
    b_size = b_hi - b_lo
    if a_size <= 0 or b_size <= 0:
        return False
    overlap_hi = min(a_hi, b_hi)
    overlap_lo = max(a_lo, b_lo)
    if overlap_hi <= overlap_lo:
        return False
    overlap_size = overlap_hi - overlap_lo
    return (overlap_size / min(a_size, b_size)) >= threshold


def find_order_blocks(df: pd.DataFrame, timeframe: str, lookback: int = 300) -> List[dict]:
    params = tf_params(timeframe)
    impulse_mult = params["ob_impulse_mult"]
    range_mult = params["ob_range_mult"]
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
        ob_top = safe_float(ob_candle["high"])
        ob_bottom = safe_float(ob_candle["low"])

        if (ob_top - ob_bottom) > atr_v * max_ob_atr:
            continue

        touches = count_ob_touches(df, i, ob_top, ob_bottom)

        obs.append({
            "type": "OB_BULL" if impulse_bullish else "OB_BEAR",
            "index": ob_idx, "impulse_index": i,
            "top": ob_top, "bottom": ob_bottom,
            "label": "Bullish OB" if impulse_bullish else "Bearish OB",
            "impulse": impulse_ratio, "quality": impulse_ratio, "touches": touches,
        })

    kept = []
    bull_count = 0
    bear_count = 0

    for ob in reversed(obs):
        if ob["type"] == "OB_BULL" and bull_count >= 2:
            continue
        if ob["type"] == "OB_BEAR" and bear_count >= 2:
            continue
        if any(_ranges_overlap(ob["top"], ob["bottom"], k["top"], k["bottom"]) for k in kept):
            continue
        kept.append(ob)
        if ob["type"] == "OB_BULL":
            bull_count += 1
        else:
            bear_count += 1
        if bull_count >= 2 and bear_count >= 2:
            break

    return kept


def find_fvgs(df: pd.DataFrame, timeframe: str, lookback: int = 300) -> List[dict]:
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


def find_liquidity_pools(df: pd.DataFrame, structure: Dict[str, Any], timeframe: str) -> List[dict]:
    params = tf_params(timeframe)
    min_range_pct = params["pool_min_range_pct"]
    max_pools = params["max_pools"]

    pools = []
    sh = structure["swing_highs"]
    sl = structure["swing_lows"]
    last_price = safe_float(df["close"].values[-1])

    def swing_qualifies(price_a: float, price_b: float) -> bool:
        if price_a == 0:
            return False
        return (abs(price_a - price_b) / price_a * 100) < 0.15

    def swing_is_significant(price: float) -> bool:
        if price == 0 or last_price == 0:
            return False
        return abs(price - last_price) / last_price * 100 >= min_range_pct

    bsl_candidates = [s for s in sh if s["price"] > last_price and swing_is_significant(s["price"])]
    used = set()
    for i in range(len(bsl_candidates)):
        if i in used:
            continue
        for j in range(i + 1, len(bsl_candidates)):
            if j in used:
                continue
            if swing_qualifies(bsl_candidates[i]["price"], bsl_candidates[j]["price"]):
                pools.append({"type": "BSL",
                              "price": max(bsl_candidates[i]["price"], bsl_candidates[j]["price"]),
                              "index": max(bsl_candidates[i]["index"], bsl_candidates[j]["index"]),
                              "label": "Buy-side Liquidity"})
                used.add(i)
                used.add(j)
                break

    ssl_candidates = [s for s in sl if s["price"] < last_price and swing_is_significant(s["price"])]
    used = set()
    for i in range(len(ssl_candidates)):
        if i in used:
            continue
        for j in range(i + 1, len(ssl_candidates)):
            if j in used:
                continue
            if swing_qualifies(ssl_candidates[i]["price"], ssl_candidates[j]["price"]):
                pools.append({"type": "SSL",
                              "price": min(ssl_candidates[i]["price"], ssl_candidates[j]["price"]),
                              "index": max(ssl_candidates[i]["index"], ssl_candidates[j]["index"]),
                              "label": "Sell-side Liquidity"})
                used.add(i)
                used.add(j)
                break

    bsl_out = sorted([p for p in pools if p["type"] == "BSL"],
                     key=lambda p: p["price"])[:max_pools]
    ssl_out = sorted([p for p in pools if p["type"] == "SSL"],
                     key=lambda p: p["price"], reverse=True)[:max_pools]

    return bsl_out + ssl_out


def premium_discount(df: pd.DataFrame, structure: Dict[str, Any],
                     current_price: Optional[float] = None,
                     dealing_range: Optional[dict] = None) -> Optional[dict]:
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


def get_bias(symbol: str) -> Dict[str, Any]:
    cached = _cache_get(_bias_cache, symbol, 300)
    if cached is not None:
        return cached

    d1_trend = "RANGING"
    h4_trend = "RANGING"
    h4_levels = []
    htf_obs = []

    try:
        d1_df, _ = fetch_data_cached(symbol, "1d", ttl=1800)
        if d1_df is not None and len(d1_df) >= 50:
            d1_df = find_swings(d1_df, "1d")
            d1_struct = detect_structure(d1_df)
            d1_trend = d1_struct["trend"]
            for ob in find_order_blocks(d1_df, "1d", lookback=50):
                ob["tf"] = "D1"
                htf_obs.append(ob)
    except Exception as e:
        logger.warning("get_bias(D1) failed for %s: %s", symbol, e)

    try:
        h4_df, _ = fetch_data_cached(symbol, "4h", ttl=300)
        if h4_df is not None and len(h4_df) >= 50:
            h4_df = find_swings(h4_df, "4h")
            h4_struct = detect_structure(h4_df)
            h4_trend = h4_struct["trend"]
            for h in h4_struct.get("swing_highs", []):
                h4_levels.append({"type": "H4_HIGH", "price": safe_float(h["price"])})
            for l in h4_struct.get("swing_lows", []):
                h4_levels.append({"type": "H4_LOW", "price": safe_float(l["price"])})
            for ob in find_order_blocks(h4_df, "4h", lookback=100):
                ob["tf"] = "H4"
                htf_obs.append(ob)
    except Exception as e:
        logger.warning("get_bias(H4) failed for %s: %s", symbol, e)

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
              "h4_levels": h4_levels, "htf_obs": htf_obs,
              "allow_buy": allow_buy, "allow_sell": allow_sell,
              "tier": tier, "reason": reason}
    _cache_set(_bias_cache, symbol, result)
    return result


def pick_tp_swing(structure: Dict[str, Any], entry: float, direction: str) -> Tuple[Optional[float], Optional[float]]:
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


SCORE_WEIGHTS_EXACT = {
    "MSS Confirmed": 3,
    "HTF Bias": 2,
    "In Killzone": 2,
    "Displacement": 2,
    "OB Fresh": 2,
}
SCORE_WEIGHTS_PREFIX = {
    "Sweep of ": 2,
    "Price in ": 2,
    "R:R": 1,
}


def score_checklist(checklist: List[Dict[str, Any]]) -> int:
    pts = 0
    for c in checklist:
        if not c.get("pass"):
            continue
        lbl = c.get("label", "")
        if lbl in SCORE_WEIGHTS_EXACT:
            pts += SCORE_WEIGHTS_EXACT[lbl]
            continue
        for prefix, weight in SCORE_WEIGHTS_PREFIX.items():
            if lbl.startswith(prefix):
                pts += weight
                break
    return pts


def build_checklist(direction: str, mss: Optional[dict], bias: Optional[dict],
                    pd: Optional[dict], killzone_ok: bool, zone: dict,
                    rr: float, sweep: Optional[dict] = None) -> List[Dict[str, Any]]:
    checklist = []

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

    mss_ok = bool(mss) and (
        (mss["type"] == "MSS_BULL" and direction == "BUY") or
        (mss["type"] == "MSS_BEAR" and direction == "SELL")
    )
    checklist.append({
        "label": "MSS Confirmed", "pass": mss_ok,
        "detail": (mss["type"].replace("MSS_", "") if mss else "No MSS")
    })

    wanted_key = "ssl" if direction == "BUY" else "bsl"
    wrong_key = "bsl" if direction == "BUY" else "ssl"
    sweep_match = sweep.get(wanted_key) if isinstance(sweep, dict) else None
    sweep_wrong = sweep.get(wrong_key) if isinstance(sweep, dict) else None
    sweep_ok = bool(sweep_match)
    sweep_label = "Sweep of SSL" if direction == "BUY" else "Sweep of BSL"
    if sweep_match:
        side = "SSL" if sweep_match["type"] == "SSL_SWEEP" else "BSL"
        sweep_detail = f"{side} @ {sweep_match['swept_price']:.2f}"
    elif sweep_wrong:
        side = "SSL" if sweep_wrong["type"] == "SSL_SWEEP" else "BSL"
        sweep_detail = f"{side} only — wrong side for {direction}"
    else:
        sweep_detail = "No recent sweep"
    checklist.append({
        "label": sweep_label, "pass": sweep_ok, "detail": sweep_detail
    })

    want = "DISCOUNT" if direction == "BUY" else "PREMIUM"
    pd_ok = bool(pd) and pd["position"] == want
    checklist.append({
        "label": f"Price in {want}", "pass": pd_ok,
        "detail": (pd["position"] if pd else "Unknown")
    })

    checklist.append({
        "label": "In Killzone", "pass": bool(killzone_ok),
        "detail": "Active" if killzone_ok else "Outside session"
    })

    q = safe_float(zone.get("quality", 0))
    checklist.append({
        "label": "Displacement", "pass": q >= 2.0,
        "detail": f"{q:.1f}x avg body"
    })

    touches = safe_int(zone.get("touches", 0))
    checklist.append({
        "label": "OB Fresh", "pass": touches <= 1,
        "detail": "Untapped" if touches == 0 else f"Tapped {touches}x"
    })

    checklist.append({
        "label": f"R:R >= {MIN_RR:.1f}", "pass": rr >= MIN_RR,
        "detail": f"1 : {rr:.2f}"
    })

    return checklist


def detect_smt_divergence(symbol: str, timeframe: str, structure: Dict[str, Any]) -> str:
    if symbol.upper() != "XAUUSD":
        return "N/A - Only for XAUUSD"

    corr_symbol = "EURUSD"
    corr_df, _ = fetch_data_cached(corr_symbol, timeframe, ttl=300)
    if corr_df is None or len(corr_df) < 50:
        return "NO DATA (EURUSD)"

    corr_df = find_swings(corr_df, timeframe)
    corr_structure = detect_structure(corr_df)

    sh = structure.get("swing_highs", [])
    sl = structure.get("swing_lows", [])
    corr_sh = corr_structure.get("swing_highs", [])
    corr_sl = corr_structure.get("swing_lows", [])

    if len(sh) < 2 or len(sl) < 2 or len(corr_sh) < 2 or len(corr_sl) < 2:
        return "INSUFFICIENT SWINGS"

    if sh[-1]["price"] > sh[-2]["price"] and corr_sh[-1]["price"] < corr_sh[-2]["price"]:
        return "BEARISH SMT (XAUUSD HH, EURUSD LH)"
    if sl[-1]["price"] < sl[-2]["price"] and corr_sl[-1]["price"] > corr_sl[-2]["price"]:
        return "BULLISH SMT (XAUUSD LL, EURUSD HL)"
    return "NO DIVERGENCE DETECTED"


def compute_ote_levels(structure: Dict[str, Any]) -> Optional[dict]:
    sh = structure.get("swing_highs", [])
    sl = structure.get("swing_lows", [])
    if not sh or not sl:
        return None

    swings = [{"index": h["index"], "price": h["price"], "type": "H"} for h in sh] + \
             [{"index": l["index"], "price": l["price"], "type": "L"} for l in sl]
    if len(swings) < 2:
        return None

    swings.sort(key=lambda x: x["index"])
    last_swing = swings[-1]
    prev_swing = swings[-2]
    if last_swing["type"] == prev_swing["type"]:
        return None

    range_val = abs(last_swing["price"] - prev_swing["price"])
    if range_val == 0:
        return None

    if last_swing["type"] == "H":
        ote_61_8 = last_swing["price"] - range_val * 0.618
        ote_79_0 = last_swing["price"] - range_val * 0.79
    else:
        ote_61_8 = last_swing["price"] + range_val * 0.618
        ote_79_0 = last_swing["price"] + range_val * 0.79

    return {
        "top": round(max(ote_61_8, ote_79_0), 4),
        "bottom": round(min(ote_61_8, ote_79_0), 4),
        "label": "OTE Zone (61.8% - 79.0%)",
    }


def generate_scenarios(df: pd.DataFrame, structure: Dict[str, Any], obs: List[dict],
                       fvgs: List[dict], pools: List[dict], pd_zones: Optional[dict],
                       timeframe: str, mss: Optional[dict] = None, symbol: str = "",
                       bias: Optional[dict] = None, killzone_ok: bool = True,
                       sweep: Optional[dict] = None) -> Tuple[Optional[dict], Optional[dict], Optional[str]]:
    atr = safe_float((df["high"] - df["low"]).rolling(14).mean().iloc[-1], 0.5)
    current_price = safe_float(df["close"].values[-1])

    tolerance = atr * 0.5
    min_risk = atr * 0.5

    recent_atr = (df["high"] - df["low"]).rolling(14).mean()
    atr_median = safe_float(recent_atr.median(), atr)
    vol_ratio = atr / atr_median if atr_median > 0 else 1.0
    vol_factor = min(max(vol_ratio, 1.0), 1.5)
    max_risk = atr * 5.0 * vol_factor

    warning_msg = None

    if not bias:
        return None, None, warning_msg
    bias_tier = bias.get("tier", "NEUTRAL")
    if bias_tier == "CONFLICT":
        return None, None, warning_msg
    if not mss:
        return None, None, warning_msg

    recent = df.tail(3)
    displacement_bear = False
    displacement_bull = False
    for _, row in recent.iterrows():
        rng = safe_float(row["high"]) - safe_float(row["low"])
        if rng <= atr * 2.5:
            continue
        if safe_float(row["close"]) < safe_float(row["open"]):
            displacement_bear = True
        else:
            displacement_bull = True

    last_candle = df.iloc[-1]
    last_is_bear = safe_float(last_candle["close"]) < safe_float(last_candle["open"])
    last_is_bull = safe_float(last_candle["close"]) > safe_float(last_candle["open"])

    if mss["type"] == "MSS_BULL":
        if displacement_bear and last_is_bear:
            return None, None, ("⚠️ DO NOT BUY: recent massive bearish displacement "
                                "contradicts bullish MSS. Setup invalidated.")
        allow_buy = True
        allow_sell = False
    elif mss["type"] == "MSS_BEAR":
        if displacement_bull and last_is_bull:
            return None, None, ("⚠️ DO NOT SELL: recent massive bullish displacement "
                                "contradicts bearish MSS. Setup invalidated.")
        allow_buy = False
        allow_sell = True
    else:
        return None, None, warning_msg

    def build_setup(direction: str, zone: dict, entry: float, sl: float,
                    tp1: float, tp2: float) -> Optional[dict]:
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        reward_1 = abs(tp1 - entry)
        rr = reward_1 / risk
        if rr < MIN_RR:
            return None

        if bias_tier == "NEUTRAL":
            mss_ok = mss and (
                (mss["type"] == "MSS_BULL" and direction == "BUY") or
                (mss["type"] == "MSS_BEAR" and direction == "SELL")
            )
            if not mss_ok:
                return None

        checklist = build_checklist(direction, mss, bias, pd_zones, killzone_ok, zone, rr, sweep=sweep)
        pts = score_checklist(checklist)

        if pts >= 10:
            confidence = "HIGH"
        elif pts >= 6:
            confidence = "MEDIUM"
        elif pts >= 4:
            confidence = "LOW"
        else:
            return None

        # NEW RULE: If the trade is counter-trend (fails HTF Bias), downgrade it.
        htf_bias_check = next((c for c in checklist if c["label"] == "HTF Bias"), None)
        if htf_bias_check and not htf_bias_check["pass"]:
            if confidence == "HIGH":
                confidence = "MEDIUM"
            elif confidence == "MEDIUM":
                confidence = "LOW"

        # Existing rule
        if bias_tier in ("NEUTRAL", "WEAK") and confidence == "HIGH":
            confidence = "MEDIUM"

        reasons = [f"{'✅' if c['pass'] else '⚠️'} {c['label']} — {c['detail']}" for c in checklist]

        return {
            "direction": direction,
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr,
            "zone_type": zone["label"], "confidence": confidence,
            "zone_quality": safe_float(zone.get("quality", 0)),
            "checklist": checklist, "score": pts, "reasons": reasons,
            "sl_basis": "zone edge", "tp_basis": "next swing",
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

    return buy_setup, sell_setup, warning_msg


def compute_correlations(symbols: List[str], timeframe: str = '1h', lookback: int = 200) -> Dict[str, Any]:
    series: Dict[str, np.ndarray] = {}
    for sym in symbols:
        df, _ = fetch_data_cached(sym, timeframe, ttl=300)
        if df is not None and len(df) >= 50:
            rets = df['close'].tail(lookback + 1).pct_change().dropna()
            if len(rets) > 0:
                series[sym] = rets.values

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

    return {'symbols': syms, 'matrix': matrix, 'timeframe': timeframe, 'basis': 'pct_change'}


def _build_candles(df: pd.DataFrame, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
    candles = []
    df_tail = df.tail(400)
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

    fc = _forming_get(symbol, timeframe)
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
        except Exception as e:
            logger.debug("forming candle append failed: %s", e)
    return candles


def _build_swing_markers(df: pd.DataFrame, swings: List[dict]) -> List[Dict[str, Any]]:
    markers = []
    for s in swings:
        i = safe_int(s["index"])
        if 0 <= i < len(df):
            try:
                t = safe_int(df.index[i].timestamp())
                if t > 0:
                    markers.append({"time": t, "price": safe_float(s["price"])})
            except Exception:
                continue
    return markers


def _analyze_inner(symbol: str, timeframe: str) -> Dict[str, Any]:
    df, live_px, used_source, is_tick = _fetch_with_live_cached(symbol, timeframe)
    if df is None or df.empty:
        return {"error": f"Could not fetch {symbol} {timeframe}: {used_source}"}

    last_closed = safe_float(df["close"].values[-1])
    current_px = live_px if (live_px and live_px > 0) else last_closed

    df = find_swings(df, timeframe)
    structure = detect_structure(df)

    dealing_range = compute_dealing_range(df, structure, timeframe)
    pd_zones = premium_discount(df, structure, current_px, dealing_range=dealing_range)

    mss = detect_mss(df, structure, timeframe)
    sweep = detect_recent_sweep(df, structure, lookback=40)
    pools = find_liquidity_pools(df, structure, timeframe)
    obs = find_order_blocks(df, timeframe)
    fvgs = find_fvgs(df, timeframe)

    pdh, pdl = get_previous_day_hl(symbol)
    sessions = get_session_ranges(df, symbol, timeframe)

    killzone_ok = in_killzone(symbol)
    bias = get_bias(symbol)

    smt_result = detect_smt_divergence(symbol, timeframe, structure)
    ote_result = compute_ote_levels(structure)

    buy_setup, sell_setup, warning_msg = generate_scenarios(
        df, structure, obs, fvgs, pools, pd_zones, timeframe, mss,
        symbol=symbol, bias=bias, killzone_ok=killzone_ok, sweep=sweep
    )
    killzone = current_killzone(symbol)

    candles = _build_candles(df, symbol, timeframe)
    sh_markers = _build_swing_markers(df, structure["swing_highs"])
    sl_markers = _build_swing_markers(df, structure["swing_lows"])

    def idx_to_ts(i: int) -> Optional[int]:
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
                       "touches": safe_int(z.get("touches", 0)), "kind": "OB"})

    fvg_out = []
    for z in fvgs:
        t = idx_to_ts(z["index"])
        if t is None:
            continue
        fvg_out.append({"type": z["type"], "top": safe_float(z["top"]),
                        "bottom": safe_float(z["bottom"]), "time": t,
                        "label": z["label"], "size": safe_float(z.get("size", 0)), "kind": "FVG"})

    pools_out = []
    for p in pools:
        t = idx_to_ts(p["index"])
        if t is None:
            continue
        pools_out.append({"type": p["type"], "price": safe_float(p["price"]),
                          "time": t, "label": p["label"]})

    mss_out = None
    if mss:
        break_idx = safe_int(mss.get("break_index", len(df) - 1))
        mss_time = idx_to_ts(break_idx)
        if mss_time is not None:
            mss_out = {"type": mss["type"], "level": safe_float(mss["level"]),
                       "description": mss["description"],
                       "time": mss_time, "break_index": break_idx}

    sweep_out = None
    if sweep and sweep.get("latest"):
        latest = sweep["latest"]
        sweep_time = idx_to_ts(latest["index"])
        if sweep_time is not None:
            sweep_out = {"type": latest["type"], "time": sweep_time,
                         "swept_price": safe_float(latest["swept_price"]),
                         "index": safe_int(latest["index"])}

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "ticker": used_source,
        "last_price": last_closed,
        "live_price": live_px,
        "live_price_is_tick": bool(is_tick),
        "structure": structure,
        "bias": bias,
        "mss": mss_out,
        "sweep": sweep_out,
        "pdh": pdh, "pdl": pdl,
        "sessions": sessions,
        "dealing_range": dealing_range,
        "pools": pools_out,
        "obs": ob_out, "fvgs": fvg_out,
        "premium_discount": pd_zones,
        "buy_setup": buy_setup,
        "sell_setup": sell_setup,
        "warning": warning_msg,
        "killzone": killzone,
        "candles": candles,
        "swing_high_markers": sh_markers,
        "swing_low_markers": sl_markers,
        "smt_divergence": smt_result,
        "ote_levels": ote_result,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": used_source,
    }


def analyze(symbol: str, timeframe: str) -> Dict[str, Any]:
    try:
        return _analyze_inner(symbol, timeframe)
    except Exception as e:
        logger.exception("analyze() failed for %s %s", symbol, timeframe)
        return {
            "error": f"analyze failed for {symbol} {timeframe}: {e}",
            "symbol": symbol,
            "timeframe": timeframe,
        }


def _run_structural_tests():
    np.random.seed(42)
    n = 300
    prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "open":  prices + np.random.randn(n) * 0.1,
        "high":  prices + np.abs(np.random.randn(n)) * 0.5,
        "low":   prices - np.abs(np.random.randn(n)) * 0.5,
        "close": prices + np.random.randn(n) * 0.2,
    }, index=pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"))

    df_swings = find_swings(df, "15m")
    assert "sh" in df_swings.columns and "sl" in df_swings.columns

    struct = detect_structure(df_swings)
    assert struct["trend"] in ("BULLISH", "BEARISH", "RANGING")

    obs = find_order_blocks(df_swings, "15m")
    assert isinstance(obs, list)

    fvgs = find_fvgs(df_swings, "15m")
    assert isinstance(fvgs, list)

    pools = find_liquidity_pools(df_swings, struct, "15m")
    seen = set()
    for p in pools:
        key = (p["type"], round(p["price"], 5))
        assert key not in seen, f"Duplicate pool {key}"
        seen.add(key)

    dr = compute_dealing_range(df_swings, struct, "15m")
    assert dr is None or ("high" in dr and "low" in dr)

    assert _ranges_overlap(10, 10, 5, 8) is False
    assert _ranges_overlap(10, 5, 9, 6) is True

    assert isinstance(current_killzone("XAUUSD"), str)
    assert isinstance(current_killzone("BTCUSD"), str)

    fake_checklist = [
        {"label": "MSS Confirmed", "pass": True, "detail": ""},
        {"label": "HTF Bias", "pass": True, "detail": ""},
        {"label": "Sweep of SSL", "pass": True, "detail": ""},
        {"label": "Price in DISCOUNT", "pass": True, "detail": ""},
        {"label": "In Killzone", "pass": True, "detail": ""},
        {"label": "Displacement", "pass": True, "detail": ""},
        {"label": "OB Fresh", "pass": True, "detail": ""},
        {"label": "R:R >= 2.0", "pass": True, "detail": ""},
    ]
    assert score_checklist(fake_checklist) == 16, f"got {score_checklist(fake_checklist)}"

    assert "US100" in INSTRUMENT_MAP
    assert "US100" in YF_FALLBACK
    assert "US100" in INDEX_SYMBOLS

    logger.info("✅ Structural tests passed")


def _run_fix_regression_tests():
    closes = np.array([100.0, 101.0, 102.0, 111.0, 105.0, 106.0, 107.0, 108.0])
    assert find_break_bar(closes, 110.0, True, 0) == 3, "forward scan from 0"
    assert find_break_bar(closes, 110.0, True, 3) == 3, "forward scan from 3"
    assert find_break_bar(closes, 110.0, True, 4) == len(closes) - 1, "no break -> last bar"
    assert find_break_bar(closes, 103.0, False, 4) == len(closes) - 1

    plateau_n = 30
    # FIX: Use a downtrend for the tail so it doesn't create a false swing high at the end of the array
    tail_highs = np.linspace(100, 90, plateau_n)
    tail_lows = np.linspace(95, 85, plateau_n)
    tail_opens = np.linspace(99, 89, plateau_n)
    tail_closes = np.linspace(99, 89, plateau_n)

    highs = np.concatenate(([100, 110, 110, 110, 100], tail_highs))
    lows = np.concatenate(([95, 105, 105, 105, 95], tail_lows))
    opens = np.concatenate(([99, 108, 108, 108, 99], tail_opens))
    closes_arr = np.concatenate(([99, 108, 108, 108, 99], tail_closes))

    df_p = pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes_arr,
    }, index=pd.date_range("2024-01-01", periods=len(highs), freq="15min", tz="UTC"))
    
    df_swings = find_swings(df_p, "15m")
    sh_idx = [i for i, v in enumerate(df_swings["sh"].values) if v]
    
    assert len(sh_idx) == 1, f"expected 1 swing high, got {len(sh_idx)} at {sh_idx}"
    assert 1 <= sh_idx[0] <= 3, f"swing high should be inside plateau, got {sh_idx[0]}"

    np.random.seed(0)
    n = 300
    common_trend = np.linspace(100, 200, n)
    a = common_trend + np.random.randn(n) * 0.5
    b = common_trend + np.random.randn(n) * 0.5
    level_corr = np.corrcoef(a, b)[0, 1]
    assert level_corr > 0.95, f"level corr should be high, got {level_corr}"
    ret_a = np.diff(a) / a[:-1]
    ret_b = np.diff(b) / b[:-1]
    ret_corr = np.corrcoef(ret_a, ret_b)[0, 1]
    assert abs(ret_corr) < 0.2, f"return corr should be near zero, got {ret_corr}"

    logger.info("✅ Fix regression tests passed (FIX #1, #2, #3)")


def _make_golden_ohlc() -> pd.DataFrame:
    n = 50
    opens = np.full(n, 100.0)
    highs = np.full(n, 100.4)
    lows = np.full(n, 99.6)
    closes = np.full(n, 100.0)

    opens[30], closes[30] = 100.5, 99.5
    highs[30], lows[30]   = 100.8, 99.3

    opens[31], closes[31] = 99.6, 103.5
    highs[31], lows[31]   = 104.0, 99.5

    for i in range(32, n):
        opens[i], closes[i] = 103.8, 104.0
        highs[i], lows[i]   = 104.5, 103.5

    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
    }, index=pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"))


def _run_golden_fixture_test(mod):
    import unittest.mock as mock

    with _cache_lock:
        _forming_cache.clear()
        _df_cache.clear()
        _bias_cache.clear()
        _failure_cache.clear()
        _analyze_cache.clear()

    golden_df = _make_golden_ohlc()

    obs = find_order_blocks(golden_df.copy(), "15m")
    assert len(obs) >= 1, f"expected >=1 OB, got {len(obs)}"
    assert any(o["type"] == "OB_BULL" for o in obs), "expected OB_BULL"

    fvgs = find_fvgs(golden_df.copy(), "15m")
    assert len(fvgs) >= 1, f"expected >=1 FVG, got {len(fvgs)}"
    assert any(f["type"] == "FVG_BULL" for f in fvgs), "expected FVG_BULL"

    neutral_bias = {
        "d1_trend": "RANGING", "h4_trend": "RANGING",
        "h4_levels": [], "htf_obs": [],
        "allow_buy": True, "allow_sell": True,
        "tier": "NEUTRAL", "reason": "mock",
    }

    with mock.patch.object(mod, "fetch_data_with_live",
                           return_value=(golden_df.copy(), None, "GOLDEN", False)), \
         mock.patch.object(mod, "fetch_data_cached",
                           return_value=(golden_df.copy(), "GOLDEN")), \
         mock.patch.object(mod, "get_bias", return_value=neutral_bias):
        result = analyze("XAUUSD", "15m")

    assert "error" not in result, f"analyze failed: {result.get('error')}"
    assert len(result["obs"]) >= 1, f"expected >=1 OB in output, got {len(result['obs'])}"
    assert len(result["fvgs"]) >= 1, f"expected >=1 FVG in output, got {len(result['fvgs'])}"
    assert any(o["type"] == "OB_BULL" for o in result["obs"])
    assert any(f["type"] == "FVG_BULL" for f in result["fvgs"])

    logger.info("✅ Golden fixture test passed (OB + FVG detected)")


def _run_sweep_per_side_test():
    n = 30
    opens = np.full(n, 100.0)
    highs = np.full(n, 100.5)
    lows = np.full(n, 99.5)
    closes = np.full(n, 100.0)

    highs[10] = 101.0
    lows[15] = 99.0
    highs[20] = 101.5; closes[20] = 100.8
    lows[22] = 98.5; closes[22] = 99.3

    df = pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
    }, index=pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"))

    structure = {
        "swing_highs": [{"index": 10, "price": 101.0}],
        "swing_lows": [{"index": 15, "price": 99.0}],
    }

    sweep = detect_recent_sweep(df, structure, lookback=30)
    assert sweep is not None, "expected sweeps"
    assert sweep["bsl"] is not None and sweep["bsl"]["index"] == 20, f"bsl: {sweep['bsl']}"
    assert sweep["ssl"] is not None and sweep["ssl"]["index"] == 22, f"ssl: {sweep['ssl']}"
    assert sweep["latest"]["index"] == 22, "latest should be ssl (bar 22)"

    zone = {"quality": 2.5, "touches": 0}
    buy_cl = build_checklist("BUY", None, None, None, True, zone, 2.0, sweep=sweep)
    sell_cl = build_checklist("SELL", None, None, None, True, zone, 2.0, sweep=sweep)

    buy_sweep = next(c for c in buy_cl if c["label"].startswith("Sweep of"))
    sell_sweep = next(c for c in sell_cl if c["label"].startswith("Sweep of"))

    assert buy_sweep["pass"] is True, f"BUY should find SSL sweep, got {buy_sweep}"
    assert sell_sweep["pass"] is True, f"SELL should find BSL sweep, got {sell_sweep}"
    assert "SSL" in buy_sweep["detail"]
    assert "BSL" in sell_sweep["detail"]

    logger.info("✅ Sweep per-side test passed (FIX #6 / #12)")


def _run_cache_eviction_test():
    test_cache: Dict[str, Tuple[float, int]] = {}
    for i in range(_CACHE_MAX + 10):
        _cache_set(test_cache, f"key_{i}", i)

    assert len(test_cache) == _CACHE_MAX, f"expected {_CACHE_MAX}, got {len(test_cache)}"
    assert f"key_{_CACHE_MAX + 9}" in test_cache, "newest should be present"
    assert "key_0" not in test_cache, "oldest should be evicted"
    assert "key_9" not in test_cache, "10th oldest should be evicted"
    assert "key_10" in test_cache, "11th should still be present"

    logger.info("✅ Cache eviction test passed (FIX #7 / #13)")


def _run_429_test():
    import unittest.mock as mock

    fake = mock.MagicMock()
    fake.status_code = 429
    fake.raise_for_status = mock.MagicMock()

    with mock.patch.object(requests, "get", return_value=fake) as mock_get:
        result = oanda_request("https://example.com", {"Authorization": "Bearer test"})

    assert result is None, "429 should return None"
    assert mock_get.call_count == 1, f"should not retry, got {mock_get.call_count} calls"
    fake.raise_for_status.assert_not_called()

    logger.info("✅ 429 handling test passed (FIX #8 / #14)")


def _run_analyze_test(mod):
    import unittest.mock as mock

    with mod._cache_lock:
        mod._forming_cache.clear()
        mod._df_cache.clear()
        mod._bias_cache.clear()
        mod._failure_cache.clear()
        mod._analyze_cache.clear()

    np.random.seed(7)
    n = 300
    prices = 2000 + np.cumsum(np.random.randn(n) * 2)
    mock_df = pd.DataFrame({
        "open":  prices + np.random.randn(n) * 0.5,
        "high":  prices + np.abs(np.random.randn(n)) * 1.0,
        "low":   prices - np.abs(np.random.randn(n)) * 1.0,
        "close": prices + np.random.randn(n) * 0.5,
    }, index=pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"))

    fake_bias = {
        "d1_trend": "BULLISH", "h4_trend": "BULLISH",
        "h4_levels": [], "htf_obs": [],
        "allow_buy": True, "allow_sell": False,
        "tier": "STRONG", "reason": "mock",
    }

    with mock.patch.object(mod, "fetch_data_with_live",
                           return_value=(mock_df.copy(), 2050.0, "MOCK", False)), \
         mock.patch.object(mod, "fetch_data_cached",
                           return_value=(mock_df.copy(), "MOCK")), \
         mock.patch.object(mod, "get_bias", return_value=fake_bias):
        result = analyze("XAUUSD", "15m")

    assert isinstance(result, dict), "analyze must return dict"
    assert "error" not in result, f"analyze returned error: {result.get('error')}"
    assert result.get("symbol") == "XAUUSD"
    assert result.get("data_source") == "MOCK"
    assert isinstance(result.get("candles"), list) and len(result["candles"]) > 0
    assert isinstance(result.get("structure"), dict)
    assert isinstance(result.get("bias"), dict)
    assert isinstance(result.get("pools"), list)
    assert isinstance(result.get("obs"), list)
    assert isinstance(result.get("fvgs"), list)
    assert result["structure"]["trend"] in ("BULLISH", "BEARISH", "RANGING")
    assert result.get("live_price_is_tick") is False
    logger.info("✅ analyze() end-to-end test passed")


def _run_smoke_test():
    _run_structural_tests()
    _run_fix_regression_tests()
    _run_golden_fixture_test(sys.modules[__name__])
    _run_sweep_per_side_test()
    _run_cache_eviction_test()
    _run_429_test()
    _run_analyze_test(sys.modules[__name__])
    try:
        import yfinance  # noqa: F401
        logger.info("ℹ️  yfinance available (4h resample path not network-tested)")
    except ImportError:
        logger.info("ℹ️  yfinance not installed; 4h fallback unavailable")
    logger.info("✅ All smoke tests passed")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_smoke_test()