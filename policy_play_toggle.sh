#!/bin/bash
#
# Quick play/pause toggle for a running policy_inference_node -- so if
# something looks weird mid-run, you don't have to remember/retype the full
# `ros2 service call .../policy_inference/enable ...` command. Same command
# each time, alternates between pause and resume. run_policy.sh's Enable pane
# tells you to use this in another terminal right after you enable.
#
# Usage:
#   ./policy_play_toggle.sh --spot spot2   # pauses if playing, resumes if paused
#   ./policy_play_toggle.sh --spot spot2   # run again to flip back
#
# Tracks play/pause state in a small local file per robot (.policy_paused_<spot_name>,
# gitignored). This can drift out of sync if you also call the enable service
# directly (bypassing this script) -- if the reported action ever looks wrong,
# check directly rather than trusting the toggle blindly:
#   ros2 service call /<spot_name>/policy_inference/enable std_srvs/srv/SetBool "{data: false}"

CONTAINER_NAME="ros2_ws_gui_record"

# Prevent Git Bash from mangling /ros2_ws/... paths into Windows paths before
# they reach the (Linux) container.
export MSYS_NO_PATHCONV=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SPOT_NAME=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --spot) SPOT_NAME="$2"; shift 2 ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 --spot <spot|spot2>"
            exit 1
            ;;
    esac
done

if [[ -z "$SPOT_NAME" ]]; then
    echo "Error: --spot is required (e.g. spot or spot2) -- no default, so you can't pause the wrong robot by accident"
    exit 1
fi

STATE_FILE="$SCRIPT_DIR/.policy_paused_$SPOT_NAME"

if [[ -f "$STATE_FILE" ]]; then
    ACTION="resume"
    ENABLE_VALUE="true"
else
    ACTION="pause"
    ENABLE_VALUE="false"
fi

# Bounded wait (not indefinite) -- if the policy node for this robot isn't
# running, ros2 service call would otherwise hang silently waiting for a
# service that'll never appear, which is exactly the wrong failure mode for a
# "something looks weird, react now" command. 20s, not a few seconds -- under
# real load (e.g. a training job also running) even a real, live service call
# has been measured taking ~9s; a too-short timeout here would falsely report
# failure exactly when the system is busiest and you need this to actually work.
RUN_CMD="source /opt/ros/humble/setup.bash && source /ros2_ws/install/setup.bash && \
timeout 20 ros2 service call /$SPOT_NAME/policy_inference/enable std_srvs/srv/SetBool \"{data: $ENABLE_VALUE}\""

if [ -f /.dockerenv ]; then
    RESULT="$(bash -c "$RUN_CMD" 2>&1)"
else
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Error: container '${CONTAINER_NAME}' is not running."
        exit 1
    fi
    RESULT="$(docker exec "$CONTAINER_NAME" bash -c "$RUN_CMD" 2>&1)"
fi

echo "$RESULT"
echo

if echo "$RESULT" | grep -q "success=True"; then
    if [[ "$ACTION" == "pause" ]]; then
        touch "$STATE_FILE"
        echo ">>> PAUSED. Run './policy_play_toggle.sh --spot $SPOT_NAME' again to resume playing. <<<"
    else
        rm -f "$STATE_FILE"
        echo ">>> PLAYING. Run './policy_play_toggle.sh --spot $SPOT_NAME' again to pause. <<<"
    fi
else
    echo "!!! No confirmed success -- state NOT changed. Is the policy node running for '$SPOT_NAME'? !!!"
    echo "    (this script never touched the pause-state file, so re-running it will retry the same action)"
fi
