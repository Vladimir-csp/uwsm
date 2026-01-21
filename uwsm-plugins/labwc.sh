#!/bin/false
# sourced by uwsm environment preloader

quirks_labwc() {
	# append "wlroots" to XDG_CURRENT_DESKTOP if not already there
	if [ "${__WM_DESKTOP_NAMES_EXCLUSIVE__}" != "true" ]; then
		case "A:${XDG_CURRENT_DESKTOP}:Z" in
		*:wlroots:*) true ;;
		*)
			export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP}:wlroots"
			;;
		esac
	fi

	# Xwayland is always on in labwc
	XWAYLAND=true

	#### allow unit reload on SIGHUP
	TEMP_WM_SERVICE="wayland-wm@${__WM_ID_UNIT_STRING__}.service"
	# deduce unit destination rung by 50_custom.conf location
	TEMP_DROPIN_RUNG=
	for temp_path in $(systemctl --user show --property=DropInPaths --value "$TEMP_WM_SERVICE"); do
		case "${temp_path}" in
		"${XDG_CONFIG_HOME}"/*/"${TEMP_WM_SERVICE}.d/50_custom.conf"*)
			TEMP_DROPIN_RUNG=${XDG_CONFIG_HOME}
			break
			;;
		"${XDG_RUNTIME_DIR}"/*/"${TEMP_WM_SERVICE}.d/50_custom.conf"*)
			TEMP_DROPIN_RUNG=${XDG_RUNTIME_DIR}
			break
			;;
		esac
	done
	# fallback to var or runtime rung
	if [ -z "$TEMP_DROPIN_RUNG" ]; then
		case "${UWSM_UNIT_RUNG:-}" in
		run) TEMP_DROPIN_RUNG=${XDG_RUNTIME_DIR} ;;
		home) TEMP_DROPIN_RUNG=${XDG_RUNTIME_DIR} ;;
		*) TEMP_DROPIN_RUNG=${XDG_RUNTIME_DIR} ;;
		esac
	fi
	TEMP_DROPIN_DIR=${TEMP_DROPIN_RUNG}/systemd/user/${TEMP_WM_SERVICE}.d
	TEMP_DROPIN_CONTENT=$(
		printf '%s\n' '[Unit]' "X-UWSMMark=${__WM_ID__}" '[Service]' 'ExecReload=kill -SIGHUP $MAINPID'
	)
	TEMP_UPDATE_REQUIRED=false
	TEMP_RELOAD_REQUIRED=false
	if [ -f "${TEMP_DROPIN_DIR}/55_reload.conf" ]; then
		{ read -r TEMP_SUM1 TEMP_DROP && read -r TEMP_SUM2 TEMP_DROP; } <<- EOF
			$(printf '%s\n' "$TEMP_DROPIN_CONTENT" | md5sum - "${TEMP_DROPIN_DIR}/55_reload.conf")
		EOF
		if [ "$TEMP_SUM1" != "$TEMP_SUM2" ]; then
			TEMP_UPDATE_REQUIRED=true
			TEMP_RELOAD_REQUIRED=true
		fi
	else
		TEMP_UPDATE_REQUIRED=true
		TEMP_RELOAD_REQUIRED=true
	fi
	if [ "$TEMP_UPDATE_REQUIRED" = "true" ]; then
		echo "Adding reload drop-in for ${TEMP_WM_SERVICE}"
		printf '%s\n' "$TEMP_DROPIN_CONTENT" > "${TEMP_DROPIN_DIR}/55_reload.conf"
	fi
	# swap rung and clean up
	case "$TEMP_DROPIN_RUNG" in
	"${XDG_CONFIG_HOME}") TEMP_DROPIN_RUNG=$XDG_RUNTIME_DIR ;;
	"${XDG_RUNTIME_DIR}") TEMP_DROPIN_RUNG=$XDG_CONFIG_HOME ;;
	esac
	TEMP_DROPIN_DIR=${TEMP_DROPIN_RUNG}/systemd/user/${TEMP_WM_SERVICE}.d
	if [ -f "${TEMP_DROPIN_DIR}/55_reload.conf" ]; then
		rm -vf "${TEMP_DROPIN_DIR}/55_reload.conf"
		TEMP_RELOAD_REQUIRED=true
	fi
	if [ "$TEMP_RELOAD_REQUIRED" = "true" ]; then
		echo "Reloading systemd"
		systemctl --user daemon-reload
	fi

	# mark additional vars for export on finalize
	UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES:+ }LABWC_PID XCURSOR_SIZE XCURSOR_THEME"
	export UWSM_FINALIZE_VARNAMES

	# mark variables to wait for
	UWSM_WAIT_VARNAMES="${UWSM_WAIT_VARNAMES}${UWSM_WAIT_VARNAMES:+ }LABWC_PID"
	export UWSM_WAIT_VARNAMES
}

labwc_environment2finalize() {
	# expects labwc env file content on stdin
	# adds varnames to UWSM_FINALIZE_VARNAMES
	while read -r line; do
		case "$line" in
		[!a-zA-Z_]*) continue ;;
		*=*) true ;;
		*) continue ;;
		esac
		IFS='=' read -r var value <<- EOF
			$line
		EOF
		case "$var" in
		*[!a-zA-Z0-9_]* | '') continue ;;
		esac
		UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES:+ }$var"
	done
}

in_each_config_dir_reversed_labwc() {
	# do normal stuff
	in_each_config_dir_reversed "$1"

	# fill UWSM_FINALIZE_VARNAMES with varnames from labwc env files
	if [ -r "${1}/labwc/environment" ]; then
		echo "Collecting varnames from \"${1}/labwc/environment\""
		labwc_environment2finalize < "${1}/labwc/environment"
		export UWSM_FINALIZE_VARNAMES
	fi
}
