import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, redirect, url_for, request, jsonify
from lessons import LESSONS, MODULES, GLOSSARY
from animations import ANIMATIONS
import scanner_engine
import backtest_engine
import synthetic_engine

app = Flask(__name__)


# ---------- CURRICULUM ----------
@app.route('/')
def index():
    return render_template('index.html', lessons=LESSONS, modules=MODULES)


@app.route('/lesson/<int:lesson_id>')
def lesson(lesson_id):
    lesson_data = next((l for l in LESSONS if l['id'] == lesson_id), None)
    if not lesson_data:
        return redirect(url_for('index'))
    anim = ANIMATIONS.get(lesson_id) if isinstance(ANIMATIONS, dict) else None
    return render_template('lesson.html', lesson=lesson_data, animation=anim)


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


@app.route('/journal')
def journal():
    return render_template('journal.html')


@app.route('/alerts')
def alerts():
    return render_template('alerts.html')


@app.route('/game')
def game():
    return render_template('game.html')


# ---------- BATCH 1: ARMOR ----------
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


# ... (existing imports)

if __name__ == '__main__':
    # Use the PORT environment variable Render provides, or 5006 locally
    port = int(os.environ.get('PORT', 5006))
    app.run(host='0.0.0.0', port=port, debug=False)