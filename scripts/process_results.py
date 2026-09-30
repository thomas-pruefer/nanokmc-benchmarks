"""Validate completed manuscript runs and export standardized Figure 5–10 data."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from benchmark_core.publication import process_publication, required_runs
from benchmark_core.run_sets import add_run_set_argument, data_root, check_run_set

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,epilog=
        "The requested job limit is inferred from completed runs. All selected publication inputs must use one uniform --jobs policy (1..32).")
    parser.add_argument("--paths", type=Path, default=ROOT / "config/paths.local.json")
    add_run_set_argument(parser)
    parser.add_argument("--figures", type=int, nargs="+", choices=range(5, 11), default=list(range(5, 11)),
                        help="5 7 8 require complete A; 6 9 require B; 10 requires C.")
    parser.add_argument("--dry-run", action="store_true", help="List coverage without processing or writing results.")
    parser.add_argument("--validate-only", action="store_true", help="Verify inputs and formulas without publishing output.")
    args = parser.parse_args(argv)
    if len(args.figures) != len(set(args.figures)):
        parser.error("Choose each figure only once.")
    specs = required_runs(ROOT, args.figures)
    try:
        dataset = data_root(ROOT,args.run_set)
        check_run_set(ROOT,args.run_set)
    except (ValueError,OSError) as exc:
        parser.error(str(exc))
    print(f"Run-set: {args.run_set}; output root: {dataset}")
    print(f"Figures {sorted(args.figures)} require {len(specs)} complete runs and {len(specs) * 19} checkpoints.", flush=True)
    if args.dry_run:
        print("Dry run only. No solver launched and no results processed.")
        return 0
    try:
        report = process_publication(dataset, args.figures, args.paths, validate_only=args.validate_only,
                                     source_root=ROOT,run_set=args.run_set)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"PROCESSING FAILED: {exc}\nUse scripts/windows/run_campaigns.bat --status to inspect missing/stale runs. No incomplete publication accepted.", file=sys.stderr)
        return 1
    print(f"PASS: {report['run_count']} runs, {report['checkpoint_count']} observations, requested jobs={report['requested_jobs']}.")
    if not args.validate_only:
        print(f"Run-set: {args.run_set}; outputs below {dataset}")
        print("CSV: results/csv/; standardized data: results/processed/publication/; configurations: results/rasmol/")
        print("Processing record: results/csv/publication_manifest.json")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
