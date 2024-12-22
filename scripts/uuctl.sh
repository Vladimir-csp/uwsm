#!/bin/sh

# Control user units via dmenu-like menus
#
# Part of UWSM, but does not depend on it.
# https://github.com/Vladimir-csp/uwsm
# https://gitlab.freedesktop.org/Vladimir-csp/uwsm

set -e

SELF="${0##*/}"
N='
'

SD_USER_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

showhelp() {
	while IFS='' read -r line; do
		printf '%s\n' "$line"
	done <<- EOH
		Usage: ${SELF} [-ah] [menu] [menu args ...]

		  menu       select menu tool (if without arguments)
		             or provide full menu command line
		             (must end with a prompt option: -p or analogous)
		  -h|--help  show this help
		  -a|--all   do not filter units

		Control user services and scopes with dmenu-like menu.
		Menu tool and options are selected from predefined profiles for:

      walker
		  fuzzel
		  wofi
		  rofi
		  tofi
		  bemenu
		  wmenu
		  dmenu

		If just a single tool name is given, it is interpreted as a preferred selection.
		If more arguments are given, they are used as full menu command line, so
		are not limited to the predefined list.
		The last argument is expected to be a prompt option (-p or analogous)
	EOH
}

dirempty() {
	# check if dir $1 is empty based purely on glob expansion
	case "$(printf '%s;' "$1"/* "$1"/.*)" in
	"${1}/*;${1}/.;${1}/..;" | "${1}/*;${1}/.;" | "${1}/*;${1}/.*;") return 0 ;;
	*) return 1 ;;
	esac
}

silence() {
	mkdir -vp "${SD_USER_DIR}/${UNIT_TEMPLATE}.d"
	set -- '[Service]'
	case "$SILENCE_ACTION" in
	# silence out
	stdout)
		set -- "$@" 'StandardOutput=null'
		# unsilence stderr if it is inheriting
		dso=''
		dse=''
		while IFS='=' read -r key value; do
			case "$key" in
			DefaultStandardOutput) dso=$value ;;
			DefaultStandardError) dse=$value ;;
			esac
		done <<- EOF
			$(systemctl --user show --property DefaultStandardOutput --property DefaultStandardError)
		EOF
		case "$dse" in
		inherit) set -- "$@" "StandardError=$dso" ;;
		esac
		;;
	# silence err
	stderr) set -- "$@" 'StandardError=null' ;;
	# silence both
	both) set -- "$@" 'StandardOutput=null' 'StandardError=null' ;;
	esac
	printf '%s\n' "$@" > "${SD_USER_DIR}/${UNIT_TEMPLATE}.d/slient.conf"
	systemctl --user daemon-reload
	# restart unit if requested
	case "$RESTART" in
	yes) systemctl --user restart "$UNIT" ;;
	esac
}

unsilence() {
	rm -v "${SD_USER_DIR}/${UNIT_TEMPLATE}.d/slient.conf"
	if dirempty "${SD_USER_DIR}/${UNIT_TEMPLATE}.d"; then
		rmdir -v "${SD_USER_DIR}/${UNIT_TEMPLATE}.d" || true
	fi
	systemctl --user daemon-reload
	# restart unit if requested
	case "$RESTART" in
	yes) systemctl --user restart "$UNIT" ;;
	esac
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
	dmenu_candidates="$1 walker fuzzel wofi rofi tofi bemenu wmenu dmenu"
	for dmenu_candidate in $dmenu_candidates; do
		! command -v "$dmenu_candidate" > /dev/null || break
	done

	case "$dmenu_candidate" in
	walker)
		set -- walker -d -p
		;;
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
		# shellcheck disable=SC2086
		echo "Could not find a menu tool among: " $dmenu_candidates
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

reset_current() {
	C_UNIT=''
	C_ACTIVE_STATE=''
	C_FREEZER_STATE=''
	C_STATE_MSG=''
	C_NAME=''
	C_SKIP=''
	C_UNIT_FILE_STATE=''
}

