#!/bin/bash
# Launch the GHOST server-side stack in a labelled tmux session, from inside
# the ros2_ws container. Each component gets its own named window (shown in
# the tmux status bar): Bridge, Drivers, Coord, Aggregator, Localize.
#
# This mirrors the working launch_multi_spot.sh (drivers + ros-sharp bridge +
# spot_multi coordination, which carries GraphNav localization) and ADDS the
# multi-operator aggregator. The coordination node's sync-drive stays dormant
# unless something publishes /multi_spot/cmd_vel, so the aggregator is the
# effective commander on /spot*/cmd_vel while joint control stays available.
#
# Localize is a re-runnable helper for the case where the robot couldn't see
# a fiducial at startup (the automatic attempt inside spot_multi then failed).
#
# Prereqs (once): colcon build --packages-select ghost_msgs ghost_aggregator
# Usage:          bash launch_ghost.sh                 (attaches to the session)
#                 START_DRIVERS=false bash launch_ghost.sh   (skip the drivers)

set -e

SESSION="ghost"
WS="/ros2_ws"
SOURCE="cd $WS && source install/setup.bash"

# tusker -> /spot, gouger -> /spot2 (per the spot_configs); swap if reversed.
TUSKER_CFG='$HOME/spot_configs/spot_tusker.yaml'
GOUGER_CFG='$HOME/spot_configs/spot_gouger.yaml'
MAP_PATH="/root/spot_configs/map/demo"

START_DRIVERS="${START_DRIVERS:-true}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session '$SESSION' already running — attaching. (tmux kill-session -t $SESSION to reset.)"
    exec tmux attach -t "$SESSION"
fi

# --- Bridge: rosbridge :9090 + file_server (URDFs for Unity) ---
tmux new-session -d -s "$SESSION" -n Bridge
tmux send-keys -t "$SESSION:Bridge" "$SOURCE" C-m
tmux send-keys -t "$SESSION:Bridge" \
    "ros2 launch file_server2 ros_sharp_communication.launch.py" C-m

# --- Drivers: the only robot-touching part; start early so coordination's
#     localization calls find the services. Toggle with START_DRIVERS. ---
if [ "$START_DRIVERS" = "true" ]; then
    tmux new-window -t "$SESSION" -n Drivers
    tmux send-keys -t "$SESSION:Drivers" "$SOURCE" C-m
    tmux send-keys -t "$SESSION:Drivers" \
        "ros2 launch spot_driver spot_driver.launch.py config_file:=$TUSKER_CFG" C-m
    tmux split-window -v -t "$SESSION:Drivers"
    tmux send-keys -t "$SESSION:Drivers" "$SOURCE" C-m
    tmux send-keys -t "$SESSION:Drivers" \
        "ros2 launch spot_driver spot_driver.launch.py config_file:=$GOUGER_CFG" C-m
fi

# --- Coord: spot_multi (GraphNav localization + ICP + dormant sync-drive) ---
tmux new-window -t "$SESSION" -n Coord
tmux send-keys -t "$SESSION:Coord" "$SOURCE" C-m
tmux send-keys -t "$SESSION:Coord" \
    "ros2 launch spot_multi spot_multi.launch.py" C-m

# --- Aggregator: multi-operator commander of /spot*/cmd_vel ---
tmux new-window -t "$SESSION" -n Aggregator
tmux send-keys -t "$SESSION:Aggregator" "$SOURCE" C-m
tmux send-keys -t "$SESSION:Aggregator" \
    "ros2 run ghost_aggregator operator_aggregator.py" C-m

# --- Localize: re-run if the startup fiducial wasn't in view ---
tmux new-window -t "$SESSION" -n Localize
tmux send-keys -t "$SESSION:Localize" "$SOURCE" C-m
tmux send-keys -t "$SESSION:Localize" \
    "alias localize='bash $WS/ghost-localize.sh $MAP_PATH'" C-m
tmux send-keys -t "$SESSION:Localize" \
    "echo; echo 'Localization runs automatically in Coord. If a robot says \"not localized\",'; echo 'point its cameras at a fiducial and run:  localize'; echo" C-m

tmux set-option -t "$SESSION" mouse on
tmux select-window -t "$SESSION:Aggregator"
echo "GHOST server stack up. Detach with Ctrl-b d; kill with: tmux kill-session -t $SESSION"
exec tmux attach -t "$SESSION"
