#!/bin/sh

SRC_NAME=uwsm

showhelp() {
	while IFS='' read -r line; do
		printf '%s\n' "$line"
	done <<- EOH
		Usage: ${0##*/} [-h|--help] [--prefix /path] [--exec-prefix /path] [--name name] [--old-name old-name]

		  --prefix	installation prefix (default: /usr/local)

		  --exec-prefix	custom installation prefix for executable,
		  		(default: value of --prefix)

		  --name	executable destination name, also affects names of
		  		plugin dir and config files (default: $SRC_NAME)

		  --old-name	for warnings on things that may have been installed
		  		under different name earlier (default: wayland-session)

		Automatically uses sudo if used by a regular user.
	EOH
}

uwsm_install() {
	UID=$(id -u)
	if [ "$UID" = "0" ]; then
		set --
		if [ -n "$SUDO_USER" ]; then
			IFS=':' read -r d d d d d HOME_DIR d <<- EOF
				$(getent passwd "$SUDO_USER")
			EOF
			FIND_CONFIG_DIRS=${HOME_DIR}/.config:/etc/xdg
		else
			FIND_CONFIG_DIRS=/etc/xdg
		fi
	else
		set -- sudo
		FIND_CONFIG_DIRS=${XDG_CONFIG_HOME:-${HOME}/.config}:${XDG_CONFIG_DIRS:-/etc/xdg}
	fi

	set -e

	echo
	echo "Installing as $NAME"
	"$@" install -vpD -o root -m 0755 -T ./"${SRC_NAME}" "${EXEC_PREFIX%/}/bin/${NAME}"
	"$@" install -vpD -o root -m 0755 -T ./"${SRC_NAME}-app" "${EXEC_PREFIX%/}/bin/${NAME}-app"
	"$@" install -vpD -o root -m 0644 -t "${PREFIX%/}/lib/${NAME}-plugins/" ./"${SRC_NAME}-plugins"/*
	echo "Finished installation"

	if [ "$NAME" != "$OLD_NAME" ]; then
		OLD_FILES=''
		if [ -f "${EXEC_PREFIX%/}/bin/${OLD_NAME}" ]; then
			OLD_FILES="  old executable exists: ${EXEC_PREFIX%/}/bin/${OLD_NAME}"
		fi
		if [ -d "${PREFIX%/}/lib/${OLD_NAME}-plugins/" ]; then
			OLD_FILES="${OLD_FILES}${OLD_FILES:+$N}  old plugin dir exists: ${PREFIX%/}/lib/${OLD_NAME}-plugins/"
		fi

		echo
		if [ -n "$OLD_FILES" ]; then
			echo "Found files related to old name \"$OLD_NAME\":"
			echo "$OLD_FILES"
		fi
		echo "Please rename any \"${OLD_NAME}-*\" config files to \"${NAME}-*\":"
		printf '  %s\n' "${OLD_NAME}-default-id" "${OLD_NAME}-env" "${OLD_NAME}-env-*"
		FOUND_CONFIGS=$(
			IFS=:
			find $FIND_CONFIG_DIRS -maxdepth 1 -name "${OLD_NAME}-*" | xargs -r printf '  %s\n'
		)
		if [ -n "$FOUND_CONFIGS" ]; then
			echo "Found:"
			echo "$FOUND_CONFIGS"
		fi
		echo "Also do not forget to rename \"${OLD_NAME}\" to \"${NAME}\" in your compositor configs!"
	fi

}

PREFIX=''
EXEC_PREFIX=''
NAME=''
OLD_NAME=''
N='
'

while [ "$#" != "0" ]; do
	case "$1" in
	--prefix)
		if [ -n "$2" ] && [ "$2" != "${2#/}" ] && [ -d "$2" ]; then
			PREFIX="$2"
			shift 2
		else
			echo "invalid prefix: $2" >&2
			exit 1
		fi
		;;
	--exec-prefix)
		if [ -n "$2" ] && [ "$2" != "${2#/}" ] && [ -d "$2" ]; then
			EXEC_PREFIX="$2"
			shift 2
		else
			echo "invalid exec prefix: $2" >&2
			exit 1
		fi
		;;
	--name)
		if [ -n "$2" ] && case "$2" in [!a-zA-Z_]*[!a-zA-Z0-9_.-] | [!a-zA-Z_] | *[!a-zA-Z0-9_.-]* | '') false ;; esac then
			NAME="$2"
			shift 2
		else
			echo "invalid name: $2" >&2
			exit 1
		fi
		;;
	--old-name)
		if [ -n "$2" ] && case "$2" in [!a-zA-Z_]*[!a-zA-Z0-9_.-] | [!a-zA-Z_] | *[!a-zA-Z0-9_.-]* | '') false ;; esac then
			OLD_NAME="$2"
			shift 2
		else
			echo "invalid previous name: $2" >&2
			exit 1
		fi
		;;
	-h | --help)
		showhelp
		exit 0
		;;
	*)
		echo "unknown arg: $1"
		exit 1
		;;
	esac
done

: "${PREFIX:=/usr/local}"
: "${EXEC_PREFIX:=$PREFIX}"
: "${NAME:=$SRC_NAME}"
: "${OLD_NAME:=wayland-session}"

cd "$(dirname "$0")"

printf '%s\n' "prefix: $PREFIX" "exec prefix: $EXEC_PREFIX" "name: $NAME" "old name: $OLD_NAME"

uwsm_install
