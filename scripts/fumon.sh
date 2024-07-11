#!/bin/sh

# Sends notifications on failed units events.
#
# Part of UWSM, but does not depend on it.
# https://github.com/Vladimir-csp/uwsm
# https://gitlab.freedesktop.org/Vladimir-csp/uwsm

set -e

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
	# skip if event is not about failed state
	case "$line" in
	*'{"ActiveState":{"type":"s","data":"failed"}'* | *'{"ActiveState":{"data":"failed","type":"s"}'*) true ;;
	*) continue ;;
	esac

	# extract and unescape unit name from path property
	UNIT=${line##*'"path":"/org/freedesktop/systemd1/unit/'}
	UNIT=${UNIT%%'"'*}
	UNIT=$(simple_sub "$UNIT" '_40' '@')
	UNIT=$(simple_sub "$UNIT" '_2e' '.')
	UNIT=$(simple_sub "$UNIT" '_5f' '_')
	UNIT=$(simple_sub "$UNIT" '_2d' '-')
	UNIT=$(simple_sub "$UNIT" '_5c' '\')

	# notify
	notify-send -a FUMonitor -u critical -i dialog-warning -- "Failed unit detected" "$UNIT"
done
