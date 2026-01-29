#!/bin/sh

# Generates and prints version string modified from "git describe" output,
# or prints back UWSM_VERSION env if set,
# or prints a fallback version if not in git repo.

VERSION=0.26.1

set -e

if [ -n "$UWSM_VERSION" ]; then
	echo "$UWSM_VERSION"

elif git rev-parse --is-inside-work-tree > /dev/null 2>&1 && d_version=$(git describe --tags); then
	VERSION=${d_version#v}
	IFS='-' read -r version cdelta ghash <<- EOF
		$VERSION
	EOF

	if [ -n "$ghash" ]; then
		VERSION="${version}+git.${cdelta}.${ghash#g}"
	fi

	echo "$VERSION"

else
	# fallback version in case not in git repo, or history is unavailable
	echo "$VERSION"
fi
