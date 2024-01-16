#!/bin/sh

# Part of UWSM, but does not depend on it.
# https://github.com/Vladimir-csp/uwsm
# https://gitlab.freedesktop.org/Vladimir-csp/uwsm

SELF="${0##*/}"

showhelp() {
	while IFS='' read -r line; do
		printf '%s\n' "$line"
	done <<- EOH
		Usage: ${SELF} [-ah] [menu] [menu args ...]

		  menu       select menu tool (if without arguments)
		             or provide full menu command line
		             (must end with a prompt option: -p or analogous)
		  -h|--help show this help
		  -a|--all  do not filter units

		Control user services and scopes with dmenu-like menu.
		Menu tool and options are selected from predefined profiles for:

		  fuzzel
		  wofi
		  rofi
		  tofi
		  bemenu
		  dmenu

		If just a single tool name is given, it is interpreted as a preferred selection.
		If more arguments are given, they are used as full menu command line, so
		are not limited to the predefined list.
		The last argument is expected to be a prompt option (-p or analogous)
	EOH
}

ALL=''
for arg in "$@"; do
	case "$arg" in
	-a | --all)
		ALL=1
		shift
		;;
	-h | --help)
		showhelp
		exit 0
		;;
	*) break ;;
	esac
done

if [ "$#" -le "1" ]; then
	read -r dmenu_candidate <<- EOF
		$(which "$@" fuzzel wofi rofi tofi bemenu dmenu)
	EOF

	case "${dmenu_candidate##*/}" in
	fuzzel)
		set -- fuzzel --dmenu -R --log-no-syslog --log-level=warning -p
		;;
	wofi)
		set -- wofi --dmenu -p
		;;
	rofi)
		set -- rofi -dmenu -p
		;;
	tofi)
		set -- tofi --prompt-text
		;;
	bemenu)
		set -- bemenu -p
		;;
	wmenu)
		set -- wmenu -p
		;;
	dmenu)
		set -- dmenu -p
		;;
	'' | *)
		echo "Could not find a menu tool among:" "$@" fuzzel wofi rofi tofi bemenu dmenu >&2
		exit 1
		;;
	esac
else
	if ! command -v "$1" > /dev/null; then
		echo "Menu tool '$1' not found" >&2
		exit 1
	fi
fi

cancel_exit() {
	echo Cancelled
	exit 0
}

report() {
	echo "Selected $1"
}

ACTIVE_UNITS=''
INACTIVE_UNITS=''
reset_current() {
	C_UNIT=''
	C_ACTIVE_STATE=''
	C_FREEZER_STATE=''
	C_STATE_MSG=''
	C_NAME=''
	C_SKIP=''
	C_UNIT_FILE_STATE=''
}

N='
'
reset_current
# get services and scopes, active or otherwise
while IFS="=" read -r prop value; do
	case "$prop" in
	Id)
		case "$value" in
		graphical-*.target | wayland-wm-env@*.service | wayland-wm@*.service | init.scope)
			if [ -n "$ALL" ]; then
				C_UNIT="${value}"
			else
				C_SKIP=1
			fi
			;;
		*) C_UNIT="${value}" ;;
		esac
		;;
	Names)
		case "x $value x" in
		*' dbus.service '*)
			if [ -z "$ALL" ]; then
				C_SKIP=1
			fi
			;;
		esac
		;;
	UnitFileState) C_UNIT_FILE_STATE="$value" ;;
	Description) C_NAME="${value}" ;;
	ActiveState) C_ACTIVE_STATE="${value}" ;;
	'' | END)
		# skip if not grabbing
		if [ -z "$C_SKIP" ]; then
			case "x $C_FREEZER_STATE $C_UNIT_FILE_STATE x" in
			*' frozen '*) C_STATE_MSG=frozen ;;
			*' masked '*) C_STATE_MSG=masked ;;
			*) C_STATE_MSG="$C_ACTIVE_STATE" ;;
			esac
			case "$C_ACTIVE_STATE" in
			activ*) ACTIVE_UNITS="${ACTIVE_UNITS}${ACTIVE_UNITS:+$N}${C_NAME} (${C_STATE_MSG}) ${C_UNIT}" ;;
			*) INACTIVE_UNITS="${INACTIVE_UNITS}${INACTIVE_UNITS:+$N}${C_NAME} (${C_STATE_MSG}) ${C_UNIT}" ;;
			esac
		fi
		reset_current
		;;
	esac
done <<- EOF
	$(systemctl --user show --type=service,scope,socket --all --no-pager --quiet --property=Id,ActiveState,Description,Names,UnitFileState)
	END
EOF

# select unit
UNIT=$(
	echo "${ACTIVE_UNITS}${ACTIVE_UNITS:+$N}${INACTIVE_UNITS}" | "$@" "Select Service: "
) || cancel_exit
UNIT=${UNIT##* }
STATE=${UNIT##* (}
STATE=${STATE%%) *}

report "$UNIT"

# get unit data
while IFS="=" read -r prop value; do
	case "$prop" in
	Description) DESCRIPTION="$value" ;;
	CanStart) CAN_START="$value" ;;
	CanStop) CAN_STOP="$value" ;;
	CanReload) CAN_RELOAD="$value" ;;
	CanFreeze) CAN_FREEZE="$value" ;;
	FreezerState) FREEZER_STATE="$value" ;;
	ActiveState) ACTIVE_STATE="$value" ;;
	UnitFileState) UNIT_FILE_STATE="$value" ;;
	WantedBy) WANTED_BY="$value" ;;
	RequiredBy) REQUIRED_BY="$value" ;;
	UpheldBy) UPHELD_BY="$value" ;;
	esac
