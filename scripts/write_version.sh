#!/usr/bin/env bash

# Stamp this working copy with the release it is (parking-lot item 289).
#
#   scripts/write_version.sh
#
# Run it on the host, in the repo, after checking out a tag (docs/deploy.md, sections 3 and 12), then restart web:
# the web container mounts only hailsys/ and cannot ask git itself, so it reads hailsys/VERSION once at
# startup and shows it in the footer.  The file is ignored by git.  It holds one line:
#   <git describe --tags --always --dirty> <short commit>     for example   v0.9.0 abc1234
# A machine running anything but an exact, clean tag says so:  v0.9.0-3-gabc1234 (three commits past it) or a
# -dirty ending (edited files), which is worth seeing in the footer.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
describe="$(git describe --tags --always --dirty)"
commit="$(git rev-parse --short HEAD)"
printf '%s %s\n' "$describe" "$commit" > hailsys/VERSION
echo "hailsys/VERSION: $describe $commit  (restart web to show it: docker compose restart web)"
