import os
import sys
import random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, redirect, url_for, request, jsonify
from lessons import LESSONS, MODULES, GLOSSARY
from animations import ANIMATIONS
import scanner_engine
import backtest_engine
import synthetic_engine
import crt_engine

app = Flask(__name__)


# ---------- CURRICULUM ----------
@app.route('/')
def index():
    grouped = {}
    for slug, lesson in LESSONS.items():
        mod = lesson.get("module", "Other")
        if mod not in grouped:
            grouped[mod] = []
        lesson_with_slug = dict(lesson)
        lesson_with_slug["slug"] = slug
        grouped[mod].append(lesson_with_slug)

    for mod in grouped:
        grouped[mod].sort(key=lambda x: x.get("number", 0))

    ordered_modules = {}
    for mod_name in MODULES.keys():
        if mod_name in grouped:
            ordered_modules[mod_name] = grouped[mod_name]

    return render_template(
        'index.html',
        lessons=LESSONS,
        modules=ordered_modules,
        modules_meta=MODULES,
    )


@app.route('/lesson/<slug>')
def lesson(slug):
    lesson_data = LESSONS.get(slug)
    if not lesson_data:
        return redirect(url_for('index'))

    lesson_with_slug = dict(lesson_data)
    lesson_with_slug["slug"] = slug

    anim = None
    if isinstance(ANIMATIONS, dict):
        num = lesson_data.get("number")
        anim = ANIMATIONS.get(num) or ANIMATIONS.get(slug)
    if anim is None:
        anim = lesson_data.get("animation")

    return render_template('lesson.html', lesson=lesson_with_slug, animation=anim)


@app.route('/glossary')
def glossary():
    return render_template('glossary.html', terms=GLOSSARY)


# ---------- TOOLS ----------
@app.route('/calculator')
def calculator():
    return render_template('calculator.html')


@app.route('/scanner')
def scanner():
    symbol = request.args.get('symbol', 'XAUUSD').upper()
    timeframe = request.args.get('tf', '15m')
    try:
        result = scanner_engine.analyze(symbol, timeframe)
    except Exception as e:
        result = {"error": str(e)}
    return render_template('scanner.html', data=result,
                           current_symbol=symbol, current_tf=timeframe)


@app.route('/api/scanner')
def api_scanner():
    symbol = request.args.get('symbol', 'XAUUSD').upper()
    timeframe = request.args.get('tf', '15m')
    try:
        result = scanner_engine.analyze(symbol, timeframe)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/api/price')
def api_price():
    symbol = request.args.get('symbol', 'XAUUSD').upper()
    tf = request.args.get('tf', '15m')
    price = scanner_engine.fetch_live_price(symbol, tf)
    if price and price > 0:
        return jsonify({'price': price, 'source': 'OANDA'})
    return jsonify({'price': None, 'source': 'unavailable'})


# ---------- SYNTHETIC INDICES ----------
@app.route('/synthetic')
def synthetic_scanner():
    symbol = request.args.get('symbol', 'R_75')
    tf = request.args.get('tf', '5m')
    if symbol not in synthetic_engine.SYNTHETIC_SYMBOLS:
        symbol = 'R_75'
    if tf not in synthetic_engine.TF_GRANULARITY:
        tf = '5m'
    try:
        result = synthetic_engine.analyze(symbol, tf)
    except Exception as e:
        result = {"error": str(e)}
    return render_template('synthetic_scanner.html', data=result,
                           current_symbol=symbol, current_tf=tf)


@app.route('/api/synthetic')
def api_synthetic():
    symbol = request.args.get('symbol', 'R_75')
    tf = request.args.get('tf', '5m')
    if symbol not in synthetic_engine.SYNTHETIC_SYMBOLS:
        symbol = 'R_75'
    if tf not in synthetic_engine.TF_GRANULARITY:
        tf = '5m'
    try:
        result = synthetic_engine.analyze(symbol, tf)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


# ---------- CRT SCANNER ----------
@app.route('/crt-scanner')
def crt_scanner():
    symbol = request.args.get('symbol', 'XAUUSD').upper()
    timeframe = request.args.get('tf', '15m')
    if timeframe not in ('15m', '1h', '4h', '1d'):
        timeframe = '15m'
    try:
        result = crt_engine.analyze(symbol, timeframe)
    except Exception as e:
        result = {"error": str(e)}
    return render_template('crt_v2.html', data=result,
                           current_symbol=symbol, current_tf=timeframe)


@app.route('/api/crt-scanner')
def api_crt_scanner():
    symbol = request.args.get('symbol', 'XAUUSD').upper()
    timeframe = request.args.get('tf', '15m')
    if timeframe not in ('15m', '1h', '4h', '1d'):
        timeframe = '15m'
    try:
        result = crt_engine.analyze(symbol, timeframe)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


