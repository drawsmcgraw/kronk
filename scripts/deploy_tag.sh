#!/usr/bin/env bash
# deploy_tag.sh — build and start the production Kronk stack from a git tag
# instead of the working tree (sprint item M17, plan step 6), and say which
# tag is running.
#
# Usage:
#   scripts/deploy_tag.sh status            which tag (or working tree) each kronk container runs
#   scripts/deploy_tag.sh plan <tag>        export the tag, then show what `up` would do (changes nothing)
#   scripts/deploy_tag.sh deploy <tag>      export, build, start, restart nginx, verify
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
PROJECT=kronk
FORBIDDEN=(homeassistant music-assistant-server)
LINKS=(data secrets .env .env.dev searxng ops)

die() { echo "deploy_tag: $*" >&2; exit 1; }

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
    # retire_calc builds from ../retirement-calc relative to the compose
    # file; give the release the same sibling (removed with retire_calc in M1).
    if [ -d "$(dirname "$REPO")/retirement-calc" ] && [ ! -e "$RELEASES/retirement-calc" ]; then
        ln -s "$(dirname "$REPO")/retirement-calc" "$RELEASES/retirement-calc"
    fi
}

compose() { docker compose -p "$PROJECT" --project-directory "$1" -f "$1/docker-compose.yml" "${@:2}"; }

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
    local ok=1
    curl -sf -o /dev/null --max-time 10 http://localhost/api/status && echo "ok   /api/status" \
        || { echo "FAIL /api/status"; ok=0; }
    if curl -sf --max-time 60 -H 'Content-Type: application/json' http://localhost/api/chat \
        -d '{"model":"kronk:latest","stream":false,"messages":[{"role":"user","content":"What time is it?"}]}' \
        | grep -q '"content"'; then echo "ok   /api/chat (shim)"; else echo "FAIL /api/chat (shim)"; ok=0; fi
    [ "$ok" = 1 ] || die "deployed, but verification failed; roll back by deploying the previous tag"
}

cmd=${1:-}; tag=${2:-}
case "$cmd" in
    status) status ;;
    plan|deploy)
        [ -n "$tag" ] || die "usage: $0 $cmd <tag>"
        dir="$RELEASES/$tag"
        export_release "$tag" "$dir"
        guard "$dir"
        if [ "$cmd" = plan ]; then
            echo "--- docker compose up -d --dry-run (nothing is built, started or stopped)"
            # compose pads its progress lines; keep one line per container action.
            compose "$dir" up -d --no-build --dry-run 2>&1 | sed 's/ *$//' \
                | grep -E '^ *Container [a-z][^ ]* (Recreate|Create|Running)$|rror' || true
            echo "--- run '$0 deploy $tag' to apply"
            exit 0
        fi
        compose "$dir" up -d --build
        compose "$dir" restart nginx
        sleep 3
        verify
        status
        ;;
    *) sed -n '2,12p' "$0"; exit 2 ;;
esac
