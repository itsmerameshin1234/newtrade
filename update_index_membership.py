#!/usr/bin/env python3
"""Fetch NSE index constituents and update symbol_common.py with index membership."""

import json
import re
import urllib.request

INDICES = [
    ("NI", "nifty50",    "https://iislliveblob.niftyindices.com/jsonfiles/Heatmap/FinalHeatMapNIFTY%2050.json"),
    ("BN", "banknifty",  "https://iislliveblob.niftyindices.com/jsonfiles/Heatmap/FinalHeatMapNIFTY%20BANK.json"),
    ("FN", "finnifty",   "https://iislliveblob.niftyindices.com/jsonfiles/Heatmap/FinalHeatMapNIFTY%20FINANCIAL%20SERVICES.json"),
    ("MS", "midselect",  "https://iislliveblob.niftyindices.com/jsonfiles/Heatmap/FinalHeatMapNIFTY%20MIDCAP%20SELECT.json"),
]

OUTPUT_JSON = "index_membership.json"
SYMBOL_COMMON = "symbol_common.py"


def fetch_symbols(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
    return sorted(entry["symbol"] for entry in data)


def build_index_data():
    index_data = {}    # {"nifty50": [...], ...}
    membership = {}    # {"SYMBOL": ["NI", ...], ...}  — used for symbol_common update
    for short_name, key, url in INDICES:
        print(f"Fetching {short_name} ({key}) ...")
        symbols = fetch_symbols(url)
        print(f"  {len(symbols)} symbols")
        index_data[key] = symbols
        for sym in symbols:
            membership.setdefault(sym, []).append(short_name)
    return index_data, membership


def save_json(index_data):
    with open(OUTPUT_JSON, "w") as f:
        json.dump(index_data, f, indent=2)
    print(f"Saved {OUTPUT_JSON}")


def get_symbol_common_symbols():
    with open(SYMBOL_COMMON, "r") as f:
        content = f.read()
    return set(re.findall(r'\[\d+,"([^"]+)","[^"]*"\]', content))


def update_symbol_common(membership):
    with open(SYMBOL_COMMON, "r") as f:
        content = f.read()

    def replace_entry(match):
        id_val = match.group(1)
        sym = match.group(2)
        indices = ",".join(membership.get(sym, []))
        return f'[{id_val},"{sym}","{indices}"]'

    updated = re.sub(r'\[(\d+),"([^"]+)","[^"]*"\]', replace_entry, content)

    with open(SYMBOL_COMMON, "w") as f:
        f.write(updated)
    print(f"Updated {SYMBOL_COMMON}")


def print_report(index_data, membership):
    all_index_symbols = set(membership.keys())
    sc_symbols = get_symbol_common_symbols()

    present     = all_index_symbols & sc_symbols
    missing     = all_index_symbols - sc_symbols   # in indices but NOT in symbol_common
    extra       = sc_symbols - all_index_symbols   # in symbol_common but NOT in any index

    print("\n" + "=" * 60)
    print("INDEX MEMBERSHIP vs SYMBOL_COMMON REPORT")
    print("=" * 60)

    print(f"\n✓ PRESENT in both ({len(present)}):")
    for sym in sorted(present):
        print(f"   {sym:20s}  [{','.join(membership[sym])}]")

    print(f"\n✗ MISSING from symbol_common ({len(missing)})  — add these:")
    if missing:
        for sym in sorted(missing):
            print(f"   {sym:20s}  [{','.join(membership[sym])}]")
    else:
        print("   (none)")

    print(f"\n+ EXTRA in symbol_common, not in any index ({len(extra)}):")
    if extra:
        for sym in sorted(extra):
            print(f"   {sym}")
    else:
        print("   (none)")

    print(f"\nPer-index breakdown:")
    for short_name, key, _ in INDICES:
        syms = set(index_data[key])
        present_count = len(syms & sc_symbols)
        missing_syms  = sorted(syms - sc_symbols)
        print(f"  {short_name} ({key}): {present_count}/{len(syms)} present", end="")
        if missing_syms:
            print(f"  — missing: {', '.join(missing_syms)}", end="")
        print()

    print("=" * 60 + "\n")


def main():
    index_data, membership = build_index_data()
    save_json(index_data)
    update_symbol_common(membership)
    print_report(index_data, membership)
    print("Done.")


if __name__ == "__main__":
    main()
