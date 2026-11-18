#!/usr/bin/env bash
# deploy_tag.sh — build and start the production Kronk stack from a git tag
# instead of the working tree (sprint item M17, plan step 6), and say which
# tag is running.
#
# Usage:
#   scripts/deploy_tag.sh status            which tag (or working tree) each kronk container runs
#   scripts/deploy_tag.sh plan <tag>        export the tag, then show what `up` would do (changes nothing)
#   scripts/deploy_tag.sh deploy <tag>      export, build, start, restart nginx, verify
#   scripts/deploy_tag.sh seed <prod|dev> <tag or commit>
#                                           create a live config directory from <rev>:config/
#                                           (refuses when it exists; the migration runbook's step)
#
# Live config directories (sprint M21 step 2; docs/features/config-directory.md):
#   Prompts and tuning are read from a live directory per instance, outside
#   the repo and outside any release: $KRONK_LIVE_CONFIG/prod and .../dev
#   (default: a `kronk-config` directory next to this repo, beside
#   kronk-releases). Each is its own git repository with local commits only;
#   the /settings page commits there. A deploy:
#     - creates a live directory that does not exist, from the tag's
#       config/ (the shipped defaults), with a first commit naming the tag;
#     - never overwrites a live file. Keys the new code adds take the
#       loaders' built-in defaults until someone writes them;
#     - validates the production live directory against the tag's own
#       loaders (scripts/config_check.py) and refuses to deploy when it
#       fails, naming file and line. That is also the rollback check: an
#       older tag that does not know a key in the live files is refused,
#       with what to do;
#     - shows, for information, which shipped defaults differ from the live
#       files;
#     - links the release's config/ to the production live directory (the
#       shipped copy is kept as config.shipped/), so the containers mount the
#       live directory. The dev pair mounts the dev live directory from the
#       working tree's compose file.
#   `plan` shows all of this and changes nothing.
#
# How it works:
#   1. `git archive <tag>` is unpacked into $KRONK_RELEASES/<tag>/ (default:
#      a `kronk-releases` directory next to this repo). The live checkout is
#      never switched; agents can keep committing on their branch.
#   2. Runtime state that git does not carry is linked from the live
#      checkout into the release: data/, secrets/, .env, .env.dev, and the
#      searxng/ and ops/ directories (each holds a gitignored file —
#      settings.yml, hosts.json — inside a directory that is bind-mounted
#      whole, so the directory is linked rather than the file).
#   3. `docker compose -p kronk -f <release>/docker-compose.yml up -d --build`
#      replaces the kronk project's production containers. The project name
#      is the same as the working-tree stack's, so the containers are
#      replaced, not duplicated. No --profile: the dev pair is untouched.
#      Home Assistant and Music Assistant live in other compose files and
#      projects and are never named; the script refuses to run if the
#      release's service list includes either.
#   4. nginx is restarted (it pins the orchestrator's address at start —
#      docs/features/voice-pipeline.md, gotchas), then /api/status and the
#      /api/chat shim are checked.
#
# Which tag is running: compose stamps every container with the directory
# it was created from (label com.docker.compose.project.working_dir). A
# container created by this script carries $KRONK_RELEASES/<tag>; one
# created by a plain `docker compose up` in the repo carries the repo path.
# `status` reads those labels, so it reports what is actually running, not
# what was last requested.
#
# Behaviour that changes once production runs from a release: the files
# compose bind-mounts from the repo (nginx/nginx.conf, litellm/config.yaml,
# kronk-context.md, magicmirror/) come from the release, so editing them in
# the working tree no longer reaches production until the next tag is
# deployed. A later `docker compose up -d --build <svc>` from the working
# tree replaces that one service with a working-tree build; `status` shows it.
#
# Rollback: deploy the previous tag. Release directories are kept.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASES="${KRONK_RELEASES:-$(dirname "$REPO")/kronk-releases}"
LIVE_ROOT="${KRONK_LIVE_CONFIG:-$(dirname "$REPO")/kronk-config}"
# The compose project. Overridable only so tests can run this script against
# a scratch project with docker stubbed; production is `kronk`.
PROJECT="${KRONK_COMPOSE_PROJECT:-kronk}"
# The interpreter that runs the tag's loaders for validation: the test
# venv has the same pinned pydantic and YAML libraries as the images.
CHECK_PY="${KRONK_CHECK_PYTHON:-$REPO/tests/.venv/bin/python}"
FORBIDDEN=(homeassistant music-assistant-server)
LINKS=(data secrets .env .env.dev searxng ops)

