"""Write the raw source drop and its answer key: `python scripts/generate_data.py [--scale 1.0]`."""

from __future__ import annotations

import argparse
from pathlib import Path

from pipeline_builder.datagen import generate


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--scale", type=float, default=1.0, help="1.0 = 60k customers, 400k orders, ~0.9M lines")
    ap.add_argument("--defect-rate", type=float, default=0.005)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    truth = generate(args.out, scale=args.scale, defect_rate=args.defect_rate, seed=args.seed)
    for f in sorted((args.out / "raw").iterdir()):
        print(f"{f.name:<22} {f.stat().st_size / 1e6:8.1f} MB")
    print(f"injected defects: {len(truth.defects)}")


if __name__ == "__main__":
    main()
