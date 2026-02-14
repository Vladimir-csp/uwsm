#!/bin/false
# sourced by uwsm environment preloader

quirks_mango() {
	# Append "wlroots" to XDG_CURRENT_DESKTOP if not already there
	# (mangowc is a wlroots-based compositor)
	if [ "${__WM_DESKTOP_NAMES_EXCLUSIVE__}" != "true" ]; then
		case "A:${XDG_CURRENT_DESKTOP}:Z" in
		*:wlroots:*) true ;;
		*) export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP}:wlroots" ;;
		esac
	fi

	# mark additional vars for export on finalize
	UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES:+ }XCURSOR_SIZE XCURSOR_THEME"
	export UWSM_FINALIZE_VARNAMES
}
