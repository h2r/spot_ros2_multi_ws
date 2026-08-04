#!/bin/bash
#
# Wrapper around scripts/evaluate_checkpoint.py, so you don't have to retype
# the full python invocation every time. Works whether you run it from the
# Windows host (shells out into the ros2_ws_gui_record container) or from a
# shell already inside that container (runs directly -- no docker CLI in there).
#
# All arguments are passed straight through to evaluate_checkpoint.py -- see
# that script's own docstring for the full option list and how to build a
# proper held-out set with merge_datasets.sh.
#
# Usage:
#   ./evaluate_checkpoint.sh --checkpoint runs/plushie_pickups/checkpoints/040000/pretrained_model \
#       --held-out merged_recordings/plushie_pickups_heldout
#
#   ./evaluate_checkpoint.sh --checkpoint-dir runs/plushie_pickups/checkpoints \
#       --held-out merged_recordings/plushie_pickups_heldout \
#       --csv-out runs/plushie_pickups/val_loss.csv

CONTAINER_NAME="ros2_ws_gui_record"

# Prevent Git Bash from mangling /ros2_ws/... paths into Windows paths before
# they reach the (Linux) container.
export MSYS_NO_PATHCONV=1

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 (--checkpoint <path> | --checkpoint-dir <path>) --held-out <path> [--batch-size N] [--device cpu] [--csv-out <path>]"
    exit 1
fi

QUOTED_ARGS=""
for arg in "$@"; do
    QUOTED_ARGS+=" $(printf '%q' "$arg")"
done

RUN_CMD="source /opt/ros/humble/setup.bash && \
source /ros2_ws/install/setup.bash && \
cd /ros2_ws && python3 /ros2_ws/scripts/evaluate_checkpoint.py$QUOTED_ARGS"

if [ -f /.dockerenv ]; then
    # Already inside the container.
    bash -c "$RUN_CMD"
else
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Error: container '${CONTAINER_NAME}' is not running."
        exit 1
    fi
    docker exec "$CONTAINER_NAME" bash -c "$RUN_CMD"
fi
