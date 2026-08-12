"""Pyth Network oracle price feed — free tier BTC/USD price.

Used as a fallback tier between Chainlink on-chain and Coinbase spot
in the price-source chain. The Pyth oracle uses a different network
and aggregation method than Chainlink, so it serves as an independent
cross-check.
"""
import os
import time
import logging
import requests

log = logging.getLogger("verge.pyth")

# Pyth price feed IDs (mainnet)
PYTH_BTC_USD_FEED = "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43"

PYTH_API = "https://api.pyth.network/v2/quotes"
_timeout = float(os.environ.get("HTTP_TIMEOUT", "10"))


def get_pyth_btc_price_value() -> float | None:
    """Fetch BTC/USD from Pyth oracle. Returns price or None on failure."""
    try:
        resp = requests.get(
            PYTH_API,
            params={"ids[]": PYTH_BTC_USD_FEED},
            timeout=_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") or data.get("parsed") or []
        if isinstance(items, list) and items:
            price_obj = items[0].get("price") or items[0]
            price = price_obj.get("price") or price_obj.get("pp")
            if price is not None:
                return float(price)
    except Exception as e:
        log.debug(f"Pyth price fetch failed (non-fatal): {e}")
    return None


if __name__ == "__main__":
    price = get_pyth_btc_price_value()
    if price:
        print(f"Pyth BTC/USD: ${price:,.2f}")
    else:
        print("Pyth fetch failed")
