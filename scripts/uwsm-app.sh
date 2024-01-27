#!/bin/sh

# Simple client for wayland-wm-app-daemon.service
# A drop-in replacement for "uwsm app"
# special arguments: ping, stop

set -e

# timeout for all pipe operations
TIMEOUT=10

# assume uwsm is under the same name as us, without "-app"
SELF_NAME=${0##*/}
UWSM_NAME=${SELF_NAME%-app}

PIPE_IN="${XDG_RUNTIME_DIR}/uwsm-app-daemon-in"
PIPE_OUT="${XDG_RUNTIME_DIR}/uwsm-app-daemon-out"

DAEMON_UNIT=wayland-wm-app-daemon.service

N='
'

message(){
	# print message "$1" to stdout
	echo "$1"
	# if notify-send is installed and stdout is not a terminal, also send notification
	if [ ! -t 1 ] && command -v notify-send >/dev/null; then
		notify-send -u normal -i info -a "${SELF_NAME}" "App message" "$1"
	fi
}

error(){
	# print message "$1" to stderr
	echo "$1" >&2
	# if notify-send is installed and stderr is not a terminal, also send notification
	if [ ! -t 2 ] && command -v notify-send >/dev/null; then
		notify-send -u critical -i error -a "${SELF_NAME}" "App failure" "$1"
	fi
	# if code is 141 (128+13, exit due to SIGPIPE), also restart daemon
	if [ "${2:-1}" = "141" ]; then
		systemctl --user restart "$DAEMON_UNIT" || true
	fi
	# exit with code $2
	exit ${2:-1}
}

# fork timeout killer
MAINPID=$$
{
	# send SIGPIPE to main process after timeout
	sleep $TIMEOUT
	if kill -0 $MAINPID 2>/dev/null; then
		kill -13 $MAINPID
	fi
} &
KILLER_PID=$!

# trap SIGPIPE
trap 'error "Timed out waiting for pipes!" 141' PIPE

# restart server if pipes are missing or not pipes
if [ ! -p "$PIPE_IN" ] || [ ! -p "$PIPE_OUT" ]; then
	systemctl --user restart "$DAEMON_UNIT"
	# wait for pipes to become pipes
	while [ ! -p "$PIPE_IN" ] || [ ! -p "$PIPE_OUT" ]; do
		sleep 1
	done
else
	# start server in background
	# this does nothing if it is already started
	systemctl --user start "$DAEMON_UNIT" &
fi

# update message
trap 'error "Timed out trying to write to ${PIPE_IN}!" 141' PIPE

# write args to input pipe
if [ "$#" = "0" ]; then
	echo "No args given!" >&2
	exit 1
elif [ "$#" = "1" ] && [ "$1" = "ping" ]; then
	printf '%s' 'ping' > "$PIPE_IN"
elif [ "$#" = "1" ] && [ "$1" = "stop" ]; then
	printf '%s' 'stop' > "$PIPE_IN"
else
	printf '\0%s' app "$@" > "$PIPE_IN"
fi

# update message
trap 'error "Timed out trying to read from ${PIPE_OUT}!" 141' PIPE

# read from output pipe
CMDLINE=
while IFS='' read line; do
	CMDLINE="${CMDLINE}${CMDLINE:+$N}${line}"
done < "$PIPE_OUT"

# kill timeout killer process and its sleep process
kill $KILLER_PID $(ps --ppid $KILLER_PID -o pid= || true) &

case "$CMDLINE" in
pong)
	message pong
	exit 0
	;;
'' | "$N")
	error "Received empty command!"
	;;
*)
	# run received commands
	eval "$CMDLINE" ;;
esac