die() { echo "deploy_tag: $*" >&2; exit 1; }

live_dir() { echo "$LIVE_ROOT/$1"; }

# git on a live directory: no system or user config, no hooks, a fixed identity.
live_git() {
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$1" \
        -c user.name="kronk deploy_tag" -c user.email="deploy_tag@kronk.invalid" \
        -c commit.gpgsign=false -c core.hooksPath=/dev/null "${@:2}"
}

describe_live() {
    local inst=$1 dir head
    dir=$(live_dir "$inst")
    if [ ! -e "$dir" ]; then
        echo "$inst: $dir (absent)"
    elif head=$(live_git "$dir" log -1 --format='%h %ad "%s"' --date=format:'%Y-%m-%d %H:%M' 2>/dev/null); then
        echo "$inst: $dir (HEAD $head)"
    else
        echo "$inst: $dir (exists, but is not a usable git repository: editing on /settings is off)"
    fi
}

seed() {
    local inst=$1 rev=$2 dir commit tmp
    case "$inst" in prod|dev) ;; *) die "usage: $0 seed prod|dev <tag or commit>" ;; esac
    dir=$(live_dir "$inst")
    [ ! -e "$dir" ] && [ ! -L "$dir" ] || die "$dir exists; a live directory is never overwritten"
    commit=$(git -C "$REPO" rev-parse -q --verify "$rev^{commit}") || die "no such tag or commit: $rev"
    git -C "$REPO" cat-file -e "$commit:config" 2>/dev/null \
        || die "$rev has no config/ (older than M21 step 1); nothing to seed"
    mkdir -p "$LIVE_ROOT"
    tmp="$dir.partial"
    rm -rf "${tmp:?}"
    mkdir -p "$tmp"
    git -C "$REPO" archive --format=tar "$commit" config | tar -x -C "$tmp" --strip-components=1
    live_git "$tmp" init -q -b main
    live_git "$tmp" add -A
    live_git "$tmp" commit -q -m "Seed from $rev ($commit)" \
        -m "The shipped defaults of $rev: the repo's config/ at $commit, byte for byte. Created by scripts/deploy_tag.sh."
    mv "$tmp" "$dir"
    echo "seeded $inst live config $dir from $rev ($commit)"
}

# validate <release dir> <live dir>: the release's own loaders on the live files.
validate() {
    [ -x "$CHECK_PY" ] || die "cannot validate: $CHECK_PY is missing (set KRONK_CHECK_PYTHON, or create tests/.venv)"
    "$CHECK_PY" "$REPO/scripts/config_check.py" --code "$1" --dir "$2"
}

# shipped_diff <shipped config dir> <live dir>: which shipped defaults differ, for information.
shipped_diff() {
    local out
    out=$(diff -ru --exclude=.git "$1" "$2" 2>&1) && { echo "(none: the live files equal the shipped defaults)"; return 0; }
    echo "$out" | head -200
    [ "$(echo "$out" | wc -l)" -le 200 ] || echo "... (cut at 200 lines; run: diff -ru --exclude=.git $1 $2)"
}

# The release's shipped config/ (a real directory, or config.shipped/ once linked).
shipped_of() {
    if [ -d "$1/config.shipped" ]; then echo "$1/config.shipped"; else echo "$1/config"; fi
}

link_config() {
    local dir=$1 live=$2
    if [ -L "$dir/config" ]; then
        [ "$(readlink "$dir/config")" = "$live" ] || die "$dir/config links to $(readlink "$dir/config"), not $live"
        return 0
    fi
    [ ! -e "$dir/config.shipped" ] || die "$dir has both config/ and config.shipped/; move one aside first"
    mv "$dir/config" "$dir/config.shipped"
    ln -s "$live" "$dir/config"
    echo "linked $dir/config -> $live (shipped copy kept in config.shipped/)"
}

# config_report <tag> <release dir>: the live directories, the validation, the
# shipped-default differences. Returns 1 when production's live files would
# be refused.
config_report() {
    local tag=$1 dir=$2 prod shipped rc=0
    prod=$(live_dir prod)
    shipped=$(shipped_of "$dir")
    echo "--- live config directories"
    describe_live prod
    describe_live dev
    [ -e "$prod" ] || echo "    (deploy seeds prod from $tag:config/)"
    [ -e "$(live_dir dev)" ] || echo "    (deploy seeds dev from $tag:config/)"
    echo "--- production live config against $tag's schema"
    if [ -e "$prod" ]; then
        validate "$dir" "$prod" || rc=1
        if [ -d "$shipped" ]; then
            echo "--- shipped defaults of $tag that differ from the production live files (information)"
            shipped_diff "$shipped" "$prod"
        fi
    elif [ -d "$shipped" ]; then
        validate "$dir" "$shipped" || rc=1
    fi
    [ -d "$shipped" ] || echo "    ($tag ships no config/: older than M21 step 1; nothing is seeded or linked)"
    return $rc
}

