import os
from web3 import Web3
import snuggle_adapter as sa

BASE_RPC = os.environ.get("ALCHEMY_BASE")
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
TOKEN_ID = 6078482

for label, vault_addr in [("snuggle", sa.VAULT_ADDRESS), ("maxfi", sa.MAXFI_VAULT_ADDRESS)]:
    vault = w3.eth.contract(address=vault_addr, abi=sa.VAULT_ABI)
    pos = vault.functions.positions(TOKEN_ID).call()
    (
        _tid, pool_id, owner, range_width_bps, tick_lower, tick_upper,
        auto_snuggle, auto_compound, rebalance_delay, out_of_range_since,
        total_rebalances, last_rebalance_time, deposit_ts, cum_fees0,
        cum_fees1, cum_rewards, _reserved,
    ) = pos
    if tick_lower == 0 and tick_upper == 0 and total_rebalances == 0 and deposit_ts == 0:
        print(f"{label}: no real position for this token_id")
        continue
    print(f"\nFound on {label}")
    print(f"deposit_ts={deposit_ts}, total_rebalances={total_rebalances}, last_rebalance_time={last_rebalance_time}")
    print(f"raw cum_fees0={cum_fees0}, cum_fees1={cum_fees1}")

    pool_cfg = sa._get_pool_config(vault, vault_addr, pool_id)
    (pool_addr, token0_addr, token1_addr, fee, tick_spacing, active,
     position_adapter, reward_adapter) = pool_cfg
    print(f"token0={token0_addr}, token1={token1_addr}, fee={fee}")
