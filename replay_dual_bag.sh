#!/bin/bash
#
# Wrapper around dual_lerobot_action_player, so you don't have to retype the full
# ros2 run command every time. Works whether you run it from the Windows host
# (shells out into the ros2_ws_gui_record container) or from a shell already
# inside that container (runs directly -- no docker CLI in there).
#
# This is the dual-robot counterpart to replay_bag.sh -- use this for recordings
# made by dual_listener_node.py (dualbag_..._lerobot under recordings_dual/), not
# for single-robot bag_*_lerobot recordings (use replay_bag.sh for those).
#
# This moves TWO physical robots at once, including an autonomous sit/stand for both
# before any teleop motion plays. Make sure both are powered on and have someone on
# the e-stop for each before running.
#
# Usage:
#   ./replay_dual_bag.sh 20260804_015604
#   ./replay_dual_bag.sh dualbag_20260804_015604 --spot-a-name spot --spot-b-name spot2
#   ./replay_dual_bag.sh 20260804_015604 --rate 0.5
#   ./replay_dual_bag.sh 20260804_015604 --yes        # skip the confirmation prompt
#   ./replay_dual_bag.sh 20260804_015604 --dry-run     # no confirmation needed either -- nothing moves
#   ./replay_dual_bag.sh dualbag_20260804_014112 --folder demo_dual   # look in recordings/demo_dual/
#
# BAG is looked up under /ros2_ws/recordings_dual/ if it's not itself a path to a
# dataset directory or .parquet file, UNLESS --folder is given, in which case it's
# looked up under recordings/<folder>/ instead (see dual_lerobot_action_player.py --
# --folder is taken exactly as given, it does not read set_recording_session.sh).
#
# Prompts for a "yes" confirmation before playback starts (pass --yes/-y to skip,
# e.g. when calling this from another script). --dry-run skips the prompt
# automatically since it never publishes/calls anything -- neither robot will move.

CONTAINER_NAME="ros2_ws_gui_record"

# Prevent Git Bash from mangling /ros2_ws/... paths into Windows paths before
# they reach the (Linux) container.
export MSYS_NO_PATHCONV=1

BAG=""
SPOT_A_NAME="spot"
SPOT_B_NAME="spot2"
FPS="8.0"
RATE="1.0"
FOLDER=""
RECORDINGS_ROOT=""
SKIP_CONFIRM=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --spot-a-name) SPOT_A_NAME="$2"; shift 2 ;;
        --spot-b-name) SPOT_B_NAME="$2"; shift 2 ;;
        --fps) FPS="$2"; shift 2 ;;
        --rate) RATE="$2"; shift 2 ;;
        --folder) FOLDER="$2"; shift 2 ;;
        --recordings-root) RECORDINGS_ROOT="$2"; shift 2 ;;
        --yes|-y) SKIP_CONFIRM=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -*)
            echo "Unknown argument: $1"
            echo "Usage: $0 <bag> [--spot-a-name spot] [--spot-b-name spot2] [--fps 8.0] [--rate 1.0] [--folder name]"
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
    echo "Error: bag is required (e.g. a timestamp like 20260804_015604, or a full dataset/parquet path)"
    echo "Usage: $0 <bag> [--spot-a-name spot] [--spot-b-name spot2] [--fps 8.0] [--rate 1.0]"
    exit 1
fi

if [[ "$SKIP_CONFIRM" == false && "$DRY_RUN" == false ]]; then
    echo "!!! About to replay '$BAG' onto '$SPOT_A_NAME' AND '$SPOT_B_NAME' -- THIS MOVES TWO PHYSICAL ROBOTS !!!"
    echo "    Make sure there is nothing in the way and you have E-stop ready for BOTH robots."
    read -r -p "Type 'yes' to continue: " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
        echo "Aborted."
        exit 1
    fi
fi

if [[ -n "$FOLDER" && -n "$RECORDINGS_ROOT" ]]; then
    echo "Error: --folder and --recordings-root are mutually exclusive"
    exit 1
fi

DRY_RUN_FLAG=""
if [[ "$DRY_RUN" == true ]]; then
    DRY_RUN_FLAG="--dry-run"
fi

LOCATION_ARGS=""
if [[ -n "$FOLDER" ]]; then
    LOCATION_ARGS="--folder '$FOLDER'"
elif [[ -n "$RECORDINGS_ROOT" ]]; then
    LOCATION_ARGS="--recordings-root '$RECORDINGS_ROOT'"
fi

RUN_CMD="source /opt/ros/humble/setup.bash && \
source /ros2_ws/install/setup.bash && \
ros2 run bag_trigger dual_lerobot_action_player '$BAG' --spot-a-name '$SPOT_A_NAME' --spot-b-name '$SPOT_B_NAME' --fps '$FPS' --rate '$RATE' $LOCATION_ARGS $DRY_RUN_FLAG"

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
