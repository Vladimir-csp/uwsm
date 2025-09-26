#!/bin/sh

# Updates debian/changelog with actual version from git
# Installs build tools (devscripts)
# Generates and installs uwsm-build-dep metapackage
# Builds package
# Installs package if -i|--install argument is given

set -e

cd "$(dirname "$0")"

. ./version.sh

DEBVERSION=${VERSION}-1~local0

case "$(dpkg-query -Wf '${db:Status-Abbrev}' devscripts)" in
ii*) echo "devscripts already installed" ;;
*)
	echo "Installing devscripts"
	sudo apt-get install devscripts
	;;
esac

if git rev-parse --is-inside-work-tree > /dev/null 2> /dev/null; then
	BUILD_DIR=/tmp/uwsm-build/uwsm-${VERSION}
	echo "Exporting HEAD to '$BUILD_DIR' and building there..."
	rm -fr "$BUILD_DIR"
	mkdir "$BUILD_DIR"
	git archive --format=tar HEAD | tar -xf - -C "$BUILD_DIR"
	cd "$BUILD_DIR"
else
	BUILD_DIR=$PWD
fi

DCHVERSION=$(dpkg-parsechangelog -l debian/changelog -S Version)

if [ "$DEBVERSION" != "$DCHVERSION" ]; then
	echo "Generating debian/changelog"
	cat <<- EOF > debian/changelog
		uwsm ($DEBVERSION) UNRELEASED; urgency=medium

		  * Upstream build.

		 -- Vladimir-csp <4061903+Vladimir-csp@users.noreply.github.com>  $(date "+%a, %d %b %Y %T %z")
	EOF
else
	echo "debian/changelog already has correct version"
fi

case "$(dpkg-query -Wf '${db:Status-Abbrev};${source:Version}' uwsm-build-deps)" in
"ii"*";$DEBVERSION") echo "uwsm-build-deps metapackage already installed" ;;
*)
	if [ ! -f "uwsm-build-deps_${DEBVERSION}_all.deb" ]; then
		echo "Creating uwsm-build-deps metapackage"
		mk-build-deps
	fi
	echo "Installing uwsm-build-deps_${DEBVERSION}_all.deb"
	sudo apt-get install "./uwsm-build-deps_${DEBVERSION}_all.deb"
	;;
esac

echo "Building"

dpkg-buildpackage -b -tc --no-sign

echo "Package: ${BUILD_DIR}/../uwsm_${DEBVERSION}_all.deb"

case "$1" in
-i | --install)
	echo "Installing uwsm_${DEBVERSION}_all.deb"
	sudo apt-get install --reinstall "../uwsm_${DEBVERSION}_all.deb"
	;;
esac
