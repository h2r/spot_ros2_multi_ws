#!/bin/bash
#
# Merge multiple single-session bag_*_lerobot folders in recordings/ into one
# multi-episode LeRobotDataset, ready to train against. Output goes to its own
# top-level merged_recordings/ folder, never back into recordings/ -- so
# re-running this later never merges a previous merge into itself.
#
# Usage:
#   ./merge_datasets.sh                                  # merged_recordings/merged_<timestamp>
#   ./merge_datasets.sh --name pick_cube_v1               # merged_recordings/pick_cube_v1
#   ./merge_datasets.sh --pattern "bag_202607*_lerobot"   # only merge a subset
#   ./merge_datasets.sh --name plushie_pickups --from plushie   # only recordings/plushie/
#                                                                # (a session made with set_recording_session.sh)
#
# Works whether you run it from the Windows host (shells out into the
# ros2_ws_gui_record container) or from a shell already inside that container
# (runs directly -- no docker CLI in there). All flags are forwarded as-is to
# scripts/merge_lerobot_datasets.py; see that file for the full option list.

CONTAINER_NAME="ros2_ws_gui_record"

# Prevent Git Bash from mangling /ros2_ws/... paths into Windows paths before
# they reach the (Linux) container.
export MSYS_NO_PATHCONV=1

if [ -f /.dockerenv ]; then
    # Already inside the container.
    python3 /ros2_ws/scripts/merge_lerobot_datasets.py "$@"
else
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Error: container '${CONTAINER_NAME}' is not running."
        exit 1
    fi
    docker exec "$CONTAINER_NAME" python3 /ros2_ws/scripts/merge_lerobot_datasets.py "$@"
fi
