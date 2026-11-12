#!/usr/bin/env python3
"""One-off clean-up for H3 / H5 on existing records.

    python3 cleanup_counts_and_supply.py            # dry run: lists what it would change
    python3 cleanup_counts_and_supply.py --apply    # applies it and saves through db.py

H3: buys_24h / holders that an old fallback filled with total_minted -> None; fractional holders -> None.
H5: 0-decimal BEP-20 supplies that were stored divided by 1e18 -> multiplied back.

WARNING: with Supabase credentials in the environment, --apply writes to the PRODUCTION database.
Read the dry run first. Never run this from the worker or on a schedule.
"""
import sys

import monitor
import worker


def main(argv=None) -> int:
    apply = "--apply" in (argv if argv is not None else sys.argv[1:])
    history = monitor.load_history()
    touched = []
    for item in history:
        before = {k: item.get(k) for k in ("holders", "buys_24h", "total_minted", "max_supply")}
        fields = worker.clean_count_junk(item) if apply else _preview(item)
        migrated = worker.migrate_zero_decimal_supply(item) if apply else _preview_migration(item)
        if fields or migrated:
            touched.append(item)
            print(f"{item.get('contract')}  {str(item.get('name'))[:20]:20}  fields={fields}  zero_decimal_supply_migrated={migrated}  was={before}")
    print(f"{len(touched)} record(s) {'changed' if apply else 'would change'}")
    if apply and touched:
        monitor.save_history(history, delta=touched)
        print("saved")
    elif touched:
        print("dry run - nothing changed (pass --apply to write it)")
    return 0


def _preview(item):
    import copy
    return worker.clean_count_junk(copy.deepcopy(item))


def _preview_migration(item):
    import copy
    return worker.migrate_zero_decimal_supply(copy.deepcopy(item))


if __name__ == "__main__":
    raise SystemExit(main())
