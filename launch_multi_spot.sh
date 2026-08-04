#!/bin/bash

# Tmux script to launch multi-spot robot system
# Creates a tmux session with 6 panes: Tusker, Gouger, ROS# Bridge, Multi-Robot,
# Recording, and an idle Replay pane for running replay_bag.sh / replay_dual_bag.sh
#
# Usage:
#   ./launch_multi_spot.sh                # launch both spot and spot2 (default);
#                                          # recording pane runs dual_listener_node
#                                          # (coordinated dual-robot recording, /bag_trigger)
#   ./launch_multi_spot.sh --spot spot    # single-spot mode: only "spot";
#                                          # recording pane runs listener_node
#   ./launch_multi_spot.sh --spot spot2   # single-spot mode: only "spot2";
#                                          # recording pane runs listener_node

SESSION_NAME="multi_spot"
SINGLE_SPOT=""

# Used below to warn in the Recording pane if no session is set (see set_recording_session.sh).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --spot)
            SINGLE_SPOT="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--spot <spot|spot2>]"
            exit 1
            ;;
    esac
done

if [[ -n "$SINGLE_SPOT" && "$SINGLE_SPOT" != "spot" && "$SINGLE_SPOT" != "spot2" ]]; then
    echo "Error: --spot must be 'spot' or 'spot2', got '$SINGLE_SPOT'"
    exit 1
fi

if [[ -n "$SINGLE_SPOT" ]]; then
    SPOT_NAMES_JSON="[\"$SINGLE_SPOT\"]"
    PIVOT_SPOT="$SINGLE_SPOT"
    BAG_TRIGGER_SPOT="$SINGLE_SPOT"
else
    SPOT_NAMES_JSON='["spot", "spot2"]'
    PIVOT_SPOT="spot"
    BAG_TRIGGER_SPOT="spot"
fi

# Check if session already exists
if tmux has-session -t $SESSION_NAME 2>/dev/null; then
    echo "Session $SESSION_NAME already exists. Attaching..."
    tmux attach-session -t $SESSION_NAME
    exit 0
fi

# Create new tmux session
tmux new-session -d -s $SESSION_NAME

# Enable mouse mode
tmux set-option -t $SESSION_NAME mouse on

# Show pane titles in a status line above each pane
tmux set-option -t $SESSION_NAME pane-border-status top
tmux set-option -t $SESSION_NAME pane-border-format "#{pane_title}"

# Source ROS2 setup in first window
tmux send-keys -t $SESSION_NAME:0.2 "ros2 launch file_server2 ros_sharp_communication.launch.py address:=0.0.0.0" C-m

# Split into 5 panes
tmux split-window -h -t $SESSION_NAME:0.0
tmux split-window -v -t $SESSION_NAME:0.0
tmux split-window -v -t $SESSION_NAME:0.1
tmux split-window -v -t $SESSION_NAME:0.3

# Setup each pane
# Pane 0: First Spot Robot (spot)
tmux send-keys -t $SESSION_NAME:0.0 "cd /ros2_ws && source install/setup.bash" C-m
if [[ -z "$SINGLE_SPOT" || "$SINGLE_SPOT" == "spot" ]]; then
    tmux send-keys -t $SESSION_NAME:0.0 "echo 'Terminal 1: Starting Spot...'" C-m
    tmux send-keys -t $SESSION_NAME:0.0 "ros2 launch spot_driver spot_driver.launch.py config_file:=\$HOME/spot_configs/spot_tusker.yaml stitch_front_images:=True" C-m
else
    tmux send-keys -t $SESSION_NAME:0.0 "echo 'Terminal 1: Spot disabled (single-spot mode: $SINGLE_SPOT)'" C-m
fi

# Pane 1: Second Spot Robot (spot2)
tmux send-keys -t $SESSION_NAME:0.1 "cd /ros2_ws && source install/setup.bash" C-m
if [[ -z "$SINGLE_SPOT" || "$SINGLE_SPOT" == "spot2" ]]; then
    tmux send-keys -t $SESSION_NAME:0.1 "echo 'Terminal 2: Starting Spot2...'" C-m
    tmux send-keys -t $SESSION_NAME:0.1 "ros2 launch spot_driver spot_driver.launch.py config_file:=\$HOME/spot_configs/spot_gouger.yaml stitch_front_images:=True" C-m
