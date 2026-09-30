"""Generate current manuscript Figures 5--10 from verified publication CSVs."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
from benchmark_core.run_sets import add_run_set_argument, data_root, check_run_set


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_run_set_argument(parser)
    parser.add_argument("--figures", nargs="+", type=int, choices=range(5, 11), default=list(range(5, 11)),
                        metavar="N", help="Figures to generate (default: 5 6 7 8 9 10).")
    parser.add_argument("--format", choices=("pdf", "png", "both"), default="both")
    parser.add_argument("--dpi", type=int, default=220, help="PNG/rasterized morphology resolution (default: 220).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Show planned inputs/outputs without requiring data or writing files.")
    mode.add_argument("--validate-only", action="store_true", help="Verify existing inputs without writing figures.")
    args = parser.parse_args(argv)
    if not 72 <= args.dpi <= 1200:
        parser.error("--dpi must be between 72 and 1200")
    try:
        root = data_root(REPOSITORY,args.run_set)
        check_run_set(REPOSITORY,args.run_set)
        csv_dir = root / "results/csv"
        output_dir = root / "figures"
        print(f"Run-set: {args.run_set}; output root: {root}")
        from plotting.inputs import sha256, verify_guards
        guarded_sources = [Path(__file__), REPOSITORY / "plotting/inputs.py", REPOSITORY / "plotting/manuscript.py"]
        implementation_guards = {str(path.resolve()): sha256(path) for path in guarded_sources}
        if args.dry_run:
            from plotting.inputs import FILES
            from plotting.manuscript import OUTPUT_NAMES
            formats = ("pdf", "png") if args.format == "both" else (args.format,)
            print("DRY RUN: no files read as results, no figures written, no solver started.")
            print(f"Requires committed processing manifest: {csv_dir / 'publication_manifest.json'}")
            for number in sorted(set(args.figures)):
                print(f"Figure {number} inputs: {', '.join(FILES[number])}")
                if number == 5:
                    print("  Plus thirteen verified XYZ configuration instances (three Binary morphology views).")
                for fmt in formats:
                    print(f"  Output: {output_dir / (OUTPUT_NAMES[number] + '.' + fmt)}")
            print("Use --validate-only after processing to check actual coverage and hashes.")
            return 0
        from benchmark_core.publication import load_publication
        from benchmark_core.run_store import CampaignLock
        from plotting.inputs import load_inputs
        with CampaignLock(REPOSITORY):
            # Verify processing/source identities in addition to plot-specific checks.
            load_publication(root, figures=args.figures, manifest_path=csv_dir / "publication_manifest.json",
                             source_root=REPOSITORY,run_set=args.run_set)
            data = load_inputs(root, csv_dir, args.figures, run_set=args.run_set)
            data["implementation_guards"].update(implementation_guards)
            if args.validate_only:
                verify_guards(implementation_guards)
                verify_guards(data["input_guards"])
                print("PASS: exact CSV coverage, fit rules, hashes and required configurations verified.")
                return 0
            from plotting.manuscript import render_figures
            formats = ("pdf", "png") if args.format == "both" else (args.format,)
            report = render_figures(data, output_dir, formats=formats, dpi=args.dpi)
        print(f"Generated {len(report['files'])} verified figure files in {output_dir}")
        print(f"Provenance: {output_dir / 'figure_manifest.json'}")
        return 0
    except (ValueError, OSError, KeyError, ImportError, RuntimeError) as error:
        print(f"FIGURE GENERATION FAILED: {error}", file=sys.stderr)
        if isinstance(error, ImportError):
            print("Use the configured benchmark Python and requirements.lock.txt; see HOW_TO_REPRODUCE.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
