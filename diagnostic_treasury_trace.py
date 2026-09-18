"""
Determine whether cumulativeFees0/1 is gross or net of Snuggle's 15%
performance fee, by checking whether the treasury address actually
receives a proportional token transfer at each rebalance (net
accounting) or shows no such pattern (gross accounting).
"""
import os
import requests
from web3 import Web3
import snuggle_adapter as sa

BASE_RPC = os.environ.get("ALCHEMY_BASE")
w3 = Web3(Web3.HTTPProvider(BASE_RPC))

TREASURY = Web3.to_checksum_address("0x93d0D1216A613Ad8745f9320bCB25Dc04EA9EC12")
VAULT_ADDRESS = sa.VAULT_ADDRESS
MAXFI_VAULT_ADDRESS = sa.MAXFI_VAULT_ADDRESS

TRANSFER_TOPIC = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().lstrip("0x")

ERC20_ABI = [{
    "inputs": [], "name": "decimals",
    "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
    "stateMutability": "view", "type": "function",
}, {
    "inputs": [], "name": "symbol",
    "outputs": [{"internalType": "string", "name": "", "type": "string"}],
    "stateMutability": "view", "type": "function",
}]


def get_logs_raw(token_addr, from_block, to_block, to_topic):
    """Raw JSON-RPC call with full error body visible, rather than
    relying on web3.py's exception which was hiding the actual reason."""
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(from_block), "toBlock": hex(to_block),
            "address": token_addr,
            "topics": [TRANSFER_TOPIC, None, to_topic],
        }],
    }
    resp = requests.post(BASE_RPC, json=payload, timeout=20)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


TEST_CASES = [
    ("maxfi", MAXFI_VAULT_ADDRESS, 6036685),
    ("snuggle", VAULT_ADDRESS, 6025655),
]

vault_abi = sa.VAULT_ABI
current_block = w3.eth.block_number
current_ts = w3.eth.get_block(current_block)["timestamp"]
print(f"Current block: {current_block}, ts: {current_ts}")

for label, vault_addr, token_id in TEST_CASES:
    print(f"\n{'='*70}\n{label} token_id={token_id}\n{'='*70}")
    vault = w3.eth.contract(address=vault_addr, abi=vault_abi)
    pos = vault.functions.positions(token_id).call()
    (
        _tid, pool_id, owner, range_width_bps, tick_lower, tick_upper,
        auto_snuggle, auto_compound, rebalance_delay, out_of_range_since,
        total_rebalances, last_rebalance_time, deposit_ts, cum_fees0,
        cum_fees1, cum_rewards, _reserved,
    ) = pos
    print(f"deposit_ts={deposit_ts}, last_rebalance_time={last_rebalance_time}, total_rebalances={total_rebalances}")
    print(f"cum_fees0_raw={cum_fees0}, cum_fees1_raw={cum_fees1}")

    pool_cfg = sa._get_pool_config(vault, vault_addr, pool_id)
    (pool_addr, token0_addr, token1_addr, fee, tick_spacing, active,
     position_adapter, reward_adapter) = pool_cfg

    t0 = w3.eth.contract(address=token0_addr, abi=ERC20_ABI)
    t1 = w3.eth.contract(address=token1_addr, abi=ERC20_ABI)
    sym0, dec0 = t0.functions.symbol().call(), t0.functions.decimals().call()
    sym1, dec1 = t1.functions.symbol().call(), t1.functions.decimals().call()
    print(f"token0={sym0} ({token0_addr}), token1={sym1} ({token1_addr})")

    if last_rebalance_time == 0:
        print("Never rebalanced — skipping.")
        continue

    seconds_ago = current_ts - last_rebalance_time
    est_block = current_block - int(seconds_ago / 2)
    from_block = max(0, est_block - 200)
    to_block = min(current_block, est_block + 200)
    print(f"Searching blocks [{from_block}, {to_block}] (est. rebalance block ~{est_block})...")

    to_topic = "0x" + "0" * 24 + TREASURY[2:].lower()
    for tok_addr, sym, dec in [(token0_addr, sym0, dec0), (token1_addr, sym1, dec1)]:
        try:
            logs = get_logs_raw(tok_addr, from_block, to_block, to_topic)
            if not logs:
                print(f"  {sym}: no transfers to treasury in this window")
            for log in logs:
                raw_amount = int(log["data"], 16)
                amount = raw_amount / (10 ** dec)
                from_addr = "0x" + log["topics"][1][-40:]
                print(f"  {sym}: block={int(log['blockNumber'], 16)} from={from_addr} amount={amount}")
        except Exception as e:
            print(f"  {sym}: FAILED: {e}")
