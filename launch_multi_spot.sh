#!/bin/bash

# Tmux script to launch multi-spot robot system
# Creates a tmux session with 4 panes for the different components

SESSION_NAME="multi_spot"

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

# Source ROS2 setup in first window
tmux send-keys -t $SESSION_NAME "cd /ros2_ws && source install/setup.bash" C-m

# Split into 4 panes
tmux split-window -h -t $SESSION_NAME
tmux split-window -v -t $SESSION_NAME:0.0
tmux split-window -v -t $SESSION_NAME:0.2

# Setup each pane
# Pane 0: First Spot Robot (gouger)
tmux send-keys -t $SESSION_NAME:0.0 "cd /ros2_ws && source install/setup.bash" C-m
tmux send-keys -t $SESSION_NAME:0.0 "echo 'Terminal 1: Starting Spot Gouger...'" C-m
tmux send-keys -t $SESSION_NAME:0.0 "ros2 launch spot_driver spot_driver.launch.py config_file:=\$HOME/spot_configs/spot_gouger.yaml" C-m

# Pane 1: Second Spot Robot (tusker)
tmux send-keys -t $SESSION_NAME:0.1 "cd /ros2_ws && source install/setup.bash" C-m
tmux send-keys -t $SESSION_NAME:0.1 "echo 'Terminal 2: Starting Spot Tusker...'" C-m
tmux send-keys -t $SESSION_NAME:0.1 "ros2 launch spot_driver spot_driver.launch.py config_file:=\$HOME/spot_configs/spot_tusker.yaml" C-m

# Pane 2: ROS# Communication Bridge
tmux send-keys -t $SESSION_NAME:0.2 "cd /ros2_ws && source install/setup.bash" C-m
tmux send-keys -t $SESSION_NAME:0.2 "echo 'Terminal 3: Starting ROS# Communication Bridge...'" C-m
tmux send-keys -t $SESSION_NAME:0.2 "ros2 launch file_server2 ros_sharp_communication.launch.py call_services_in_new_thread:=true send_action_goals_in_new_thread:=true" C-m

# Pane 3: Multi-Robot Coordination
tmux send-keys -t $SESSION_NAME:0.3 "cd /ros2_ws && source install/setup.bash" C-m
tmux send-keys -t $SESSION_NAME:0.3 "echo 'Terminal 4: Starting Multi-Robot Coordination...'" C-m
tmux send-keys -t $SESSION_NAME:0.3 "ros2 launch spot_multi spot_multi.launch.py" C-m

# Set pane titles
tmux select-pane -t $SESSION_NAME:0.0 -T "Spot Gouger"
tmux select-pane -t $SESSION_NAME:0.1 -T "Spot Tusker"
tmux select-pane -t $SESSION_NAME:0.2 -T "ROS# Bridge"
tmux select-pane -t $SESSION_NAME:0.3 -T "Multi-Robot"

# Focus on first pane
tmux select-pane -t $SESSION_NAME:0.0

# Attach to session
tmux attach-session -t $SESSION_NAME