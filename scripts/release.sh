#!/usr/bin/env bash

# Tag a release on hail-dev and push the tag (parking-lot item 289).
#
#   scripts/release.sh v0.9.0            check, then tag the current commit and push the tag
#   scripts/release.sh --check v0.9.0    check only; tag nothing
#
# A person runs this, nothing calls it.  It refuses unless:
#   - the version looks like vMAJOR.MINOR.PATCH and no tag of that name exists here or on GitHub;
#   - the working tree is clean and this is main, level with origin/main (pushed, nothing to pull);
#   - CHANGELOG.md has a section headed "## vX.Y.Z" for it.
# It does not deploy.  Deploying is docs/deploy.md:  on the server, check out the tag, run
# scripts/write_version.sh, restart web, then the migration and image steps.
#
# Numbers:  PATCH (v0.9.1) for fixes only; MINOR (v0.10.0) for anything new or changed that people will
# notice (a page, a setting, a migration); MAJOR for a milestone (v1.0.0 is the pilot, Phase 6).
# The GitHub repo is public:  a tag message and the changelog never name people, addresses or secrets.

set -euo pipefail

check_only=0
if [[ "${1:-}" == "--check" ]]; then check_only=1; shift; fi
version="${1:-}"

die() { echo "release: $*" >&2; exit 1; }

[[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "give a version like v0.9.0 (got '${version}')"
cd "$(git rev-parse --show-toplevel)"

[[ -z "$(git status --porcelain)" ]] || die "the working tree has changes; commit or stash them first"
[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || die "releases are tagged on main"
git fetch --quiet --tags origin main
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] \
    || die "main is not level with origin/main; push or pull first"
if git rev-parse -q --verify "refs/tags/$version" >/dev/null; then die "tag $version already exists"; fi
if git ls-remote --exit-code --tags origin "refs/tags/$version" >/dev/null 2>&1; then
    die "tag $version already exists on GitHub"
fi
grep -q "^## ${version}\b" CHANGELOG.md || die "CHANGELOG.md has no section headed '## ${version}'"

echo "release: $version would tag $(git rev-parse --short HEAD) ($(git log -1 --format=%s))"
if (( check_only )); then echo "release: --check, nothing tagged"; exit 0; fi

git tag -a "$version" -m "$version: see CHANGELOG.md"
git push origin "$version"
echo "release: tagged and pushed $version.  On the server: docs/deploy.md section 12."
