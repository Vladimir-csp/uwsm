#!/bin/sh

# shellcheck disable=SC2059
printf_out() {
	printf "$@" 2> /dev/null
	[ -z "$UWSM_SH_NO_STDOUT" ] || return
	printf "$@" >&3 2> /dev/null
}

# shellcheck disable=SC2059
printf_err() {
	printf "$@" >&2 2> /dev/null
	printf "$@" >&4 2> /dev/null
}

start() {
	printf_out '%s\n' "Starting ${UNIT}..."
	kmscon_background
	{
		trap '' TERM HUP INT
		exec systemctl --user start --wait "${UNIT}"
	} &
	STARTPID=$!
	printf_out '%s\n' "Forked systemctl, PID ${STARTPID}."
}

# shellcheck disable=SC2329
stop() {
	trap '' TERM HUP INT
	SIGNAL=$1
	printf_out '%s\n' "Received SIG${SIGNAL}, stopping ${UNIT}..."
	# request foreground right away before systemd takes stdout away.
	kmscon_foreground
	systemctl --user stop "${UNIT}" &
	finish
}

finish() {
	wait "${STARTPID}"
	RC=$?
	# request kmscon foreground on normal exit
	[ -n "$SIGNAL" ] || kmscon_foreground
	case "$RC" in
	0) printf_out '%s\n' "PID ${STARTPID} exited with RC ${RC}" ;;
	*) printf_err '%s\n' "PID ${STARTPID} exited with RC ${RC}" ;;
	esac
	exit "$RC"
}

# kmscon_* functions partially based on kmscon-launch-gui script
kmscon_background() {
	# do nothing if we are not on kmscon
	case "${TERM_SESSION_TYPE:-}" in
	kms | fb) true ;;
	*) return 0 ;;
	esac
	# Store current tty number
	kms_tty=
	active_tty_file=/sys/class/tty/tty0/active
	if [ -f "$active_tty_file" ]; then
		read -r kms_tty < "$active_tty_file"
	fi
	printf_out '%s\n' "Requesting kmscon background."
	# shellcheck disable=SC1003
	case "${TERM_PROGRAM:-}" in
	tmux) printf '%b' '\033Ptmux;\033\033]setBackground\a\033\\' >&3 ;;
	*) printf '\033]setBackground\a' >&3 ;;
	esac
	# pause to allow kmscon to take DRM master
	{ sleep 0.2 || /bin/sleep 0.2 || sleep 1; } 2> /dev/null
}

kmscon_wait_tty() {
	# return if launch tty was not saved
	[ -n "${kms_tty:-}" ] || return 0
	read -r check_kms_tty < "$active_tty_file"
	# return if on same tty
	[ "${check_kms_tty}" != "${kms_tty}" ] || return 0
	wait=5
	# If the current tty has changed, wait 5 seconds until the user switches back.
	# Waiting introduces three bad options:
	#   - drop signal traps, be terminated, leave kmscon inoperable
	#   - wait long/forever and thus hold session open, potentially hindering shutdown
	#   - send setForeground early, get temporarily inoperable kmscon with log spam
	#     until tty switch in/out/in (least bad option).
	# if terminated, trap will skip this and request foreground right away
	# https://github.com/kmscon/kmscon/issues/638
	ts1=$(date +%s)
	ts2=$ts1
	while [ "$((ts2 - ts1))" -lt "$wait" ]; do
		printf_out '%s\n' "Waiting for ${kms_tty} to return to foreground..."
		# use inotifywait if available, fallback to plain sleep
		if command -v inotifywait > /dev/null; then
			inotifywait -qqe modify -t "$wait" "$active_tty_file"
		else
			{ sleep 0.2 || /bin/sleep 0.2 || sleep 1; } 2> /dev/null
		fi
		read -r check_kms_tty < "$active_tty_file"
		if [ "${check_kms_tty}" = "${kms_tty}" ]; then
			printf_out '%s\n' "${kms_tty} returned to foreground."
			return 0
		fi
		ts2=$(date +%s)
	done
	printf_err '%s\n' "Giving up on ${kms_tty}."
}

kmscon_foreground() {
	# do nothing if we are not on kmscon
	case "${TERM_SESSION_TYPE:-}" in
	kms | fb) true ;;
	*) return 0 ;;
	esac
	# wait for foreground tty on normal exit
	[ -n "$SIGNAL" ] || kmscon_wait_tty
	printf_out '%s\n' "Requesting kmscon foreground."
	# shellcheck disable=SC1003
	case "${TERM_PROGRAM:-}" in
	tmux) printf '%b' '\033Ptmux;\033\033]setForeground\a\033\\' >&3 ;;
	*) printf '\033]setForeground\a' >&3 ;;
	esac
	# pause after foreground request on normal exit
	[ -n "$SIGNAL" ] || { sleep 0.2 || /bin/sleep 0.2 || sleep 1; } 2> /dev/null
}

if [ "$#" != "1" ]; then
	printf_err '%s\n' "Designed to be run by uwsm! Exiting."
	exit 1
fi

UNIT=$1
SIGNAL=

trap "stop TERM" TERM
trap "stop HUP" HUP
trap "stop INT" INT

start
finish
