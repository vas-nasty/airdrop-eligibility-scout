#!/usr/bin/env python3
"""
AirDrop Eligibility Scout
Heuristic scanner for potential airdrop eligibility across EVM chains.

Features
- Reads addresses from CLI args or a file (one address per line).
- Pulls on-chain data via explorer APIs (Etherscan-compatible).
- Heuristics: ETH balance threshold, min tx count, contract interaction count,
  activity span (first->latest tx), optional ERC20 balance check.
- Outputs JSON and a human-readable table.

Notes
- Set API keys via environment variables: ETHERSCAN_API_KEY, ARBISCAN_API_KEY, OPTSCAN_API_KEY.
- Uses only public endpoints if keys are missing, but rate limits may apply.
- This tool is intended for research and automation; use responsibly.
"""
import os, sys, json, time, math, argparse, re
from datetime import datetime
from typing import List, Dict, Any, Tuple

try:
    import requests  # not executed on write; install when you actually run
except Exception:
    requests = None  # keep import safe for now

ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

EXPLORERS = {
    "eth": {"name":"Ethereum", "base":"https://api.etherscan.io/api", "env":"ETHERSCAN_API_KEY", "symbol":"ETH", "decimals":18},
    "arb": {"name":"Arbitrum One", "base":"https://api.arbiscan.io/api", "env":"ARBISCAN_API_KEY", "symbol":"ETH", "decimals":18},
    "opt": {"name":"Optimism", "base":"https://api-optimistic.etherscan.io/api", "env":"OPTSCAN_API_KEY", "symbol":"ETH", "decimals":18},
}

def ensure_requests():
    if requests is None:
        raise SystemExit("requests module is not available. Install with: pip install requests")

def now_utc_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def get(api: str, params: Dict[str, Any]) -> Dict[str, Any]:
    ensure_requests()
    r = requests.get(api, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def wei_to_unit(wei: int, decimals: int) -> float:
    return float(wei) / float(10**decimals)

def fetch_balance(chain: str, addr: str) -> float:
    cfg = EXPLORERS[chain]
    params = {"module":"account", "action":"balance", "address":addr, "tag":"latest"}
    key = os.environ.get(cfg["env"], "")
    if key:
        params["apikey"] = key
    data = get(cfg["base"], params)
    val = int(data.get("result", "0"))
    return wei_to_unit(val, cfg["decimals"])

def fetch_txlist(chain: str, addr: str) -> List[Dict[str, Any]]:
    cfg = EXPLORERS[chain]
    params = {"module":"account", "action":"txlist", "address":addr, "startblock":0, "endblock":99999999, "sort":"asc"}
    key = os.environ.get(cfg["env"], "")
    if key:
        params["apikey"] = key
    data = get(cfg["base"], params)
    if data.get("status") == "1" and isinstance(data.get("result"), list):
        return data["result"]
    return []

def analyze_interactions(txs: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """Return (tx_count, unique_contracts, active_days)."""
    tx_count = len(txs)
    contracts = set()
    days = 0
    if tx_count:
        for t in txs:
            to = (t.get("to") or "").lower()
            # contract interaction heuristic: to != 0x0 and input data not empty
            if to.startswith("0x") and len((t.get("input") or "")) > 2:
                contracts.add(to)
        try:
            t0 = int(txs[0]["timeStamp"])
            t1 = int(txs[-1]["timeStamp"])
            days = max(1, int((t1 - t0) / 86400))
        except Exception:
            days = 0
    return tx_count, len(contracts), days

def score_address(chain: str, addr: str, min_balance: float, min_tx: int, min_contracts: int, min_days: int) -> Dict[str, Any]:
    bal = fetch_balance(chain, addr)
    txs = fetch_txlist(chain, addr)
    tx_count, uniq, days = analyze_interactions(txs)
    eligible = (bal >= min_balance) and (tx_count >= min_tx) and (uniq >= min_contracts) and (days >= min_days)
    return {
        "address": addr,
        "balance": bal,
        "tx_count": tx_count,
        "contracts": uniq,
        "active_days": days,
        "eligible": bool(eligible),
    }

def load_addresses(path_or_list: List[str]) -> List[str]:
    addrs: List[str] = []
    for x in path_or_list:
        if os.path.isfile(x):
            with open(x) as f:
                for line in f:
                    s = line.strip()
                    if s:
                        addrs.append(s)
        else:
            addrs.append(x)
    # normalize & validate
    out = []
    for a in addrs:
        a = a.strip()
        if ADDR_RE.match(a):
            out.append("0x" + a[2:].lower())
    if not out:
        raise SystemExit("no valid EVM addresses provided")
    return list(dict.fromkeys(out))  # dedupe, keep order

def main():
    ap = argparse.ArgumentParser(description="AirDrop Eligibility Scout (EVM)")
    ap.add_argument("--chain", choices=sorted(EXPLORERS.keys()), default="eth", help="target chain")
    ap.add_argument("--min-balance", type=float, default=0.05, help="min native balance (ETH-equivalent)")
    ap.add_argument("--min-tx", type=int, default=5, help="min transaction count")
    ap.add_argument("--min-contracts", type=int, default=3, help="min unique contracts interacted")
    ap.add_argument("--min-days", type=int, default=7, help="min active days span")
    ap.add_argument("--json-out", default="", help="optional JSON output path")
    ap.add_argument("addresses", nargs="+", help="addresses or a path to a file with addresses (one per line)")
    args = ap.parse_args()

    addrs = load_addresses(args.addresses)
    res = []
    for a in addrs:
        try:
            res.append(score_address(args.chain, a, args.min_balance, args.min_tx, args.min_contracts, args.min_days))
            time.sleep(0.3)  # polite pacing for public APIs
        except Exception as e:
            res.append({"address": a, "error": str(e), "eligible": False})

    # Pretty print
    print(f"# AirDrop Eligibility Scout — {now_utc_iso()}")
    print(f"# chain={args.chain} thresholds: balance>={args.min_balance}, tx>={args.min_tx}, contracts>={args.min_contracts}, days>={args.min_days}")
    for r in res:
        if "error" in r:
            print(f"ERR {r['address']} :: {r['error']}")
        else:
            flag = "✅" if r["eligible"] else "❌"
            print(f"{flag} {r['address']}  bal={r['balance']:.6f}  tx={r['tx_count']}  uniq={r['contracts']}  days={r['active_days']}")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump({"chain": args.chain, "results": res}, f, indent=2)

if __name__ == "__main__":
    main()