else
    tmux send-keys -t $SESSION_NAME:0.1 "echo 'Terminal 2: Spot2 disabled (single-spot mode: $SINGLE_SPOT)'" C-m
fi

# Pane 2: ROS# Communication Bridge
tmux send-keys -t $SESSION_NAME:0.2 "cd /ros2_ws && source install/setup.bash" C-m
tmux send-keys -t $SESSION_NAME:0.2 "echo 'Terminal 3: Starting ROS# Communication Bridge...'" C-m
tmux send-keys -t $SESSION_NAME:0.2 "ros2 launch file_server2 ros_sharp_communication.launch.py" C-m

# Pane 3: Multi-Robot Coordination
tmux send-keys -t $SESSION_NAME:0.3 "cd /ros2_ws && source install/setup.bash" C-m
tmux send-keys -t $SESSION_NAME:0.3 "echo 'Terminal 4: Starting Multi-Robot Coordination...'" C-m
tmux send-keys -t $SESSION_NAME:0.3 "ros2 launch spot_multi spot_multi.launch.py spot_names:='$SPOT_NAMES_JSON' pivot_spot:=$PIVOT_SPOT" C-m

# Pane 4: ROS2 Bag Trigger Listener
# No --spot flag (both robots up) -> dual_listener_node, for coordinated dual-robot
# recording. --spot given (single-robot mode) -> listener_node, as before. Both bind
# the same /bag_trigger service name, so Unity/RecordAction.cs never needs to change.
tmux send-keys -t $SESSION_NAME:0.4 "cd /ros2_ws && source install/setup.bash" C-m
if [[ ! -s "$SCRIPT_DIR/.recording_session" ]]; then
    tmux send-keys -t $SESSION_NAME:0.4 "echo '!!! No recording session set -- new recordings will go to recordings/unsorted (or unsorted_dual) !!!'" C-m
    tmux send-keys -t $SESSION_NAME:0.4 "echo '    Run ./set_recording_session.sh --name <name> in another pane to use a named folder instead.'" C-m
fi
if [[ -z "$SINGLE_SPOT" ]]; then
    tmux send-keys -t $SESSION_NAME:0.4 "echo 'Terminal 5: Starting Dual Bag Trigger Listener Node (spot + spot2)...'" C-m
    tmux send-keys -t $SESSION_NAME:0.4 "ros2 run bag_trigger dual_listener_node --ros-args -p spot_a_name:=spot -p spot_b_name:=spot2" C-m
else
    tmux send-keys -t $SESSION_NAME:0.4 "echo 'Terminal 5: Starting Bag Trigger Listener Node...'" C-m
    tmux send-keys -t $SESSION_NAME:0.4 "ros2 run bag_trigger listener_node --ros-args -p spot_name:=$BAG_TRIGGER_SPOT" C-m
fi

# Pane 5: Idle -- left empty on purpose for running replay_bag.sh / replay_dual_bag.sh
# by hand, so a replay doesn't share a pane (and Ctrl+C) with anything else running.
tmux split-window -v -t $SESSION_NAME:0.4
tmux send-keys -t $SESSION_NAME:0.5 "cd /ros2_ws && source install/setup.bash" C-m
tmux send-keys -t $SESSION_NAME:0.5 "echo 'Terminal 6: Idle -- run ./replay_bag.sh or ./replay_dual_bag.sh here'" C-m

# Set pane titles
tmux select-pane -t $SESSION_NAME:0.0 -T " Tusker "
tmux select-pane -t $SESSION_NAME:0.1 -T " Gouger "
tmux select-pane -t $SESSION_NAME:0.2 -T " ROS# Bridge "
tmux select-pane -t $SESSION_NAME:0.3 -T " Multi-Robot "
if [[ -z "$SINGLE_SPOT" ]]; then
    tmux select-pane -t $SESSION_NAME:0.4 -T " Recording (dual) "
else
    tmux select-pane -t $SESSION_NAME:0.4 -T " Recording "
fi
tmux select-pane -t $SESSION_NAME:0.5 -T " Replay "

# Focus on first pane
tmux select-pane -t $SESSION_NAME:0.0

# Attach to session
tmux attach-session -t $SESSION_NAME
