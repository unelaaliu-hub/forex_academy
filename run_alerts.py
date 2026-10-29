import time
import logging
import scanner_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("alerts")

# The pairs you want the bot to monitor automatically
WATCHLIST = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD"]

def run_scanner_loop():
    logger.info("🚀 Starting Background Telegram Scanner...")
    logger.info(f"Watching: {WATCHLIST}")
    
    while True:
        for symbol in WATCHLIST:
            try:
                # The analyze function triggers the Telegram alerts inside it
                result = scanner_engine.analyze(symbol, "15m")
                
                if "error" in result:
                    logger.warning(f"Error on {symbol}: {result['error']}")
                    
            except Exception as e:
                logger.error(f"Crash on {symbol}: {e}")
            
            # Sleep 2 seconds between pairs to protect OANDA API limits
            time.sleep(2)
            
        logger.info("💤 Scan cycle complete. Sleeping for 5 minutes...")
        time.sleep(300) # Wait 5 minutes before checking all pairs again

if __name__ == "__main__":
    run_scanner_loop()