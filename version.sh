#!/bin/sh

# Generates version string modified from "git describe" output,
# or a fallback version if not in git repo.

VERSION=0.17.4

set -e

# fallback version in case not in git repo
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	echo "$VERSION"
	exit 0
fi

VERSION=$(git describe --tags)
VERSION=${VERSION#v}

IFS='-' read -r version cdelta ghash <<- EOF
	$VERSION
EOF

if [ -n "$ghash" ]; then
	VERSION="${version}+git.${cdelta}.${ghash#g}"
fi

echo "$VERSION"
