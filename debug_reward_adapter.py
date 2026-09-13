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

APPROVED_POOLS_AND_POSITIONS_ABI = sa.VAULT_ABI + [
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

        for fn_name in ["rewardToken", "REWARD_TOKEN", "token", "cakeToken", "CAKE"]:
            selector = Web3.keccak(text=f"{fn_name}()")[:4]
            try:
                raw = w3.eth.call({"to": reward_adapter, "data": selector})
                if len(raw) == 32:
                    addr = "0x" + raw[-20:].hex()
                    print(f"  {fn_name}() -> {Web3.to_checksum_address(addr)}")
            except Exception:
                pass
