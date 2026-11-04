#!/usr/bin/env bash
# The names check for words outside the tree (AGENTS.md, Where CI runs). `commits` reads the commits a push or pull request
# brings, their messages, authors and committers. `pull-request` reads a pull request's title, body and branch as they are now,
# from the API and not from the event, because a re-run reuses the event, and an edit that removed a name could then never pass.
# A run's log is public, so this says where a name is and never the name or the line. Words from a pull request are only ever
# data here: they reach grep through a file and never the shell. Anything it cannot read fails the check, so that a check that
# did not run never passes.
set -u

list=$(mktemp)
words=$(mktemp)
trap 'rm -f "$list" "$words"' EXIT

printf '%s\n' "${FORBIDDEN_NAMES:-}" | tr -d '\r' | sed '/^[[:space:]]*$/d' > "$list"
if [ ! -s "$list" ]; then
  echo "::error::The FORBIDDEN_NAMES secret holds no names. The owner sets it (AGENTS.md, Where CI runs)."
  exit 1
fi

failed=0

# grep's status: 0 a name, 1 none, anything else the check did not run.
holds_a_name() {
  grep -a -q -i -F -f "$list" "$words"
  case $? in
    0) return 0 ;;
    1) return 1 ;;
    *) echo "::error::grep failed, so the names check did not run."; failed=1; return 1 ;;
  esac
}

read_event() {
  if ! jq -r "$1" "$GITHUB_EVENT_PATH"; then
    echo "::error::The event could not be read, so the names check did not run." >&2
    return 1
  fi
}

check_commits() {
  local from to shas sha
  case "$GITHUB_EVENT_NAME" in
    pull_request)
      from=$(read_event '.pull_request.base.sha') || { failed=1; return; }
      to=$(read_event '.pull_request.head.sha') || { failed=1; return; }
      ;;
    push)
      from=$(read_event '.before') || { failed=1; return; }
      to=$(read_event '.after') || { failed=1; return; }
      ;;
    *)
      echo "::error::The names check does not read the commits of a $GITHUB_EVENT_NAME event."
      failed=1
      return
      ;;
  esac
  if ! [[ "$from" =~ ^[0-9a-f]{40,64}$ && "$to" =~ ^[0-9a-f]{40,64}$ ]]; then
    echo "::error::The event names no commits to read, so the names check did not run."
    failed=1
    return
  fi
  # A push with no earlier commit (the first push of a branch) has only its own to read.
  if [[ "$from" =~ ^0+$ ]]; then
    shas=$(git rev-list -1 "$to") || { echo "::error::Git could not read the commits, so the names check did not run."; failed=1; return; }
  else
    shas=$(git rev-list "$from..$to") || { echo "::error::Git could not read the commits, so the names check did not run."; failed=1; return; }
  fi
  for sha in $shas; do
    if ! git log -1 --format='%an%n%ae%n%cn%n%ce%n%B' "$sha" > "$words"; then
      echo "::error::Git could not read commit $sha, so the names check did not run."
      failed=1
    elif holds_a_name; then
      echo "::error::Commit $sha holds a name that must not appear in this repository, in its message, author or committer. Reword it. Only the owner can read the list."
      failed=1
    fi
  done
}

check_pull_request() {
  local number
  [ "$GITHUB_EVENT_NAME" = pull_request ] || return
  number=$(read_event '.pull_request.number') || { failed=1; return; }
  if ! [[ "$number" =~ ^[0-9]+$ ]] || ! gh api "repos/$GITHUB_REPOSITORY/pulls/$number" --jq '.title, (.body // ""), .head.ref, .head.label' > "$words" 2> /dev/null; then
    echo "::error::The pull request could not be read, so the names check did not run. Re-run the job."
    failed=1
    return
  fi
  if holds_a_name; then
    echo "::error::This pull request's title, body or branch holds a name that must not appear in this repository. Edit it, and delete the earlier text from its edit history. Only the owner can read the list."
    failed=1
  fi
}

for mode in "$@"; do
  case "$mode" in
    commits) check_commits ;;
    pull-request) check_pull_request ;;
    *) echo "::error::names_in_words.sh does not know '$mode'."; failed=1 ;;
  esac
done

exit "$failed"
