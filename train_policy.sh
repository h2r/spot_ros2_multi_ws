#!/bin/bash
#
# Wrapper around `lerobot-train`, so you don't have to retype the full command
# every time. Works whether you run it from the Windows host (shells out into
# the ros2_ws_gui_record container) or from a shell already inside that
# container (runs directly -- no docker CLI in there).
#
# Usage:
#   ./train_policy.sh --dataset bag_20260727_183730_lerobot
#   ./train_policy.sh --dataset merged_20260727 --steps 40000 --batch-size 16
#   ./train_policy.sh --dataset merged_20260727 --no-wandb          # skip wandb
#   ./train_policy.sh --dataset merged_20260727 --name pick_cube_v2
#
# --dataset can be a folder name inside merged_recordings/ or recordings/ (the
# common case, checked in that order) or an absolute /ros2_ws/... path if
# you're pointing somewhere else.

CONTAINER_NAME="ros2_ws_gui_record"

# Prevent Git Bash from mangling /ros2_ws/... paths into Windows paths before
# they reach the (Linux) container.
export MSYS_NO_PATHCONV=1

# This script's own directory is /ros2_ws, whether we're on the Windows host
# (bind-mounted from spot_ros2_multi_ws/) or already inside the container --
# used below to resolve --dataset shorthand names against the real filesystem.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DATASET=""
NAME=""
POLICY="act"
STEPS=20000
BATCH_SIZE=8
NUM_WORKERS=4
SAVE_FREQ=2000
LOG_FREQ=50
WANDB=true
WANDB_PROJECT="spot-pick-cube"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dataset) DATASET="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --policy) POLICY="$2"; shift 2 ;;
        --steps) STEPS="$2"; shift 2 ;;
        --batch-size) BATCH_SIZE="$2"; shift 2 ;;
        --num-workers) NUM_WORKERS="$2"; shift 2 ;;
        --save-freq) SAVE_FREQ="$2"; shift 2 ;;
        --log-freq) LOG_FREQ="$2"; shift 2 ;;
        --wandb-project) WANDB_PROJECT="$2"; shift 2 ;;
        --no-wandb) WANDB=false; shift ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 --dataset <name|path> [--name run_name] [--policy act] [--steps N] [--batch-size N] [--no-wandb] [--wandb-project name]"
            exit 1
            ;;
    esac
done

if [[ -z "$DATASET" ]]; then
    echo "Error: --dataset is required (folder name inside recordings/, or an absolute /ros2_ws/... path)"
    exit 1
fi

if [[ "$DATASET" == /* ]]; then
    DATASET_ROOT="$DATASET"
elif [[ -d "$SCRIPT_DIR/merged_recordings/$DATASET" ]]; then
    DATASET_ROOT="/ros2_ws/merged_recordings/$DATASET"
elif [[ -d "$SCRIPT_DIR/recordings/$DATASET" ]]; then
    DATASET_ROOT="/ros2_ws/recordings/$DATASET"
else
    echo "Error: could not find '$DATASET' under merged_recordings/ or recordings/"
    exit 1
fi

if [[ -z "$NAME" ]]; then
    NAME="$(basename "$DATASET_ROOT")_$(date +%Y%m%d_%H%M%S)"
fi

WANDB_ARGS="--wandb.enable=false"
if [[ "$WANDB" == true ]]; then
    WANDB_ARGS="--wandb.enable=true --wandb.project=$WANDB_PROJECT"
fi

echo "Training '$POLICY' on $DATASET_ROOT -> /ros2_ws/runs/$NAME"

TRAIN_CMD="cd /ros2_ws && lerobot-train \
  --dataset.repo_id=jtoribio/$NAME \
  --dataset.root=$DATASET_ROOT \
  --policy.type=$POLICY \
  --policy.push_to_hub=false \
  --output_dir=/ros2_ws/runs/$NAME \
  --job_name=$NAME \
  --steps=$STEPS \
  --batch_size=$BATCH_SIZE \
  --num_workers=$NUM_WORKERS \
  --save_checkpoint=true \
  --save_freq=$SAVE_FREQ \
  --log_freq=$LOG_FREQ \
  $WANDB_ARGS"

if [ -f /.dockerenv ]; then
    # Already inside the container.
    bash -c "$TRAIN_CMD"
else
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Error: container '${CONTAINER_NAME}' is not running."
        exit 1
    fi
    docker exec "$CONTAINER_NAME" bash -c "$TRAIN_CMD"
fi