refused() {
    local tag=$1 prod
    prod=$(live_dir prod)
    die "refused: the production live config does not validate against $tag's schema (problems above). Nothing was changed. To deploy $tag, make the live files valid for it: see the edits with 'git -C $prod log -p', revert the ones $tag does not accept ('git -C $prod revert <commit>', or edit and commit by hand), then run '$0 plan $tag' again"
}

status() {
    local rows
    rows=$(docker ps --filter "label=com.docker.compose.project=$PROJECT" \
        --format '{{.Names}}|{{.Label "com.docker.compose.service"}}|{{.Label "com.docker.compose.project.working_dir"}}|{{.RunningFor}}')
    [ -n "$rows" ] || die "no running containers in compose project '$PROJECT'"
    printf '%-28s %-18s %-34s %s\n' CONTAINER SERVICE RUNNING_FROM CREATED
    while IFS='|' read -r name svc wd age; do
        local from
        if [ "$wd" = "$REPO" ]; then
            from="working tree"
        elif [ "$(dirname "$wd")" = "$RELEASES" ] && [ -f "$wd/.kronk-release" ]; then
            from="tag $(sed -n 's/^tag=//p' "$wd/.kronk-release")"
        else
            from="other: $wd"
        fi
        printf '%-28s %-18s %-34s %s\n' "$name" "$svc" "$from" "$age"
    done <<< "$rows"
}

export_release() {
    local tag=$1 dir=$2 commit
    git -C "$REPO" rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "no such tag: $tag"
    commit=$(git -C "$REPO" rev-parse "$tag^{commit}")
    if [ -d "$dir" ]; then
        grep -qx "commit=$commit" "$dir/.kronk-release" 2>/dev/null \
            || die "$dir exists but is not tag $tag at $commit; move it aside first"
        echo "release $tag already exported at $dir"
    else
        mkdir -p "$RELEASES"
        local tmp="$dir.partial"
        rm -rf "$tmp"; mkdir -p "$tmp"
        git -C "$REPO" archive --format=tar "$commit" | tar -x -C "$tmp"
        printf 'tag=%s\ncommit=%s\nexported=%s\n' "$tag" "$commit" "$(date -Is)" > "$tmp/.kronk-release"
        mv "$tmp" "$dir"
        echo "exported $tag ($commit) to $dir"
    fi
    # Runtime state from the live checkout. A tracked copy in the release
    # (searxng/, ops/ hold examples) is replaced by the link to the live one.
    for p in "${LINKS[@]}"; do
        [ -e "$REPO/$p" ] || continue
        if [ -L "$dir/$p" ]; then continue; fi
        rm -rf "${dir:?}/$p"
        ln -s "$REPO/$p" "$dir/$p"
    done
}

compose() { KRONK_RELEASE="$tag" docker compose -p "$PROJECT" --project-directory "$1" -f "$1/docker-compose.yml" "${@:2}"; }

guard() {
    local dir=$1 svcs
    svcs=$(compose "$dir" config --services)
    for f in "${FORBIDDEN[@]}"; do
        if grep -qx "$f" <<< "$svcs" || compose "$dir" config | grep -q "container_name: $f\$"; then
            die "release $dir defines $f; this script never deploys Home Assistant or Music Assistant"
        fi
    done
}

verify() {
    local ok=1 ua=()
    # A caller may name the User-Agent of these checks (KRONK_CLIENT_UA): the
    # upgrade run does, so its own /api/chat check is not read as a household
    # request (scripts/upgrades.py, house_busy).
    [ -z "${KRONK_CLIENT_UA:-}" ] || ua=(-A "$KRONK_CLIENT_UA")
    curl -sf "${ua[@]}" -o /dev/null --max-time 10 http://localhost/api/status && echo "ok   /api/status" \
        || { echo "FAIL /api/status"; ok=0; }
    if curl -sf "${ua[@]}" --max-time 60 -H 'Content-Type: application/json' http://localhost/api/chat \
        -d '{"model":"kronk:latest","stream":false,"messages":[{"role":"user","content":"What time is it?"}]}' \
        | grep -q '"content"'; then echo "ok   /api/chat (shim)"; else echo "FAIL /api/chat (shim)"; ok=0; fi
    [ "$ok" = 1 ] || die "deployed, but verification failed; roll back by deploying the previous tag"
}

