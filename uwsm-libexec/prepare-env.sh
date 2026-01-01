#!/bin/sh

#### functions

reverse() {
	# returns list $1 delimited by ${2:-:} in reverse
	__reverse_out__=''
	IFS="${2:-:}"
	for __item__ in $1; do
		if [ -n "${__item__}" ]; then
			__reverse_out__="${__item__}${__reverse_out__:+$IFS}${__reverse_out__}"
		fi
	done
	printf '%s' "${__reverse_out__}"
	unset __reverse_out__
	IFS="${__OIFS__}"
}

lowercase() {
	# returns lowercase string
	printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

source_file() {
	# sources file if exists, with messaging
	if [ -f "${1}" ]; then
		if [ -r "${1}" ]; then
			printf '%s\n' "Loading environment from \"${1}\"."
			. "${1}"
		else
			"Environment file ${1} is not readable" >&2
		fi
	fi
}

source_dir() {
	# applies source_file to every file in dir
	if [ -d "${1}" ]; then
		# process in standard order and visibility given by ls
		while IFS='' read -r __env_file__; do
			source_file "${1}/${__env_file__}"
		done <<- EOF
			$(ls "${1}")
		EOF
		unset __env_file__
	fi
}

get_all_config_dirs() {
	# returns whole XDG_CONFIG hierarchy, :-delimited
	printf '%s' "${XDG_CONFIG_HOME}:${XDG_CONFIG_DIRS}"
}

get_all_config_dirs_extended() {
	# returns whole XDG_CONFIG and system XDG_DATA hierarchies, :-delimited
	printf '%s' "${XDG_CONFIG_HOME}:${XDG_CONFIG_DIRS}:${XDG_DATA_DIRS}"
}

in_each_config_dir() {
	# called for each config dir (decreasing priority)
	true
}

in_each_config_dir_reversed() {
	# called for each config dir in reverse (increasing priority)

	# compose sequence of env files from lowercase desktop names in reverse
	IFS=':'
	__env_files__=''
	for __dnlc__ in $(lowercase "$(reverse "${XDG_CURRENT_DESKTOP}")"); do
		IFS="${__OIFS__}"
		__env_files__="${__SELF_NAME__}/env-${__dnlc__}${__env_files__:+:}${__env_files__}"
	done
	# add common env file at the beginning
	__env_files__="${__SELF_NAME__}/env${__env_files__:+:}${__env_files__}"
	unset __dnlc__

	# load env file sequence from this config dir rung
	IFS=':'
	for __env_file__ in ${__env_files__}; do
		source_file "${1}/${__env_file__}"
		source_dir "${1}/${__env_file__}.d"
	done
	unset __env_file__
	unset __env_files__
	IFS="${__OIFS__}"
}

process_config_dirs() {
	# iterate over config dirs (decreasing importance) and call in_each_config_dir* functions
	IFS=":"
	for __config_dir__ in $(get_all_config_dirs_extended); do
		IFS="${__OIFS__}"
		if type "in_each_config_dir_${__WM_BIN_ID__}" > /dev/null 2>&1; then
			"in_each_config_dir_${__WM_BIN_ID__}" "${__config_dir__}" || return $?
		else
			in_each_config_dir "${__config_dir__}" || return $?
		fi
	done
	unset __config_dir__
	IFS="${__OIFS__}"
	return 0
}

process_config_dirs_reversed() {
	# iterate over reverse config dirs (increasing importance) and call in_each_config_dir_reversed* functions
	IFS=":"
	for __config_dir__ in $(reverse "$(get_all_config_dirs_extended)"); do
		IFS="${__OIFS__}"
		if type "in_each_config_dir_reversed_${__WM_BIN_ID__}" > /dev/null 2>&1; then
			"in_each_config_dir_reversed_${__WM_BIN_ID__}" "${__config_dir__}" || return $?
		else
			in_each_config_dir_reversed "${__config_dir__}" || return $?
		fi
	done
	unset __config_dir__
	IFS="${__OIFS__}"
	return 0
}

load_wm_env() {
	# calls reverse config dir processing
	if type "process_config_dirs_reversed_${__WM_BIN_ID__}" > /dev/null 2>&1; then
		"process_config_dirs_reversed_${__WM_BIN_ID__}" || return $?
	else
		process_config_dirs_reversed
	fi
}

#### Failsafe
if [ "$#" -lt "1" ]; then
	printf '%s\n' "Designed to be executed by uwsm! Exiting." >&2
	exit 1
fi
if [ ! -f "${1}" ]; then
	printf '%s\n' "Aux vars file ${1} not found! Exiting." >&2
	exit 1
fi

#### Aux vars and plugins
. "${1}"
rm -f "${1}"
shift

if [ -z "${__RANDOM_MARK__}" ]; then
	# shellcheck disable=SC2016
	printf '%s\n' 'Random mark ${__RANDOM_MARK__} not set! Exiting.' >&2
	exit 1
fi

for __plugin__ in "$@"; do
	printf '%s\n' "Loading plugin \"${__plugin__}\""
	. "${__plugin__}"
done
unset __plugin__

#### Basic environment

if [ "${__LOAD_PROFILE__}" = "true" ]; then
	printf '%s\n' "Loading shell profile."
	[ -f /etc/profile ] && . /etc/profile
	[ -f "${HOME}/.profile" ] && . "${HOME}/.profile"
	export PATH
fi

export XDG_CONFIG_DIRS="${XDG_CONFIG_DIRS:-/etc/xdg}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
export XDG_DATA_DIRS="${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export XDG_STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"

export XDG_CURRENT_DESKTOP="${__WM_DESKTOP_NAMES__}"
export XDG_SESSION_DESKTOP="${__WM_FIRST_DESKTOP_NAME__}"

XDG_MENU_PREFIX="$(lowercase "${__WM_FIRST_DESKTOP_NAME__}")-"
export XDG_MENU_PREFIX

export XDG_SESSION_TYPE="wayland"
export XDG_BACKEND="wayland"

#### apply quirks

if type "quirks_${__WM_BIN_ID__}" > /dev/null 2>&1; then
	printf '%s\n' "Applying quirks for \"${__WM_BIN_ID__}\"."
	"quirks_${__WM_BIN_ID__}" || exit $?
fi

#### load env files

if type "load_wm_env_${__WM_BIN_ID__}" > /dev/null 2>&1; then
	"load_wm_env_${__WM_BIN_ID__}" || exit $?
else
	load_wm_env || exit $?
	true
fi

#### integrate XDG User Dirs
# TODO: remove this in a couple of years of xdg-user-dirs 0.19 spread
# update XDG User Dirs, XDG Autostart would be too late

if command -v xdg-user-dirs-update > /dev/null && ! systemctl --user is-enabled -q xdg-user-dirs.service; then
	printf '%s\n' "Updating XDG User Dirs"
	xdg-user-dirs-update
fi

#### print random string and environment
printf '%s' "${__RANDOM_MARK__}"
exec env -0