get_units() {
	# fills ACTIVE_UNITS, INACTIVE_UNITS with units
	NEED_DAEMON_RELOAD=''
	ACTIVE_UNITS=''
	INACTIVE_UNITS=''
	# get services and scopes, active or otherwise
	reset_current
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
		NeedDaemonReload)
			case "$value" in
			yes) NEED_DAEMON_RELOAD=yes ;;
			esac
			;;
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
		$(systemctl --user show --type=service,scope,socket --all --no-pager --quiet --property=Id,ActiveState,Description,Names,UnitFileState,NeedDaemonReload)
		END
	EOF
}

get_units

case "$NEED_DAEMON_RELOAD" in
yes)
	DO_DAEMON_RELOAD=$(
		printf '%s\n' yes no | "$@" "Daemon reload is needed, perform it?"
	) || cancel_exit
	case "$DO_DAEMON_RELOAD" in
	yes)
		systemctl --user daemon-reload
		get_units
		;;
	esac
	;;
esac

# select unit
UNIT=$(
	echo "${ACTIVE_UNITS}${ACTIVE_UNITS:+$N}${INACTIVE_UNITS}" | "$@" "Select Service: "
) || cancel_exit
STATE=${UNIT##* (}
STATE=${STATE%%) *}
UNIT=${UNIT##* }

report "$UNIT"

# pre-reset vars
DESCRIPTION=
CAN_START=
CAN_STOP=
CAN_RELOAD=
CAN_FREEZE=
FREEZER_STATE=
ACTIVE_STATE=
UNIT_FILE_STATE=
WANTED_BY=
REQUIRED_BY=
UPHELD_BY=

# get unit data
while IFS="=" read -r prop value; do
	case "$prop" in
	Description) DESCRIPTION=$value ;;
	CanStart) CAN_START=$value ;;
	CanStop) CAN_STOP=$value ;;
	CanReload) CAN_RELOAD=$value ;;
	CanFreeze) CAN_FREEZE=$value ;;
	FreezerState) FREEZER_STATE=$value ;;
	ActiveState) ACTIVE_STATE=$value ;;
	UnitFileState) UNIT_FILE_STATE=$value ;;
	WantedBy) WANTED_BY=$value ;;
	RequiredBy) REQUIRED_BY=$value ;;
	UpheldBy) UPHELD_BY=$value ;;
	esac
done <<- EOF
	$(systemctl --user show --no-pager --quiet --property=Description,CanFreeze,CanStart,CanReload,CanStop,FreezerState,ActiveState,UnitFileState,WantedBy,RequiredBy,UpheldBy "$UNIT")
EOF

SILENT_STATE=false

case "$UNIT" in
*.service)
	UNIT_TYPE=service
	# get unit template
	case "$UNIT" in
	*@*) UNIT_TEMPLATE=${UNIT%%@*}@.service ;;
	*) UNIT_TEMPLATE=${UNIT} ;;
	esac
	# silent state is determined only by our drop-in, not actual configuration
	if [ -f "${SD_USER_DIR}/${UNIT_TEMPLATE}.d/slient.conf" ]; then
		SILENT_STATE=true
	fi
	;;
*.scope) UNIT_TYPE=scope ;;
*.socket) UNIT_TYPE=socket ;;
esac

