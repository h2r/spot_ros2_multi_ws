#!/usr/bin/env python3
"""Rebuild bag_trigger LeRobot recordings with duplicate (frozen) camera frames dropped.

listener_node.py samples cameras via a fixed-rate timer (lerobot_fps), but the camera
topics themselves publish slower (measured ~8Hz vs. the old 15Hz sample rate on
2026-08-03), so most existing recordings contain long runs of exact-duplicate images
-- image_callback/hand_image_callback have no staleness check, unlike cmd_vel, so a
stale cached frame just gets re-written every tick until a genuinely new one arrives.

This rebuilds each dataset keeping only rows where at least one camera image actually
changed from the last kept row, using the same LeRobotDataset.create()/add_frame()/
save_episode()/finalize() path listener_node.py uses to record in the first place, so
stats/metadata (meta/info.json, meta/episodes/*, per-feature stats) come out correctly
recomputed rather than hand-patched.

IMPORTANT: LeRobotDataset assigns each frame a synthetic timestamp of
frame_index/fps -- it does not preserve real wall-clock arrival time. So each
recording's output fps is computed per-episode as (frames kept) / (original real
duration), NOT hardcoded, otherwise the reconstructed dataset would silently play back
faster or slower than the original demonstration actually moved, corrupting the
timing/velocity semantics of cmd_vel and arm motion for training.

Always writes to --output-root, alongside but separate from --recordings-root -- the
original recording directories are never modified or deleted.

Usage (run inside the ros2_ws container, where the `lerobot` package is installed):
    python3 /ros2_ws/scripts/dedupe_lerobot_recording.py bag_20260803_210842_lerobot
    python3 /ros2_ws/scripts/dedupe_lerobot_recording.py --pattern 'bag_20260803_2*_lerobot'
"""
import argparse
import glob
import hashlib
import os

import cv2
import numpy as np
import pandas as pd

from lerobot.datasets.lerobot_dataset import LeRobotDataset

# Must match bag_trigger/listener_node.py's lerobot_features exactly -- this is what
# was used to originally write these datasets, so it's also what correctly reads them.
LEROBOT_FEATURES = {
    "observation.images.front": {
        "dtype": "image",
        "shape": (224, 224, 3),
        "names": ["height", "width", "channel"],
    },
    "observation.images.hand": {
        "dtype": "image",
        "shape": (224, 224, 3),
        "names": ["height", "width", "channel"],
    },
    "observation.state": {
        "dtype": "float32",
        "shape": (19,),
        "names": [
            "front_left_hip_x", "front_left_hip_y", "front_left_knee",
            "front_right_hip_x", "front_right_hip_y", "front_right_knee",
            "rear_left_hip_x", "rear_left_hip_y", "rear_left_knee",
            "rear_right_hip_x", "rear_right_hip_y", "rear_right_knee",
            "arm_sh0", "arm_sh1", "arm_el0", "arm_el1", "arm_wr0", "arm_wr1", "arm_f1x",
        ],
    },
    "action": {
        "dtype": "float32",
        "shape": (14,),
        "names": [
            "linear_x", "linear_y", "linear_z", "angular_x", "angular_y", "angular_z",
            "arm_pos_x", "arm_pos_y", "arm_pos_z",
            "arm_quat_x", "arm_quat_y", "arm_quat_z", "arm_quat_w",
            "gripper_angle",
        ],
    },
}

DEFAULT_RECORDINGS_ROOT = "/ros2_ws/recordings"
DEFAULT_OUTPUT_ROOT = "/ros2_ws/recordings_dedup"


