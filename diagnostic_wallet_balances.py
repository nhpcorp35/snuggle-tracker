"""
Snapshot of current wallet balances for tokens involved in the user's
tracked Snuggle/MaxFi positions — the best available proxy for "what
have they sent me" without a full historical transfer-log scan (not
practical: 10-block RPC cap, no BaseScan API key available).
"""
import os
from web3 import Web3
import snuggle_adapter as sa

BASE_RPC = os.environ.get("ALCHEMY_BASE")
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
WALLET = "0xc33Fc161686ED2B8649162Ff9BfC3ED3a7f24801"

ERC20_ABI = [{
    "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
    "name": "balanceOf",
    "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
    "stateMutability": "view", "type": "function",
}, {
    "inputs": [], "name": "decimals",
    "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
    "stateMutability": "view", "type": "function",
}, {
    "inputs": [], "name": "symbol",
    "outputs": [{"internalType": "string", "name": "", "type": "string"}],
    "stateMutability": "view", "type": "function",
}]

TEST_CASES = [
    ("maxfi", sa.MAXFI_VAULT_ADDRESS, [6036685]),
    ("snuggle", sa.VAULT_ADDRESS, [2125771, 5992263, 6025655]),
]

vault_abi = sa.VAULT_ABI
seen_tokens = {}

for label, vault_addr, token_ids in TEST_CASES:
    vault = w3.eth.contract(address=vault_addr, abi=vault_abi)
    for token_id in token_ids:
        pos = vault.functions.positions(token_id).call()
        pool_id = pos[1]
        pool_cfg = sa._get_pool_config(vault, vault_addr, pool_id)
        (pool_addr, token0_addr, token1_addr, fee, tick_spacing, active,
         position_adapter, reward_adapter) = pool_cfg
        for addr in (token0_addr, token1_addr):
            seen_tokens[addr] = None

print(f"Checking current balance of {len(seen_tokens)} distinct tokens in wallet {WALLET}...\n")
eth_balance = w3.eth.get_balance(WALLET)
print(f"Native ETH: {eth_balance / 1e18}")

for addr in seen_tokens:
    try:
        c = w3.eth.contract(address=addr, abi=ERC20_ABI)
        sym = c.functions.symbol().call()
        dec = c.functions.decimals().call()
        bal = c.functions.balanceOf(WALLET).call()
        print(f"{sym} ({addr}): {bal / (10**dec)}")
    except Exception as e:
        print(f"{addr}: FAILED: {e}")
