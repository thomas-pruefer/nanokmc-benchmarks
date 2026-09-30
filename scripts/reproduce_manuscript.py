"""Optional manual front door. No arguments show help; --execute starts real work."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchmark_core.run_sets import add_run_set_argument


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_run_set_argument(parser,required=False)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Build/verify, run/resume A+B+C, process and plot all six figures")
    mode.add_argument("--dry-run", action="store_true", help="Show all selections without launching any calculation")
    parser.add_argument("--paths", type=Path, default=ROOT / "config/paths.local.json")
    parser.add_argument("--collect-existing", action="store_true", help="Verify existing build artifacts instead of recompiling")
    parser.add_argument("--smoke", action="store_true", help="Include tiny N=256, 0/10 MCS checks before real campaigns")
    parser.add_argument("--jobs", type=int, default=1, help="Concurrent single-thread campaign jobs (1..32; default 1)")
    parser.add_argument("--build-jobs", type=int, default=4, help="Compiler jobs, independent of campaign concurrency")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32 or args.build_jobs < 1:
        parser.error("--jobs must be 1..32 and --build-jobs must be positive")
    if not args.execute and not args.dry_run:
        parser.print_help()
        return 0
    if args.run_set is None:
        parser.error("--run-set NAME is required with --execute or --dry-run")
    def run(script, *options):
        subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / script), *map(str, options)], cwd=ROOT, check=True)
    try:
        if not args.dry_run:
            run("check_environment.py", "--paths", args.paths)
            run("fetch_or_verify_sources.py", "--paths", args.paths)
            flags = (["--collect-existing"] if args.collect_existing else []) + (["--smoke"] if args.smoke else [])
            run("build_all.py", "--paths", args.paths, "--jobs", args.build_jobs, *flags)
        run("run_manuscript_campaigns.py", "--paths", args.paths, "--run-set", args.run_set, "--campaign", "all", "--jobs", args.jobs, *( ["--dry-run"] if args.dry_run else ["--resume"]))
        run("process_results.py", "--paths", args.paths, "--run-set", args.run_set, *(["--dry-run"] if args.dry_run else []))
        run("make_figures.py", "--run-set", args.run_set, *(["--dry-run"] if args.dry_run else []))
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except KeyboardInterrupt:
        print("Interrupted. Use scripts/windows/run_campaigns.bat --resume; incomplete trajectories restart from their original seed.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
