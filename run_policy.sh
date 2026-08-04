#!/bin/bash
#
# Wrapper around policy_inference_node, so you don't have to retype the full
# ros2 run command every time. Opens a tmux session with two panes -- "Policy
# Inference" (the node itself) and "Enable" (prompts to confirm, then calls
# the enable service) -- so you never need to manually open a second terminal
# to arm it. Works whether you run it from the Windows host (shells out into
# the ros2_ws_gui_record container) or from a shell already inside that
# container (runs directly -- no docker CLI in there).
#
# Requires the robot's ROS graph already running (e.g. ./launch_multi_spot.sh)
# so camera/joint topics exist for it to read.
#
# Usage:
#   ./run_policy.sh --checkpoint plushie_pickups/checkpoints/040000/pretrained_model --spot spot
#   ./run_policy.sh --checkpoint /ros2_ws/runs/<run>/checkpoints/<step>/pretrained_model --spot spot
#   ./run_policy.sh --checkpoint ... --spot spot2
#   ./run_policy.sh --checkpoint ... --spot spot2 --device cpu   # avoid GPU contention with a training job
#   ./run_policy.sh --checkpoint ... --spot spot2 --dry-run      # predictions logged only, never published -- no confirmation needed
#   ./run_policy.sh --checkpoint ... --spot spot2 --yes          # skip the confirmation prompt (e.g. scripted use)
#
# --spot is required (no default) -- this is a two-robot setup, and
# there's no safe robot to silently fall back to if you forget it.
#
# The real safety gate lives in the Enable pane, not before the node loads --
# loading the checkpoint was never itself dangerous (see policy_inference_node.py's
# own docstring: loading it never by itself starts commanding the robot).
# Predicted actions can only start publishing once you type 'yes' in that pane
# (dry_run defaults to FALSE, same "confirm for real, --dry-run skips it"
# pattern as replay_bag.sh/replay_dual_bag.sh). Either way, the control loop
# itself stays off until that pane's enable call goes through:
#   ros2 service call /<spot_name>/policy_inference/enable std_srvs/srv/SetBool "{data: true}"
# Namespaced per robot -- so running this for spot and spot2 at once can't have
# one's enable call accidentally reach the other's node.

CONTAINER_NAME="ros2_ws_gui_record"

# Prevent Git Bash from mangling /ros2_ws/... paths into Windows paths before
# they reach the (Linux) container.
export MSYS_NO_PATHCONV=1

# This script's own directory is /ros2_ws, whether we're on the Windows host
# (bind-mounted from spot_ros2_multi_ws/) or already inside the container --
# used below to resolve --checkpoint shorthand against the real filesystem.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CHECKPOINT=""
SPOT_NAME=""
DRY_RUN=false
DEVICE=""
SKIP_CONFIRM=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --checkpoint) CHECKPOINT="$2"; shift 2 ;;
        --spot) SPOT_NAME="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --yes|-y) SKIP_CONFIRM=true; shift ;;
        --device) DEVICE="$2"; shift 2 ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 --checkpoint <path> --spot <spot|spot2> [--device cpu|cuda] [--dry-run] [--yes]"
            exit 1
            ;;
    esac
done

if [[ -z "$CHECKPOINT" ]]; then
    echo "Error: --checkpoint is required (path to a .../pretrained_model directory)"
    exit 1
fi

# Resolve shorthand: <run>/checkpoints/<step>/pretrained_model (relative to
# runs/, the common case) without needing to type "runs/" yourself. Absolute
# paths and paths relative to the repo root still work as before.
if [[ "$CHECKPOINT" == /* ]]; then
    CHECKPOINT_PATH="$CHECKPOINT"
elif [[ -d "$SCRIPT_DIR/runs/$CHECKPOINT" ]]; then
    CHECKPOINT_PATH="/ros2_ws/runs/$CHECKPOINT"
elif [[ -d "$SCRIPT_DIR/$CHECKPOINT" ]]; then
    CHECKPOINT_PATH="/ros2_ws/$CHECKPOINT"
else
    echo "Error: could not find '$CHECKPOINT' under runs/ (or as an absolute/repo-relative path)"
    exit 1
fi

if [[ -z "$SPOT_NAME" ]]; then
    echo "Error: --spot is required (e.g. spot or spot2) -- no default, so you can't run against the wrong robot by accident"
    exit 1
fi

if [[ "$DRY_RUN" == true ]]; then
    echo "--dry-run: predictions will be logged only, nothing gets published. The Enable pane won't prompt either."
fi

DEVICE_ARG=""
if [[ -n "$DEVICE" ]]; then
    DEVICE_ARG="-p device:=$DEVICE"
fi

POLICY_CMD="source /opt/ros/humble/setup.bash && \
source /ros2_ws/install/setup.bash && \
ros2 run bag_trigger policy_inference_node --ros-args \
  -p checkpoint_path:=$CHECKPOINT_PATH \
  -p spot_name:=$SPOT_NAME \
  -p dry_run:=$DRY_RUN \
  $DEVICE_ARG"

ENABLE_CMD="source /opt/ros/humble/setup.bash && \
source /ros2_ws/install/setup.bash && \
/ros2_ws/scripts/policy_enable_prompt.sh '$SPOT_NAME' '$DRY_RUN' '$SKIP_CONFIRM'"

SESSION_NAME="policy_${SPOT_NAME}"

TMUX_CMD="if tmux has-session -t $SESSION_NAME 2>/dev/null; then
    echo \"Session $SESSION_NAME already exists. Attaching...\"
    tmux attach-session -t $SESSION_NAME
    exit 0
fi
tmux new-session -d -s $SESSION_NAME
tmux split-window -h -t $SESSION_NAME:0.0
tmux send-keys -t $SESSION_NAME:0.0 \"$POLICY_CMD\" C-m
tmux send-keys -t $SESSION_NAME:0.1 \"$ENABLE_CMD\" C-m
tmux select-pane -t $SESSION_NAME:0.0 -T ' Policy Inference '
tmux select-pane -t $SESSION_NAME:0.1 -T ' Enable '
tmux set-option -t $SESSION_NAME pane-border-status top
tmux set-option -t $SESSION_NAME pane-border-format '#{pane_title}'
tmux attach-session -t $SESSION_NAME"

if [ -f /.dockerenv ]; then
    # Already inside the container.
    bash -c "$TMUX_CMD"
else
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Error: container '${CONTAINER_NAME}' is not running."
        exit 1
    fi
    docker exec -it "$CONTAINER_NAME" bash -c "$TMUX_CMD"
fi
