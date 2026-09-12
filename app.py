"""
Snuggle Vault Tracker — standalone service.
Tracks Snuggle vault LP positions (Base) for one or more wallets.
"""

import os
import time
import json
import fcntl
import base64
import logging
import threading

import requests
from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from web3 import Web3
from dotenv import load_dotenv

from snuggle_adapter import (
    fetch_snuggle_positions,
    VAULT_ADDRESS, VIEWHELPER_ADDRESS,
    MAXFI_VAULT_ADDRESS, MAXFI_VIEWHELPER_ADDRESS,
)

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
# Falls back to DEFAULT_WALLET if you use the same wallet for both protocols.
DEFAULT_WALLET_MAXFI = os.environ.get("DEFAULT_WALLET_MAXFI", "").strip() or DEFAULT_WALLET

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

        # Out-of-range duration, in seconds. 0 means currently in range.
        p["out_of_range_seconds"] = (now - p["out_of_range_since"]) if p["out_of_range_since"] > 0 else 0

    return positions


def compute_portfolio_summary(positions: list) -> dict:
    """Aggregate totals across all positions. Value-weighted average
    APR (weighted by each position's USD value) rather than a naive
    average, so a small position's noisy APR doesn't skew the total."""
    total_value_usd = sum(p["position_value_usd"] for p in positions if p["position_value_usd"] is not None)
    total_fees_usd = sum(p["cumulative_fees_usd"] for p in positions if p["cumulative_fees_usd"] is not None)

    weighted_apr_sum = 0.0
    weight_total = 0.0
    for p in positions:
        if p["lifetime_apr_pct"] is not None and p["position_value_usd"]:
            weighted_apr_sum += p["lifetime_apr_pct"] * p["position_value_usd"]
            weight_total += p["position_value_usd"]
    blended_apr_pct = (weighted_apr_sum / weight_total) if weight_total > 0 else None

    return {
        "total_value_usd": total_value_usd if total_value_usd > 0 else None,
        "total_fees_usd": total_fees_usd if total_fees_usd > 0 else None,
        "blended_apr_pct": blended_apr_pct,
        "position_count": len(positions),
        "out_of_range_count": sum(1 for p in positions if not p["in_range"]),
    }


@app.route("/")
def index():
    return app.send_static_file("index.html")


def _handle_positions_request(cache_prefix: str, vault_address: str, view_helper_address: str,
                               default_wallet: str):
    """Shared logic for both /api/snuggle/positions and /api/maxfi/positions —
    same caching, error-handling, and enrichment, just pointed at different
    contract addresses."""
    wallet = request.args.get("wallet", "").strip() or default_wallet
    if not wallet:
        return jsonify({"error": f"No wallet specified and no default wallet configured for {cache_prefix}"}), 400
    if not wallet.startswith("0x") or len(wallet) != 42:
        return jsonify({"error": "Invalid wallet address"}), 400

    cache_key = f"{cache_prefix}:{wallet.lower()}"
    bust = request.args.get("bust", "0") == "1"
    cached = _cache.get(cache_key)
    if not bust and cached and time.time() - cached["fetched_at"] < CACHE_TTL:
        return jsonify({"positions": cached["positions"], "portfolio": cached["portfolio"],
                        "cached": True, "fetched_at": cached["fetched_at"]})

    w3 = get_w3()
    if not w3:
        return jsonify({"error": "ALCHEMY_BASE RPC not configured"}), 500

    try:
        positions = fetch_snuggle_positions(wallet, w3, vault_address, view_helper_address)
        positions = enrich_with_usd_and_apr(positions)
    except Exception as e:
        app.logger.error("%s fetch failed for %s: %s", cache_prefix, wallet, e)
        stale = _stale_cache.get(cache_key)
        if stale:
            app.logger.warning("Serving stale %s data for %s", cache_prefix, wallet)
            return jsonify({"positions": stale["positions"], "portfolio": stale["portfolio"],
                            "cached": True, "stale": True, "fetched_at": stale["fetched_at"]})
        return jsonify({"error": str(e)}), 500

    portfolio = compute_portfolio_summary(positions)
    result = {"positions": positions, "portfolio": portfolio, "fetched_at": time.time()}
    _cache[cache_key] = result
    _stale_cache[cache_key] = result
    return jsonify({"positions": positions, "portfolio": portfolio, "cached": False,
                    "fetched_at": result["fetched_at"]})


