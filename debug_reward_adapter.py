"""
Checking what rewardAdapter actually is on-chain, for real positions —
not guessing at an interface. Prints cumulativeRewards (raw) per
position and tries common ERC20-reward-token getter names against the
adapter address to find the actual interface.
"""
import os
from web3 import Web3
import snuggle_adapter as sa

ALCHEMY_BASE = os.environ.get("ALCHEMY_BASE", "")
DEFAULT_WALLET = os.environ.get("DEFAULT_WALLET", "").strip()
w3 = Web3(Web3.HTTPProvider(ALCHEMY_BASE))
print(f"Connected: {w3.is_connected()}, block {w3.eth.block_number}")

APPROVED_POOLS_AND_POSITIONS_ABI = sa.VAULT_ABI  # approvedPools() is already in here

for label, viewhelper, vault in [
    ("Snuggle", sa.VIEWHELPER_ADDRESS, sa.VAULT_ADDRESS),
    ("MaxFi", sa.MAXFI_VIEWHELPER_ADDRESS, sa.MAXFI_VAULT_ADDRESS),
]:
    print(f"\n=== {label} ===")
    vh = w3.eth.contract(address=viewhelper, abi=sa.VIEWHELPER_ABI)
    token_ids = vh.functions.getUserPositions(DEFAULT_WALLET).call()
    print(f"Position tokenIds: {token_ids}")

    vault_c = w3.eth.contract(address=vault, abi=APPROVED_POOLS_AND_POSITIONS_ABI)
    for tid in token_ids:
        pos = vault_c.functions.positions(tid).call()
        pool_id = pos[1]
        cum_fees0, cum_fees1, cum_rewards = pos[13], pos[14], pos[15]
        print(f"\n  tokenId {tid}: cumulativeRewards (raw) = {cum_rewards}")

        pool_cfg = vault_c.functions.approvedPools(pool_id).call()
        reward_adapter = pool_cfg[7]
        print(f"  poolId {pool_id.hex()} -> rewardAdapter: {reward_adapter}")

        if reward_adapter == "0x0000000000000000000000000000000000000000":
            print("  (zero address — no reward adapter configured for this pool)")
            continue

        code_len = len(w3.eth.get_code(reward_adapter))
        print(f"  rewardAdapter bytecode: {code_len} bytes")

        for fn_name in ["rewardToken", "REWARD_TOKEN", "token", "cakeToken", "CAKE",
                        "getRewardToken", "rewardsToken", "REWARDS_TOKEN", "incentiveToken",
                        "emissionToken", "rewardCoin", "payoutToken", "distributionToken",
                        "farmToken", "stakingToken", "want", "asset", "underlying",
                        "rewardAsset", "outputToken"]:
            selector = Web3.keccak(text=f"{fn_name}()")[:4]
            try:
                raw = w3.eth.call({"to": reward_adapter, "data": selector})
                if len(raw) == 32:
                    addr = "0x" + raw[-20:].hex()
                    print(f"  {fn_name}() -> {Web3.to_checksum_address(addr)}")
            except Exception:
                pass

        # Try tokenId-parameterized candidates too — reward might be
        # looked up per-position rather than fixed for the whole adapter.
        from eth_abi import encode as abi_encode
        encoded_tid = abi_encode(["uint256"], [tid])
        for fn_name in ["getReward", "getRewards", "pendingReward", "pendingRewards",
                         "earned", "claimable", "rewardOf", "pending"]:
            selector = Web3.keccak(text=f"{fn_name}(uint256)")[:4]
            try:
                raw = w3.eth.call({"to": reward_adapter, "data": selector + encoded_tid})
                print(f"  {fn_name}(tokenId={tid}) -> raw {len(raw)} bytes: {raw.hex()}")
            except Exception:
                pass
