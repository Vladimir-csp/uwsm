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

	# Disable Hyprland's own systemd notification, supported sice:
	# https://github.com/hyprwm/Hyprland/commit/bd952dcef2ead3b0b7e2d730930a3fc528813ee0
	# Without this unit will be declared started before "finalize" is executed,
	# So some autostarted units may not get custom vars
	export HYPRLAND_NO_SD_NOTIFY=1

	# mark additional vars for export on finalize
	UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES+: }HYPRLAND_INSTANCE_SIGNATURE XCURSOR_SIZE XCURSOR_THEME"
	export UWSM_FINALIZE_VARNAMES
}
