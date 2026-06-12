#!/usr/bin/env bash
# Record a teleoperation session: every operator's raw input, the aggregated
# UI state, the fused per-robot commands, and TF. One timestamped bag per
# invocation, under ./session_logs (or the directory given as $1).
set -euo pipefail

out_dir="${1:-session_logs}/session_$(date +%Y%m%d_%H%M%S)"
echo "recording to ${out_dir} (ctrl-c to stop)"

exec ros2 bag record -o "${out_dir}" \
  /operators/input \
  /ui_state \
  /spot/cmd_vel \
  /spot2/cmd_vel \
  /multi_spot/cmd_vel \
  /tf \
  /tf_static
