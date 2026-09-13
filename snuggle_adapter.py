"""
snuggle_adapter.py

Fetches vault LP positions (Base) directly from on-chain contracts,
for Snuggle itself or any white-labeled deployment of the same
contract architecture (e.g. MaxFi — confirmed via maxfi.tech's own
site footer: "Powered by Snuggle").

Validated against Snuggle's own UI on 4 real positions (2 Uniswap V3,
2 PancakeSwap V3) — all matched exactly or within expected drift from
ongoing fee compounding.

Self-contained: does its own ERC20 symbol/decimals lookups and
caching, so this file has no dependency on any other app's helpers.
"""

from web3 import Web3

# Snuggle's own deployment (default).
VIEWHELPER_ADDRESS = Web3.to_checksum_address("0x298028007e2aeb04d787c8a8bfa03144cc976a1c")
VAULT_ADDRESS = Web3.to_checksum_address("0xd3923beccb6e1ddb048ed00a0a9bd602d16b7470")

# MaxFi's separate deployment of the identical contract code (same
# ABIs, verified against their own security page listing 15 deployed
# contracts on Base, audited by the same team under the same
# methodology as Snuggle).
MAXFI_VIEWHELPER_ADDRESS = Web3.to_checksum_address("0x286490622bcc7261c0ce794b7166dc67d3ce18bd")
MAXFI_VAULT_ADDRESS = Web3.to_checksum_address("0x7d27cdfbfcc878f7e7349e216d44204bfd2afd55")

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

# rewardAdapter — found via direct on-chain probing, not any published
# interface (this is a private Snuggle contract, unindexed by search).
# pendingRewards(tokenId) confirmed to work and return live amounts for
# real positions. The reward TOKEN itself couldn't be confirmed the
# same direct way — tried ~25 candidate getter names (rewardToken(),
# CAKE(), asset(), etc., both bare and tokenId-parameterized), none
# hit. Inferred as CAKE instead, from strong circumstantial evidence:
# every reward-bearing position found is a PancakeSwap pool (Uniswap V3
# pools show a zero reward adapter), and this adapter's own
# pendingRewards() value is the same order of magnitude as calling
# PancakeSwap's MasterChefV3.pendingCake() directly on the same
# tokenId (not identical — Snuggle likely nets its own fee/timing logic
# on top — but consistent with the same underlying token). If a future
# Aerodrome-backed Snuggle pool ever gets a reward adapter, this
# assumption would need re-checking — it does NOT generalize to AERO.
CAKE_TOKEN_BASE = Web3.to_checksum_address("0x3055913c90Fcc1a6CE9a358911721eEb942013A1")

REWARD_ADAPTER_ABI = [
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "pendingRewards",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
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


def _get_pool_config(vault, vault_address: str, pool_id: bytes) -> tuple:
    cache_key = (vault_address, pool_id)
    if cache_key in _pool_cache:
        return _pool_cache[cache_key]
    cfg = vault.functions.approvedPools(pool_id).call()
    _pool_cache[cache_key] = cfg
    return cfg


def fetch_snuggle_positions(wallet: str, w3, vault_address: str = VAULT_ADDRESS,
                             view_helper_address: str = VIEWHELPER_ADDRESS) -> list:
    """
    Fetch all vault positions for a wallet on Base, for any deployment
    of this contract architecture (Snuggle itself, or a white-labeled
    fork like MaxFi — same ABIs, different addresses).

    Defaults to Snuggle's own deployment. Pass vault_address and
    view_helper_address to point at a different deployment (e.g.
    MaxFi's contracts).

    Raises on total failure (e.g. RPC down); caller should wrap in
    try/except.
    """
    wallet = Web3.to_checksum_address(wallet)
    vault_address = Web3.to_checksum_address(vault_address)
    view_helper_address = Web3.to_checksum_address(view_helper_address)
    view_helper = w3.eth.contract(address=view_helper_address, abi=VIEWHELPER_ABI)
    vault = w3.eth.contract(address=vault_address, abi=VAULT_ABI)

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

        pool_cfg = _get_pool_config(vault, vault_address, pool_id)
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
        current_price = None
        price_lower = None
        price_upper = None
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

            # Human-readable price, quoted as token1 per token0 (standard
            # Uniswap V3 convention), decimal-adjusted. E.g. for a
            # WETH(0)/USDC(1) pool this gives USDC per WETH — matches
            # what lptracker.info shows on its price-range bar.
            decimal_adjustment = 10 ** (dec0 - dec1)
            current_price = (sqrt_price ** 2) * decimal_adjustment
            price_lower = (sqrt_lower ** 2) * decimal_adjustment
            price_upper = (sqrt_upper ** 2) * decimal_adjustment
        except Exception:
            pass  # leave amount0/amount1/prices as None — caller shows "unavailable"

        pending_reward = None
        reward_token_symbol = None
        reward_token_address = None
        if reward_adapter != "0x0000000000000000000000000000000000000000":
            try:
                reward_contract = w3.eth.contract(
                    address=Web3.to_checksum_address(reward_adapter), abi=REWARD_ADAPTER_ABI
                )
                pending_raw = reward_contract.functions.pendingRewards(token_id).call()
                reward_token_address = CAKE_TOKEN_BASE  # see CAKE_TOKEN_BASE comment above
                reward_token_symbol = _token_symbol(w3, reward_token_address)
                reward_dec = _token_decimals(w3, reward_token_address)
                pending_reward = pending_raw / (10 ** reward_dec)
            except Exception:
                pass  # leave reward fields as None rather than guess

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
            "is_staked": reward_adapter != "0x0000000000000000000000000000000000000000",
            "reward_token_address": reward_token_address,
            "reward_token_symbol": reward_token_symbol,
            "pending_reward": pending_reward,
            "amount0": amount0,
            "amount1": amount1,
            "current_price": current_price,
            "price_lower": price_lower,
            "price_upper": price_upper,
            "position_adapter": position_adapter,
            "range_width_bps": range_width_bps,
            "deposit_timestamp": deposit_ts,
            "out_of_range_since": out_of_range_since,
        })

    return results
