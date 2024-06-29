#!/bin/false
# sourced by uwsm environment preloader

quirks_labwc() {
	# append "wlroots" to XDG_CURRENT_DESKTOP if not already there
	if [ "${__WM_DESKTOP_NAMES_EXCLUSIVE__}" != "true" ]
	then
		case "A:${XDG_CURRENT_DESKTOP}:Z" in
		*:wlroots:*) true ;;
		*)
			export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP}:wlroots"
			;;
		esac
	fi

	# Xwayland is always on in labwc
	XWAYLAND=true

	# allow unit reload on SIGHUP
	TEMP_WM_SERVICE="wayland-wm@${__WM_ID_UNIT_STRING__}.service"
	TEMP_DROPIN_DIR="${XDG_RUNTIME_DIR}/systemd/user/${TEMP_WM_SERVICE}.d"
	TEMP_DROPIN_CONTENT=$(
		printf '%s\n' '[Unit]' "X-UWSM-ID=${__WM_ID__}" '[Service]' 'ExecReload=kill -SIGHUP $MAINPID'
	)
	TEMP_UPDATE_REQUIRED=false
	if [ -f "${TEMP_DROPIN_DIR}/55_reload.conf" ]; then
		{ read -r TEMP_SUM1 TEMP_DROP && read -r TEMP_SUM2 TEMP_DROP ; } <<- EOF
			$(printf '%s\n' "$TEMP_DROPIN_CONTENT" | md5sum - "${TEMP_DROPIN_DIR}/55_reload.conf")
		EOF
		if [ "$TEMP_SUM1" != "$TEMP_SUM2" ]; then
			TEMP_UPDATE_REQUIRED=true
		fi
	else
		TEMP_UPDATE_REQUIRED=true
	fi
	if [ "$TEMP_UPDATE_REQUIRED" = "true" ]; then
		echo "Adding reload drop-in for ${TEMP_WM_SERVICE}"
		printf '%s\n' "$TEMP_DROPIN_CONTENT" > "${TEMP_DROPIN_DIR}/55_reload.conf"
		systemctl --user daemon-reload
	fi

	# mark additional vars for export on finalize
	UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES+: }LABWC_PID XCURSOR_SIZE XCURSOR_THEME"
	export UWSM_FINALIZE_VARNAMES
}

labwc_environment2finalize() {
	# expects labwc env file content on stdin
	# adds varnames to UWSM_FINALIZE_VARNAMES
	while read -r line; do
		case "$line" in
		[!a-zA-Z_]* ) continue ;;
		*=*) true ;;
		*) continue ;;
		esac
		IFS='=' read -r var value <<- EOF
			$line
		EOF
		case "$var" in
		*[!a-zA-Z0-9_]* | '') continue ;;
		esac
		UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES+: }$var"
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