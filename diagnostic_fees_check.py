"""Live check of snuggle_adapter's collected + uncollected fee fields."""
import os
from web3 import Web3
import snuggle_adapter as sa
w3 = Web3(Web3.HTTPProvider(os.environ["ALCHEMY_BASE"]))
wallet = os.environ.get("DEFAULT_WALLET", "0xc33Fc161686ED2B8649162Ff9BfC3ED3a7f24801")
for label, v, vh in [("snuggle", sa.VAULT_ADDRESS, sa.VIEWHELPER_ADDRESS), ("maxfi", sa.MAXFI_VAULT_ADDRESS, sa.MAXFI_VIEWHELPER_ADDRESS)]:
    for p in sa.fetch_snuggle_positions(wallet, w3, v, vh):
        s0, s1 = p["token0"]["symbol"], p["token1"]["symbol"]
        print(f"{label} #{p['token_id']} {s0}/{s1}: collected {p['collected_fees0']:.8f}/{p['collected_fees1']:.6f}"
              f" uncollected {p['uncollected_fees0']}/{p['uncollected_fees1']} -> lifetime {p['cumulative_fees0']:.8f}/{p['cumulative_fees1']:.6f}", flush=True)
print("DONE", flush=True)
