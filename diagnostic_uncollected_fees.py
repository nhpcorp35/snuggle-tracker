"""One-off: does the Snuggle vault's cumulativeFees counter include fees
earned but not yet collected? Compares it to a static NPM.collect() call
(from the NFT owner) which returns everything currently collectable,
including accrued-but-uncollected fees (NPM pokes the pool first)."""
import os
from web3 import Web3
import snuggle_adapter as sa

w3 = Web3(Web3.HTTPProvider(os.environ["ALCHEMY_BASE"]))
TOKEN_ID = int(os.environ.get("TOKEN_ID", "6078482"))
MAX128 = 2**128 - 1

NPMS = {
    "uniswap_v3": "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",
    "pancakeswap_v3": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
}
NPM_ABI = [
    {"name": "ownerOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [{"name": "", "type": "address"}]},
    {"name": "collect", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "params", "type": "tuple", "components": [
         {"name": "tokenId", "type": "uint256"}, {"name": "recipient", "type": "address"},
         {"name": "amount0Max", "type": "uint128"}, {"name": "amount1Max", "type": "uint128"}]}],
     "outputs": [{"name": "amount0", "type": "uint256"}, {"name": "amount1", "type": "uint256"}]},
]
ERC20 = [{"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
         {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "string"}]}]

for label, vault_addr in [("snuggle", sa.VAULT_ADDRESS), ("maxfi", sa.MAXFI_VAULT_ADDRESS)]:
    vault = w3.eth.contract(address=vault_addr, abi=sa.VAULT_ABI)
    pos = vault.functions.positions(TOKEN_ID).call()
    (_tid, pool_id, owner, _w, tl, tu, _as, _ac, _rd, _oor, rebal, last_rebal, dep_ts, cf0, cf1, _cr, _r) = pos
    if tl == 0 and tu == 0 and rebal == 0 and dep_ts == 0:
        continue
    cfg = sa._get_pool_config(vault, vault_addr, pool_id)
    t0, t1 = cfg[1], cfg[2]
    d0 = w3.eth.contract(address=t0, abi=ERC20).functions.decimals().call()
    d1 = w3.eth.contract(address=t1, abi=ERC20).functions.decimals().call()
    s0 = w3.eth.contract(address=t0, abi=ERC20).functions.symbol().call()
    s1 = w3.eth.contract(address=t1, abi=ERC20).functions.symbol().call()
    print(f"Vault={label} rebalances={rebal} last_rebalance_ts={last_rebal} deposit_ts={dep_ts}")
    print(f"Vault cumulativeFees: {cf0 / 10**d0:.8f} {s0} + {cf1 / 10**d1:.6f} {s1}")

    for dex, npm_addr in NPMS.items():
        npm = w3.eth.contract(address=Web3.to_checksum_address(npm_addr), abi=NPM_ABI)
        try:
            nft_owner = npm.functions.ownerOf(TOKEN_ID).call()
        except Exception:
            continue
        print(f"NFT on {dex}, owner={nft_owner}")
        a0, a1 = npm.functions.collect((TOKEN_ID, nft_owner, MAX128, MAX128)).call({"from": nft_owner})
        print(f"Collectable now (uncollected fees): {a0 / 10**d0:.8f} {s0} + {a1 / 10**d1:.6f} {s1}")
print("DONE")
