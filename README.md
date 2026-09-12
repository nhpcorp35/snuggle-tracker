# Snuggle Vault Tracker

Standalone tracker for Snuggle vault LP positions on Base. Validated
against Snuggle's own UI on 4 real positions (Uniswap V3 + PancakeSwap V3)
— matched exactly or within expected drift from ongoing fee compounding.

## Environment variables

| Var | Required | Description |
|---|---|---|
| `ALCHEMY_BASE` | Yes | Base RPC URL (Alchemy or similar) |
| `DEFAULT_WALLET` | No | Wallet address to use when none is passed in the UI |
| `PASSWORD` | No | If set, enables HTTP Basic Auth on the whole app |
| `PORT` | Set by Railway | — |

USD pricing uses GeckoTerminal's free, keyless onchain API — no signup,
no API key, no cost.

## Local dev

```bash
pip install -r requirements.txt
export ALCHEMY_BASE="https://base-mainnet.g.alchemy.com/v2/YOUR_KEY"
python app.py
```

Visit `http://localhost:8080`.

## Deploy

Railway auto-deploys from `main` via `railway.toml`. Set `ALCHEMY_BASE`
(and optionally `DEFAULT_WALLET`, `PASSWORD`) as service variables, then
add a custom domain pointing at this service.

## What it does NOT do yet

- Read-only. No wallet connection, no transaction capability.
- Lifetime APR is a simple average (fees earned ÷ current value, annualized
  over days held) — not the same methodology as Snuggle's own "Earnings Rate,"
  which appears to use a recent-window rate rather than a lifetime average.
