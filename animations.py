# Enhanced chart data for ALL lessons
# Each chart supports: candles, zones, lines (horizontal), annotations (pointers), steps

ANIMATIONS = {
    "what-is-forex": {
        "title": "EUR/USD — Trading One Currency For Another",
        "candles": [
            {"o":100,"h":100.3,"l":99.8,"c":100.1},{"o":100.1,"h":100.4,"l":100,"c":100.3},
            {"o":100.3,"h":100.7,"l":100.2,"c":100.5},{"o":100.5,"h":100.9,"l":100.4,"c":100.8},
            {"o":100.8,"h":101.2,"l":100.7,"c":101.0},{"o":101.0,"h":101.4,"l":100.9,"c":101.2},
            {"o":101.2,"h":101.6,"l":101.0,"c":101.4},{"o":101.4,"h":101.8,"l":101.2,"c":101.6},
            {"o":101.6,"h":102.0,"l":101.4,"c":101.8},{"o":101.8,"h":102.2,"l":101.6,"c":102.0}
        ],
        "zones": [],
        "lines": [
            {"price": 100.0, "color": "green", "label": "Bought here: 1 EUR = 1.00", "dashed": True},
            {"price": 102.0, "color": "gold", "label": "Sold here: 1 EUR = 1.02 (profit!)", "dashed": True}
        ],
        "annotations": [
            {"index": 0, "price": 100.1, "text": "You buy EUR using USD", "color": "green", "position": "below"},
            {"index": 9, "price": 102.0, "text": "You sell EUR back", "color": "gold", "position": "above"}
        ],
        "steps": ["Buy EUR with USD", "Price rises from 1.00 to 1.02", "Sell back → +2% profit"]
    },

    "currency-pairs": {
        "title": "EUR/USD — Base vs Quote",
        "candles": [
            {"o":100,"h":100.4,"l":99.8,"c":100.2},{"o":100.2,"h":100.5,"l":100,"c":100.3},
            {"o":100.3,"h":100.7,"l":100.2,"c":100.5},{"o":100.5,"h":100.8,"l":100.4,"c":100.6},
            {"o":100.6,"h":101.0,"l":100.5,"c":100.8},{"o":100.8,"h":101.2,"l":100.7,"c":101.0},
            {"o":101.0,"h":101.3,"l":100.8,"c":101.1},{"o":101.1,"h":101.5,"l":101.0,"c":101.3},
            {"o":101.3,"h":101.6,"l":101.1,"c":101.4},{"o":101.4,"h":101.8,"l":101.3,"c":101.6}
        ],
        "zones": [],
        "lines": [],
        "annotations": [
            {"index": 2, "price": 100.5, "text": "EUR = BASE (you're buying this)", "color": "green", "position": "above"},
            {"index": 7, "price": 101.3, "text": "USD = QUOTE (you pay with this)", "color": "red", "position": "below"}
        ],
        "steps": ["First = base currency you BUY", "Second = quote currency you PAY", "Price UP = base getting stronger"]
    },

    "pips-lots-spreads": {
        "title": "1 Pip = The Smallest Move",
        "candles": [
            {"o":100.000,"h":100.010,"l":99.995,"c":100.005},
            {"o":100.005,"h":100.015,"l":100.000,"c":100.010},
            {"o":100.010,"h":100.020,"l":100.005,"c":100.015},
            {"o":100.015,"h":100.025,"l":100.010,"c":100.020},
            {"o":100.020,"h":100.030,"l":100.015,"c":100.025},
            {"o":100.025,"h":100.035,"l":100.020,"c":100.030},
            {"o":100.030,"h":100.040,"l":100.025,"c":100.035},
            {"o":100.035,"h":100.045,"l":100.030,"c":100.040},
            {"o":100.040,"h":100.050,"l":100.035,"c":100.045},
            {"o":100.045,"h":100.055,"l":100.040,"c":100.050}
        ],
        "zones": [],
        "lines": [
            {"price": 100.000, "color": "red", "label": "SELL 1.0000", "dashed": True},
            {"price": 100.002, "color": "green", "label": "BUY 1.0002 = spread 2 pips", "dashed": True}
        ],
        "annotations": [
            {"index": 5, "price": 100.030, "text": "Price moved 30 pips here", "color": "cyan", "position": "above"}
        ],
        "steps": ["Spread = gap between BUY and SELL", "1 pip = 4th decimal", "Standard lot = $10 per pip"]
    },

    "reading-charts": {
        "title": "Anatomy of One Candle",
        "candles": [
            {"o":100,"h":100.3,"l":99.8,"c":100.2},
            {"o":100.2,"h":100.4,"l":100.1,"c":100.3},
            {"o":100.3,"h":100.5,"l":100.2,"c":100.4},
            {"o":100.4,"h":102.5,"l":100.3,"c":102.0},   # Big green candle with tall wick
            {"o":102.0,"h":102.2,"l":101.8,"c":102.1},
            {"o":102.1,"h":102.3,"l":102.0,"c":102.2},
            {"o":102.2,"h":102.4,"l":102.1,"c":102.3},
            {"o":102.3,"h":102.5,"l":102.2,"c":102.4},
            {"o":102.4,"h":102.6,"l":102.3,"c":102.5}
        ],
        "zones": [],
        "lines": [
            {"price": 100.4, "color": "cyan", "label": "OPEN", "dashed": True},
            {"price": 102.0, "color": "green", "label": "CLOSE", "dashed": True},
            {"price": 102.5, "color": "red", "label": "HIGH (top wick)", "dashed": True},
            {"price": 100.3, "color": "gold", "label": "LOW (bottom wick)", "dashed": True}
        ],
        "annotations": [
            {"index": 3, "price": 101.2, "text": "Body = OPEN to CLOSE", "color": "cyan", "position": "below"},
            {"index": 3, "price": 102.5, "text": "Long wick = rejection", "color": "red", "position": "above"}
        ],
        "steps": ["GREEN = close > open (up)", "RED = close < open (down)", "Wicks show rejected prices"]
    },

    "market-structure": {
        "title": "Higher Highs & Higher Lows = Uptrend",
        "candles": [
            {"o":100,"h":100.5,"l":99.8,"c":100.3},{"o":100.3,"h":101.0,"l":100.2,"c":100.9},
            {"o":100.9,"h":101.5,"l":100.8,"c":101.4},{"o":101.4,"h":101.3,"l":100.5,"c":100.8},
            {"o":100.8,"h":100.9,"l":100.0,"c":100.2},{"o":100.2,"h":100.5,"l":100.1,"c":100.4},
            {"o":100.4,"h":102.0,"l":100.3,"c":101.9},{"o":101.9,"h":102.5,"l":101.8,"c":102.4},
            {"o":102.4,"h":102.3,"l":101.5,"c":101.8},{"o":101.8,"h":102.0,"l":101.0,"c":101.2},
            {"o":101.2,"h":103.0,"l":101.1,"c":102.9}
        ],
        "zones": [],
        "lines": [],
        "annotations": [
            {"index": 2, "price": 101.5, "text": "1st HIGH", "color": "red", "position": "above"},
            {"index": 4, "price": 100.0, "text": "1st LOW", "color": "green", "position": "below"},
            {"index": 7, "price": 102.5, "text": "HH (higher high)", "color": "red", "position": "above"},
            {"index": 9, "price": 101.0, "text": "HL (higher low)", "color": "green", "position": "below"}
        ],
        "steps": ["High gets higher = HH", "Low gets higher = HL", "HH + HL = BULLISH trend"]
    },

    "supply-demand": {
        "title": "Demand Zone Bounce vs Supply Zone Rejection",
        "candles": [
            {"o":100,"h":100.3,"l":99.6,"c":99.8},
            {"o":99.8,"h":100.0,"l":99.3,"c":99.5},   # Small base = demand
            {"o":99.5,"h":101.5,"l":99.4,"c":101.4},  # Strong bounce
            {"o":101.4,"h":102.5,"l":101.3,"c":102.3},
            {"o":102.3,"h":103.0,"l":102.1,"c":102.9},
            {"o":102.9,"h":103.2,"l":102.5,"c":102.7},  # Base at top = supply
            {"o":102.7,"h":102.8,"l":101.0,"c":101.2},  # Strong rejection down
            {"o":101.2,"h":101.4,"l":100.0,"c":100.2},
            {"o":100.2,"h":100.5,"l":99.5,"c":99.7},
            {"o":99.7,"h":100.0,"l":99.4,"c":99.6}
        ],
        "zones": [
            {"start": 0, "end": 10, "top": 99.8, "bottom": 99.3, "color": "green", "label": "DEMAND zone"},
            {"start": 0, "end": 10, "top": 103.2, "bottom": 102.7, "color": "red", "label": "SUPPLY zone"}
        ],
        "lines": [],
        "annotations": [
            {"index": 2, "price": 101.5, "text": "Price bounced UP", "color": "green", "position": "above"},
            {"index": 6, "price": 101.0, "text": "Price rejected DOWN", "color": "red", "position": "below"}
        ],
        "steps": ["Demand zone = buyers push price up", "Supply zone = sellers push price down", "Fresh zones are strongest"]
    },

    "timeframes": {
        "title": "Zoom Out = See The Real Trend",
        "candles": [
            {"o":100,"h":100.6,"l":99.8,"c":100.4},{"o":100.4,"h":100.8,"l":100.2,"c":100.6},
            {"o":100.6,"h":101.2,"l":100.5,"c":101.0},{"o":101.0,"h":101.5,"l":100.8,"c":101.3},
            {"o":101.3,"h":102.0,"l":101.2,"c":101.8},{"o":101.8,"h":102.5,"l":101.6,"c":102.3},
            {"o":102.3,"h":102.8,"l":102.1,"c":102.6},{"o":102.6,"h":103.2,"l":102.4,"c":103.0},
            {"o":103.0,"h":103.5,"l":102.8,"c":103.3},{"o":103.3,"h":103.8,"l":103.1,"c":103.6}
        ],
        "zones": [],
        "lines": [
            {"price": 100.0, "color": "green", "label": "H4 uptrend start", "dashed": True},
            {"price": 103.8, "color": "gold", "label": "H4 trend still going UP", "dashed": True}
        ],
        "annotations": [
            {"index": 4, "price": 101.8, "text": "Small H1 dip", "color": "red", "position": "below"},
            {"index": 9, "price": 103.6, "text": "But H4 shows clean uptrend", "color": "green", "position": "above"}
        ],
        "steps": ["D1/H4 = direction", "H1 = details", "M15 = entries", "Only trade when HTF agrees"]
    },

    "the-strategy": {
        "title": "The 4 Steps: Break → Retest → Reject → Enter",
        "candles": [
            {"o":100,"h":100.4,"l":99.8,"c":100.2},
            {"o":100.2,"h":100.6,"l":100.0,"c":100.5},
            {"o":100.5,"h":101.0,"l":100.4,"c":100.8},  # Swing high at 101.0
            {"o":100.8,"h":101.0,"l":100.5,"c":100.7},  # Pullback
            {"o":100.7,"h":101.8,"l":100.6,"c":101.6},  # BREAKOUT
            {"o":101.6,"h":102.0,"l":101.4,"c":101.7},
            {"o":101.7,"h":101.8,"l":100.9,"c":101.0},  # RETEST to 101.0
            {"o":101.0,"h":101.5,"l":100.9,"c":101.4},  # REJECTION + entry
            {"o":101.4,"h":102.3,"l":101.3,"c":102.1},
            {"o":102.1,"h":102.8,"l":102.0,"c":102.6}
        ],
        "zones": [],
        "lines": [
            {"price": 101.0, "color": "gold", "label": "Key level 101.0", "dashed": True},
            {"price": 100.7, "color": "red", "label": "STOP LOSS", "dashed": True},
            {"price": 102.8, "color": "green", "label": "TAKE PROFIT", "dashed": True}
        ],
        "annotations": [
            {"index": 4, "price": 101.8, "text": "1. BREAKOUT", "color": "cyan", "position": "above"},
            {"index": 6, "price": 100.9, "text": "2. RETEST", "color": "gold", "position": "below"},
            {"index": 7, "price": 101.5, "text": "3. REJECTION", "color": "green", "position": "above"},
            {"index": 9, "price": 102.6, "text": "4. PROFIT", "color": "green", "position": "above"}
        ],
        "steps": ["Wait for CLOSE above level", "Wait for RETEST", "Look for rejection wick", "Enter with SL/TP"]
    },

    "entry-sl-tp": {
        "title": "Entry, Stop Loss, Take Profit",
        "candles": [
            {"o":100,"h":100.3,"l":99.8,"c":100.2},
            {"o":100.2,"h":100.5,"l":100.0,"c":100.4},
            {"o":100.4,"h":101.0,"l":100.3,"c":100.8},
            {"o":100.8,"h":101.5,"l":100.7,"c":101.3},
            {"o":101.3,"h":101.4,"l":100.6,"c":100.8},  # Retest
            {"o":100.8,"h":101.0,"l":100.5,"c":100.6},  # Bottom
            {"o":100.6,"h":101.8,"l":100.6,"c":101.6},  # Bounce
            {"o":101.6,"h":102.5,"l":101.5,"c":102.3},
            {"o":102.3,"h":103.0,"l":102.2,"c":102.8}
        ],
        "zones": [],
        "lines": [
            {"price": 100.7, "color": "gold", "label": "ENTRY 100.70", "dashed": False},
            {"price": 100.4, "color": "red", "label": "STOP LOSS 100.40 (30 pips)", "dashed": True},
            {"price": 101.6, "color": "green", "label": "TP1 101.60 (90 pips)", "dashed": True},
            {"price": 102.8, "color": "green", "label": "TP2 102.80", "dashed": True}
        ],
        "annotations": [
            {"index": 4, "price": 100.6, "text": "SL below swing low", "color": "red", "position": "below"},
            {"index": 8, "price": 102.8, "text": "R:R = 1:3", "color": "green", "position": "above"}
        ],
        "steps": ["Entry at retested level", "SL beyond swing low/high", "TP at next zone", "Aim R:R ≥ 1:2"]
    },

    "risk-management": {
        "title": "The 1% Rule — Never Risk More",
        "candles": [
            {"o":100,"h":100.3,"l":99.8,"c":100.1},{"o":100.1,"h":100.4,"l":100.0,"c":100.2},
            {"o":100.2,"h":100.6,"l":100.1,"c":100.4},{"o":100.4,"h":100.8,"l":100.3,"c":100.6},
            {"o":100.6,"h":101.0,"l":100.5,"c":100.8},{"o":100.8,"h":101.3,"l":100.7,"c":101.1},
            {"o":101.1,"h":101.6,"l":101.0,"c":101.4},{"o":101.4,"h":101.9,"l":101.3,"c":101.7},
            {"o":101.7,"h":102.2,"l":101.5,"c":102.0}
        ],
        "zones": [],
        "lines": [
            {"price": 100.0, "color": "green", "label": "Entry", "dashed": True},
            {"price": 99.5, "color": "red", "label": "SL = $10 risk (1% of $1,000)", "dashed": True},
            {"price": 102.0, "color": "gold", "label": "TP = $40 reward (4x risk)", "dashed": True}
        ],
        "annotations": [
            {"index": 3, "price": 100.8, "text": "Small position = survive", "color": "green", "position": "above"},
            {"index": 8, "price": 102.0, "text": "Account grows slowly", "color": "gold", "position": "above"}
        ],
        "steps": ["Risk max 1% per trade", "Position size = risk ÷ SL pips", "10 losses in a row = only -10%"]
    },

    "psychology": {
        "title": "Emotional vs Disciplined Trading",
        "candles": [
            {"o":100,"h":103.0,"l":99.5,"c":102.5},    # Wild candle
            {"o":102.5,"h":102.7,"l":98.0,"c":98.5},   # Emotional drop
            {"o":98.5,"h":101.5,"l":98.0,"c":101.0},   # Emotional spike
            {"o":101.0,"h":101.2,"l":97.5,"c":98.0},   # Another drop
            {"o":98.0,"h":100.5,"l":97.8,"c":100.2},
            {"o":100.2,"h":100.5,"l":97.0,"c":97.5},
            {"o":97.5,"h":100.0,"l":97.3,"c":99.5},    # End: still down
            {"o":99.5,"h":100.0,"l":98.0,"c":98.5},
            {"o":98.5,"h":99.5,"l":97.5,"c":98.0}
        ],
        "zones": [],
        "lines": [
            {"price": 100.0, "color": "red", "label": "Started here", "dashed": True},
            {"price": 98.0, "color": "red", "label": "Ended here (LOSS)", "dashed": True}
        ],
        "annotations": [
            {"index": 0, "price": 103.0, "text": "GREED: Oversized entry", "color": "red", "position": "above"},
            {"index": 3, "price": 97.5, "text": "REVENGE: Chasing losses", "color": "red", "position": "below"},
            {"index": 6, "price": 99.5, "text": "Emotions = empty account", "color": "red", "position": "above"}
        ],
        "steps": ["FEAR = close winners early", "GREED = oversized positions", "REVENGE = overtrading after losses", "Discipline beats all three"]
    },

    "your-first-trade": {
        "title": "Your First Trade — All Boxes Checked",
        "candles": [
            {"o":100,"h":100.4,"l":99.8,"c":100.2},
            {"o":100.2,"h":100.6,"l":100.0,"c":100.5},
            {"o":100.5,"h":101.0,"l":100.4,"c":100.8},   # Swing high
            {"o":100.8,"h":100.9,"l":100.2,"c":100.4},   # Pullback
            {"o":100.4,"h":101.7,"l":100.3,"c":101.5},   # Breakout
            {"o":101.5,"h":101.8,"l":101.2,"c":101.4},
            {"o":101.4,"h":101.5,"l":100.9,"c":101.0},   # Retest
            {"o":101.0,"h":101.3,"l":100.8,"c":101.2},   # Rejection wick
            {"o":101.2,"h":102.5,"l":101.1,"c":102.3},
            {"o":102.3,"h":103.5,"l":102.2,"c":103.2}
        ],
        "zones": [],
        "lines": [
            {"price": 101.0, "color": "gold", "label": "Key level (broken + retested)", "dashed": True},
            {"price": 100.5, "color": "red", "label": "SL: 1% risk", "dashed": True},
            {"price": 103.5, "color": "green", "label": "TP: 3R profit", "dashed": True}
        ],
        "annotations": [
            {"index": 2, "price": 101.0, "text": "✅ HTF bullish", "color": "green", "position": "above"},
            {"index": 6, "price": 100.9, "text": "✅ Retest held", "color": "green", "position": "below"},
            {"index": 7, "price": 101.3, "text": "✅ Rejection wick", "color": "green", "position": "above"},
            {"index": 9, "price": 103.5, "text": "✅ R:R = 1:3", "color": "gold", "position": "above"}
        ],
        "steps": ["ALL checklist boxes must be ✅", "If ANY fails → skip the trade", "Missing a trade costs $0", "Journal every single trade"]
    }
}