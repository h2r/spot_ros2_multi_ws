#!/bin/bash
#
# Wrapper around policy_inference_node, so you don't have to retype the full
# ros2 run command every time. Works whether you run it from the Windows host
# (shells out into the ros2_ws_gui_record container) or from a shell already
# inside that container (runs directly -- no docker CLI in there).
#
# Requires the robot's ROS graph already running (e.g. ./launch_multi_spot.sh)
# so camera/joint topics exist for it to read.
#
# Usage:
#   ./run_policy.sh --checkpoint /ros2_ws/recordings/runs/<run>/checkpoints/<step>/pretrained_model --spot-name spot
#   ./run_policy.sh --checkpoint ... --spot-name spot2
#   ./run_policy.sh --checkpoint ... --spot-name spot2 --device cpu   # avoid GPU contention with a training job
#   ./run_policy.sh --checkpoint ... --spot-name spot2 --no-dry-run   # ACTUALLY MOVES THE ROBOT once enabled
#
# --spot-name is required (no default) -- this is a two-robot setup, and
# there's no safe robot to silently fall back to if you forget it.
#
# dry_run defaults to true -- predictions are logged, never published to the
# robot -- until you explicitly pass --no-dry-run. Either way, the control
# loop itself stays off until you separately call:
#   ros2 service call /policy_inference/enable std_srvs/srv/SetBool "{data: true}"

CONTAINER_NAME="ros2_ws_gui_record"

# Prevent Git Bash from mangling /ros2_ws/... paths into Windows paths before
# they reach the (Linux) container.
export MSYS_NO_PATHCONV=1

CHECKPOINT=""
SPOT_NAME=""
DRY_RUN=true
DEVICE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --checkpoint) CHECKPOINT="$2"; shift 2 ;;
        --spot-name) SPOT_NAME="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --no-dry-run) DRY_RUN=false; shift ;;
        --device) DEVICE="$2"; shift 2 ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 --checkpoint <path> --spot-name <spot|spot2> [--device cpu|cuda] [--no-dry-run]"
            exit 1
            ;;
    esac
done

if [[ -z "$CHECKPOINT" ]]; then
    echo "Error: --checkpoint is required (path to a .../pretrained_model directory)"
    exit 1
fi

if [[ -z "$SPOT_NAME" ]]; then
    echo "Error: --spot-name is required (e.g. spot or spot2) -- no default, so you can't run against the wrong robot by accident"
    exit 1
fi

if [[ "$DRY_RUN" == false ]]; then
    echo "!!! --no-dry-run: predicted actions WILL be published once you call the enable service !!!"
    echo "    Make sure the robot is powered on, standing, and someone is on the e-stop."
else
    echo "dry_run=true (default): predictions will be logged only, nothing gets published."
fi

DEVICE_ARG=""
if [[ -n "$DEVICE" ]]; then
    DEVICE_ARG="-p device:=$DEVICE"
fi

RUN_CMD="source /opt/ros/humble/setup.bash && \
source /ros2_ws/install/setup.bash && \
ros2 run bag_trigger policy_inference_node --ros-args \
  -p checkpoint_path:=$CHECKPOINT \
  -p spot_name:=$SPOT_NAME \
  -p dry_run:=$DRY_RUN \
  $DEVICE_ARG"

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
