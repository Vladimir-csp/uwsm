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
	# append list with item, retrun new list, separated by separator
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
	# deprive list of item, retrun new list
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
	# substitutes: $1 in $2 what $3 for
	# adopted from https://stackoverflow.com/a/75037170
	RIGHT=$1
	SEARCH=$2
	SUB=$3
	OUT=''

	while [ -n "$RIGHT" ]; do
		# get LEFT from first $SEARCH occurance
		LEFT="${RIGHT%%"${SEARCH}"*}"
		# return if nothing else to replace
		if [ "$LEFT" = "$RIGHT" ]; then
			printf "%s" "${OUT}${RIGHT}"
			return
		fi
		# APPEND substituted LEFT to OUT
		OUT="${OUT}${LEFT}${SUB}"
		# get RIGHT from first $SEARCH occurance
		RIGHT=${RIGHT#*"${SEARCH}"}
	done
	printf "%s" "$OUT"
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

	# extract and unescape unit name from path property
	UNIT=${line##*'"path":"/org/freedesktop/systemd1/unit/'}
	UNIT=${UNIT%%'"'*}
	UNIT=$(simple_sub "$UNIT" '_40' '@')
	UNIT=$(simple_sub "$UNIT" '_2e' '.')
	UNIT=$(simple_sub "$UNIT" '_5f' '_')
	UNIT=$(simple_sub "$UNIT" '_2d' '-')
	# shellcheck disable=SC1003
	UNIT=$(simple_sub "$UNIT" '_5c' '\')

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
			# shellcheck disable=SC2086
			notify-send -a FUMonitor $ID_ARG -u normal -i dialog-info -- "Unit recovered" "$UNIT"
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
				notify-send -a FUMonitor -p -u critical -i dialog-warning -- "Failed unit detected" "$UNIT"
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
