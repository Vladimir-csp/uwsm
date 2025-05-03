#!/bin/sh

# Monitor UWSM service statuses
# Part of UWSM, but does not depend on it.
# https://github.com/Vladimir-csp/uwsm

set -e

SELF="${0##*/}"
REFRESH_RATE=2

# Handle arguments
while [ $# -gt 0 ]; do
    case "$1" in
        -r|--refresh)
            REFRESH_RATE="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $SELF [-r SECONDS] [-h]"
            echo "Monitor UWSM service status"
            echo "  -r, --refresh  Refresh rate in seconds (default: 2)"
            echo "  -h, --help     Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Handle Ctrl+C gracefully
trap 'echo "Exiting..."; exit 0' INT

check_service() {
    systemctl --user --no-pager status "$1" 2>/dev/null || echo "Service $1 not found"
}

while true; do
    clear
    echo "=== UWSM Status Monitor ==="
    echo "Press Ctrl+C to exit"
    echo
    
    echo "=== Compositor Service ==="
    check_service "wayland-wm@*.service"
    echo
    
    echo "=== Environment Service ==="
    check_service "wayland-wm-env@*.service"
    echo
    
    echo "=== Session Target ==="
    check_service "wayland-session@*.target"
    
    sleep "$REFRESH_RATE"
done
