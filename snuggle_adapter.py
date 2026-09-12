"""
snuggle_adapter.py

Fetches Snuggle vault LP positions (Base) directly from on-chain
contracts. Validated against Snuggle's own UI on 4 real positions
(2 Uniswap V3, 2 PancakeSwap V3) — all matched exactly or within
expected drift from ongoing fee compounding.

Self-contained: does its own ERC20 symbol/decimals lookups and
caching, so this file has no dependency on any other app's helpers.
"""

from web3 import Web3

VIEWHELPER_ADDRESS = Web3.to_checksum_address("0x298028007e2aeb04d787c8a8bfa03144cc976a1c")
VAULT_ADDRESS = Web3.to_checksum_address("0xd3923beccb6e1ddb048ed00a0a9bd602d16b7470")

VIEWHELPER_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getUserPositions",
        "outputs": [{"internalType": "uint256[]", "name": "", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
]

VAULT_ABI = [
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
                    {"internalType": "bytes32", "name": "poolId", "type": "bytes32"},
                    {"internalType": "address", "name": "owner", "type": "address"},
                    {"internalType": "uint24", "name": "rangeWidthBps", "type": "uint24"},
                    {"internalType": "int24", "name": "currentTickLower", "type": "int24"},
                    {"internalType": "int24", "name": "currentTickUpper", "type": "int24"},
                    {"internalType": "bool", "name": "autoSnuggleEnabled", "type": "bool"},
                    {"internalType": "bool", "name": "autoCompoundEnabled", "type": "bool"},
                    {"internalType": "uint64", "name": "rebalanceDelay", "type": "uint64"},
                    {"internalType": "uint64", "name": "outOfRangeSince", "type": "uint64"},
                    {"internalType": "uint32", "name": "totalRebalances", "type": "uint32"},
                    {"internalType": "uint32", "name": "lastRebalanceTime", "type": "uint32"},
                    {"internalType": "uint64", "name": "depositTimestamp", "type": "uint64"},
                    {"internalType": "uint128", "name": "cumulativeFees0", "type": "uint128"},
                    {"internalType": "uint128", "name": "cumulativeFees1", "type": "uint128"},
                    {"internalType": "uint128", "name": "cumulativeRewards", "type": "uint128"},
                    {"internalType": "uint128", "name": "__reserved", "type": "uint128"},
                ],
                "internalType": "struct ISnuggleVault.UserPosition",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "poolId", "type": "bytes32"}],
        "name": "approvedPools",
        "outputs": [
            {
                "components": [
                    {"internalType": "address", "name": "pool", "type": "address"},
                    {"internalType": "address", "name": "token0", "type": "address"},
                    {"internalType": "address", "name": "token1", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "int24", "name": "tickSpacing", "type": "int24"},
                    {"internalType": "bool", "name": "active", "type": "bool"},
                    {"internalType": "address", "name": "positionAdapter", "type": "address"},
                    {"internalType": "address", "name": "rewardAdapter", "type": "address"},
                ],
                "internalType": "struct ISnuggleVault.PoolConfig",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

ADAPTER_ABI = [
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "getPosition",
        "outputs": [
            {"internalType": "address", "name": "token0", "type": "address"},
            {"internalType": "address", "name": "token1", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "int24", "name": "tickLower", "type": "int24"},
            {"internalType": "int24", "name": "tickUpper", "type": "int24"},
            {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

# Standard Uniswap V3 pool slot0() — feeProtocol as uint8.
UNISWAP_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

# PancakeSwap V3 pool slot0() — feeProtocol is a packed uint32, not
# uint8. Confirmed by decoding a real on-chain response.
PANCAKE_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint32", "name": "feeProtocol", "type": "uint32"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "symbol",
     "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
]

_symbol_cache: dict = {}
_decimals_cache: dict = {}
_pool_cache: dict = {}


def _token_symbol(w3, address: str) -> str:
    address = Web3.to_checksum_address(address)
    if address in _symbol_cache:
        return _symbol_cache[address]
    try:
        c = w3.eth.contract(address=address, abi=ERC20_ABI)
        sym = c.functions.symbol().call()
    except Exception:
        sym = address[:10] + "..."
    _symbol_cache[address] = sym
    return sym


def _token_decimals(w3, address: str) -> int:
    address = Web3.to_checksum_address(address)
    if address in _decimals_cache:
        return _decimals_cache[address]
    try:
        c = w3.eth.contract(address=address, abi=ERC20_ABI)
        dec = c.functions.decimals().call()
    except Exception:
        dec = 18
    _decimals_cache[address] = dec
    return dec


def _get_sqrt_price_x96(w3, pool_addr: str) -> int:
    """Try Uniswap-shaped slot0() first, fall back to PancakeSwap's
    uint32-feeProtocol variant on decode failure."""
    pool_addr = Web3.to_checksum_address(pool_addr)
    try:
        pool = w3.eth.contract(address=pool_addr, abi=UNISWAP_POOL_ABI)
        return pool.functions.slot0().call()[0]
    except Exception:
        pool = w3.eth.contract(address=pool_addr, abi=PANCAKE_POOL_ABI)
        return pool.functions.slot0().call()[0]


def _tick_to_sqrt_price(tick: int) -> float:
    return 1.0001 ** (tick / 2)


def _amounts_for_liquidity(sqrt_price: float, sqrt_lower: float, sqrt_upper: float,
                            liquidity: int) -> tuple:
    """Standard Uniswap V3 liquidity -> token amounts math."""
    if sqrt_lower > sqrt_upper:
        sqrt_lower, sqrt_upper = sqrt_upper, sqrt_lower
    if sqrt_price <= sqrt_lower:
        return liquidity * (1 / sqrt_lower - 1 / sqrt_upper), 0
    elif sqrt_price >= sqrt_upper:
        return 0, liquidity * (sqrt_upper - sqrt_lower)
    else:
        amount0 = liquidity * (1 / sqrt_price - 1 / sqrt_upper)
        amount1 = liquidity * (sqrt_price - sqrt_lower)
        return amount0, amount1


def _get_pool_config(vault, pool_id: bytes) -> tuple:
    if pool_id in _pool_cache:
        return _pool_cache[pool_id]
    cfg = vault.functions.approvedPools(pool_id).call()
    _pool_cache[pool_id] = cfg
    return cfg


def fetch_snuggle_positions(wallet: str, w3) -> list:
    """
    Fetch all Snuggle vault positions for a wallet on Base.

    Returns a list of dicts, one per position — see field comments
    below. Raises on total failure (e.g. RPC down); caller should
    wrap in try/except.
    """
    wallet = Web3.to_checksum_address(wallet)
    view_helper = w3.eth.contract(address=VIEWHELPER_ADDRESS, abi=VIEWHELPER_ABI)
    vault = w3.eth.contract(address=VAULT_ADDRESS, abi=VAULT_ABI)

    token_ids = view_helper.functions.getUserPositions(wallet).call()
    results = []

    for token_id in token_ids:
        pos = vault.functions.positions(token_id).call()
        (
            _tid, pool_id, owner, range_width_bps,
            tick_lower, tick_upper, auto_snuggle, auto_compound,
            rebalance_delay, out_of_range_since, total_rebalances,
            last_rebalance_time, deposit_ts, cum_fees0, cum_fees1,
            cum_rewards, _reserved,
        ) = pos

        pool_cfg = _get_pool_config(vault, pool_id)
        (pool_addr, token0_addr, token1_addr, fee, tick_spacing, active,
         position_adapter, reward_adapter) = pool_cfg

        sym0 = _token_symbol(w3, token0_addr)
        sym1 = _token_symbol(w3, token1_addr)
        dec0 = _token_decimals(w3, token0_addr)
        dec1 = _token_decimals(w3, token1_addr)

        cum_fees0_readable = cum_fees0 / (10 ** dec0)
        cum_fees1_readable = cum_fees1 / (10 ** dec1)

        amount0 = None
        amount1 = None
        live_tick_lower = tick_lower
        live_tick_upper = tick_upper
        try:
            adapter = w3.eth.contract(
                address=Web3.to_checksum_address(position_adapter), abi=ADAPTER_ABI
            )
            _t0a, _t1a, _feea, live_tick_lower, live_tick_upper, liquidity = (
                adapter.functions.getPosition(token_id).call()
            )
            sqrt_price_x96 = _get_sqrt_price_x96(w3, pool_addr)
            sqrt_price = sqrt_price_x96 / (2 ** 96)
            sqrt_lower = _tick_to_sqrt_price(live_tick_lower)
            sqrt_upper = _tick_to_sqrt_price(live_tick_upper)
            amt0_raw, amt1_raw = _amounts_for_liquidity(
                sqrt_price, sqrt_lower, sqrt_upper, liquidity
            )
            amount0 = amt0_raw / (10 ** dec0)
            amount1 = amt1_raw / (10 ** dec1)
        except Exception:
            pass  # leave amount0/amount1 as None — caller shows "unavailable"

        in_range = out_of_range_since == 0

        results.append({
            "token_id": token_id,
            "pool_address": pool_addr,
            "token0": {"address": token0_addr, "symbol": sym0, "decimals": dec0},
            "token1": {"address": token1_addr, "symbol": sym1, "decimals": dec1},
            "fee_tier": fee,
            "tick_lower": live_tick_lower,
            "tick_upper": live_tick_upper,
            "in_range": in_range,
            "total_rebalances": total_rebalances,
            "auto_snuggle": auto_snuggle,
            "auto_compound": auto_compound,
            "cumulative_fees0": cum_fees0_readable,
            "cumulative_fees1": cum_fees1_readable,
            "cumulative_rewards_raw": cum_rewards,
            "amount0": amount0,
            "amount1": amount1,
            "position_adapter": position_adapter,
            "range_width_bps": range_width_bps,
            "deposit_timestamp": deposit_ts,
        })

    return results
