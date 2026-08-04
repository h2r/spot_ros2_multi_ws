#!/usr/bin/env python3
"""Compute validation loss for one or more trained checkpoints against a held-out
LeRobotDataset, using the SAME forward-pass loss lerobot-train computes during
training (policy.forward(batch), after the checkpoint's own saved preprocessor
-- same normalization stats it was trained with, not recomputed from the
held-out set), just in eval mode with no gradient/optimizer step.

lerobot-train's built-in --eval_freq/--eval only works with a simulated gym
environment (see lerobot/scripts/lerobot_eval.py) -- there's no built-in
held-out DATASET validation-loss support for a real-robot (no simulator)
setup, hence this script.

IMPORTANT: the held-out set must be episodes the checkpoint was NOT trained
on -- this script has no way to verify that itself, it just computes loss on
whatever dataset you point it at. Build a proper held-out set by merging a
few episodes you excluded from the training merge, e.g.:
    ./merge_datasets.sh --name plushie_pickups_heldout --from plushie_recording \
        --pattern "bag_20260803_213019_lerobot"

Usage (run inside the ros2_ws container):
    # Single checkpoint:
    python3 scripts/evaluate_checkpoint.py \
        --checkpoint runs/plushie_pickups/checkpoints/040000/pretrained_model \
        --held-out merged_recordings/plushie_pickups_heldout

    # Every checkpoint in a run, to build a val-loss-vs-step curve:
    python3 scripts/evaluate_checkpoint.py \
        --checkpoint-dir runs/plushie_pickups/checkpoints \
        --held-out merged_recordings/plushie_pickups_heldout \
        --csv-out runs/plushie_pickups/val_loss.csv
"""
import argparse
import csv
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from lerobot.configs.policies import PreTrainedConfig
from lerobot.datasets.factory import resolve_delta_timestamps
from lerobot.datasets.lerobot_dataset import LeRobotDataset, LeRobotDatasetMetadata
from lerobot.policies.factory import get_policy_class, make_pre_post_processors
from lerobot.utils.utils import get_safe_torch_device


def load_held_out_dataset(held_out_root: str, policy_cfg: PreTrainedConfig) -> LeRobotDataset:
    """delta_timestamps (the action-chunk window) depends on the policy's chunk_size,
    so the held-out set has to be loaded per-config -- same pattern lerobot-train
    itself uses (see resolve_delta_timestamps in lerobot/datasets/factory.py).
    """
    ds_meta = LeRobotDatasetMetadata(repo_id="held_out", root=held_out_root)
    delta_timestamps = resolve_delta_timestamps(policy_cfg, ds_meta)
    return LeRobotDataset(repo_id="held_out", root=held_out_root, delta_timestamps=delta_timestamps)


def evaluate_one(checkpoint_path: str, dataset: LeRobotDataset, batch_size: int, device_override: str) -> dict:
    policy_cfg = PreTrainedConfig.from_pretrained(checkpoint_path)
    if device_override:
        policy_cfg.device = device_override
    device = get_safe_torch_device(policy_cfg.device, log=False)

    policy_cls = get_policy_class(policy_cfg.type)
    policy = policy_cls.from_pretrained(checkpoint_path)
    policy.to(device)
    # NOT .eval() -- ACT's VAE encoder branch (needed for the KL loss term) is
    # gated on self.training; in eval mode it's skipped entirely (returns None,
    # since eval mode is for inference, where there's no ground-truth action to
    # encode) and forward() crashes trying to compute kld_loss from it. Getting
    # a loss number comparable to the training curve requires .train() mode so
    # the same encoder path runs -- torch.no_grad() below is what actually
    # prevents any weights from updating, not .eval().
    policy.train()

    # Reuses the checkpoint's own saved normalization stats (via pretrained_path)
    # rather than recomputing from the held-out set -- the held-out set must be
    # normalized the same way the model was trained, not by its own stats.
    preprocessor, _ = make_pre_post_processors(
        policy_cfg,
        pretrained_path=checkpoint_path,
        preprocessor_overrides={"device_processor": {"device": str(device)}},
    )

    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0)

    total_loss = 0.0
    total_l1 = 0.0
    n_batches = 0
    with torch.no_grad():
        for batch in dataloader:
            batch = preprocessor(batch)
            loss, output_dict = policy.forward(batch)
            total_loss += float(loss)
            total_l1 += float(output_dict.get("l1_loss", loss))
            n_batches += 1

    return {
        "val_loss": total_loss / n_batches,
        "val_l1_loss": total_l1 / n_batches,
        "n_batches": n_batches,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", help="Path to a single pretrained_model directory")
    parser.add_argument(
        "--checkpoint-dir",
        help="Path to a checkpoints/ dir -- evaluates every <step>/pretrained_model found under it",
    )
    parser.add_argument(
        "--held-out", required=True,
        help="Path to a LeRobotDataset root of episodes NOT used to train the checkpoint(s)",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="", help="Override the checkpoint's saved device, e.g. cpu")
    parser.add_argument("--csv-out", default=None, help="Optional path to append results to as CSV")
    args = parser.parse_args()

    if bool(args.checkpoint) == bool(args.checkpoint_dir):
        parser.error("Specify exactly one of --checkpoint or --checkpoint-dir")

    if args.checkpoint:
        checkpoints = [("single", args.checkpoint)]
    else:
        base = Path(args.checkpoint_dir)
        checkpoints = []
        for step_dir in sorted(base.iterdir()):
            pm = step_dir / "pretrained_model"
            if pm.is_dir():
                checkpoints.append((step_dir.name, str(pm)))
        if not checkpoints:
            raise SystemExit(f"No <step>/pretrained_model directories found under {base}")

    # delta_timestamps only depends on chunk_size, which is fixed for a whole
    # run -- resolve it once against the first checkpoint so the held-out
    # dataset is loaded a single time, not once per checkpoint evaluated.
    first_cfg = PreTrainedConfig.from_pretrained(checkpoints[0][1])
    dataset = load_held_out_dataset(args.held_out, first_cfg)
    print(f"Held-out set: {dataset.num_frames} frames, {dataset.num_episodes} episode(s) from {args.held_out}")
    print(f"Evaluating {len(checkpoints)} checkpoint(s)...\n")

    rows = []
    for step_name, ckpt_path in checkpoints:
        result = evaluate_one(ckpt_path, dataset, args.batch_size, args.device)
        print(
            f"step {step_name}: val_loss={result['val_loss']:.4f} "
            f"val_l1_loss={result['val_l1_loss']:.4f} ({result['n_batches']} batches)"
        )
        rows.append({"step": step_name, **result})

    if args.csv_out:
        write_header = not Path(args.csv_out).exists()
        with open(args.csv_out, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["step", "val_loss", "val_l1_loss", "n_batches"])
            if write_header:
                writer.writeheader()
            writer.writerows(rows)
        print(f"\nResults written to {args.csv_out}")


if __name__ == "__main__":
    main()
