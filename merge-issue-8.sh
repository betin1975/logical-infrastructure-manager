#!/usr/bin/env bash
set -Eeuo pipefail

COMMIT_MESSAGE="${COMMIT_MESSAGE:-feat(collector): implement Linux collector foundation}"
TAG="${TAG:-v4.0.0-beta.6}"
TAG_MESSAGE="${TAG_MESSAGE:-Linux Collector Foundation}"
BASE_BRANCH="${BASE_BRANCH:-beta}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '\n==> %s\n' "$*"
}

command -v git >/dev/null 2>&1 || fail "git is not installed."
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Run this script from inside the LIM Git repository."

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

FEATURE_BRANCH="$(git branch --show-current)"
[ -n "$FEATURE_BRANCH" ] || fail "Detached HEAD is not supported."
[ "$FEATURE_BRANCH" != "$BASE_BRANCH" ] || fail "You are on '$BASE_BRANCH'. Run this from the Issue #8 feature branch."
[ "$FEATURE_BRANCH" != "main" ] || fail "Do not run this from main."

info "Repository: $REPO_ROOT"
info "Feature branch: $FEATURE_BRANCH"
info "Target branch: $BASE_BRANCH"
info "Release tag: $TAG"

git diff --check || fail "git diff --check failed."

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

command -v "$PYTHON" >/dev/null 2>&1 || fail "Python was not found. Activate the project virtual environment first."

if [ -x ".venv/bin/ruff" ]; then
  RUFF=".venv/bin/ruff"
elif command -v ruff >/dev/null 2>&1; then
  RUFF="ruff"
else
  fail "ruff was not found. Install requirements-dev.txt or activate .venv."
fi

info "Running tests with coverage"
"$PYTHON" -m pytest --cov=app

info "Running Ruff"
"$RUFF" check .

info "Compiling Python sources"
"$PYTHON" -m compileall -q app tests

info "Staging Issue #8 changes"
git add -A

if git diff --cached --quiet; then
  info "No uncommitted changes found; using the existing branch commit."
else
  info "Creating commit"
  git commit -m "$COMMIT_MESSAGE"
fi

info "Pushing feature branch"
git push -u origin "$FEATURE_BRANCH"

if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "Working tree is not clean after commit."
fi

info "Updating local $BASE_BRANCH"
git fetch origin
git checkout "$BASE_BRANCH"
git pull --ff-only origin "$BASE_BRANCH"

info "Merging $FEATURE_BRANCH into $BASE_BRANCH"
git merge --no-ff "$FEATURE_BRANCH" -m "merge: Issue #8 Linux collector foundation"

info "Pushing $BASE_BRANCH"
git push origin "$BASE_BRANCH"

if git rev-parse "$TAG" >/dev/null 2>&1; then
  fail "Tag '$TAG' already exists. Set another tag, for example: TAG=v4.0.0-beta.7 ./merge-issue-8.sh"
fi

info "Creating annotated tag $TAG"
git tag -a "$TAG" -m "$TAG_MESSAGE"
git push origin "$TAG"

info "Completed successfully"
printf '\nMerged branch: %s\nBase branch: %s\nTag: %s\n' "$FEATURE_BRANCH" "$BASE_BRANCH" "$TAG"
printf '\nYou are now on branch: %s\n' "$(git branch --show-current)"
