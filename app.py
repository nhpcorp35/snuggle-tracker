"""
Snuggle Vault Tracker — standalone service.
Tracks Snuggle vault LP positions (Base) for one or more wallets.
"""

import os
import time
import base64
import logging

import requests
from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from web3 import Web3
from dotenv import load_dotenv

from snuggle_adapter import fetch_snuggle_positions

load_dotenv()

app = Flask(__name__, static_folder="static")
CORS(app)

logging.basicConfig(level=logging.INFO)

# ── Basic Auth (optional — same pattern as lp-tracker) ──────────────────────
_PASSWORD = os.environ.get("PASSWORD", "")

@app.before_request
def require_auth():
    if not _PASSWORD:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            _, pw = base64.b64decode(auth[6:]).decode().split(":", 1)
            if pw == _PASSWORD:
                return
        except Exception:
            pass
    return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="Snuggle Tracker"'})

# ── RPC setup ─────────────────────────────────────────────────────────────
ALCHEMY_BASE = os.environ.get("ALCHEMY_BASE", "")
DEFAULT_WALLET = os.environ.get("DEFAULT_WALLET", "").strip()

_w3 = None

def get_w3():
    global _w3
    if _w3 is None and ALCHEMY_BASE:
        _w3 = Web3(Web3.HTTPProvider(ALCHEMY_BASE))
    return _w3

# ── Simple in-memory cache (mirrors lp-tracker's pattern) ───────────────────
_cache = {}
_stale_cache = {}
CACHE_TTL = 120

# ── Token price lookup (GeckoTerminal onchain API, by contract address) ────
# Fully free, keyless — no signup required. Confirmed against
# GeckoTerminal's public "Keyless Public API" docs.
_GECKOTERMINAL_TOKEN_PRICE_URL = "https://api.geckoterminal.com/api/v2/simple/networks/base/token_price/{}"


def get_token_prices_usd(addresses: list) -> dict:
    """
    Returns {lowercase_address: price_usd}. Missing/failed lookups are
    simply absent from the returned dict — caller treats those as
    "price unavailable" rather than erroring the whole request.

    Uses GeckoTerminal's free onchain API (no API key needed).
    """
    if not addresses:
        return {}
    unique = sorted(set(a.lower() for a in addresses))
    url = _GECKOTERMINAL_TOKEN_PRICE_URL.format(",".join(unique))
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        token_prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
        return {addr.lower(): float(price) for addr, price in token_prices.items()}
    except Exception as e:
        app.logger.warning("GeckoTerminal price fetch failed: %s", e)
        return {}


def enrich_with_usd_and_apr(positions: list) -> list:
    """Adds range_width_pct, USD values, and a lifetime-average APR
    estimate to each position. Fields are None where price data or
    holdings weren't available — never fabricated."""
    all_addresses = []
    for p in positions:
        all_addresses.append(p["token0"]["address"])
        all_addresses.append(p["token1"]["address"])
    prices = get_token_prices_usd(all_addresses)

    now = time.time()
    for p in positions:
        p["range_width_pct"] = p["range_width_bps"] / 100.0

        price0 = prices.get(p["token0"]["address"].lower())
        price1 = prices.get(p["token1"]["address"].lower())

        position_value_usd = None
        if p["amount0"] is not None and p["amount1"] is not None and price0 is not None and price1 is not None:
            position_value_usd = p["amount0"] * price0 + p["amount1"] * price1
        p["position_value_usd"] = position_value_usd

        cumulative_fees_usd = None
        if price0 is not None and price1 is not None:
            cumulative_fees_usd = p["cumulative_fees0"] * price0 + p["cumulative_fees1"] * price1
        p["cumulative_fees_usd"] = cumulative_fees_usd

        # Lifetime-average APR: (fees earned / current value) annualized
        # over days since deposit. This is a lifetime average, NOT the
        # same methodology as Snuggle's own "Earnings Rate" (which
        # appears to be a recent-window rate, not lifetime) — labelled
        # accordingly in the UI to avoid implying it matches exactly.
        lifetime_apr_pct = None
        days_held = (now - p["deposit_timestamp"]) / 86400.0
        if (cumulative_fees_usd is not None and position_value_usd
                and position_value_usd > 0 and days_held > 0.5):
            lifetime_apr_pct = (cumulative_fees_usd / position_value_usd) * (365.0 / days_held) * 100.0
        p["lifetime_apr_pct"] = lifetime_apr_pct

    return positions


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/snuggle/positions")
def api_snuggle_positions():
    """
    GET /api/snuggle/positions?wallet=0x...
    If wallet is omitted, uses DEFAULT_WALLET env var.
    """
    wallet = request.args.get("wallet", "").strip() or DEFAULT_WALLET
    if not wallet:
        return jsonify({"error": "No wallet specified and no DEFAULT_WALLET configured"}), 400
    if not wallet.startswith("0x") or len(wallet) != 42:
        return jsonify({"error": "Invalid wallet address"}), 400

    cache_key = wallet.lower()
    bust = request.args.get("bust", "0") == "1"
    cached = _cache.get(cache_key)
    if not bust and cached and time.time() - cached["fetched_at"] < CACHE_TTL:
        return jsonify({"positions": cached["positions"], "cached": True,
                        "fetched_at": cached["fetched_at"]})

    w3 = get_w3()
    if not w3:
        return jsonify({"error": "ALCHEMY_BASE RPC not configured"}), 500

    try:
        positions = fetch_snuggle_positions(wallet, w3)
        positions = enrich_with_usd_and_apr(positions)
    except Exception as e:
        app.logger.error("Snuggle fetch failed for %s: %s", wallet, e)
        stale = _stale_cache.get(cache_key)
        if stale:
            app.logger.warning("Serving stale data for %s", wallet)
            return jsonify({"positions": stale["positions"], "cached": True, "stale": True,
                            "fetched_at": stale["fetched_at"]})
        return jsonify({"error": str(e)}), 500

    result = {"positions": positions, "fetched_at": time.time()}
    _cache[cache_key] = result
    _stale_cache[cache_key] = result
    return jsonify({"positions": positions, "cached": False, "fetched_at": result["fetched_at"]})


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "rpc_configured": bool(ALCHEMY_BASE)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
