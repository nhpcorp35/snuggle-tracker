"""
Check whether auto-compound's "cash out the rest" fee portion actually
lands as a direct Transfer to the position owner's wallet during the
same rebalance transaction, or whether it needs a separate claim.
"""
import os
import requests
from web3 import Web3
import snuggle_adapter as sa

BASE_RPC = os.environ.get("ALCHEMY_BASE")
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
TRANSFER_TOPIC = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().lstrip("0x")

# Same rebalance transaction identified earlier (snuggle WETH/UNI position)
TX_HASH = "0x5946ab7b56915ae42119f33eea6d27f20d5f2784021eb3821969294ce5586a7b"
TOKEN_ID = 6025655

vault = w3.eth.contract(address=sa.VAULT_ADDRESS, abi=sa.VAULT_ABI)
pos = vault.functions.positions(TOKEN_ID).call()
owner = pos[2]
print(f"Position owner: {owner}")

receipt = w3.eth.get_transaction_receipt(TX_HASH)
to_topic = "0x" + "0" * 24 + owner[2:].lower()

hits = []
for log in receipt["logs"]:
    if log["topics"] and log["topics"][0].hex().lower().lstrip("0x") == TRANSFER_TOPIC.lstrip("0x") \
       and len(log["topics"]) > 2 and log["topics"][2].hex().lower().lstrip("0x") == to_topic.lstrip("0x").lower():
        raw_amount = int(log["data"].hex(), 16) if isinstance(log["data"], (bytes, bytearray)) else int(log["data"], 16)
        hits.append((log["address"], raw_amount))

print(f"\nTransfers TO owner's wallet in this rebalance tx: {len(hits)}")
for tok_addr, raw_amount in hits:
    print(f"  token={tok_addr}, raw_amount={raw_amount}")

if not hits:
    print("\nNo direct transfer to owner found in this transaction.")
