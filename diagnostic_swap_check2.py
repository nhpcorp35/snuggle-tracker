import os
import requests
from web3 import Web3

BASE_RPC = os.environ.get("ALCHEMY_BASE")
w3 = Web3(Web3.HTTPProvider(BASE_RPC))

TREASURY = Web3.to_checksum_address("0x93d0D1216A613Ad8745f9320bCB25Dc04EA9EC12")
TRANSFER_TOPIC = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().lstrip("0x")
SWAP_TOPIC = "0x" + Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex().lstrip("0x")
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


to_topic = "0x" + "0" * 24 + TREASURY[2:].lower()
logs = get_block_logs(TARGET_BLOCK)

# Find the exact transaction(s) with a Transfer TO treasury
treasury_txs = set()
for log in logs:
    if log["topics"] and log["topics"][0] == TRANSFER_TOPIC and len(log["topics"]) > 2 and log["topics"][2].lower() == to_topic.lower():
        treasury_txs.add(log["transactionHash"])

print(f"Transactions in block {TARGET_BLOCK} with a transfer to treasury: {len(treasury_txs)}")
for tx_hash in treasury_txs:
    tx_logs = [l for l in logs if l["transactionHash"] == tx_hash]
    topics0 = [l["topics"][0] for l in tx_logs if l["topics"]]
    has_swap = SWAP_TOPIC in topics0
    print(f"\ntx {tx_hash}: {len(tx_logs)} logs, contains Swap event: {has_swap}")
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    print(f"  to={receipt['to']}, gasUsed={receipt['gasUsed']}")
