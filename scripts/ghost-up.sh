#!/bin/bash
# The whole GHOST stack as ONE flat 6-pane tmux, on the server. Operators reach
# everything here (the always-reachable host), which also solves multi-device
# access. Windows runs ONLY Unity, pushing its video stream to this machine.
#
#   ┌────────────┬────────────┬──────────────┐
#   │ Drivers    │ Aggregator │  Video       │   The right column (Video/Web)
#   ├────────────┼────────────┤──────────────┤   runs on THIS host; the four
#   │ Bridge     │ Coord      │  Web         │   ROS panes run in the ros2_ws
#   └────────────┴────────────┴──────────────┘   container via `docker exec`.
#
# Run this on the SERVER. It self-heals to a known-good state every launch:
# seeds robot configs, (re)starts the ros2_ws container mounting THIS repo,
# builds the workspace (full on first run), builds the web console, fetches
# MediaMTX, then opens the flat tmux. The only one-time prep is a dedicated
# clone + submodules + tmux on the host:
#   git clone <repo> ~/many-humans && cd ~/many-humans
#   git checkout many-humans && git submodule update --init --recursive
#   sudo apt-get install -y tmux        # if the host lacks it
#
# Usage: bash scripts/ghost-up.sh        (creates + attaches the 'ghost' session)

set -e

SESSION="ghost"
CONTAINER="ros2_ws_mh"   # our own container (docker-compose.override.yml) — never the lab's ros2_ws
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENTER="docker exec -it $CONTAINER bash"
SRC="source /opt/ros/humble/setup.bash && source /ros2_ws/install/setup.bash"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session '$SESSION' already running — attaching. (tmux kill-session -t $SESSION to reset.)"
    exec tmux attach -t "$SESSION"
fi

# ===================== clean bring-up (idempotent) =====================
# Make every launch a known-good state: the container that mounts THIS repo,
# robot configs in place, workspace built. Safe to re-run.
SHARED_SECRETS="$HOME/spot_ros2_multi_ws/secrets"   # where the real configs live

# 1. Robot configs (gitignored) — seed from the shared clone if ours is empty.
if [ ! -e "$ROOT/secrets/spot_tusker.yaml" ] && [ -d "$SHARED_SECRETS" ]; then
    echo "Seeding robot configs from $SHARED_SECRETS ..."
    cp -rn "$SHARED_SECRETS/." "$ROOT/secrets/" 2>/dev/null || true
fi

# 2. Bring up OUR container ($CONTAINER, named via docker-compose.override.yml)
#    mounting THIS repo. We only ever drop/recreate ours — the lab's shared
#    ros2_ws container is never touched.
echo "Bringing up the $CONTAINER container from $ROOT ..."
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
( cd "$ROOT" && docker compose up -d ros2_ws ) || { echo "ERROR: 'docker compose up' failed."; exit 1; }
for _ in 1 2 3 4 5; do docker exec "$CONTAINER" true 2>/dev/null && break; sleep 1; done

# 3. Build the workspace. Sentinel is spot_driver (a core package), NOT
#    install/setup.bash — the latter exists after a ghost-only build, which
#    would wrongly skip building the drivers. Full build if spot_driver is
#    missing; otherwise just refresh our packages.
if docker exec "$CONTAINER" test -d /ros2_ws/install/spot_driver 2>/dev/null; then
    echo "Building ghost packages ..."
    docker exec "$CONTAINER" bash -lc \
        "cd /ros2_ws && source /opt/ros/humble/setup.bash && colcon build --packages-select ghost_msgs ghost_aggregator" \
        || echo "WARNING: ghost build failed."
else
    echo "spot_driver not built — building the FULL workspace; this takes a while..."
    docker exec "$CONTAINER" bash -lc \
        "cd /ros2_ws && source /opt/ros/humble/setup.bash && colcon build" \
        || echo "WARNING: full build failed — ROS panes may not start."
fi
# =================== end clean bring-up ===================

# Build the web console on first run (the Web pane serves web/dist).
if [ ! -d "$ROOT/web/dist" ]; then
    echo "Building web console (first run)..."
    bash "$ROOT/scripts/build-web.sh" \
        || echo "WARNING: web build failed — the Web pane won't serve until it succeeds."
fi

# Fetch the Linux MediaMTX on first run (the Video pane runs it).
if [ ! -x "$ROOT/stream/bin/mediamtx" ]; then
    echo "Fetching MediaMTX (first run)..."
    ( cd "$ROOT/stream" && bash get_mediamtx.sh linux ) \
        || echo "WARNING: MediaMTX download failed — the Video pane won't serve."
fi

# Even 3x2 grid: make six panes, then let tmux tile them to equal sizes
# (robust across tmux versions, unlike -p/-l percentages). Tiled places panes
# row-major by index:
#   0 1 2  ->  Drivers   Aggregator  Video
#   3 4 5  ->  Bridge    Coord       Web
tmux new-session -d -s "$SESSION" -x 220 -y 50
tmux split-window -h -t "$SESSION":0.0
tmux split-window -h -t "$SESSION":0.0
tmux split-window -v -t "$SESSION":0.0
tmux split-window -v -t "$SESSION":0.1
tmux split-window -v -t "$SESSION":0.2
tmux select-layout -t "$SESSION" tiled
tmux set-option -t "$SESSION" pane-border-status top
tmux set-option -t "$SESSION" mouse on

# Title a pane, enter the container, run a sourced command. Two send-keys
# (enter shell, then run) avoids nested quoting through docker exec.
ros_pane() {  # $1 pane index   $2 title   $3 command
    tmux select-pane -t "$SESSION:0.$1" -T "$2"
    tmux send-keys    -t "$SESSION:0.$1" "$ENTER" C-m
    tmux send-keys    -t "$SESSION:0.$1" "$SRC && $3" C-m
}

# ROS panes (left + middle columns), inside the container.
ros_pane 0 "Drivers"    "ros2 launch spot_driver spot_driver.launch.py config_file:=\$HOME/spot_configs/spot_tusker.yaml & ros2 launch spot_driver spot_driver.launch.py config_file:=\$HOME/spot_configs/spot_gouger.yaml"
ros_pane 1 "Aggregator" "ros2 run ghost_aggregator operator_aggregator.py"
ros_pane 3 "Bridge"     "ros2 launch file_server2 ros_sharp_communication.launch.py"
ros_pane 4 "Coord"      "ros2 launch spot_multi spot_multi.launch.py"

# Right column — content, on THIS host.
tmux select-pane -t "$SESSION:0.2" -T "Video (MediaMTX)"
tmux send-keys    -t "$SESSION:0.2" "cd '$ROOT/stream' && ./bin/mediamtx mediamtx.yml" C-m

tmux select-pane -t "$SESSION:0.5" -T "Web (console)"
tmux send-keys    -t "$SESSION:0.5" "cd '$ROOT/web/dist' && python3 -m http.server 5173" C-m

tmux select-pane -t "$SESSION:0.0"
echo "Up. Detach with Ctrl-b d; kill everything with: tmux kill-session -t $SESSION"
exec tmux attach -t "$SESSION"
