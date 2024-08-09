#!/bin/false
# sourced by uwsm environment preloader

quirks_hyprland() {
	# Disable Hyprland's own systemd notification, supported sice:
	# https://github.com/hyprwm/Hyprland/commit/bd952dcef2ead3b0b7e2d730930a3fc528813ee0
	# Without this unit will be declared started before "finalize" is executed,
	# So some autostarted units may not get custom vars
	export HYPRLAND_NO_SD_NOTIFY=1

	# mark additional vars for export on finalize
	UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES+: }HYPRLAND_INSTANCE_SIGNATURE XCURSOR_SIZE XCURSOR_THEME"
	export UWSM_FINALIZE_VARNAMES
}
