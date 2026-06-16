#!/bin/bash
# Upload the GraphNav map and set fiducial localization for both robots.
# This is exactly what spot_graphnav_loc.py does at startup, pulled out as a
# standalone helper so localization can be (re-)run without also starting the
# sync-drive node that would bypass the aggregator.
#
# The robots must PHYSICALLY SEE a registered fiducial when this runs, or the
# set_localization call fails — re-run it once a marker is in view.
#
# Usage: ./ghost-localize.sh [map_path]   (default: /root/spot_configs/map/demo)

set -e

MAP_PATH="${1:-/root/spot_configs/map/demo}"
ROBOTS=(spot spot2)

if [ ! -d "$MAP_PATH" ]; then
    echo "WARNING: map path '$MAP_PATH' not found in this container — upload will fail."
    echo "         Check your spot_configs mount, then pass the right path."
fi

for robot in "${ROBOTS[@]}"; do
    echo "=== /$robot: uploading graph ==="
    ros2 service call "/$robot/graph_nav_upload_graph" \
        spot_msgs/srv/GraphNavUploadGraph "{upload_filepath: '$MAP_PATH'}"

    echo "=== /$robot: setting fiducial localization ==="
    ros2 service call "/$robot/graph_nav_set_localization" \
        spot_msgs/srv/GraphNavSetLocalization "{method: 'fiducial', waypoint_id: ''}"
done

echo
echo "Done. If a robot reported failure, make sure it can SEE a fiducial and re-run: localize"
