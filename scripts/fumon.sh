#!/bin/sh

# Sends notifications on failed units events.
#
# Part of UWSM, but does not depend on it.
# https://github.com/Vladimir-csp/uwsm
# https://gitlab.freedesktop.org/Vladimir-csp/uwsm

set -e

N='
'

list_contains() {
	# check if list contains item separated by separator
	CLIST=$1
	CITEM=$2
	CSEP=${3:- }
	case "${CSEP}${CLIST}${CSEP}" in
	*"${CSEP}${CITEM}${CSEP}"*) return 0 ;;
	*) return 1 ;;
	esac
}

list_add() {
	# append list with item, return new list, separated by separator
	ALIST=$1
	AITEM=$2
	ASEP=${3:- }
	if list_contains "$ALIST" "$AITEM" "$ASEP"; then
		printf '%s' "$ALIST"
	else
		printf '%s' "${ALIST}${ALIST:+${ASEP}}${AITEM}"
	fi
}

list_del() {
	# deprive list of item, return new list
	DLIST=$1
	DITEM=$2
	DSEP=${3:- }
	if list_contains "$DLIST" "$DITEM" "$DSEP"; then
		OIFS=$IFS
		IFS=$DSEP
		NLIST=''
		for citem in $DLIST; do
			case "$citem" in
			"$DITEM") true ;;
			*) NLIST=${NLIST}${NLIST:+$DSEP}${citem} ;;
			esac
		done
		IFS=$OIFS
		printf '%s' "$NLIST"
	else
		printf '%s' "$DLIST"
	fi
}

get_id() {
	# gets id from list of unit;id items
	ILIST=$1
	IUNIT=$2
	while IFS=';' read -r unit id; do
		case "$unit" in
		"$IUNIT")
			echo "$id"
			return 0
			;;
		esac
	done <<- EOF
		$ILIST
	EOF
	return 1
}

simple_sub() {
	# substitutes in $1: $2 with $3 and so on in pairs
	# adopted initial version from https://stackoverflow.com/a/75037170
	ss_str=$1
	shift

	ss_out=
	while [ "$#" -ge "1" ]; do
		ss_right=$ss_str
		ss_search=$1
		ss_sub=${2-}
		ss_out=
		case "$#" in
		1) shift ;;
		*) shift 2 ;;
		esac

		while [ -n "$ss_right" ]; do
			# get ss_left from first $ss_search occurrence
			ss_left="${ss_right%%"${ss_search}"*}"
			# return if nothing else to replace
			if [ "$ss_left" = "$ss_right" ]; then
				ss_out=${ss_out}${ss_right}
				ss_right=
				continue
			fi
			# APPEND substituted ss_left to ss_out
			ss_out="${ss_out}${ss_left}${ss_sub}"
			# get ss_right from first $ss_search occurrence
			ss_right=${ss_right#*"${ss_search}"}
		done
		ss_str=$ss_out
	done

	printf "%s" "$ss_str"
}

check_failed_units() {
	# checks for failed units, notifies if there are
	COUNTER=0
	FAILED_UNITS=''
	FAILED_UNITS_RAW=$(
		systemctl --user show --state=failed --property=Id --value '*'
	)

	[ -n "$FAILED_UNITS_RAW" ] || return 0

	# parse and count failed units
	while read -r line; do
		[ -n "$line" ] || continue
		FAILED_UNITS="${FAILED_UNITS}${FAILED_UNITS:+ }${line}"
		COUNTER=$((COUNTER + 1))
	done <<- EOF
		$FAILED_UNITS_RAW
	EOF

	if [ "$COUNTER" -gt "1" ]; then
		HEADER="${COUNTER} Failed units detected"
	elif [ "$COUNTER" = "1" ]; then
		HEADER="Failed unit detected"
	fi

	notify-send -a FUMonitor -u critical -i dialog-warning -- "${HEADER}" "${FAILED_UNITS}"
}
busctl_trigger() {
	# outputs json oneliners on properties changes
	busctl --user monitor \
		--json short \
		--match "type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'"
}

if ! command -v notify-send > /dev/null; then
	echo "Command not found: notify-send" >&2
	exit 1
fi

# check for current state
check_failed_units

# main loop for catching unit ActiveState changes to failed
busctl_trigger | while read -r line; do
	# skip if event is not about systemd unit
	case "$line" in
	*'"path":"/org/freedesktop/systemd1/unit/'*) true ;;
	*) continue ;;
	esac
	# skip if event is not about ActiveState
	case "$line" in
	*'"ActiveState":{'*) true ;;
	*) continue ;;
	esac

	# extract and unescape unit ID from path property
	UNIT=${line##*'"path":"/org/freedesktop/systemd1/unit/'}
	UNIT=${UNIT%%'"'*}
	# shellcheck disable=SC1003
	UNIT=$(simple_sub "$UNIT" '_40' '@' '_2e' '.' '_5f' '_' '_2d' '-' '_5c' '\')

	if list_contains "${FAILED_UNITS}" "$UNIT"; then
		case "$line" in
		# still in failed state
		*'{"ActiveState":{"type":"s","data":"failed"}'* | *'{"ActiveState":{"data":"failed","type":"s"}'*)
			continue
			;;
		# process
		*)
			# remove from failed list
			FAILED_UNITS=$(list_del "$FAILED_UNITS" "$UNIT")
			# get notification ID
			if NID=$(get_id "$NOTIFICATION_IDS" "$UNIT"); then
				ID_ARG=--replace-id=$NID
				# remove notification ID entry
				NOTIFICATION_IDS=$(list_del "$NOTIFICATION_IDS" "${UNIT};${NID}" "$N")
			else
				ID_ARG=''
			fi
			# notify
			# shellcheck disable=SC2086,SC1003
			notify-send -a FUMonitor $ID_ARG -u normal -i dialog-info -- "Unit recovered" "$(simple_sub "$UNIT" '\' '\\')"
			;;
		esac
	else
		case "$line" in
		# became failed, process
		*'{"ActiveState":{"type":"s","data":"failed"}'* | *'{"ActiveState":{"data":"failed","type":"s"}'*)
			# add to failed list
			FAILED_UNITS=$(list_add "$FAILED_UNITS" "$UNIT")
			# notify
			NID=$(
				notify-send -a FUMonitor -p -u critical -i dialog-warning -- "Failed unit detected" "$(simple_sub "$UNIT" '\' '\\')"
			)
			# save notification ID in newline-separated list
			NOTIFICATION_IDS=$(list_add "$NOTIFICATION_IDS" "${UNIT};${NID}" "$N")
			;;
		# ignore
		*)
			continue
			;;
		esac
	fi
done
