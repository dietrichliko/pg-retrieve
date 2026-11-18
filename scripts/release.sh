#!/usr/bin/env bash
# Cut a release with commitizen.
#
#   scripts/release.sh [--dry-run] [patch|minor|major|<version>] [--prerelease alpha|beta|rc]
#
# Without an increment, commitizen derives it from the conventional commits since the
# last tag. The bump updates every version location configured in pyproject.toml
# ([tool.commitizen]: project.version, uv.lock, version_files), writes CHANGELOG.md,
# commits and tags v<version>. The script then builds locally as a check and pushes
# the commit and tag together; the Release workflow publishes the GitHub release.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

dry_run=0
bump_args=()
while (($#)); do
  case "$1" in
    --dry-run) dry_run=1 ;;
    patch | minor | major) bump_args+=(--increment "${1^^}") ;;
    --prerelease) bump_args+=(--prerelease "$2"); shift ;;
    [0-9]*) bump_args+=("$1") ;;
    -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "release: $*" >&2; exit 1; }

branch="$(git branch --show-current)"
[[ "$branch" == main ]] || die "on branch '$branch', releases are cut from main"
[[ -z "$(git status --porcelain)" ]] || die "working tree is not clean"

git fetch --quiet --tags origin
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] \
  || die "main differs from origin/main; pull or push first"

uv sync --locked
uv run pre-commit run --all-files
uv run pytest

if ((dry_run)); then
  uv run cz bump --dry-run --yes "${bump_args[@]}"
  exit 0
fi

uv run cz bump --yes "${bump_args[@]}"
version="$(uv version --short)"

rm -rf dist
uv build

git push --atomic origin main "v${version}"
echo "released v${version}"
