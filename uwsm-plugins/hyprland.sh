#!/bin/false
# sourced by uwsm environment preloader

quirks_hyprland() {
	# append "wlroots" to XDG_CURRENT_DESKTOP if not already there
	if [ "${__WM_DESKTOP_NAMES_EXCLUSIVE__}" != "true" ]
	then
		case "A:${XDG_CURRENT_DESKTOP}:Z" in
		*:wlroots:*) true ;;
		*) export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP}:wlroots" ;;
		esac
	fi

	# Disable Hyprland's own systemd notification, supported sice:
	# https://github.com/hyprwm/Hyprland/commit/bd952dcef2ead3b0b7e2d730930a3fc528813ee0
	# Without this unit will be declared started before "finalize" is executed,
	# So some autostarted units may not get custom vars
	export HYPRLAND_NO_SD_NOTIFY=true
}
