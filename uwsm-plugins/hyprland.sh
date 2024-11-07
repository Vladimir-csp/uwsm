#!/bin/false
# sourced by uwsm environment preloader

quirks_hyprland() {
	# append "Hyprland" to XDG_CURRENT_DESKTOP if not already there
	if [ "${__WM_DESKTOP_NAMES_EXCLUSIVE__}" != "true" ]
	then
		case "A:${XDG_CURRENT_DESKTOP}:Z" in
		*:Hyprland:*) true ;;
		*) export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP}:Hyprland" ;;
		esac
	fi

	# mark additional vars for export on finalize
	UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES+: }HYPRLAND_INSTANCE_SIGNATURE HYPRLAND_CMD HYPRCURSOR_THEME HYPRCURSOR_SIZE XCURSOR_SIZE XCURSOR_THEME"
	export UWSM_FINALIZE_VARNAMES

	# mark additional vars to wait for
	UWSM_WAIT_VARNAMES="${UWSM_WAIT_VARNAMES}${UWSM_WAIT_VARNAMES+: }HYPRLAND_INSTANCE_SIGNATURE"
	export UWSM_WAIT_VARNAMES
}
