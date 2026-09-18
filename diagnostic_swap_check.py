"""
Check whether an actual Snuggle rebalance transaction contains a Swap
event (Uniswap V3 pools emit Swap(address,address,int256,int256,
uint160,uint128,int24) on any swap) — verifying the "zero-swap
rebalancing" marketing claim directly against a real transaction,
not taking it on faith.
"""
import os
import requests
from web3 import Web3

BASE_RPC = os.environ.get("ALCHEMY_BASE")
w3 = Web3(Web3.HTTPProvider(BASE_RPC))

SWAP_TOPIC = "0x" + Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex().lstrip("0x")

# The exact block where the snuggle WETH/UNI rebalance's treasury
# transfers landed, found in the previous trace.
TARGET_BLOCK = 51473938


def get_block_logs(block_num):
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
        "params": [{"fromBlock": hex(block_num), "toBlock": hex(block_num)}],
    }
    resp = requests.post(BASE_RPC, json=payload, timeout=20)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


logs = get_block_logs(TARGET_BLOCK)
print(f"Block {TARGET_BLOCK}: {len(logs)} total logs")

# Group by transaction hash to isolate the rebalance tx specifically
by_tx = {}
for log in logs:
    by_tx.setdefault(log["transactionHash"], []).append(log)

for tx_hash, tx_logs in by_tx.items():
    topics0 = [log["topics"][0] for log in tx_logs if log["topics"]]
    has_swap = SWAP_TOPIC in topics0
    print(f"\ntx {tx_hash}: {len(tx_logs)} logs, contains Swap event: {has_swap}")
    if has_swap:
        for log in tx_logs:
            if log["topics"] and log["topics"][0] == SWAP_TOPIC:
                print(f"  SWAP on pool {log['address']}")