# compose actions
ACTIONS=''
DISABLE_ACTIONS=''
ENABLE_ACTIONS=''
for ACTION in start reload restart stop kill reset-failed enable disable freeze thaw silence unsilence mask unmask; do
	: "${ACTION}+++type:${UNIT_TYPE:-unknown}+as:${ACTIVE_STATE:-unknown}+fs:${FREEZER_STATE:-unknown}+cstart:${CAN_START:-unknown}+creload:${CAN_RELOAD:-unknown}+cstop:${CAN_STOP:-unknown}+cfreeze:${CAN_FREEZE:-unknown}+ufs:${UNIT_FILE_STATE}+silent:${SILENT_STATE}+install:${WANTED_BY}${REQUIRED_BY}${UPHELD_BY}+++"
	case "${ACTION}+++type:${UNIT_TYPE:-unknown}+as:${ACTIVE_STATE:-unknown}+fs:${FREEZER_STATE:-unknown}+cstart:${CAN_START:-unknown}+creload:${CAN_RELOAD:-unknown}+cstop:${CAN_STOP:-unknown}+cfreeze:${CAN_FREEZE:-unknown}+ufs:${UNIT_FILE_STATE}+silent:${SILENT_STATE}+install:${WANTED_BY}${REQUIRED_BY}${UPHELD_BY}+++" in
	## skip various combinations
	# actions unsuited for scopes
	start+*+type:scope+* | restart+*+type:scope+* | reload+*+type:scope+* | enable+*+type:scope+* | disable+*+type:scope+* | silence+*+type:scope+* | unsilence+*+type:scope+*) continue ;;
	# actions unsuited for sockets
	kill+*+type:socket+* | freeze+*+type:socket+* | thaw+*+type:socket+* | silence+*+type:socket+* | unsilence+*+type:socket+*) continue ;;
	# start for active, reloading, can not start, masked
	start+*+as:activ* | start+*+as:reloading+* | start+*+cstart:no+* | start+*+ufs:masked+*) continue ;;
	# stop for inactive, deactivating, can not stop, masked
	stop+*+as:failed+* | stop+*+as:inactive+* | stop+*+as:deactivating+* | stop+*+cstop:no+* | stop+*+ufs:masked+*) continue ;;
	# kill for inactive or masked
	kill+*+as:failed+* | kill+*+as:inactive+* | kill+*+ufs:masked+*) continue ;;
	# strictly speaking, restarting a stopped unit is valid, but exclude it anyway
	restart+*+as:failed+* | restart+*+as:inactive+* | restart+*+as:deactivating+* | restart+*+ufs:masked+*) continue ;;
	# reload for inactive or can not reload
	reload+*+as:failed+* | reload+*+as:inactive+* | reload+*+as:deactivating+* | reload+*+creload:no+*) continue ;;
	# reset-failed for not failed
	reset-failed+*+as:[!f][!a][!i]*) continue ;;
	# freeze for can not freeze, frozen, inactive, masked
	freeze+*+cfreeze:no+* | freeze+*+fs:frozen+* | freeze+*+as:failed+* | freeze+*+as:inactive+* | freeze+*+as:deactivating+* | freeze+*+ufs:masked+*) continue ;;
	# thaw for not frozen
	thaw+*+fs:[!f][!r][!o]*) continue ;;
	# mask for masked, active
	mask+*+ufs:masked+* | mask+*+as:activ* | mask+*+as:reloading+*) continue ;;
	# unmask for not masked
	unmask+*+ufs:[!m][!a][!s][!k][!e][!d]*) continue ;;
	# enable for empty install, generated, enabled, masked
	enable+*+install:+* | enable+*+ufs:generated+* | enable+*+ufs:enabled+* | enable+*+ufs:runtime-enabled+* | enable+*+ufs:masked+*) continue ;;
	# disable for generated, transient
	disable+*+ufs:generated+* | disable+*+ufs:transient+*) continue ;;
	# silence states toggle
	silence+*+silent:true+* | unsilence+*+silent:false+*) continue ;;
	## special handling of some surviving actions
	disable+*+ufs:runtime-enabled+*+as:activ* | disable+*+ufs:runtime-enabled+*+as:reloading*)
		DISABLE_ACTIONS="${DISABLE_ACTIONS}${DISABLE_ACTIONS:+$N}disable --runtime${N}disable --runtime --now"
		ACTIONS="${ACTIONS}${ACTIONS:+$N}${ACTION}"
		;;
	disable+*+ufs:runtime-enabled+*)
		DISABLE_ACTIONS="${DISABLE_ACTIONS}${DISABLE_ACTIONS:+$N}disable --runtime"
		ACTIONS="${ACTIONS}${ACTIONS:+$N}${ACTION}"
		;;
	disable+*+as:activ* | disable+*+as:reloading*)
		DISABLE_ACTIONS="${DISABLE_ACTIONS}${DISABLE_ACTIONS:+$N}disable${N}disable --now"
		ACTIONS="${ACTIONS}${ACTIONS:+$N}${ACTION}"
		;;
	enable+*+as:activ* | enable+*+as:reloading*)
		ENABLE_ACTIONS="${ENABLE_ACTIONS}${ENABLE_ACTIONS:+$N}enable${N}enable --runtime"
		ACTIONS="${ACTIONS}${ACTIONS:+$N}${ACTION}"
		;;
	enable+*)
		ENABLE_ACTIONS="${ENABLE_ACTIONS}${ENABLE_ACTIONS:+$N}enable${N}enable --now${N}enable --runtime${N}enable --runtime --now"
		ACTIONS="${ACTIONS}${ACTIONS:+$N}${ACTION}"
		;;
	## another skip, has to be down here, disable for not enabled
	disable+*+ufs:[!e][!n][!a][!b][!l][!e][!d]*) continue ;;
	## generic add surviving action
	*) ACTIONS="${ACTIONS}${ACTIONS:+$N}${ACTION}" ;;
	esac