def decode_png_rgb(raw_bytes: bytes) -> np.ndarray:
    """Undo LeRobotDataset's PNG encoding back to the RGB array add_frame() expects.

    cv2.imdecode always returns BGR regardless of the source PNG's channel order, so
    this converts back to RGB to match what listener_node.py originally passed in
    (straight from CvBridge with desired_encoding='rgb8').
    """
    bgr = cv2.imdecode(np.frombuffer(raw_bytes, np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def dedupe_one(src_dir: str, output_root: str) -> None:
    name = os.path.basename(os.path.normpath(src_dir))
    parquet_path = os.path.join(src_dir, "data", "chunk-000", "file-000.parquet")
    tasks_path = os.path.join(src_dir, "meta", "tasks.parquet")

    if not os.path.exists(parquet_path) or not os.path.exists(tasks_path):
        print(f"{name}: SKIP (missing data/chunk-000/file-000.parquet or meta/tasks.parquet)")
        return

    df = pd.read_parquet(parquet_path)
    task = pd.read_parquet(tasks_path).index[0]

    front_hashes = [hashlib.md5(row["bytes"]).digest() for row in df["observation.images.front"]]
    hand_hashes = [hashlib.md5(row["bytes"]).digest() for row in df["observation.images.hand"]]

    # Keep frame 0, then any frame where either camera actually changed since the
    # last *kept* frame (not just the previous frame -- avoids keeping a run of
    # frames that are each identical to the one before but drift from what we kept).
    keep = [0]
    for i in range(1, len(df)):
        if front_hashes[i] != front_hashes[keep[-1]] or hand_hashes[i] != hand_hashes[keep[-1]]:
            keep.append(i)

    if len(keep) == len(df):
        print(f"{name}: no duplicate frames found, skipping ({len(df)} frames)")
        return

    dst_dir = os.path.join(output_root, name)
    if os.path.exists(dst_dir):
        print(f"{name}: SKIP ({dst_dir} already exists)")
        return

    # The original recording's timestamps are a faithful real-time clock (each frame
    # really was captured ~1/original_fps apart, even if the image content was stale),
    # so this ratio is the real average rate of the frames we're keeping -- use it so
    # the rebuilt dataset's synthetic timestamp grid matches how fast the demonstration
    # actually happened, not a made-up constant.
    real_duration = float(df["timestamp"].iloc[-1] - df["timestamp"].iloc[0])
    effective_fps = (len(keep) - 1) / real_duration

    dataset = LeRobotDataset.create(
        repo_id=f"jtoribio/{name}_dedup",
        fps=effective_fps,
        root=dst_dir,
        features=LEROBOT_FEATURES,
        robot_type="spot",
    )

    for i in keep:
        row = df.iloc[i]
        dataset.add_frame({
            "observation.images.front": decode_png_rgb(row["observation.images.front"]["bytes"]),
            "observation.images.hand": decode_png_rgb(row["observation.images.hand"]["bytes"]),
            "observation.state": np.asarray(row["observation.state"], dtype=np.float32),
            "action": np.asarray(row["action"], dtype=np.float32),
            "task": task,
        })

    dataset.save_episode()
    dataset.finalize()
    pct_kept = 100 * len(keep) / len(df)
    print(
        f"{name}: {len(df)} -> {len(keep)} frames ({pct_kept:.0f}% kept, "
        f"{effective_fps:.2f} fps, {real_duration:.1f}s unchanged) written to {dst_dir}"
    )


def resample_one(src_dir: str, output_root: str, target_fps: float) -> None:
    """Resample a recording onto a fixed, SHARED fps across a whole batch, instead of
    dedupe_one()'s per-episode effective_fps.

    lerobot's aggregate_datasets() (used by merge_lerobot_datasets.py) requires every
    source dataset being merged to declare the exact same fps -- dedupe_one()'s
    per-episode effective_fps breaks that (each episode gets its own, since it keeps a
    different fraction of frames depending on how fast that session's cameras happened
    to update). This picks the nearest real frame to each of target_fps's evenly-spaced
    timestamps, so every output episode is exactly num_frames/target_fps long (matching
    its real recorded duration) using genuinely-existing frames -- not invented or
    interpolated -- as long as target_fps is at or below the slowest episode's real
    achievable rate in the batch you're processing.
    """
    name = os.path.basename(os.path.normpath(src_dir))
    parquet_path = os.path.join(src_dir, "data", "chunk-000", "file-000.parquet")
    tasks_path = os.path.join(src_dir, "meta", "tasks.parquet")

    if not os.path.exists(parquet_path) or not os.path.exists(tasks_path):
        print(f"{name}: SKIP (missing data/chunk-000/file-000.parquet or meta/tasks.parquet)")
        return

    dst_dir = os.path.join(output_root, name)
    if os.path.exists(dst_dir):
        print(f"{name}: SKIP ({dst_dir} already exists)")
        return

    df = pd.read_parquet(parquet_path)
    task = pd.read_parquet(tasks_path).index[0]

    timestamps = df["timestamp"].to_numpy()
    real_duration = float(timestamps[-1] - timestamps[0])
    num_frames = int(real_duration * target_fps) + 1
    target_times = timestamps[0] + np.arange(num_frames) / target_fps
    keep = [int(np.argmin(np.abs(timestamps - t))) for t in target_times]

    dataset = LeRobotDataset.create(
        repo_id=f"jtoribio/{name}_resampled",
        fps=target_fps,
        root=dst_dir,
        features=LEROBOT_FEATURES,
        robot_type="spot",
    )

    for i in keep:
        row = df.iloc[i]
        dataset.add_frame({
            "observation.images.front": decode_png_rgb(row["observation.images.front"]["bytes"]),
            "observation.images.hand": decode_png_rgb(row["observation.images.hand"]["bytes"]),
            "observation.state": np.asarray(row["observation.state"], dtype=np.float32),
            "action": np.asarray(row["action"], dtype=np.float32),
            "task": task,
        })

    dataset.save_episode()
    dataset.finalize()
    print(
        f"{name}: {len(df)} -> {len(keep)} frames at shared {target_fps:.2f} fps "
        f"({real_duration:.1f}s unchanged) written to {dst_dir}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "recordings", nargs="*",
        help="Recording directory name(s) under --recordings-root, e.g. bag_20260803_210842_lerobot",
    )
    parser.add_argument(
        "--pattern", default=None,
        help="Glob pattern under --recordings-root instead of naming recordings explicitly, "
             "e.g. 'bag_20260803_2*_lerobot'",
    )
    parser.add_argument("--recordings-root", default=DEFAULT_RECORDINGS_ROOT)
    parser.add_argument(
        "--output-root", default=DEFAULT_OUTPUT_ROOT,
        help="Where cleaned datasets are written -- originals under --recordings-root are never modified",
    )
    parser.add_argument(
        "--target-fps", type=float, default=None,
        help="Resample every recording in this run to this SHARED fps (see resample_one()), "
             "instead of dedupe_one()'s default per-episode effective_fps. Required if you plan "
             "to merge_lerobot_datasets.py these recordings together afterward -- pick a value at "
             "or below the slowest episode's real achievable rate in the batch.",
    )
    args = parser.parse_args()

    if args.pattern:
        targets = sorted(glob.glob(os.path.join(args.recordings_root, args.pattern)))
    elif args.recordings:
        targets = [os.path.join(args.recordings_root, r) for r in args.recordings]
    else:
        parser.error("Specify recording name(s) or --pattern")
        return

    if not targets:
        print("No matching recordings found.")
        return

    os.makedirs(args.output_root, exist_ok=True)
    for src in targets:
        try:
            if args.target_fps:
                resample_one(src, args.output_root, args.target_fps)
            else:
                dedupe_one(src, args.output_root)
        except Exception as e:
            print(f"{os.path.basename(src)}: FAILED ({e})")


if __name__ == "__main__":
    main()