@app.route("/api/snuggle/positions")
def api_snuggle_positions():
    """GET /api/snuggle/positions?wallet=0x... — Snuggle's own deployment."""
    return _handle_positions_request("snuggle", VAULT_ADDRESS, VIEWHELPER_ADDRESS, DEFAULT_WALLET)


@app.route("/api/maxfi/positions")
def api_maxfi_positions():
    """GET /api/maxfi/positions?wallet=0x... — MaxFi's separate deployment
    of the same contract architecture (confirmed: maxfi.tech is
    white-labeled Snuggle, "Powered by Snuggle" in their own footer)."""
    return _handle_positions_request("maxfi", MAXFI_VAULT_ADDRESS, MAXFI_VIEWHELPER_ADDRESS, DEFAULT_WALLET_MAXFI)


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "rpc_configured": bool(ALCHEMY_BASE)})


# ── Out-of-range Pushover alerts ─────────────────────────────────────────
# Background thread polling every ALERT_CHECK_INTERVAL seconds. Alerts once
# per "stuck episode" (tracked by out_of_range_since timestamp) rather than
# repeatedly — a position that's been out of range for days won't spam you
# every poll, only once when it first crosses the threshold.
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "")
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "")
OUT_OF_RANGE_ALERT_HOURS = float(os.environ.get("OUT_OF_RANGE_ALERT_HOURS", "6"))
ALERT_CHECK_INTERVAL = 600  # 10 minutes

_alerted_episodes = {}  # {token_id: out_of_range_since value already alerted for}


def send_pushover(title: str, message: str):
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        app.logger.warning("Pushover not configured — skipping alert: %s", title)
        return
    try:
        requests.post(
            "https://api.pushover.net/1/messages.json",
            data={"token": PUSHOVER_TOKEN, "user": PUSHOVER_USER, "title": title, "message": message},
            timeout=8,
        )
    except Exception as e:
        app.logger.warning("Pushover send failed: %s", e)


def check_out_of_range_alerts():
    """Runs in a background thread. Checks both Snuggle's and MaxFi's
    positions and fires a Pushover alert for any position freshly
    crossing the out-of-range threshold."""
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        return  # nowhere to send — stay quiet, don't spam logs on every poll
    w3 = get_w3()
    if not w3:
        return

    threshold_seconds = OUT_OF_RANGE_ALERT_HOURS * 3600

    protocols = [
        ("Snuggle", DEFAULT_WALLET, VAULT_ADDRESS, VIEWHELPER_ADDRESS),
        ("MaxFi", DEFAULT_WALLET_MAXFI, MAXFI_VAULT_ADDRESS, MAXFI_VIEWHELPER_ADDRESS),
    ]

    for protocol_name, wallet, vault_address, view_helper_address in protocols:
        if not wallet:
            continue
        try:
            positions = fetch_snuggle_positions(wallet, w3, vault_address, view_helper_address)
        except Exception as e:
            app.logger.warning("Alert check (%s): fetch failed: %s", protocol_name, e)
            continue

        for p in positions:
            oor_since = p["out_of_range_since"]
            # Episode key includes protocol so Snuggle and MaxFi token IDs
            # (which can collide as plain integers) never clash.
            episode_key = (protocol_name, p["token_id"])
            if oor_since == 0:
                _alerted_episodes.pop(episode_key, None)  # back in range — reset for next episode
                continue
            duration = time.time() - oor_since
            if duration >= threshold_seconds and _alerted_episodes.get(episode_key) != oor_since:
                sym0, sym1 = p["token0"]["symbol"], p["token1"]["symbol"]
                hours = duration / 3600
                send_pushover(
                    f"{protocol_name} position stuck out of range",
                    f"{sym0}/{sym1} #{p['token_id']} has been out of range for {hours:.1f}h. "
                    f"Check if auto-rebalance/keeper is functioning.",
                )
                _alerted_episodes[episode_key] = oor_since


def _alert_polling_loop():
    while True:
        try:
            check_out_of_range_alerts()
        except Exception as e:
            app.logger.error("Alert polling loop error: %s", e)
        time.sleep(ALERT_CHECK_INTERVAL)


