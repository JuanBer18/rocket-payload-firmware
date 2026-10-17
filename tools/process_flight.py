#!/usr/bin/env python3
"""One-command decoder and plot/report generator for payload flight logs.

Examples:
    python tools/process_flight.py flight_020
    python tools/process_flight.py "downloaded flight data/flight_020.bin"
    python tools/process_flight.py

With no input, the newest downloaded BIN is selected. Results are placed in a
folder beside the BIN, named ``<flight>_analysis``.
"""

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import decode
from tools.analyze_flight import analyze
from tools.report_pdf import build_pdf


def _flight_bins():
    roots = [PROJECT_ROOT / "downloaded flight data", PROJECT_ROOT]
    found = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("flight_*.bin"):
            if any(part.endswith("_analysis") for part in path.parts):
                continue
            found[str(path.resolve()).lower()] = path.resolve()
    return list(found.values())


def resolve_input(value):
    if value:
        supplied = Path(value).expanduser()
        candidates = [supplied]
        if not supplied.is_absolute():
            candidates.extend([PROJECT_ROOT / supplied,
                               PROJECT_ROOT / "downloaded flight data" / supplied])
        if not supplied.suffix:
            candidates += [path.with_suffix(".bin") for path in list(candidates)]
            candidates += [path.with_suffix(".csv") for path in list(candidates)]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        wanted = supplied.name if supplied.suffix else supplied.name + ".bin"
        exact = [path for path in _flight_bins() if path.name.lower() == wanted.lower()]
        if len(exact) == 1:
            return exact[0]
        raise FileNotFoundError("could not find %r" % value)
    bins = _flight_bins()
    if not bins:
        raise FileNotFoundError("no flight_*.bin files were found")
    return max(bins, key=lambda path: path.stat().st_mtime)


def _available_text():
    bins = sorted(_flight_bins(), key=lambda path: path.name)
    if not bins:
        return "No downloaded flight BIN files were found."
    return "Available flights:\n" + "\n".join(
        "  %-16s %s" % (path.name, path.parent) for path in bins)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flight", nargs="?",
                        help="flight number/name or path; default: newest BIN")
    parser.add_argument("--output", help="analysis output directory")
    parser.add_argument("--salvage", action="store_true",
                        help="attempt decoding if the BIN header is damaged")
    args = parser.parse_args(argv)

    try:
        source = resolve_input(args.flight)
    except FileNotFoundError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        print(_available_text(), file=sys.stderr)
        print("\nExample: python tools\\process_flight.py flight_020", file=sys.stderr)
        return 2

    output = Path(args.output).resolve() if args.output else source.parent / (source.stem + "_analysis")
    output.mkdir(parents=True, exist_ok=True)
    binary = None
    if source.suffix.lower() == ".bin":
        binary = source
        csv_path = output / (source.stem + ".csv")
        print("Found: %s" % source)
        print("Decoding BIN -> CSV...")
        decode_args = [str(source), "-o", str(csv_path), "--report"]
        if args.salvage:
            decode_args.append("--salvage")
        if decode.main(decode_args):
            print("ERROR: decoding failed; no plots were generated", file=sys.stderr)
            return 1
    elif source.suffix.lower() == ".csv":
        csv_path = source
        print("Found decoded CSV: %s" % source)
    else:
        print("ERROR: input must be a .bin or .csv file", file=sys.stderr)
        return 2

    print("Generating analysis, plots, and altitude-resolved SEN65 profile...")
    try:
        metrics = analyze(csv_path, output, binary)
    except Exception as exc:
        print("ERROR: analysis failed: %s" % exc, file=sys.stderr)
        print("The decoded CSV remains available at %s" % csv_path, file=sys.stderr)
        return 1

    print("Building readable PDF report...")
    try:
        pdf_path = build_pdf(output)
    except Exception as exc:
        print("ERROR: PDF generation failed: %s" % exc, file=sys.stderr)
        print("The CSV, plots and text report remain available in %s" % output,
              file=sys.stderr)
        return 1

    print("\nDONE")
    print("Results folder: %s" % output)
    print("Open first:     %s" % (output / "analysis_report.md"))
    print("PDF report:     %s" % pdf_path)
    print("Main plot:      %s" % (output / "altitude_and_acceleration.png"))
    profile = metrics.get("sen65_altitude_profile")
    if profile:
        print("SEN65 profile:  %s" % (output / "sen65_altitude_profiles.png"))
        print("Profile table:  %s" % (output / "sen65_altitude_binned.csv"))
    else:
        print("SEN65 profile:  unavailable (no overlapping SEN65/barometer data)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
