#!/usr/bin/env python3
"""One-off clean-up for H2: clear market data on records bound to another token's DexScreener pair
(e.g. MARSID NFT showing the MarsCoin/SPCXB pair's $1.02M liquidity).

    python3 cleanup_foreign_pairs.py            # dry run: only lists the records it would clear
    python3 cleanup_foreign_pairs.py --apply    # clears them and saves through db.py

WARNING: with Supabase credentials in the environment, --apply writes to the PRODUCTION database.
Run the dry run first and read the list. Never run this from the worker or on a schedule.
"""
import sys

import monitor
import worker


def main(argv=None) -> int:
    apply = "--apply" in (argv if argv is not None else sys.argv[1:])
    history = monitor.load_history()
    bad = worker.find_foreign_pair_records(history)
    for item in bad:
        print(f"{item.get('contract')}  {item.get('name') or item.get('token_symbol')}  pair_url={item.get('pair_url')}  "
              f"lp_usd={item.get('lp_usd')}  market_cap={item.get('market_cap')}")
    print(f"{len(bad)} record(s) bound to another token's pair")
    if apply and bad:
        for item in bad:
            worker.clear_foreign_pair_market(item)
        monitor.save_history(history, delta=bad)
        print("cleared and saved")
    elif bad:
        print("dry run - nothing changed (pass --apply to clear them)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
