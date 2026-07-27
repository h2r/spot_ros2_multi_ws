#!/usr/bin/env python3
"""Merge multiple single-session LeRobot datasets (e.g. bag_*_lerobot folders
produced by bag_trigger) into one multi-episode LeRobotDataset, using
lerobot's built-in aggregate_datasets().

Merged output always goes under the fixed /ros2_ws/merged_recordings/<name>
folder (sibling to recordings/), never back into the input folder -- so
re-running a merge from recordings/ never picks up a previous merge as a
source. --name is the only thing you usually need to set.

Usage (run inside the ros2_ws container):
    python3 /ros2_ws/scripts/merge_lerobot_datasets.py --name pick_cube_v1
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from lerobot.datasets.aggregate import aggregate_datasets

MERGED_RECORDINGS_DIR = Path("/ros2_ws/merged_recordings")


def find_source_datasets(input_dir: Path, pattern: str) -> list[Path]:
    candidates = sorted(p for p in input_dir.glob(pattern) if p.is_dir())
    valid = []
    for path in candidates:
        info_path = path / "meta" / "info.json"
        data_path = path / "data" / "chunk-000" / "file-000.parquet"
        if not info_path.exists() or not data_path.exists():
            print(f"  SKIP {path.name}: missing meta/info.json or data file (incomplete conversion)")
            continue
        info = json.loads(info_path.read_text())
        if info.get("total_episodes", 0) == 0 or info.get("total_frames", 0) == 0:
            print(f"  SKIP {path.name}: 0 episodes/frames recorded")
            continue
        valid.append(path)
    return valid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("/ros2_ws/recordings"))
    parser.add_argument("--pattern", default="bag_*_lerobot")
    parser.add_argument("--name", default=None,
                         help="Name for this merge. Written to merged_recordings/<name>. "
                              "Defaults to merged_<timestamp>")
    parser.add_argument("--repo-id", default=None,
                         help="repo_id label for the merged dataset. Defaults to jtoribio/<name>")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = args.name or f"merged_{timestamp}"
    output_root = MERGED_RECORDINGS_DIR / name
    repo_id = args.repo_id or f"jtoribio/{name}"

    if output_root.exists():
        raise SystemExit(f"Output directory already exists, refusing to overwrite: {output_root}")
    MERGED_RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Scanning {args.input_dir} for datasets matching '{args.pattern}'...")
    sources = find_source_datasets(args.input_dir, args.pattern)

    if not sources:
        raise SystemExit("No valid source datasets found. Nothing to merge.")

    print(f"\nMerging {len(sources)} dataset(s) into {output_root}:")
    total_frames = 0
    for src in sources:
        info = json.loads((src / "meta" / "info.json").read_text())
        frames = info.get("total_frames", 0)
        total_frames += frames
        print(f"  + {src.name}  ({info.get('total_episodes', 0)} episode(s), {frames} frames)")

    repo_ids = [f"jtoribio/{p.name}" for p in sources]
    roots = list(sources)

    aggregate_datasets(
        repo_ids=repo_ids,
        aggr_repo_id=repo_id,
        roots=roots,
        aggr_root=output_root,
    )

    print(f"\nDone. Merged dataset written to: {output_root}")
    print(f"Total frames merged: {total_frames}")
    print(f"\nTrain against it with:  --dataset.root={output_root}")


if __name__ == "__main__":
    main()
