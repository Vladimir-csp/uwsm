#!/bin/sh

# Generates version string modified from "git describe" output,
# or a fallback version if not in git repo.

VERSION=0.23.1

set -e

# fallback version in case not in git repo, or history is unavailable
if git rev-parse --is-inside-work-tree > /dev/null 2>&1 && d_version=$(git describe --tags); then
	VERSION=${d_version#v}
else
	echo "$VERSION"
	return 0 || exit 0
fi

IFS='-' read -r version cdelta ghash <<- EOF
	$VERSION
EOF

if [ -n "$ghash" ]; then
	VERSION="${version}+git.${cdelta}.${ghash#g}"
fi

echo "$VERSION"
