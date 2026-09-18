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


# ── Pool trading volume (GeckoTerminal OHLCV, by pool address) ─────────────
# Same free/keyless API family as token prices above. Pool-level, not
# wallet/position-specific — shared across anyone tracking the same
# pool — so cached separately with a longer TTL (daily-granularity data
# doesn't need to be fresher than that, and it keeps us well under
# GeckoTerminal's free-tier rate limit).
_GECKOTERMINAL_OHLCV_URL = "https://api.geckoterminal.com/api/v2/networks/base/pools/{}/ohlcv/day"
_POOL_VOLUME_CACHE = {}
_POOL_VOLUME_CACHE_TTL = 1800  # 30 minutes

_VOLUME_RANGE_DAYS = {"7d": 7, "30d": 30, "60d": 60, "90d": 90, "180d": 180}


def get_pool_volume_usd(pool_address: str, days: int) -> list:
    """Returns [{"ts": unix_seconds, "volume_usd": float}, ...] in
    chronological order (oldest first), one entry per day, for the
    given pool over the last `days` days. Raises on failure — unlike
    get_token_prices_usd, there's no sane "missing" fallback for a
    whole volume chart, so the caller surfaces the error instead of
    silently showing an empty chart."""
    cache_key = f"{pool_address.lower()}:{days}"
    cached = _POOL_VOLUME_CACHE.get(cache_key)
    if cached and time.time() - cached["fetched_at"] < _POOL_VOLUME_CACHE_TTL:
        return cached["candles"]

    url = _GECKOTERMINAL_OHLCV_URL.format(pool_address)
    resp = requests.get(url, params={"aggregate": 1, "limit": days, "currency": "usd"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    ohlcv_list = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
    # GeckoTerminal returns newest-first; charts want chronological.
    candles = sorted(
        ({"ts": row[0], "volume_usd": row[5]} for row in ohlcv_list),
        key=lambda c: c["ts"],
    )
    _POOL_VOLUME_CACHE[cache_key] = {"candles": candles, "fetched_at": time.time()}
    return candles


def enrich_with_usd_and_apr(positions: list) -> list:
    """Adds range_width_pct, USD values, and a lifetime-average APR
    estimate to each position. Fields are None where price data or
    holdings weren't available — never fabricated."""
    all_addresses = []
    for p in positions:
        all_addresses.append(p["token0"]["address"])
        all_addresses.append(p["token1"]["address"])
        if p.get("reward_token_address"):
            all_addresses.append(p["reward_token_address"])
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

        pending_reward_usd = None
        if p.get("pending_reward") is not None and p.get("reward_token_address"):
            reward_price = prices.get(p["reward_token_address"].lower())
            if reward_price is not None:
                pending_reward_usd = p["pending_reward"] * reward_price
        p["pending_reward_usd"] = pending_reward_usd

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
        p["days_held"] = days_held

        # Distance from current price to each range edge, as a % of
        # current price — matches lptracker.info's "X% away" convention
        # (verified against its own displayed numbers).
        pct_from_lower = None
        pct_from_upper = None
        cp, pl, pu = p.get("current_price"), p.get("price_lower"), p.get("price_upper")
        if cp and pl is not None and pu is not None and cp > 0:
            pct_from_lower = (cp - pl) / cp * 100.0
            pct_from_upper = (pu - cp) / cp * 100.0
        p["pct_from_lower"] = pct_from_lower
        p["pct_from_upper"] = pct_from_upper

        # Out-of-range duration, in seconds. 0 means currently in range.
        p["out_of_range_seconds"] = (now - p["out_of_range_since"]) if p["out_of_range_since"] > 0 else 0

    return positions


def attach_pnl(positions: list, protocol_name: str) -> list:
    """Attaches pnl_usd/pnl_pct to each position, relative to the
    baseline recorded in known_<protocol>.json by the background
    snapshot loop. Read-only — this file's writer is capture_snapshot()
    exclusively, to avoid two code paths racing on the same file.
    Positions with no recorded baseline (brand new, or fetched under a
    non-default wallet the snapshot loop doesn't track) get None —
    never a fabricated number."""
    known = _read_json_locked(_known_positions_file_path(protocol_name), {})
    for p in positions:
        entry = known.get(str(p["token_id"]))
        baseline = entry.get("baseline_value_usd") if entry else None
        pnl_usd = None
        pnl_pct = None
        if (baseline is not None and baseline > 0
                and p["position_value_usd"] is not None):
            pnl_usd = p["position_value_usd"] - baseline
            pnl_pct = pnl_usd / baseline * 100.0
        p["baseline_value_usd"] = baseline
        p["pnl_usd"] = pnl_usd
        p["pnl_pct"] = pnl_pct
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

    # Total P&L: sum of pnl_usd across positions that have a baseline.
    # total_pnl_pct is baseline-weighted (total pnl / total baseline),
    # not an average of each position's %, so a large position doesn't
    # get diluted by a small one's noisy percentage.
    pnl_positions = [p for p in positions if p.get("pnl_usd") is not None]
    total_pnl_usd = sum(p["pnl_usd"] for p in pnl_positions) if pnl_positions else None
    total_baseline_usd = sum(p["baseline_value_usd"] for p in pnl_positions) if pnl_positions else 0
    total_pnl_pct = (total_pnl_usd / total_baseline_usd * 100.0) if total_pnl_usd is not None and total_baseline_usd > 0 else None

    return {
        "total_value_usd": total_value_usd if total_value_usd > 0 else None,
        "total_fees_usd": total_fees_usd if total_fees_usd > 0 else None,
        "blended_apr_pct": blended_apr_pct,
        "position_count": len(positions),
        "out_of_range_count": sum(1 for p in positions if not p["in_range"]),
        "total_pnl_usd": total_pnl_usd,
        "total_pnl_pct": total_pnl_pct,
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
        positions = attach_pnl(positions, cache_prefix)
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


def _read_json_locked(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        app.logger.warning("Failed to read %s: %s", path, e)
        return default


def _write_json_locked_nolock(path: str, data):
    """Does the flock-protected file write WITHOUT acquiring the
    in-process _history_lock — for callers that already hold it as
    part of a larger read-modify-write critical section."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump(data if isinstance(data, list) else [], f)
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                f.truncate()
                json.dump(data, f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        app.logger.error("Failed to write %s: %s", path, e)


def _write_json_locked(path: str, data):
    with _history_lock:  # single in-process lock covers all state files — writes are infrequent
        _write_json_locked_nolock(path, data)


def load_history(protocol: str) -> list:
    return _read_json_locked(_history_file_path(protocol), [])


def append_history_snapshot(protocol: str, snapshot: dict):
    path = _history_file_path(protocol)
    with _history_lock:  # held for the ENTIRE read-modify-write — do not release in between
        history = _read_json_locked(path, [])
        history.append(snapshot)
        _write_json_locked_nolock(path, history)


def _known_positions_file_path(protocol: str) -> str:
    return os.path.join(HISTORY_DIR, f"known_{protocol}.json")


def _closed_positions_file_path(protocol: str) -> str:
    return os.path.join(HISTORY_DIR, f"closed_{protocol}.json")


TRANSFER_TOPIC = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().lstrip("0x")
CASHOUT_POLL_INTERVAL = 90  # seconds — matches Base's ~2s blocks comfortably
CASHOUT_CHUNK_BLOCKS = 10  # Alchemy free-tier eth_getLogs cap, confirmed directly

ERC20_MINI_ABI = [{
    "inputs": [], "name": "decimals",
    "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
    "stateMutability": "view", "type": "function",
}, {
    "inputs": [], "name": "symbol",
    "outputs": [{"internalType": "string", "name": "", "type": "string"}],
    "stateMutability": "view", "type": "function",
}]
_token_meta_cache = {}


def _token_meta_cached(w3, addr: str) -> tuple:
    key = addr.lower()
    if key not in _token_meta_cache:
        c = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_MINI_ABI)
        _token_meta_cache[key] = (c.functions.symbol().call(), c.functions.decimals().call())
    return _token_meta_cache[key]


def _cashout_state_file_path() -> str:
    return os.path.join(HISTORY_DIR, "cashout_state.json")


def _cashout_log_file_path() -> str:
    return os.path.join(HISTORY_DIR, "cashout_log.json")


def _get_logs_raw(rpc_url: str, token_addr: str, from_block: int, to_block: int, from_topic: str, to_topic: str) -> list:
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(from_block), "toBlock": hex(to_block),
            "address": token_addr, "topics": [TRANSFER_TOPIC, from_topic, to_topic],
        }],
    }
    resp = requests.post(rpc_url, json=payload, timeout=20)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


def check_cashout_transfers():
    """Detects auto-compound 'cash out' transfers landing directly in
    the tracked wallets — Transfer events FROM a known vault TO the
    position owner, for tokens involved in that owner's current
    positions. Confirmed on-chain earlier that these land as direct
    transfers in the same rebalance transaction, not a separate claim.

    Chunks in CASHOUT_CHUNK_BLOCKS-block windows (Alchemy free-tier
    eth_getLogs cap, hit directly and confirmed via the RPC's own
    error message — not guessed). Starts tracking from whenever this
    first runs, not retroactively (rebuilding history would need
    tens of thousands of chunked calls per position, not practical
    with this RPC tier)."""
    w3 = get_w3()
    if w3 is None:
        return
    rpc_url = ALCHEMY_BASE
    current_block = w3.eth.block_number

    state = _read_json_locked(_cashout_state_file_path(), {})
    last_checked = state.get("last_checked_block")
    if last_checked is None:
        # First run ever — start from now, don't attempt to backfill.
        _write_json_locked(_cashout_state_file_path(), {"last_checked_block": current_block})
        return
    if current_block <= last_checked:
        return

    cases = [
        ("snuggle", DEFAULT_WALLET, VAULT_ADDRESS, VIEWHELPER_ADDRESS),
        ("maxfi", DEFAULT_WALLET_MAXFI, MAXFI_VAULT_ADDRESS, MAXFI_VIEWHELPER_ADDRESS),
    ]
    new_entries = []
    for protocol, wallet, vault_addr, view_helper_addr in cases:
        if not wallet:
            continue
        try:
            positions = fetch_snuggle_positions(wallet, w3, vault_addr, view_helper_addr)
        except Exception as e:
            app.logger.warning("Cashout check: position fetch failed for %s: %s", protocol, e)
            continue

        token_addrs = set()
        for p in positions:
            token_addrs.add(p["token0"]["address"])
            token_addrs.add(p["token1"]["address"])

        from_topic = "0x" + "0" * 24 + vault_addr[2:].lower()
        to_topic = "0x" + "0" * 24 + wallet[2:].lower()

        for token_addr in token_addrs:
            b = last_checked + 1
            while b <= current_block:
                chunk_to = min(b + CASHOUT_CHUNK_BLOCKS - 1, current_block)
                try:
                    logs = _get_logs_raw(rpc_url, token_addr, b, chunk_to, from_topic, to_topic)
                    for log in logs:
                        sym, dec = _token_meta_cached(w3, token_addr)
                        raw_amount = int(log["data"], 16)
                        amount = raw_amount / (10 ** dec)
                        new_entries.append({
                            "protocol": protocol,
                            "token_symbol": sym,
                            "token_address": token_addr,
                            "amount": amount,
                            "tx_hash": log["transactionHash"],
                            "block_number": int(log["blockNumber"], 16),
                            "detected_at": time.time(),
                        })
                except Exception as e:
                    app.logger.warning("Cashout check: log fetch failed for %s [%d,%d]: %s", token_addr, b, chunk_to, e)
                b += CASHOUT_CHUNK_BLOCKS

    if new_entries:
        # Value in USD using current prices — a reasonable snapshot
        # approximation since detection happens close to real-time.
        addrs = list({e["token_address"] for e in new_entries})
        prices = get_token_prices_usd(addrs)
        for e in new_entries:
            price = prices.get(e["token_address"].lower())
            e["amount_usd"] = e["amount"] * price if price is not None else None

        with _history_lock:
            log = _read_json_locked(_cashout_log_file_path(), [])
            log.extend(new_entries)
            _write_json_locked_nolock(_cashout_log_file_path(), log)

    _write_json_locked(_cashout_state_file_path(), {"last_checked_block": current_block})


def _cashout_loop():
    while True:
        try:
            check_cashout_transfers()
        except Exception as e:
            app.logger.error("Cashout loop error: %s", e)
        time.sleep(CASHOUT_POLL_INTERVAL)


_cashout_thread = threading.Thread(target=_cashout_loop, daemon=True)
_cashout_thread.start()


@app.route("/api/cashout-log")
def api_cashout_log():
    log = _read_json_locked(_cashout_log_file_path(), [])
    total_usd = sum(e["amount_usd"] for e in log if e.get("amount_usd") is not None)
    by_token = {}
    for e in log:
        key = e["token_symbol"]
        by_token.setdefault(key, 0.0)
        by_token[key] += e["amount"]
    return jsonify({
        "entries": log,
        "entry_count": len(log),
        "total_usd": total_usd if log else None,
        "totals_by_token": by_token,
    })


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
        # Deliberately return before touching known/closed state below —
        # a failed fetch must never be mistaken for "wallet has zero
        # positions now", which would falsely mark everything closed.
        app.logger.warning("Snapshot capture (%s) failed: %s", protocol_name, e)
        return

    now = time.time()
    snapshot = {
        "ts": now,
        "total_value_usd": portfolio["total_value_usd"],
        "total_fees_usd": portfolio["total_fees_usd"],
        "blended_apr_pct": portfolio["blended_apr_pct"],
        "position_count": portfolio["position_count"],
        "out_of_range_count": portfolio["out_of_range_count"],
    }
    append_history_snapshot(protocol_name, snapshot)

    # Per-position history — one file per (protocol, token_id), same
    # pattern as the portfolio-level file, so the same load/filter/lock
    # logic works for both without new code paths.
    for p in positions:
        pos_snapshot = {
            "ts": now,
            "token_id": p["token_id"],
            "pool": f"{p['token0']['symbol']}/{p['token1']['symbol']}",
            "value_usd": p["position_value_usd"],
            "fees_usd": p["cumulative_fees_usd"],
            "apr_pct": p["lifetime_apr_pct"],
            "in_range": p["in_range"],
        }
        append_history_snapshot(f"{protocol_name}_pos_{p['token_id']}", pos_snapshot)

    # ── Discover new / remove closed positions ──────────────────────────
    # "Known" = the set of token_ids seen live as of the last successful
    # check. New positions need no special handling — they just start
    # accumulating per-position history above, automatically. Closed
    # ones (previously known, now absent) get a final marker appended to
    # their history and move into the closed-positions record.
    known_path = _known_positions_file_path(protocol_name)
    closed_path = _closed_positions_file_path(protocol_name)

    with _history_lock:
        known = _read_json_locked(known_path, {})  # {str(token_id): {"pool": ..., "last_seen": ts}}
        current_ids = {str(p["token_id"]) for p in positions}
        current_by_id = {str(p["token_id"]): p for p in positions}

        closed_now = [tid for tid in known if tid not in current_ids]

        if closed_now:
            closed_list = _read_json_locked(closed_path, [])
            for tid in closed_now:
                last_known = known[tid]
                closed_list.append({
                    "token_id": int(tid),
                    "pool": last_known.get("pool"),
                    "closed_at": now,
                    "last_value_usd": last_known.get("last_value_usd"),
                })
                # Final marker in the position's own history file, so its
                # chart visibly shows where it ends rather than just
                # stopping with no explanation.
                _append_to_list_at_path(
                    _history_file_path(f"{protocol_name}_pos_{tid}"),
                    {"ts": now, "token_id": int(tid), "closed": True},
                )
                _alerted_episodes.pop((_PROTOCOL_DISPLAY_NAME.get(protocol_name, protocol_name), int(tid)), None)
            _write_json_locked_nolock(closed_path, closed_list)

        # Rebuild the known set from current positions (drops closed ones,
        # adds new ones, refreshes last_value_usd/last_seen for the rest).
        #
        # baseline_value_usd/baseline_ts: a "P&L since we started tracking"
        # marker, NOT true lifetime cost basis (we have no historical
        # deposit-time price data). Set once per token_id and carried
        # forward untouched after that. Self-healing: any tid missing a
        # baseline — a brand-new discovery, OR one of the positions that
        # was already open before this feature existed — just gets
        # baselined at its current value on whichever cycle first sees
        # it with a valid position_value_usd. No migration step needed.
        new_known = {}
        for tid in current_ids:
            p = current_by_id[tid]
            prior = known.get(tid, {})
            baseline_value_usd = prior.get("baseline_value_usd")
            baseline_ts = prior.get("baseline_ts")
            if baseline_value_usd is None and p["position_value_usd"] is not None:
                baseline_value_usd = p["position_value_usd"]
                baseline_ts = now
            new_known[tid] = {
                "pool": f"{p['token0']['symbol']}/{p['token1']['symbol']}",
                "pool_address": p["pool_address"],
                "last_value_usd": p["position_value_usd"],
                "last_seen": now,
                "baseline_value_usd": baseline_value_usd,
                "baseline_ts": baseline_ts,
            }
        _write_json_locked_nolock(known_path, new_known)


def _append_to_list_at_path(path: str, item: dict):
    """Like append_history_snapshot but takes a raw path — used for the
    closed-position marker, which writes into an existing per-position
    history file identified by the SAME naming scheme. Caller must
    already hold _history_lock."""
    history = _read_json_locked(path, [])
    history.append(item)
    _write_json_locked_nolock(path, history)


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

# Must match exactly the protocol_name strings used as the first element
# of the tuples in check_out_of_range_alerts()'s `protocols` list —
# "maxfi".capitalize() gives "Maxfi", not "MaxFi", so this can't be
# derived automatically without risking a silent mismatch.
_PROTOCOL_DISPLAY_NAME = {"snuggle": "Snuggle", "maxfi": "MaxFi"}


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


@app.route("/api/<protocol>/history/<int:token_id>")
def api_position_history(protocol, token_id):
    if protocol not in ("snuggle", "maxfi"):
        return jsonify({"error": "Unknown protocol"}), 404

    range_key = request.args.get("range", "30d")
    if range_key not in _RANGE_TO_SECONDS:
        return jsonify({"error": "range must be one of: 7d, 30d, 90d, all"}), 400

    history = load_history(f"{protocol}_pos_{token_id}")
    window_seconds = _RANGE_TO_SECONDS[range_key]
    if window_seconds is not None:
        cutoff = time.time() - window_seconds
        history = [s for s in history if s["ts"] >= cutoff]

    return jsonify({"snapshots": history, "range": range_key, "token_id": token_id})


@app.route("/api/<protocol>/closed")
def api_closed_positions(protocol):
    if protocol not in ("snuggle", "maxfi"):
        return jsonify({"error": "Unknown protocol"}), 404
    closed = _read_json_locked(_closed_positions_file_path(protocol), [])
    closed_sorted = sorted(closed, key=lambda c: c["closed_at"], reverse=True)
    return jsonify({"closed": closed_sorted})


@app.route("/api/<protocol>/pool-volume/<int:token_id>")
def api_pool_volume(protocol, token_id):
    """Pool trading volume (USD), daily bars — external market data via
    GeckoTerminal, not our own tracked history. Looks up the position's
    pool_address from known_<protocol>.json (populated by the
    background snapshot loop) rather than an RPC call, so this stays
    fast even for a position the live wallet fetch hasn't touched."""
    if protocol not in ("snuggle", "maxfi"):
        return jsonify({"error": "Unknown protocol"}), 404

    range_key = request.args.get("range", "30d")
    if range_key not in _VOLUME_RANGE_DAYS:
        return jsonify({"error": "range must be one of: 7d, 30d, 60d, 90d, 180d"}), 400

    known = _read_json_locked(_known_positions_file_path(protocol), {})
    entry = known.get(str(token_id))
    pool_address = entry.get("pool_address") if entry else None
    if not pool_address:
        return jsonify({"error": "Pool address not yet known for this position — "
                                  "check back after the next snapshot cycle."}), 404

    try:
        candles = get_pool_volume_usd(pool_address, _VOLUME_RANGE_DAYS[range_key])
    except Exception as e:
        app.logger.warning("Pool volume fetch failed for %s: %s", pool_address, e)
        return jsonify({"error": "Volume data unavailable right now"}), 502

    return jsonify({"candles": candles, "range": range_key, "token_id": token_id})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
