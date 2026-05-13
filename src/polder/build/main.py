"""CLI dispatcher voor `polder-build`."""

from __future__ import annotations

import argparse
from pathlib import Path

from .to_csv import build_csv
from .to_datapackage import build_datapackage
from .to_sqlite import build_sqlite
from .to_viz import build_viz

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DIST_DIR = REPO_ROOT / "dist"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="polder-build",
        description="Bouw afgeleide formaten uit YAML-source-of-truth in data/.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Pad naar data/ directory (default: %(default)s).",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Pad naar output dist/ directory (default: %(default)s).",
    )
    sub = parser.add_subparsers(dest="target", required=True)
    sub.add_parser("sqlite", help="Bouw dist/polder.db SQLite database.")
    sub.add_parser("csv", help="Bouw dist/csv/ CSV-files.")
    sub.add_parser("datapackage", help="Bouw dist/datapackage.json descriptor.")
    sub.add_parser("viz", help="Bouw dist/site/data/ JSON-bundels voor de org chart.")
    sub.add_parser("all", help="Bouw alle targets.")

    args = parser.parse_args()
    data_dir: Path = args.data_dir
    dist_dir: Path = args.dist_dir
    dist_dir.mkdir(parents=True, exist_ok=True)

    if args.target in ("sqlite", "all"):
        out = dist_dir / "polder.db"
        build_sqlite(data_dir, out)
        print(f"wrote {out}")

    if args.target in ("csv", "all"):
        out_dir = dist_dir / "csv"
        build_csv(data_dir, out_dir)
        print(f"wrote {out_dir}/")

    if args.target in ("datapackage", "all"):
        csv_dir = dist_dir / "csv"
        out = dist_dir / "datapackage.json"
        build_datapackage(data_dir, csv_dir, out)
        print(f"wrote {out}")

    if args.target in ("viz", "all"):
        out_dir = dist_dir / "site"
        build_viz(data_dir, out_dir)
        print(f"wrote {out_dir}/")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
