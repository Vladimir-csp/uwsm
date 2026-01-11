#!/bin/sh

set -e

# Lock session if its TTY becomes unfocused.
# Operate on single argument input: session ID.

TTY_FILE=/sys/class/tty/tty0/active

if [ ! -f "$TTY_FILE" ]; then
	printf '%s\n' "${TTY_FILE} does not exist, can not read current TTY!" >&2
	exit 1
fi
if [ ! -r "$TTY_FILE" ]; then
	printf '%s\n' "No permission to read current TTY from ${TTY_FILE}!" >&2
	exit 1
fi

S_ID=${1:-}
if [ -z "$S_ID" ]; then
	printf '%s\n' "No session ID provided, assuming auto."
	S_ID=auto
fi

# get session TTY and Id (in case of auto)
while IFS='=' read -r key value; do
	case "$key" in
	Id)
		case "$S_ID" in
		auto) printf '%s\n' "Auto session ID: $value" ;;
		esac
		S_ID=$value
		;;
	TTY) WATCH_TTY=$value ;;
	esac
done <<-EOF
	$(
		# unset session vars to make auto fallback to find graphical session
		unset XDG_SEAT XDG_SEAT_PATH XDG_SESSION_ID XDG_SESSION_PATH XDG_VTNR
		loginctl show-session "$S_ID" --property TTY --property Id
	)
EOF

case "$S_ID" in
*[!0-9]* | '')
	printf '%s\n' "Could not (re)determine session id ${S_ID}!" >&2
	exit 1
	;;
esac
if [ -z "$WATCH_TTY" ]; then
	printf '%s\n' "Could not find TTY of session ${S_ID}!" >&2
	exit 1
fi

TTY=$WATCH_TTY
get_tty() {
	PREV_TTY=$TTY
	read -r TTY <"$TTY_FILE"
	if [ -z "$TTY" ]; then
		printf '%s\n' "Could not get current TTY" >&2
		exit 1
	fi
}

try_lock() {
	case "${PREV_TTY}:${TTY}" in
	# arriving to watched tty: do nothing
	*":${WATCH_TTY}") true ;;
	# leaving watched tty: lock
	"${WATCH_TTY}:"*)
		if ! loginctl show-session "$S_ID" --property Id >/dev/null; then
			printf '%s\n' "Session disappeared, exiting."
			exit 0
		fi
		printf '%s\n' "Locking session $S_ID as ${WATCH_TTY} has lost focus."
		loginctl lock-session "$S_ID"
		;;
	# also do nothing in any other case
	esac
}

printf '%s\n' "Will lock session $S_ID if ${WATCH_TTY} becomes unfocused."

get_tty
try_lock

while inotifywait -qqe modify "$TTY_FILE"; do
	get_tty
	try_lock
done
