#!/bin/false
# sourced by uwsm environment preloader

quirks_niri() {
	# append "niri" to XDG_CURRENT_DESKTOP if not already there
	if [ "${__WM_DESKTOP_NAMES_EXCLUSIVE__}" != "true" ]; then
		case "A:${XDG_CURRENT_DESKTOP}:Z" in
		*:niri:*) true ;;
		*) export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP}:niri" ;;
		esac
	fi

	# mark additional vars for export on finalize
	UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES:+ }NIRI_SOCKET XCURSOR_SIZE XCURSOR_THEME"
	export UWSM_FINALIZE_VARNAMES

	# can't detect if niri has --session arg
}

quirks_niri_session() {
	quirks_niri "$@"
	# mark additional vars to wait for
	UWSM_WAIT_VARNAMES="${UWSM_WAIT_VARNAMES}${UWSM_WAIT_VARNAMES:+ }NIRI_SOCKET"
	export UWSM_WAIT_VARNAMES
}
