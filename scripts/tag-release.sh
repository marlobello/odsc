#!/usr/bin/env bash
# tag-release.sh — bump the VERSION, commit, and create a git tag.
#
# Usage:
#   ./scripts/tag-release.sh <new-version>   e.g.  ./scripts/tag-release.sh 1.1.0
#
# After running this script, push to trigger the release workflow:
#   git push origin main --tags

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
    echo "Usage: $0 <version>  (e.g. $0 1.1.0)"
    exit 1
}

[[ $# -eq 1 ]] || usage

NEW_VERSION="$1"

# Validate semver-ish format
if ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-.+)?$ ]]; then
    echo "Error: version must be in X.Y.Z or X.Y.Z-suffix format (got: $NEW_VERSION)"
    exit 1
fi

cd "$REPO_ROOT"

# Ensure working tree is clean (untracked files are OK)
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Error: working tree has uncommitted changes. Commit or stash them first."
    exit 1
fi

CURRENT_VERSION=$(cat VERSION)
echo "Current version : $CURRENT_VERSION"
echo "New version     : $NEW_VERSION"
echo ""

read -p "Proceed? [y/N] " -n 1 -r
echo
[[ $REPLY =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# Bump VERSION file
echo "$NEW_VERSION" > VERSION

# Stage and commit
git add VERSION
git commit -m "Release v${NEW_VERSION}

Bump version to ${NEW_VERSION}.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"

# Create annotated tag
git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"

echo ""
echo "✓ Version bumped to $NEW_VERSION and tagged as v${NEW_VERSION}."
echo ""
echo "To publish the release, push the tag:"
echo "  git push origin main --tags"