# ---------- MULTI SCANNER ----------
@app.route('/multi-scanner')
def multi_scanner():
    timeframe = request.args.get('tf', '1h')
    symbols = ['XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'BTCUSD', 'ETHUSD']
    results = []
    for sym in symbols:
        try:
            r = scanner_engine.analyze(sym, timeframe)
            results.append(r)
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return render_template('multi_scanner.html', results=results, current_tf=timeframe)


# ---------- BACKTEST ----------
@app.route('/backtest', methods=['GET', 'POST'])
def backtest_page():
    if request.method == 'POST':
        symbol = request.form.get('symbol', 'XAUUSD').upper()
        tf = request.form.get('tf', request.form.get('timeframe', '1h'))
        try:
            result = backtest_engine.run_backtest(symbol, tf)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)})
    return render_template('backtest.html')


@app.route('/api/backtest')
def api_backtest():
    symbol = request.args.get('symbol', 'XAUUSD').upper()
    timeframe = request.args.get('tf', '1h')
    try:
        result = backtest_engine.run_backtest(symbol, timeframe)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


# ---------- GAME: SPOT THE SETUP ----------
GAME_SYMBOLS = ['XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD']
GAME_TIMEFRAMES = ['15m', '1h', '4h']


@app.route('/game')
def game():
    return render_template('game.html')


@app.route('/api/game-round')
def api_game_round():
    try:
        symbol = random.choice(GAME_SYMBOLS)
        timeframe = random.choice(GAME_TIMEFRAMES)

        df, live_px, src = scanner_engine.fetch_data_with_live(symbol, timeframe)
        if df is None or len(df) < 230:
            # Retry once with a different symbol if data is thin
            symbol = 'XAUUSD'
            df, live_px, src = scanner_engine.fetch_data_with_live(symbol, '1h')
            if df is None or len(df) < 230:
                return jsonify({"error": "Not enough historical data"})

        VISIBLE = 180
        FUTURE = 40
        total_needed = VISIBLE + FUTURE

        subset = df.tail(total_needed).copy()
        visible_df = subset.iloc[:VISIBLE].copy()
        future_df = subset.iloc[VISIBLE:].copy()

        # Structure on the visible portion
        visible_sw = scanner_engine.find_swings(visible_df.copy(), timeframe)
        structure = scanner_engine.detect_structure(visible_sw)
        trend = structure.get("trend", "RANGING")

        # Price moves in the future portion
        entry_price = float(visible_df["close"].iloc[-1])
        highest = float(future_df["high"].max())
        lowest = float(future_df["low"].min())
        move_up = highest - entry_price
        move_down = entry_price - lowest

        # ATR from visible window for dynamic thresholds
        tr = (visible_df["high"] - visible_df["low"]).tail(14)
        atr = float(tr.mean()) if len(tr) else 0.0

        up_thresh = atr * 1.5
        down_thresh = atr * 1.5

        if move_up >= up_thresh and move_up > move_down * 1.5:
            correct = 'BUY'
        elif move_down >= down_thresh and move_down > move_up * 1.5:
            correct = 'SELL'
        else:
            correct = 'NONE'

        def row_to_candle(idx, row):
            try:
                return {
                    "time": int(idx.timestamp()),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            except Exception:
                return None

        visible_candles = [c for c in (row_to_candle(i, r) for i, r in visible_df.iterrows()) if c]
        future_candles = [c for c in (row_to_candle(i, r) for i, r in future_df.iterrows()) if c]

        cutoff_time = int(visible_df.index[-1].timestamp())

        return jsonify({
            "symbol": symbol,
            "timeframe": timeframe,
            "structure_trend": trend,
            "candles": visible_candles,
            "future_candles": future_candles,
            "cutoff_time": cutoff_time,
            "correct": correct,
            "move_up": round(move_up, 2),
            "move_down": round(move_down, 2),
            "source": src,
        })
    except Exception as e:
        return jsonify({"error": str(e)})


# ---------- OTHER PAGES ----------
@app.route('/journal')
def journal():
    return render_template('journal.html')


@app.route('/alerts')
def alerts():
    return render_template('alerts.html')


@app.route('/risk-of-ruin')
def risk_of_ruin():
    return render_template('risk_of_ruin.html')


@app.route('/economic-calendar')
def economic_calendar():
    return render_template('economic_calendar.html')


@app.route('/correlation')
def correlation():
    tf = request.args.get('tf', '1h')
    symbols = ['XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'BTCUSD', 'ETHUSD']
    try:
        result = scanner_engine.compute_correlations(symbols, tf, 200)
    except Exception as e:
        result = {"error": str(e)}
    return render_template('correlation.html', data=result, current_tf=tf)


@app.route('/prop-firm')
def prop_firm():
    return render_template('prop_firm.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5006))
    app.run(host='0.0.0.0', port=port, debug=False)