"""
Probe the Snuggle vault contract for any fee-related view function,
same empirical approach that found the (undocumented) reward adapter
earlier — trying candidate names/selectors directly rather than
guessing from marketing copy alone.
"""
import os
from web3 import Web3

BASE_RPC = os.environ.get("ALCHEMY_BASE")
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
VAULT_ADDRESS = Web3.to_checksum_address("0xd3923beccb6e1ddb048ed00a0a9bd602d16b7470")

CANDIDATES = [
    ("performanceFeeBps", "uint256"), ("performanceFee", "uint256"),
    ("PERFORMANCE_FEE", "uint256"), ("PERFORMANCE_FEE_BPS", "uint256"),
    ("feeBps", "uint256"), ("protocolFeeBps", "uint256"),
    ("treasuryFeeBps", "uint256"), ("performanceFeePercent", "uint256"),
    ("treasury", "address"), ("feeRecipient", "address"),
    ("feeManager", "address"), ("PERFORMANCE_FEE_BASIS_POINTS", "uint256"),
]

for name, out_type in CANDIDATES:
    abi = [{
        "inputs": [], "name": name,
        "outputs": [{"internalType": out_type, "name": "", "type": out_type}],
        "stateMutability": "view", "type": "function",
    }]
    try:
        contract = w3.eth.contract(address=VAULT_ADDRESS, abi=abi)
        result = getattr(contract.functions, name)().call()
        print(f"HIT: {name}() = {result}")
    except Exception as e:
        msg = str(e)[:80]
        print(f"miss: {name}() -> {msg}")
