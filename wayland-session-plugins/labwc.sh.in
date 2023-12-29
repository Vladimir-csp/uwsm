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
}

in_each_config_dir_reversed_labwc() {
	# load wm env normally
	in_each_config_dir_reversed "${1}"

	# also add env from labwc location
	source_file "${1}/${__WM_BIN_ID__}/env"
}
