#!/usr/bin/env bash
# Synchronize the filtered OpenAPI MCP Server history from AWS Labs.
set -euo pipefail

readonly DEFAULT_UPSTREAM_URL='https://github.com/awslabs/mcp.git'
readonly DEFAULT_UPSTREAM_BRANCH='main'
readonly DEFAULT_UPSTREAM_PATH='src/openapi-mcp-server'
readonly DEFAULT_FORK_BRANCH='main'
readonly SYNC_COMMIT_MESSAGE='chore(sync): update from awslabs/mcp'

upstream_url="${UPSTREAM_URL:-$DEFAULT_UPSTREAM_URL}"
upstream_branch="${UPSTREAM_BRANCH:-$DEFAULT_UPSTREAM_BRANCH}"
upstream_path="${UPSTREAM_PATH:-$DEFAULT_UPSTREAM_PATH}"
fork_branch="${FORK_BRANCH:-$DEFAULT_FORK_BRANCH}"
repository_root=$(git rev-parse --show-toplevel)

fail() {
    printf 'sync-upstream: %s\n' "$1" >&2
    exit 1
}

command -v git >/dev/null 2>&1 || fail 'git is required'
command -v git-filter-repo >/dev/null 2>&1 || fail 'git-filter-repo is required; install it before synchronizing'

cd "$repository_root"
current_branch=$(git branch --show-current)
[[ "$current_branch" == "$fork_branch" ]] || fail "must run on $fork_branch (current: $current_branch)"
git diff --quiet && git diff --cached --quiet || fail 'working tree must be clean'
git rev-parse --verify -q MERGE_HEAD >/dev/null && fail 'finish or abort the existing merge first'

temporary_directory=$(mktemp -d "${TMPDIR:-/tmp}/openapi-mcp-sync.XXXXXX")
cleanup() {
    rm -rf "$temporary_directory"
}
trap cleanup EXIT

filtered_repository="$temporary_directory/upstream"
printf 'Fetching %s (%s)\n' "$upstream_url" "$upstream_branch"
git clone --quiet --branch "$upstream_branch" --single-branch "$upstream_url" "$filtered_repository" \
    || fail 'unable to clone the upstream repository'

git -C "$filtered_repository" filter-repo --force --subdirectory-filter "$upstream_path" \
    || fail "unable to filter upstream path: $upstream_path"

if git remote get-url upstream >/dev/null 2>&1; then
    configured_upstream=$(git remote get-url upstream)
    [[ "$configured_upstream" == "$upstream_url" ]] ||
        fail "remote upstream points to $configured_upstream, expected $upstream_url"
else
    git remote add upstream "$upstream_url"
fi

git fetch --no-tags "$filtered_repository" \
    "$upstream_branch:refs/remotes/upstream/$upstream_branch" \
    || fail 'unable to update the filtered upstream reference'

if git merge --no-ff --no-commit --allow-unrelated-histories \
    "refs/remotes/upstream/$upstream_branch"; then
    if git diff --cached --quiet; then
        git merge --abort >/dev/null 2>&1 || true
        printf 'Upstream is already synchronized; no commit created.\n'
        exit 0
    fi
    git commit -m "$SYNC_COMMIT_MESSAGE"
    printf 'Created synchronization commit: %s\n' "$SYNC_COMMIT_MESSAGE"
else
    git merge --abort >/dev/null 2>&1 || true
    fail 'merge conflict detected; local changes were preserved and the merge was aborted'
fi
