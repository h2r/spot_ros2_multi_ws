#!/bin/bash
#
# Sets which subfolder under recordings/ new recordings get saved into, without
# restarting listener_node.py -- it re-reads .recording_session on every /bag_trigger
# start call, so this takes effect on the next recording, not just future ones after
# a restart.
#
# No docker/ROS involved here on purpose: this only touches a plain text file and a
# directory under recordings/, which is bind-mounted between the host and the
# container (see docker-compose.yml), so writing it from either side has the same
# effect. Works run from the Windows host or from inside the container either way.
#
# Usage:
#   ./set_recording_session.sh --name plushie   # new recordings go to recordings/plushie/
#   ./set_recording_session.sh --clear          # no session -- recordings go to recordings/unsorted/
#                                                # (dual-robot recordings similarly go to
#                                                #  recordings/<name>_dual/ or recordings/unsorted_dual/)
#   ./set_recording_session.sh                  # show the current session name
#
# recordings/ never gets anything written directly into its root -- an unset session
# still lands in an organized subfolder (recordings/unsorted/), not loose at the top
# level. listener_node.py / dual_listener_node.py also log a warning on every
# recording start while no session is set.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_FILE="$SCRIPT_DIR/.recording_session"

NAME=""
CLEAR=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) NAME="$2"; shift 2 ;;
        --clear) CLEAR=true; shift ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--name <session>] [--clear]"
            exit 1
            ;;
    esac
done

if [[ -n "$NAME" && "$CLEAR" == true ]]; then
    echo "Error: --name and --clear are mutually exclusive"
    exit 1
fi

if [[ -n "$NAME" && ! "$NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "Error: --name may only contain letters, numbers, underscores, and hyphens (got '$NAME')"
    exit 1
fi

if [[ "$CLEAR" == true ]]; then
    rm -f "$SESSION_FILE"
    echo "Recording session cleared -- new recordings will go to recordings/unsorted/ (or unsorted_dual/ in dual mode)."
elif [[ -n "$NAME" ]]; then
    mkdir -p "$SCRIPT_DIR/recordings/$NAME"
    printf '%s' "$NAME" > "$SESSION_FILE"
    echo "Recording session set to '$NAME' -- new recordings will go to recordings/$NAME/ (or recordings/${NAME}_dual/ in dual mode)"
else
    if [[ -f "$SESSION_FILE" ]]; then
        echo "Current recording session: $(cat "$SESSION_FILE")"
    else
        echo "No recording session set -- new recordings go to recordings/unsorted/ (or unsorted_dual/ in dual mode)."
    fi
fi