done <<- EOF
	$(systemctl --user show --no-pager --quiet --property=Description,CanFreeze,CanStart,CanReload,CanStop,FreezerState,ActiveState,UnitFileState,WantedBy,RequiredBy,UpheldBy "$UNIT")
EOF

case "$UNIT" in
*.service) UNIT_TYPE=service ;;
*.scope) UNIT_TYPE=scope ;;
*.socket) UNIT_TYPE=socket ;;
esac

# compose actions
ACTIONS=''
for ACTION in start stop reload restart reset-failed enable disable freeze thaw mask unmask; do
	: "${ACTION}+++type:${UNIT_TYPE:-unknown}+as:${ACTIVE_STATE:-unknown}+fs:${FREEZER_STATE:-unknown}+cstart:${CAN_START:-unknown}+creload:${CAN_RELOAD:-unknown}+cstop:${CAN_STOP:-unknown}+cfreeze:${CAN_FREEZE:-unknown}+ufs:${UNIT_FILE_STATE}+install:${WANTED_BY}${REQUIRED_BY}${UPHELD_BY}+++"
	case "${ACTION}+++type:${UNIT_TYPE:-unknown}+as:${ACTIVE_STATE:-unknown}+fs:${FREEZER_STATE:-unknown}+cstart:${CAN_START:-unknown}+creload:${CAN_RELOAD:-unknown}+cstop:${CAN_STOP:-unknown}+cfreeze:${CAN_FREEZE:-unknown}+ufs:${UNIT_FILE_STATE}+install:${WANTED_BY}${REQUIRED_BY}${UPHELD_BY}+++" in
	# skip various combinations
	start+*+type:scope+* | restart+*+type:scope+* | reload+*+type:scope+* | enable+*+type:scope+* | disable+*+type:scope+*) continue ;;
	start+*+as:activ* | start+*+as:reloading+* | start+*+cstart:no+* | start+*+ufs:masked+*) continue ;;
	stop+*+as:failed+* | stop+*+as:inactive+* | stop+*+as:deactivating+* | stop+*+cstop:no+* | stop+*+ufs:masked+*) continue ;;
	# strictly speaking, restarting a stopped unit is valid, but exclude it anyway
	restart+*+as:failed+* | restart+*+as:inactive+* | restart+*+as:deactivating+* | restart+*+ufs:masked+*) continue ;;
	reload+*+as:failed+* | reload+*+as:inactive+* | reload+*+as:deactivating+* | reload+*+creload:no+*) continue ;;
	reset-failed+*+as:[!f][!a][!i]*) continue ;;
	freeze+*+cfreeze:no+* | freeze+*+fs:frozen+* | freeze+*+as:failed+* | freeze+*+as:inactive+* | freeze+*+as:deactivating+* | freeze+*+ufs:masked+*) continue ;;
	thaw+*+fs:[!f][!r][!o]*) continue ;;
	mask+*+ufs:masked+* | mask+*+as:activ* | mask+*+as:reloading+*) continue ;;
	unmask+*+ufs:[!m][!a][!s][!k][!e][!d]*) continue ;;
	enable+*+install:+* | enable+*+ufs:generated+* | enable+*+ufs:enabled+* | enable+*+ufs:runtime-enabled+* | enable+*+ufs:masked+*) continue ;;
	disable+*+ufs:generated+* | disable+*+ufs:transient+*) continue ;;
	# special handling of some surviving actions
	disable+*+ufs:runtime-enabled+*+as:activ* | disable+*+ufs:runtime-enabled+*+as:reloading*) ACTIONS="${ACTIONS}${ACTIONS:+$N}disable --runtime${N}disable --runtime --now" ;;
	disable+*+ufs:runtime-enabled+*) ACTIONS="${ACTIONS}${ACTIONS:+$N}disable --runtime" ;;
	disable+*+as:activ* | disable+*+as:reloading*) ACTIONS="${ACTIONS}${ACTIONS:+$N}disable${N}disable --now" ;;
	enable+*+as:activ* | enable+*+as:reloading*) ACTIONS="${ACTIONS}${ACTIONS:+$N}enable${N}enable --runtime" ;;
	enable+*) ACTIONS="${ACTIONS}${ACTIONS:+$N}enable${N}enable --now${N}enable --runtime${N}enable --runtime --now" ;;
	# another skip, has to be down here
	disable+*+ufs:[!e][!n][!a][!b][!l][!e][!d]*) continue ;;
	# generic add surviving action
	*) ACTIONS="${ACTIONS}${ACTIONS:+$N}${ACTION}" ;;
	esac
done

# select action
ACTION=$(
	printf '%s' "$ACTIONS" | "$@" "${DESCRIPTION#*=} (${STATE}): "
) || cancel_exit

report "$ACTION"

# apply selected action
if command -v notify-send > /dev/null; then
	# capture only stderr
	ERR=$(
		# shellcheck disable=SC2086
		systemctl --user $ACTION "$UNIT" 2>&1 > /dev/null
	)
	RC="$?"
	if [ "$RC" = "0" ] && [ -z "$ERR" ]; then
		AST="${ACTION} successful"
		URG=normal
	elif [ "$RC" = "0" ]; then
		AST="${ACTION} probably successful"
		URG=normal
	else
		AST="${ACTION} failed (RC $RC)"
		URG=critical
	fi
	notify-send -a "$SELF" -u "$URG" "$AST" "${UNIT}${ERR:+:$N}${ERR}"
	exit "$RC"
else
	# shellcheck disable=SC2086
	exec systemctl --user $ACTION "$UNIT"
fi
