#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 {MissionControl|Observatory|TelemetryConsole|all}"
    exit 1
}

[[ $# -eq 1 ]] || usage

launch_one() {
    local app="$1"
    "$app" -g dcs &
    "$app" -g daq &
}

case "$1" in
    MissionControl|missioncontrol)
        launch_one MissionControl
        ;;
    Observatory|observatory)
        launch_one Observatory
        ;;
    TelemetryConsole|telemetryconsole|telemetry)
        launch_one TelemetryConsole
        ;;
    all)
        launch_one MissionControl
        launch_one Observatory
        launch_one TelemetryConsole
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        echo "Unknown GUI type: $1"
        usage
        ;;
esac

wait