# Started at module level (not inside __main__) so it runs under gunicorn too.
_alert_thread = threading.Thread(target=_alert_polling_loop, daemon=True)
_alert_thread.start()


# ── Historical snapshots ─────────────────────────────────────────────────
# Stored on a Railway persistent volume (/data by default — survives
# redeploys and restarts, unlike the rest of this app's state). Only
# records going forward from whenever this is first deployed — there's
# no way to reconstruct past portfolio value from current on-chain state,
# so "7d"/"30d" views will be sparse until that much real time has passed.
HISTORY_DIR = os.environ.get("HISTORY_DIR", "/data")
SNAPSHOT_INTERVAL = 3600  # 1 hour


def _history_file_path(protocol: str) -> str:
    return os.path.join(HISTORY_DIR, f"history_{protocol}.json")


# flock() only reliably serializes across separate PROCESSES (e.g. multiple
# gunicorn workers) — verified it does NOT reliably serialize across
# threads within one process (a 20-thread test lost all but 1 write).
# This app's background threads live within a single process, so a real
# in-process Lock is required in addition to flock for cross-process safety.
_history_lock = threading.Lock()


def load_history(protocol: str) -> list:
    path = _history_file_path(protocol)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        app.logger.warning("Failed to read history for %s: %s", protocol, e)
        return []


def append_history_snapshot(protocol: str, snapshot: dict):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = _history_file_path(protocol)
    with _history_lock:
        try:
            if not os.path.exists(path):
                with open(path, "w") as f:
                    json.dump([], f)
            with open(path, "r+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    try:
                        history = json.load(f)
                    except Exception:
                        history = []
                    history.append(snapshot)
                    f.seek(0)
                    f.truncate()
                    json.dump(history, f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            app.logger.error("Failed to write history snapshot for %s: %s", protocol, e)


def capture_snapshot(protocol_name: str, wallet: str, vault_address: str, view_helper_address: str):
    if not wallet:
        return
    w3 = get_w3()
    if not w3:
        return
    try:
        positions = fetch_snuggle_positions(wallet, w3, vault_address, view_helper_address)
        positions = enrich_with_usd_and_apr(positions)
        portfolio = compute_portfolio_summary(positions)
    except Exception as e:
        app.logger.warning("Snapshot capture (%s) failed: %s", protocol_name, e)
        return

    snapshot = {
        "ts": time.time(),
        "total_value_usd": portfolio["total_value_usd"],
        "total_fees_usd": portfolio["total_fees_usd"],
        "blended_apr_pct": portfolio["blended_apr_pct"],
        "position_count": portfolio["position_count"],
        "out_of_range_count": portfolio["out_of_range_count"],
    }
    append_history_snapshot(protocol_name, snapshot)


def _snapshot_loop():
    while True:
        try:
            capture_snapshot("snuggle", DEFAULT_WALLET, VAULT_ADDRESS, VIEWHELPER_ADDRESS)
            capture_snapshot("maxfi", DEFAULT_WALLET_MAXFI, MAXFI_VAULT_ADDRESS, MAXFI_VIEWHELPER_ADDRESS)
        except Exception as e:
            app.logger.error("Snapshot loop error: %s", e)
        time.sleep(SNAPSHOT_INTERVAL)


_snapshot_thread = threading.Thread(target=_snapshot_loop, daemon=True)
_snapshot_thread.start()


_RANGE_TO_SECONDS = {"7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400, "all": None}


@app.route("/api/<protocol>/history")
def api_history(protocol):
    if protocol not in ("snuggle", "maxfi"):
        return jsonify({"error": "Unknown protocol"}), 404

    range_key = request.args.get("range", "30d")
    if range_key not in _RANGE_TO_SECONDS:
        return jsonify({"error": "range must be one of: 7d, 30d, 90d, all"}), 400

    history = load_history(protocol)
    window_seconds = _RANGE_TO_SECONDS[range_key]
    if window_seconds is not None:
        cutoff = time.time() - window_seconds
        history = [s for s in history if s["ts"] >= cutoff]

    return jsonify({"snapshots": history, "range": range_key})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
