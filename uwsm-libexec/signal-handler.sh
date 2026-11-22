#!/bin/sh

# shellcheck disable=SC2059
printf_out() {
	printf "$@"
	[ -z "$UWSM_SH_NO_STDOUT" ] || return
	printf "$@" >&3
}

# shellcheck disable=SC2059
printf_err() {
	printf "$@" >&2
	printf "$@" >&4
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
	printf_out '%s\n' "Received SIG${1}, stopping ${UNIT}..."
	systemctl --user stop "${UNIT}" &
	finish
}

finish() {
	wait "${STARTPID}"
	RC=$?
	kmscon_foreground
	case "$RC" in
	0) printf_out '%s\n' "PID ${STARTPID} exited with RC ${RC}" ;;
	*) printf_err '%s\n' "PID ${STARTPID} exited with RC ${RC}" ;;
	esac
	exit "$RC"
}

kmscon_background() {
	# do nothing if we are not on kmscon
	case "${TERM_SESSION_TYPE:-}" in
	kms | fb) true ;;
	*) return 0 ;;
	esac
	# the rest is mostly from kmscon-launch-gui
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
	{ sleep 0.2 || /bin/sleep 0.2 || sleep 1; } 2> /dev/null
}

kmscon_foreground() {
	# do nothing if we are not on kmscon
	case "${TERM_SESSION_TYPE:-}" in
	kms | fb) true ;;
	*) return 0 ;;
	esac
	# wait for session type to become tty again, 5 second limit
	ts=$(date +%s)
	while [ "$(loginctl show-session '' --property=Type --value)" != "tty" ]; do
		ts2=$(date +%s)
		if [ "$((ts2 - ts))" -ge "5" ]; then
			printf_err '%s\n' 'Session did not return to "tty" type'
			break
		fi
		{ sleep 0.2 || /bin/sleep 0.2 || sleep 1; } 2> /dev/null
	done
	# the rest is mostly from kmscon-launch-gui
	# If the current tty has changed, wait until the user switches back.
	if [ -n "${kms_tty:-}" ]; then
		read -r check_kms_tty < "$active_tty_file"
		while [ "${check_kms_tty}" != "${kms_tty}" ]; do
			printf_out '%s\n' "Waiting for ${kms_tty} to return to foreground..."
			# use inotifywait if available, fallback to plain sleep
			if command -v inotifywait > /dev/null; then
				inotifywait -qqe modify "$active_tty_file"
			else
				sleep 1
			fi
			read -r check_kms_tty < "$active_tty_file"
		done
	fi
	printf_out '%s\n' "Requesting kmscon foreground."
	# shellcheck disable=SC1003
	case "${TERM_PROGRAM:-}" in
	tmux) printf '%b' '\033Ptmux;\033\033]setForeground\a\033\\' >&3 ;;
	*) printf '\033]setForeground\a' >&3 ;;
	esac
	{ sleep 0.2 || /bin/sleep 0.2 || sleep 1; } 2> /dev/null
}

if [ "$#" != "1" ]; then
	printf_err '%s\n' "Designed to be run by uwsm! Exiting."
	exit 1
fi

UNIT=$1

trap "stop TERM" TERM
trap "stop HUP" HUP
trap "stop INT" INT

start
finish
