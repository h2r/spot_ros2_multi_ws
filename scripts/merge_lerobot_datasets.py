#!/usr/bin/env python3
"""Merge multiple single-session LeRobot datasets (e.g. bag_*_lerobot folders
produced by bag_trigger) into one multi-episode LeRobotDataset, using
lerobot's built-in aggregate_datasets().

Merged output always goes under the fixed /ros2_ws/merged_recordings/<name>
folder (sibling to recordings/), never back into the input folder -- so
re-running a merge from recordings/ never picks up a previous merge as a
source. --name is the only thing you usually need to set.

--from is shorthand for scoping the search to a named session folder made with
set_recording_session.sh (recordings/<name>/), instead of the flat recordings/
root -- e.g. --from plushie searches recordings/plushie/ only.

If the source folder's name ends in "_dual" (dual_listener_node.py's convention,
e.g. demo_dual), this automatically: (1) searches for "dualbag_*_lerobot" instead
of "bag_*_lerobot" -- dual recordings use a different filename prefix, so the
default pattern would otherwise silently match nothing; (2) appends "_dual" to
the merge --name if it doesn't already end with one, so the merged dataset is
clearly labeled as dual-robot data. Both are overridable with --pattern / --name.

Usage (run inside the ros2_ws container):
    python3 /ros2_ws/scripts/merge_lerobot_datasets.py --name pick_cube_v1
    python3 /ros2_ws/scripts/merge_lerobot_datasets.py --name plushie_pickups --from plushie
    python3 /ros2_ws/scripts/merge_lerobot_datasets.py --name demo --from demo_dual  # -> merged_recordings/demo_dual
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from lerobot.datasets.aggregate import aggregate_datasets

RECORDINGS_ROOT = Path("/ros2_ws/recordings")
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
    parser.add_argument("--input-dir", type=Path, default=None,
                         help="Directory to search for datasets in. Defaults to --recordings-root, "
                              "or --recordings-root/<--from> if --from is given. Mutually exclusive "
                              "with --from.")
    parser.add_argument("--from", dest="from_session", default=None,
                         help="Search recordings/<name>/ instead of the flat recordings root -- "
                              "matches a session folder made with set_recording_session.sh. "
                              "Mutually exclusive with --input-dir.")
    parser.add_argument("--recordings-root", type=Path, default=RECORDINGS_ROOT,
                         help="Base recordings directory --from is relative to")
    parser.add_argument("--pattern", default=None,
                         help="Glob pattern to search for. Defaults to 'dualbag_*_lerobot' if the "
                              "source folder name ends in '_dual', else 'bag_*_lerobot'.")
    parser.add_argument("--name", default=None,
                         help="Name for this merge. Written to merged_recordings/<name>. "
                              "Defaults to merged_<timestamp>")
    parser.add_argument("--repo-id", default=None,
                         help="repo_id label for the merged dataset. Defaults to jtoribio/<name>")
    args = parser.parse_args()

    if args.input_dir and args.from_session:
        raise SystemExit("--input-dir and --from are mutually exclusive")
    input_dir = args.input_dir or (
        args.recordings_root / args.from_session if args.from_session else args.recordings_root
    )
    is_dual = input_dir.name.endswith("_dual")

    pattern = args.pattern or ("dualbag_*_lerobot" if is_dual else "bag_*_lerobot")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = args.name or f"merged_{timestamp}"
    if is_dual and not name.endswith("_dual"):
        name = f"{name}_dual"
        print(f"Source folder '{input_dir.name}' looks like dual-robot data -- naming merge output '{name}'")
    output_root = MERGED_RECORDINGS_DIR / name
    repo_id = args.repo_id or f"jtoribio/{name}"

    if output_root.exists():
        raise SystemExit(f"Output directory already exists, refusing to overwrite: {output_root}")
    MERGED_RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Scanning {input_dir} for datasets matching '{pattern}'...")
    sources = find_source_datasets(input_dir, pattern)

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
