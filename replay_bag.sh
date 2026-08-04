#!/bin/bash
#
# Wrapper around lerobot_action_player, so you don't have to retype the full
# ros2 run command every time. Works whether you run it from the Windows host
# (shells out into the ros2_ws_gui_record container) or from a shell already
# inside that container (runs directly -- no docker CLI in there).
#
# This moves the physical robot. Make sure it's powered on, standing, and in
# the same starting arm configuration the recording began from before running.
#
# Usage:
#   ./replay_bag.sh 20260727_175555 --spot spot
#   ./replay_bag.sh bag_20260727_175555 --spot spot2 --rate 0.5
#   ./replay_bag.sh /ros2_ws/recordings/bag_20260727_175555_lerobot --spot spot
#   ./replay_bag.sh 20260727_175555 --spot spot --yes   # skip the confirmation prompt
#   ./replay_bag.sh 20260727_175555 --spot spot --dry-run   # no confirmation needed either -- nothing moves
#
# BAG is looked up under /ros2_ws/recordings/ if it's not itself a path to a
# dataset directory or .parquet file (see lerobot_action_player.py).
#
# Prompts for a "yes" confirmation before playback starts (pass --yes/-y to skip,
# e.g. when calling this from another script). --dry-run skips the prompt
# automatically since it never publishes/calls anything -- the robot won't move.

CONTAINER_NAME="ros2_ws_gui_record"

# Prevent Git Bash from mangling /ros2_ws/... paths into Windows paths before
# they reach the (Linux) container.
export MSYS_NO_PATHCONV=1

BAG=""
SPOT_NAME="spot"
FPS="15.0"
RATE="1.0"
SKIP_CONFIRM=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --spot) SPOT_NAME="$2"; shift 2 ;;
        --fps) FPS="$2"; shift 2 ;;
        --rate) RATE="$2"; shift 2 ;;
        --yes|-y) SKIP_CONFIRM=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -*)
            echo "Unknown argument: $1"
            echo "Usage: $0 <bag> [--spot spot|spot2] [--fps 15.0] [--rate 1.0]"
            exit 1
            ;;
        *)
            if [[ -n "$BAG" ]]; then
                echo "Unexpected extra argument: $1"
                exit 1
            fi
            BAG="$1"
            shift
            ;;
    esac
done

if [[ -z "$BAG" ]]; then
    echo "Error: bag is required (e.g. a timestamp like 20260727_175555, or a full dataset/parquet path)"
    echo "Usage: $0 <bag> [--spot spot|spot2] [--fps 15.0] [--rate 1.0]"
    exit 1
fi

if [[ "$SKIP_CONFIRM" == false && "$DRY_RUN" == false ]]; then
    echo "!!! About to replay '$BAG' onto '$SPOT_NAME' -- THIS MOVES THE PHYSICAL ROBOT !!!"
    echo "    Make sure there is nothing in the way and you have E-stop ready."
    read -r -p "Type 'yes' to continue: " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
        echo "Aborted."
        exit 1
    fi
fi

DRY_RUN_FLAG=""
if [[ "$DRY_RUN" == true ]]; then
    DRY_RUN_FLAG="--dry-run"
fi

RUN_CMD="source /opt/ros/humble/setup.bash && \
source /ros2_ws/install/setup.bash && \
ros2 run bag_trigger lerobot_action_player '$BAG' --spot_name '$SPOT_NAME' --fps '$FPS' --rate '$RATE' $DRY_RUN_FLAG"

if [ -f /.dockerenv ]; then
    # Already inside the container.
    bash -c "$RUN_CMD"
else
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Error: container '${CONTAINER_NAME}' is not running."
        exit 1
    fi
    docker exec -it "$CONTAINER_NAME" bash -c "$RUN_CMD"
fi
