#!/bin/sh

# Link any user unit to a graphical slice
# arguments:
#   1. unit
#   2. a|app-graphical.slice | b|background-graphical.slice | s|session-graphical.slice
#      or reset
#      (optional)


UNIT=${1?First argument should be a unit to edit}
SLICE=${2:-app-graphical.slice}

case "$SLICE" in
a) SLICE=app-graphical.slice ;;
b) SLICE=background-graphical.slice ;;
s) SLICE=session-graphical.slice ;;
app-graphical.slice | background-graphical.slice | session-graphical.slice | reset) true ;;
*)
	echo "expected a|b|s or full slice name or 'reset', got: ${SLICE}!" >&2
	exit 1
	;;
esac

case "$(systemctl --user show --property LoadState --value "$UNIT")__${UNIT##*.}" in
loaded__service) true ;;
loaded__*)
	echo "Unit '$UNIT' is not a service!" >&2
	exit 1
	;;
*)
	echo "Unit '$UNIT' not found!" >&2
	exit 1
	;;
esac


case "$SLICE" in
reset)
	echo "Removing 'graphical-slice.conf' drop-in from '$UNIT'"
	for DROPIN in $(systemctl --user show --property DropInPaths --value "$UNIT"); do
		case "$DROPIN" in
		*/graphical-slice.conf)
			rm -v "$DROPIN" && systemctl --user daemon-reload
			exit
			;;
		esac
	done
	echo "Unit '$UNIT' has no 'graphical-slice.conf' drop-in"
	;;
*)
	echo "Assigning '$UNIT' to '$SLICE'"
	systemctl --user edit --stdin --drop-in graphical-slice.conf "$UNIT" <<- EOF
		[Unit]
		After=graphical-session.target
		[Service]
		Slice=$SLICE
	EOF
	;;
esac