# The upgrade run (scripts/upgrades.py, sprint M18) holds an flock on its
# lock file while it works; a deploy by anyone else in that time would race
# it, so `deploy` refuses and says who holds it. The run's own deploys carry
# the token it wrote into the file (KRONK_UPGRADE_TOKEN).
#
# With the lock free, an unfinished intent record in the status file means a
# run died inside a component (sprint M18 fix 2, G3): its recovery compares
# what runs with the record, and a deploy by hand in between is exactly what
# it then cannot account for. `deploy` refuses until the record is dealt
# with, by the recovery or by `ack`.
UPGRADE_LOCK="${KRONK_UPGRADE_STATE:-$REPO/data/upgrades}/lock"
UPGRADE_STATUS="${KRONK_UPGRADE_STATE:-$REPO/data/upgrades}/status.json"
upgrade_lock_check() {
    if [ -e "$UPGRADE_LOCK" ] && ! flock -n "$UPGRADE_LOCK" true 2>/dev/null; then
        if [ -n "${KRONK_UPGRADE_TOKEN:-}" ] && grep -qxF "token=$KRONK_UPGRADE_TOKEN" "$UPGRADE_LOCK"; then
            return 0        # the run itself
        fi
        die "refused: an upgrade run holds $UPGRADE_LOCK ($(grep -v '^token=' "$UPGRADE_LOCK" | tr '\n' ' ')); wait for it to finish (journalctl --user -u kronk-upgrade-minor.service -f)"
    fi
    [ -e "$UPGRADE_STATUS" ] || return 0
    local rec
    rec=$(python3 -I -c 'import json, sys
d = json.loads(open(sys.argv[1], encoding="utf-8").read())
it = d.get("intent")
if it:
    print("%s %s -> %s, step %s, run %s" % tuple(it.get(k) for k in ("component", "from", "to", "step", "run"))
          + ("; blocked (%s)" % it["blocked"] if it.get("blocked") else ""))' \
        "$UPGRADE_STATUS" 2>&1) \
        || die "refused: cannot tell whether an upgrade run was interrupted: $UPGRADE_STATUS cannot be read (${rec##*$'\n'}); python3 scripts/upgrades.py status says why"
    case "$rec" in *"; blocked ("*) die "refused: an upgrade run was interrupted and left blocked ($rec; the record is in $UPGRADE_STATUS). No recovery acts on a blocked record: put it right by hand, then python3 scripts/upgrades.py ack" ;; esac
    [ -z "$rec" ] || die "refused: an upgrade run was interrupted ($rec; the record is in $UPGRADE_STATUS). Its recovery compares what runs with that record, so nothing is deployed by hand until it is dealt with. Either let it recover now: python3 scripts/upgrades.py minor --ignore-window (scripts/upgrade_minor.sh --ignore-window); or drop the record and take over by hand: python3 scripts/upgrades.py ack"
}

cmd=${1:-}; tag=${2:-}
[ "$cmd" != deploy ] || upgrade_lock_check
case "$cmd" in
    status) status ;;
    seed) seed "${2:-}" "${3:-}" ;;
    plan|deploy)
        [ -n "$tag" ] || die "usage: $0 $cmd <tag>"
        dir="$RELEASES/$tag"
        export_release "$tag" "$dir"
        guard "$dir"
        ok=1
        config_report "$tag" "$dir" || ok=0
        if [ "$cmd" = plan ]; then
            [ "$ok" = 1 ] || echo "!!! deploy $tag would be REFUSED: the production live config does not validate (above)"
            echo "--- docker compose up -d --dry-run (nothing is built, started or stopped)"
            # compose pads its progress lines; keep one line per container action.
            compose "$dir" up -d --no-build --dry-run 2>&1 | sed 's/ *$//' \
                | grep -E '^ *Container [a-z][^ ]* (Recreate|Create|Running)$|rror' || true
            echo "--- run '$0 deploy $tag' to apply"
            exit 0
        fi
        [ "$ok" = 1 ] || refused "$tag"
        if [ -d "$(shipped_of "$dir")" ]; then
            [ -e "$(live_dir prod)" ] || seed prod "$tag"
            [ -e "$(live_dir dev)" ] || seed dev "$tag"
            link_config "$dir" "$(live_dir prod)"
        fi
        compose "$dir" up -d --build
        compose "$dir" restart nginx
        sleep 3
        verify
        status
        ;;
    *) sed -n '2,15p' "$0"; exit 2 ;;
esac
