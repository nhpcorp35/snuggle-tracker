"""
Check why token_id=6042464 shows "Out of Range" when the displayed
current price ($2,642) appears to sit inside the shown bounds
($2,453-$2,649) — verify against real on-chain tick state directly.
"""
import os
from web3 import Web3
import snuggle_adapter as sa

BASE_RPC = os.environ.get("ALCHEMY_BASE")
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
TOKEN_ID = 6042464

for label, vault_addr in [("snuggle", sa.VAULT_ADDRESS), ("maxfi", sa.MAXFI_VAULT_ADDRESS)]:
    vault = w3.eth.contract(address=vault_addr, abi=sa.VAULT_ABI)
    try:
        pos = vault.functions.positions(TOKEN_ID).call()
    except Exception as e:
        print(f"{label}: position not found ({e})")
        continue

    (
        _tid, pool_id, owner, range_width_bps, tick_lower, tick_upper,
        auto_snuggle, auto_compound, rebalance_delay, out_of_range_since,
        total_rebalances, last_rebalance_time, deposit_ts, cum_fees0,
        cum_fees1, cum_rewards, _reserved,
    ) = pos
    if tick_lower == 0 and tick_upper == 0 and total_rebalances == 0 and deposit_ts == 0:
        print(f"{label}: no real position for this token_id (empty struct returned, not reverted)")
        continue
    print(f"\n{'='*60}\nFound on {label}: tick_lower={tick_lower}, tick_upper={tick_upper}")
    print(f"out_of_range_since={out_of_range_since}, total_rebalances={total_rebalances}")

    pool_cfg = sa._get_pool_config(vault, vault_addr, pool_id)
    (pool_addr, token0_addr, token1_addr, fee, tick_spacing, active,
     position_adapter, reward_adapter) = pool_cfg

    pool_abi = [{
        "inputs": [], "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint8"},
            {"name": "unlocked", "type": "bool"},
        ], "stateMutability": "view", "type": "function",
    }]
    pool = w3.eth.contract(address=pool_addr, abi=pool_abi)
    slot0 = pool.functions.slot0().call()
    current_tick = slot0[1]

    print(f"REAL current_tick={current_tick}")
    print(f"Position range: [{tick_lower}, {tick_upper}]")
    is_in_range = tick_lower <= current_tick < tick_upper
    print(f"Is current_tick within [tick_lower, tick_upper)? {is_in_range}")
    print(f"Distance to tick_upper: {tick_upper - current_tick} ticks")
    print(f"Distance to tick_lower: {current_tick - tick_lower} ticks")
