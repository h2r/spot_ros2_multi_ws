#!/bin/bash
#
# Wrapper around `lerobot-train`, so you don't have to retype the full command
# every time. Works whether you run it from the Windows host (shells out into
# the ros2_ws_gui_record container) or from a shell already inside that
# container (runs directly -- no docker CLI in there).
#
# Usage:
#   ./train_policy.sh --dataset bag_20260727_183730_lerobot
#   ./train_policy.sh --dataset plushie_pickups --steps 40000 --batch-size 16
#   ./train_policy.sh --dataset plushie_pickups --no-wandb          # skip wandb
#   ./train_policy.sh --dataset plushie_pickups --name pick_cube_v2 # override the run name
#   ./train_policy.sh --dataset plushie_pickups --refresh-cache     # force a fresh local copy
#   ./train_policy.sh --dataset plushie_pickups --chunk-size 20     # shorter action-chunk horizon, much faster data loading
#
# --dataset can be a folder name inside merged_recordings/ or recordings/ (the
# common case, checked in that order) or an absolute /ros2_ws/... path if
# you're pointing somewhere else.
#
# Checkpoints land in runs/<name>/checkpoints/<step>/pretrained_model/. --name
# defaults to the dataset folder's own name (no timestamp) -- refuses to run if
# runs/<name> already exists, so re-running the same --dataset without --name
# never silently overwrites a previous run's checkpoints; pass --name to use a
# different run name instead.
#
# Before training, --dataset is copied into a container-local cache under
# /root/.cache/spot_training_data/ (reused on later runs, not re-copied) and
# training reads from THAT copy, not the original -- measured ~4x faster raw
# reads than the Windows bind-mount recordings/merged_recordings live on (see
# docker-compose.yml). Worth keeping regardless, but NOTE this is not the main
# lever for slow data loading -- see --chunk-size below for the one that
# actually matters. The cache lives in the container's own writable layer: if
# the container is ever recreated the cache is gone, which is harmless -- it
# just gets re-copied on the next run. Since merge_lerobot_datasets.py already
# refuses to overwrite an existing merged dataset by the same name, a cached
# copy going stale (source changed after caching) shouldn't normally happen --
# --refresh-cache forces a re-copy anyway if you ever need it.
#
# --chunk-size controls ACT's action-chunk horizon (forwarded to both
# --policy.chunk_size and --policy.n_action_steps). This is the actual lever
# for slow data loading, not disk I/O: fetching a single frame is ~4ms, but
# ACT's default chunk_size=100 means every training sample needs a 100-step
# future action sequence assembled, measured at ~333ms/item -- ~85x slower,
# and the dominant cost in each training step's data_s. Smaller chunk_size
# scales down roughly linearly. Also more proportionate for short episodes:
# chunk_size=100 is 10s of future actions at 10fps, which can be most of an
# entire ~10-28s pickup episode. Defaults to 20 (2s) here rather than ACT's
# usual 100.

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
REFRESH_CACHE=false
CACHE_ROOT="/root/.cache/spot_training_data"
CHUNK_SIZE=20

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
        --refresh-cache) REFRESH_CACHE=true; shift ;;
        --chunk-size) CHUNK_SIZE="$2"; shift 2 ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 --dataset <name|path> [--name run_name] [--policy act] [--steps N] [--batch-size N] [--no-wandb] [--wandb-project name] [--refresh-cache] [--chunk-size N]"
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
    NAME="$(basename "$DATASET_ROOT")"
fi

if [[ -d "$SCRIPT_DIR/runs/$NAME" ]]; then
    echo "Error: runs/$NAME already exists -- refusing to overwrite a previous run's checkpoints."
    echo "Pass --name to use a different run name, e.g. --name ${NAME}_v2"
    exit 1
fi

WANDB_ARGS="--wandb.enable=false"
if [[ "$WANDB" == true ]]; then
    WANDB_ARGS="--wandb.enable=true --wandb.project=$WANDB_PROJECT"
fi

CACHED_DATASET_ROOT="$CACHE_ROOT/$(basename "$DATASET_ROOT")"

CACHE_CMD="mkdir -p '$CACHE_ROOT'"
if [[ "$REFRESH_CACHE" == true ]]; then
    CACHE_CMD="$CACHE_CMD && rm -rf '$CACHED_DATASET_ROOT'"
fi
CACHE_CMD="$CACHE_CMD && if [ ! -d '$CACHED_DATASET_ROOT' ]; then \
echo 'Copying dataset to local cache for faster training I/O (one-time per dataset)...' && \
cp -r '$DATASET_ROOT' '$CACHED_DATASET_ROOT'; \
else echo 'Using existing local cache: $CACHED_DATASET_ROOT'; fi"

echo "Training '$POLICY' on $DATASET_ROOT -> /ros2_ws/runs/$NAME"

TRAIN_CMD="cd /ros2_ws && lerobot-train \
  --dataset.repo_id=jtoribio/$NAME \
  --dataset.root=$CACHED_DATASET_ROOT \
  --policy.type=$POLICY \
  --policy.push_to_hub=false \
  --policy.chunk_size=$CHUNK_SIZE \
  --policy.n_action_steps=$CHUNK_SIZE \
  --output_dir=/ros2_ws/runs/$NAME \
  --job_name=$NAME \
  --steps=$STEPS \
  --batch_size=$BATCH_SIZE \
  --num_workers=$NUM_WORKERS \
  --save_checkpoint=true \
  --save_freq=$SAVE_FREQ \
  --log_freq=$LOG_FREQ \
  $WANDB_ARGS"

FULL_CMD="$CACHE_CMD && $TRAIN_CMD"

if [ -f /.dockerenv ]; then
    # Already inside the container.
    bash -c "$FULL_CMD"
else
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Error: container '${CONTAINER_NAME}' is not running."
        exit 1
    fi
    docker exec "$CONTAINER_NAME" bash -c "$FULL_CMD"
fi
