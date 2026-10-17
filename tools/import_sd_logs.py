#!/usr/bin/env python3
"""Copy payload logs from an SD card, decode BIN files, and make plots.

Examples:
    python tools/import_sd_logs.py E:\\
    python tools/import_sd_logs.py E:\\ --output mission_data\\my_test

The source card is opened read-only by this program: files are copied from it,
never renamed, deleted, or overwritten.
"""

import argparse
from datetime import datetime
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.decode import Decoder, LogError, LogReader, merge_timeline, report, write_csv


def unique_destination(path):
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name("%s_copy%d%s" %
                                   (path.stem, index, path.suffix))
        if not candidate.exists():
            return candidate
        index += 1


def find_logs(source):
    logs = []
    for pattern in ("flight_*.bin", "flight_*.csv"):
        logs.extend(path for path in source.rglob(pattern) if path.is_file())
    return sorted(logs)


def import_logs(source, output, salvage=False, make_plots=True):
    source = Path(source).resolve()
    output = Path(output).resolve()
    if not source.is_dir():
        raise ValueError("SD-card path is not a directory: %s" % source)

    found = find_logs(source)
    if not found:
        raise ValueError("no flight_*.bin or flight_*.csv files found on %s" % source)

    raw_dir = output / "raw_sd_copy"
    csv_dir = output / "csv"
    report_dir = output / "reports"
    plot_dir = output / "plots"
    for directory in (raw_dir, csv_dir, report_dir, plot_dir):
        directory.mkdir(parents=True, exist_ok=True)

    copied = []
    binaries = []
    for original in found:
        relative = original.relative_to(source)
        destination = unique_destination(raw_dir / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
        copied.append((original, destination))
        if original.suffix.lower() == ".bin":
            binaries.append(destination)

    converted = []
    failures = []
    for binary in binaries:
        csv_path = unique_destination(csv_dir / (binary.stem + ".csv"))
        report_path = unique_destination(report_dir / (binary.stem + "_report.txt"))
        try:
            reader = LogReader(binary, salvage=salvage).open()
            decoder = Decoder(reader).run()
            rows = merge_timeline(decoder)
            count = write_csv(rows, csv_path)
            text = report(decoder, reader)
            report_path.write_text(text + "\n", encoding="utf-8")
            converted.append((binary, csv_path, report_path, count))
        except (LogError, OSError, ValueError) as exc:
            failures.append((binary, str(exc)))

    plots = []
    if make_plots and converted:
        from tools.plot_mission import plot_file
        for _binary, csv_path, _report_path, _count in converted:
            try:
                generated = plot_file(csv_path)
                destination = unique_destination(plot_dir / generated.name)
                shutil.move(str(generated), destination)
                plots.append(destination)
            except Exception as exc:
                failures.append((csv_path, "plot failed: %s" % exc))

    manifest = output / "import_manifest.txt"
    lines = [
        "Payload SD import",
        "source: %s" % source,
        "created: %s" % datetime.now().isoformat(timespec="seconds"),
        "copied files: %d" % len(copied),
        "decoded BIN files: %d" % len(converted),
        "plots: %d" % len(plots),
        "failures: %d" % len(failures),
        "",
    ]
    for original, destination in copied:
        lines.append("COPIED %s -> %s" % (original, destination))
    for binary, csv_path, report_path, count in converted:
        lines.append("DECODED %s -> %s (%d rows), report %s" %
                     (binary, csv_path, count, report_path))
    for path, error in failures:
        lines.append("FAILED %s: %s" % (path, error))
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "output": output,
        "copied": copied,
        "converted": converted,
        "plots": plots,
        "failures": failures,
        "manifest": manifest,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="mounted SD-card drive or directory, e.g. E:\\")
    parser.add_argument("--output", help="destination directory (default: dated mission_data folder)")
    parser.add_argument("--salvage", action="store_true",
                        help="attempt decoding even when a BIN header is damaged")
    parser.add_argument("--no-plots", action="store_true",
                        help="copy and decode without creating PNG quick-look plots")
    args = parser.parse_args(argv)

    output = (Path(args.output) if args.output else
              Path("mission_data") / datetime.now().strftime("import_%Y%m%d_%H%M%S"))
    try:
        result = import_logs(args.source, output, salvage=args.salvage,
                             make_plots=not args.no_plots)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    print("Copied %d SD files" % len(result["copied"]))
    print("Decoded %d BIN files" % len(result["converted"]))
    print("Created %d plots" % len(result["plots"]))
    print("Failures: %d" % len(result["failures"]))
    print("Output: %s" % result["output"])
    print("Manifest: %s" % result["manifest"])
    return 2 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
