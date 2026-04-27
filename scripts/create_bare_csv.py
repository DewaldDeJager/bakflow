#!/usr/bin/env python3
"""Create a "bare" version of a TreeSize CSV, keeping only core columns.

Usage:
    .venv/bin/python scripts/create_bare_csv.py <input_csv> [--skip-rows N] [--output PATH]

Examples:
    .venv/bin/python scripts/create_bare_csv.py data/THREADRIPPER_C_Level_4.csv --skip-rows 4
    .venv/bin/python scripts/create_bare_csv.py data/THREADRIPPER_F_All.csv --skip-rows 4 --output data/custom_bare.csv
"""

import argparse
import csv
import io
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.importer.csv_importer import _sanitise_csv_line

KEEP_COLUMNS = ["Name", "Path", "Size", "Last Modified", "Type"]


def create_bare_csv(input_path: str, output_path: str, skip_rows: int) -> int:
    """Strip a TreeSize CSV down to core columns.

    Returns the number of data rows written.
    """
    with open(input_path, encoding="utf-8-sig") as fin, \
         open(output_path, "w", newline="", encoding="utf-8") as fout:
        preamble = [fin.readline() for _ in range(skip_rows)]
        for line in preamble:
            fout.write(line)

        sanitised = io.StringIO("".join(_sanitise_csv_line(l) for l in fin))
        reader = csv.DictReader(sanitised)
        writer = csv.DictWriter(fout, fieldnames=KEEP_COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()

        count = 0
        for row in reader:
            writer.writerow({k: row[k] for k in KEEP_COLUMNS})
            count += 1

    return count


def main():
    parser = argparse.ArgumentParser(description="Create a bare TreeSize CSV with only core columns.")
    parser.add_argument("input", help="Path to the full TreeSize CSV")
    parser.add_argument("--skip-rows", type=int, default=4, help="Preamble lines before the header (default: 4)")
    parser.add_argument("--output", help="Output path (default: <input>_Bare.csv)")
    args = parser.parse_args()

    if args.output:
        output_path = args.output
    else:
        base, ext = os.path.splitext(args.input)
        output_path = f"{base}_Bare{ext}"

    count = create_bare_csv(args.input, output_path, args.skip_rows)
    print(f"Wrote {count} data rows to {output_path}")


if __name__ == "__main__":
    main()
