# Vault LP Tracker (Snuggle + MaxFi)

Standalone tracker for LP positions on Base, covering both **Snuggle**
and **MaxFi** — confirmed to be the same underlying contract
architecture (maxfi.tech's own footer: "Powered by Snuggle"), just
separate deployments with different addresses. Validated against both
protocols' own UIs — matched exactly or within expected drift from
ongoing fee compounding.

## Environment variables

| Var | Required | Description |
|---|---|---|
| `ALCHEMY_BASE` | Yes | Base RPC URL (Alchemy or similar) — shared by both protocols |
| `DEFAULT_WALLET` | No | Wallet used when none is passed in the UI, for Snuggle (and MaxFi too, if `DEFAULT_WALLET_MAXFI` isn't set) |
| `DEFAULT_WALLET_MAXFI` | No | Separate default wallet for MaxFi, if different from your Snuggle wallet |
| `PASSWORD` | No | If set, enables HTTP Basic Auth on the whole app |
| `PUSHOVER_TOKEN` / `PUSHOVER_USER` | No | If both set, sends a Pushover alert when a position (either protocol) has been out of range longer than `OUT_OF_RANGE_ALERT_HOURS` |
| `OUT_OF_RANGE_ALERT_HOURS` | No (default 6) | Threshold, in hours, before an out-of-range position triggers an alert |
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
(and optionally `DEFAULT_WALLET`, `DEFAULT_WALLET_MAXFI`, `PASSWORD`) as
service variables, then add a custom domain pointing at this service.

## Architecture note

`snuggle_adapter.py`'s `fetch_snuggle_positions()` takes optional
`vault_address` / `view_helper_address` params (defaulting to Snuggle's
own deployment). `app.py` calls it twice — once per protocol — reusing
the identical fetch, enrichment, caching, and alerting logic. Adding a
third white-labeled deployment of this same contract architecture
would just mean adding its addresses and one more route.

## What it does NOT do yet

- Read-only. No wallet connection, no transaction capability.
- Lifetime APR is a simple average (fees earned ÷ current value, annualized
  over days held) — not the same methodology as either protocol's own
  "Earnings Rate," which appears to use a recent-window rate rather than
  a lifetime average.
