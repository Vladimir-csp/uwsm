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
	case "${TERM_SESSION_TYPE:-}" in
	kms)
		printf_out '%s\n' "Requesting kmscon background."
		# shellcheck disable=SC1003
		case "${TERM_PROGRAM:-}" in
		tmux) printf '%b' '\033Ptmux;\033\033]setBackground\a\033\\' >&3 ;;
		*) printf '\033]setBackground\a' >&3 ;;
		esac
		;;
	esac
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
	case "${TERM_SESSION_TYPE:-}" in
	kms)
		printf_out '%s\n' "Requesting kmscon foreground."
		# shellcheck disable=SC1003
		case "${TERM_PROGRAM:-}" in
		tmux) printf '%b' '\033Ptmux;\033\033]setForeground\a\033\\' >&3 ;;
		*) printf '\033]setForeground\a' >&3 ;;
		esac
		;;
	esac
	case "$RC" in
	0) printf_out '%s\n' "PID ${STARTPID} exited with RC ${RC}" ;;
	*) printf_err '%s\n' "PID ${STARTPID} exited with RC ${RC}" ;;
	esac
	exit "$RC"
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
