#!/bin/sh

# Bundled with UWSM, but is independent of it.
# https://github.com/Vladimir-csp/uwsm
# https://gitlab.freedesktop.org/Vladimir-csp/uwsm

# NOTE: this is a hack to locally prop up applications that do not properly
# wait for tray themselves, but should.
#
# Put it into `ExecStartPre=-` of the autostart unit or make it a part of a
# wrapper to delay application startup until tray is available or timeout is
# reached (default: 30 seconds, optional argument).
#
# This is not a part of any standard mechanism nor it should be, because there
# is no standard. Other custom mechanisms exist, i.e NixOS's `tray.target`, but
# nothing of sorts should be encouraged.
#
# If you find yourself in need of putting this script to use, consider filing a
# bug report for the culprit app.

set -e

wait_tray() {
	# Run busctl monitor in background, save pid to $MON_PID.
	# It will exit when any of tray dbus services' owner changes from
	# nothing to something.

	# wrap tray service names with dbus match args
	set --
	for arg in $TRAY_NAMES; do
		# shellcheck disable=SC2089
		set -- "$@" --match "type='signal',member='NameOwnerChanged',path='/org/freedesktop/DBus',arg0='${arg}',arg1=''"
	done

	# watch for message
	busctl --user monitor \
		--json=short \
		--no-pager \
		--no-legend \
		"$@" \
		--limit-messages 1 \
		--timeout "${TIMEOUT}s" \
		> /dev/null 2>&1 &
	MON_PID=$!
}

check_tray() {
	# return 0 if any of tray serivces is active
	for service in $TRAY_NAMES; do
		if busctl --user status "$service" --no-pager --no-legend > /dev/null 2>&1; then
			return 0
		fi
	done
	return 1
}

# shellcheck disable=SC2329
trapterm() {
	# cancel trap to make it single-use
	trap - INT TERM HUP EXIT

	# suppress stderr, including "Terminated" message
	exec 2> /dev/null

	kill "$MON_PID" || true
	wait "$MON_PID" || true
}

########

TRAY_NAMES="org.kde.StatusNotifierWatcher org.freedesktop.StatusNotifierWatcher"

TIMEOUT=${1:-30}

case "$TIMEOUT" in
*[!0-9]*)
	printf '%s\n' "Expected number of seconds as timeout, got: $TIMEOUT" >&2
	exit 1
	;;
esac

## start busctl monitor in background
wait_tray

## set cleanup trap
trap trapterm INT TERM HUP EXIT

## check current state and exit on success
if check_tray; then
	printf '%s\n' "Tray is active"
	exit 0
fi

printf '%s\n' "Waiting $TIMEOUT seconds for Tray to activate..."
wait "$MON_PID" || true

# recheck current status
if check_tray; then
	printf '%s\n' "Tray is active"
	exit 0
else
	RC=$?
	printf '%s\n' "Tray failed to activate!" >&2
	exit "$RC"
fi
