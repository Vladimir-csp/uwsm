#!/bin/false
# sourced by uwsm environment preloader

quirks_sway() {
	# detect disabled xwayland
	if grep -qE '^[[:space:]]*xwayland[[:space:]]+disable' "${XDG_CONFIG_HOME}/sway/config" 2>/dev/null
	then
		XWAYLAND=false
	else
		XWAYLAND=true
	fi

	# append "wlroots" to XDG_CURRENT_DESKTOP if not already there
	if [ "${__WM_DESKTOP_NAMES_EXCLUSIVE__}" != "true" ]
	then
		case "A:${XDG_CURRENT_DESKTOP}:Z" in
		*:wlroots:*) true ;;
		*) export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP}:wlroots" ;;
		esac
	fi

	# mark additional vars for export on finalize
	UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES+: }SWAYSOCK I3SOCK XCURSOR_SIZE XCURSOR_THEME"
	export UWSM_FINALIZE_VARNAMES

	# mark additional vars to wait for
	UWSM_WAIT_VARNAMES="${UWSM_WAIT_VARNAMES}${UWSM_WAIT_VARNAMES+: }SWAYSOCK"
	export UWSM_WAIT_VARNAMES
}