done

# select action
ACTION=$(
	"$@" "${DESCRIPTION#*=} (${STATE}): " <<- EOF
		$ACTIONS
	EOF
) || cancel_exit

report "$ACTION"

# additional selections
case "$ACTION" in
enable)
	if [ -n "$ENABLE_ACTIONS" ]; then
		ACTION=$(
			"$@" "Select enable action for ${DESCRIPTION#*=}: " <<- EOF
				$ENABLE_ACTIONS
			EOF
		) || cancel_exit
	fi
	;;
disable)
	if [ -n "$DISABLE_ACTIONS" ]; then
		ACTION=$(
			"$@" "Select disable action for ${DESCRIPTION#*=}: " <<- EOF
				$DISABLE_ACTIONS
			EOF
		) || cancel_exit
	fi
	;;
kill)
	SIGNAL=$(
		"$@" "Select signal for ${DESCRIPTION#*=}: " <<- EOF
			SIGTERM
			SIGHUP
			SIGINT
			SIGUSR1
			SIGUSR2
			SIGABRT
			SIGKILL
		EOF
	) || cancel_exit
	report "$SIGNAL"
	ACTION="kill --signal=$SIGNAL"
	;;
silence)
	SILENCE_ACTION=$(
		"$@" "Silence for ${DESCRIPTION#*=}: " <<- EOF
			stdout
			stderr
			both
		EOF
	) || cancel_exit
	report "$SILENCE_ACTION"
	RESTART=$(
		"$@" "Restart ${DESCRIPTION#*=}?: " <<- EOF
			no
			yes
		EOF
	) || cancel_exit
	report "$RESTART"
	;;
unsilence)
	RESTART=$(
		"$@" "Restart ${DESCRIPTION#*=}?: " <<- EOF
			no
			yes
		EOF
	) || cancel_exit
	report "$RESTART"
	;;
esac

# set final command
case "$ACTION" in
silence)
	# rig function as action
	set -- silence
	;;
unsilence)
	# rig function as action
	set -- unsilence
	;;
*)
	# shellcheck disable=SC2086
	set -- systemctl --user $ACTION "$UNIT"
	;;
esac

# apply selected action
if command -v notify-send > /dev/null; then
	# capture only stderr
	ERR=$(
		# shellcheck disable=SC2086
		"$@" 2>&1 > /dev/null
	)
	RC="$?"
	if [ "$RC" = "0" ] && [ -z "$ERR" ]; then
		AST="${ACTION%% *} request successful"
		URG=normal
	elif [ "$RC" = "0" ]; then
		AST="${ACTION%% *} request probably successful"
		URG=normal
	else
		AST="${ACTION%% *} failed (RC $RC)"
		URG=critical
	fi
	notify-send -a "$SELF" -u "$URG" "$AST" "${UNIT}${ERR:+:$N}${ERR}"
	exit "$RC"
else
	"$@"
fi
