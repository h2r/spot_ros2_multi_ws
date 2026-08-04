#!/bin/bash
#
# Runs in the "Enable" tmux pane run_policy.sh creates alongside the
# "Policy Inference" pane. This is where the real safety confirmation lives
# now -- loading the policy node (the other pane) was never itself dangerous;
# calling <spot_name>/policy_inference/enable is the moment predicted actions
# can start publishing to the robot, so that's where the "type yes" gate belongs.
#
# The service is namespaced per robot (/<spot_name>/policy_inference/enable),
# not a bare /policy_inference/enable -- otherwise running this for two robots
# at once leaves two nodes offering the identical global service name, with no
# way to tell (or control) which one an enable call actually reaches.
#
# Usage: policy_enable_prompt.sh <spot_name> <dry_run: true|false> <skip_confirm: true|false>
# (ROS must already be sourced by the caller -- run_policy.sh does this.)

SPOT_NAME="$1"
DRY_RUN="$2"
SKIP_CONFIRM="$3"
ENABLE_SERVICE="/$SPOT_NAME/policy_inference/enable"

echo "This pane enables the policy loaded in the other pane."
echo "Waiting for the policy_inference_node service to come up (it can take a few seconds to load)..."

if [[ "$DRY_RUN" != "true" && "$SKIP_CONFIRM" != "true" ]]; then
    echo
    echo "!!! About to ENABLE policy inference on '$SPOT_NAME' -- predicted actions WILL be published !!!"
    echo "    Make sure the robot is powered on, standing, and someone is on the e-stop."
    read -r -p "Type 'yes' to enable: " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
        echo "Not enabled. The policy node in the other pane is still loaded (idle) -- enable manually later with:"
        echo "  ros2 service call $ENABLE_SERVICE std_srvs/srv/SetBool \"{data: true}\""
        exec bash
    fi
fi

ros2 service call "$ENABLE_SERVICE" std_srvs/srv/SetBool "{data: true}"

echo
echo "Enabled. If anything looks weird, open another terminal and run:"
echo "  ./policy_play_toggle.sh --spot-name $SPOT_NAME"
echo "That pauses it immediately -- run the exact same command again to resume playing."
exec bash
