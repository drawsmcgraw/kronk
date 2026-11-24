#!/usr/bin/env bash
# build_rocm_libs.sh — extract the ROCm runtime libraries that Whisper
# (CTranslate2) and Talkie's llama-server load, from the ROCm image both were
# built in, into a directory with a manifest (work item M26).
#
# Usage:
#   scripts/build_rocm_libs.sh build <dir> [--image <ref>]
#                     extract into <dir>/lib and write <dir>/MANIFEST;
#                     <dir> is resolved first (symlinks, "..", a trailing
#                     slash); refuses one that exists and is not empty, or
#                     whose parent does not exist
#   scripts/build_rocm_libs.sh verify <dir>
#                     check <dir>/lib against <dir>/MANIFEST; exit 1 on a
#                     missing, extra or changed entry, a manifest written
#                     for another image, or an entry whose name or link
#                     target holds whitespace or a control character
#   scripts/build_rocm_libs.sh manifest <libdir>
#                     print the manifest a directory would have (no docker)
#   scripts/build_rocm_libs.sh list
#                     print the fixed list of members taken from the image
#
#   The real directory is ~/services/rocm-7.2 (the units name
#   %h/services/rocm-7.2/lib):
#     scripts/build_rocm_libs.sh build ~/services/rocm-7.2
#     scripts/build_rocm_libs.sh verify ~/services/rocm-7.2
#
# Where the libraries come from:
#   CTranslate2 (~/services/wyoming-whisper/build/build-in-container.sh) and
#   Talkie's server (pai_workspace/llama-cpp/Dockerfile.talkie-hip) were both
#   compiled inside rocm/dev-ubuntu-24.04:7.2-complete. This script takes the
#   ROCm libraries they link from that same image, run by digest, so the
#   programs load the build they were compiled against. Before M26 they
#   loaded another project's rebuild of the same versions from Ollama's
#   install directory.
#
#   The list below is fixed: the ten ROCm sonames the programs need (each as
#   its versioned file plus its soname symlink) and rocBLAS's kernel data,
#   rocblas/library. It was read from `readelf -d` (docs/sprint/M26-review.md,
#   point 9). That it is enough is checked by host `ldd` and a device
#   enumeration with the units' environment, not computed here.
#
#   libdrm, libdrm_amdgpu, libelf and libnuma are not taken: the host's own
#   copies are used (they export every symbol the image's libhsa-runtime64
#   imports).
#
#   hipblaslt/library (4.5 GB in the image) is left out on purpose. The units
#   ran without it before M26 (Ollama's directory has none), so libhipblaslt
#   is loaded with its kernel data absent, as before. Adding it could change
#   which GEMM path rocBLAS takes, a second change on top of the change of
#   build. The manifest records the omission.
#
# How it extracts (nothing is mounted, nothing is pulled):
#   docker run --rm --network none --pull never --read-only --cap-drop ALL \
#     --entrypoint tar <image@digest> -C /opt/rocm-7.2.0/lib -cf - <list>
#   The archive leaves the container on stdout and is unpacked here with the
#   image's modes and the caller as owner (Docker is rootful on this host; a
#   bind mount would leave root's files). GNU tar drops leading "/" and ".."
#   members. The result is checked against the list before the manifest is
#   written.
#
# The manifest (<dir>/MANIFEST, beside lib/ so lib/ holds only what was
# extracted and rocBLAS finds rocblas/library next to librocblas as in the
# image): a header (image digest, ROCm version, source path, the omission,
# totals), then one line per entry under lib/, sorted under LC_ALL=C:
#   <relative path> TAB <f|l|d> TAB <mode> TAB <size|-> TAB <sha256|link target|->
# No timestamp, host name, user or destination path, so two builds from the
# same image give byte-identical manifests.
#
# The image is local only (29.3 GB, created 2026-01-21). If it is pruned, a
# rebuild needs `docker pull rocm/dev-ubuntu-24.04@<IMAGE_DIGEST>`, by
# digest, on purpose; this script never pulls.
#
# Pinning a new ROCm image, on purpose:
#   1. Pull the new image by digest and read its /opt/rocm/.info/version.
#   2. Edit ROCM_VERSION, IMAGE_DIGEST and LIBS below together (the versioned
#      file names change with the release; read them from the new image's
#      /opt/rocm-<version>/lib, and the sonames from `readelf -d` of
#      libctranslate2.so.4 and Talkie's libggml-hip.so.0).
#   3. Rebuild CTranslate2 and Talkie's server in the new image first: the
#      libraries should be the build the programs were compiled against.
#   4. Build into a new directory, verify it, and point the units at it.
#   The --image option does not pick an image; it only accepts the pinned
#   reference and refuses any other, so a tag or a stale digest fails loud.
#
# Exit status: 0 done; 1 verify found a difference, or the extraction did
# not match the list; 2 usage or a refusal (another image, a non-empty
# directory, run as root); 3 docker failed or the image is not on this host.

set -euo pipefail
umask 022

IMAGE_REPO="rocm/dev-ubuntu-24.04"
IMAGE_DIGEST="sha256:86e11093b4a7ec2a79b1b6701d10e840a6994f21c7e05929b51eb9be361c683a"
IMAGE="${IMAGE_REPO}@${IMAGE_DIGEST}"
ROCM_VERSION="7.2.0"
SRC="/opt/rocm-${ROCM_VERSION}/lib"
GFX="gfx1151"

# soname symlink, then the versioned file it points at.
LIBS=(
    libamd_comgr.so.3              libamd_comgr.so.3.0.0
    libamdhip64.so.7               libamdhip64.so.7.2.70200
    libhipblas.so.3                libhipblas.so.3.2.70200
    libhipblaslt.so.1              libhipblaslt.so.1.2.70200
    libhsa-runtime64.so.1          libhsa-runtime64.so.1.18.70200
    librocblas.so.5                librocblas.so.5.2.70200
    librocprofiler-register.so.0   librocprofiler-register.so.0.6.0
    librocroller.so.1              librocroller.so.1.0.0
    librocsolver.so.0              librocsolver.so.0.7.70200
    libroctx64.so.4                libroctx64.so.4.1.70200
)
DATA="rocblas/library"
OMITTED="hipblaslt/library"

die()  { echo "build_rocm_libs: $2" >&2; exit "$1"; }
usage() { sed -n '/^# Usage:/,/^# Where/{/^# Where/d;s/^# \{0,1\}//;p}' "$0" >&2; exit 2; }

list_members() {
    printf '%s\n' "${LIBS[@]}" "$DATA"
}

# One line per entry under $1, sorted, relative paths, no times or owners.
# The listing is read NUL-separated, so a name or a link target holding a
# newline or a tab cannot pose as another manifest line: such an entry is
# refused (exit 1) and named, escaped. errexit does not hold here when the
# caller tests the result (`got=$(manifest …) || die`), so every step
# checks its own status.
manifest_body() {
    local dir=$1 p t m s h
    [ -d "$dir" ] || die 2 "no such directory: $dir"
    (
        cd -- "$dir" || exit 1
        find . -mindepth 1 -printf '%P\0%y\0%m\0%s\0' |
        while IFS= read -r -d '' p && IFS= read -r -d '' t &&
              IFS= read -r -d '' m && IFS= read -r -d '' s; do
            case $p in *[[:space:]]* | *[[:cntrl:]]*)
                printf 'build_rocm_libs: refusing entry %q: its name holds whitespace or a control character\n' "$p" >&2
                exit 1 ;;
            esac
            case $t in
                f) h=$(sha256sum -- "$p") || exit 1; h=${h%% *} ;;
                # The trailing "x" keeps a target's own trailing newlines,
                # which $(...) would strip.
                l) h=$(readlink -- "$p" && echo x) || exit 1; h=${h%$'\nx'}; s=-
                   case $h in *[[:space:]]* | *[[:cntrl:]]*)
                       printf 'build_rocm_libs: refusing entry %q: its link target %q holds whitespace or a control character\n' "$p" "$h" >&2
                       exit 1 ;;
                   esac ;;
                d) h=-; s=- ;;
                *) printf 'build_rocm_libs: refusing entry %q: unexpected file type %q\n' "$p" "$t" >&2; exit 1 ;;
            esac
            printf '%s\t%s\t%s\t%s\t%s\n' "$p" "$t" "$m" "$s" "$h"
        done
    ) | LC_ALL=C sort
}

# The whole manifest for $1: the header from the pinned constants, then the body.
manifest() {
    local body files links dirs bytes
    body=$(manifest_body "$1") || return 1
    files=$(awk -F'\t' '$2=="f"' <<<"$body" | wc -l)
    links=$(awk -F'\t' '$2=="l"' <<<"$body" | wc -l)
    dirs=$(awk -F'\t' '$2=="d"' <<<"$body" | wc -l)
    bytes=$(awk -F'\t' '$2=="f"{n+=$4} END{printf "%d", n}' <<<"$body")
    printf '# ROCm runtime libraries for Whisper (CTranslate2) and Talkie (llama-server).\n'
    printf '# Written by scripts/build_rocm_libs.sh; check with: scripts/build_rocm_libs.sh verify <dir>\n'
    printf 'image\t%s\n' "$IMAGE"
    printf 'rocm-version\t%s\n' "$ROCM_VERSION"
    printf 'source\t%s\n' "$SRC"
    printf 'omitted\t%s\t%s\n' "$OMITTED" "left out on purpose: the units ran without it before M26"
    printf 'totals\tfiles=%d links=%d dirs=%d bytes=%d\n' "$((files))" "$((links))" "$((dirs))" "$((bytes))"
    printf 'entries\n'
    printf '%s\n' "$body"
}

check_image_ref() {
    [ "$1" = "$IMAGE" ] && return 0
    die 2 "refusing image '$1': this script runs only $IMAGE.
To use another ROCm image, pin it on purpose: edit ROCM_VERSION, IMAGE_DIGEST
and LIBS in $0 together (see 'Pinning a new ROCm image' in its header)."
}

# Checks that the extracted tree is exactly the list.
check_extracted() {
    local lib=$1 i link file target want got
    for ((i = 0; i < ${#LIBS[@]}; i += 2)); do
        link=${LIBS[i]} file=${LIBS[i+1]}
        [ -f "$lib/$file" ] && [ ! -L "$lib/$file" ] || die 1 "not a regular file after extraction: $file"
        [ -L "$lib/$link" ] || die 1 "not a symlink after extraction: $link"
        target=$(readlink -- "$lib/$link")
        [ "$target" = "$file" ] || die 1 "$link points at '$target', expected '$file'"
    done
    want=$( { printf '%s\n' "${LIBS[@]}"; echo "${DATA%%/*}"; } | LC_ALL=C sort)
    got=$(cd "$lib" && find . -mindepth 1 -maxdepth 1 -printf '%P\n' | LC_ALL=C sort)
    [ "$want" = "$got" ] || die 1 "top level of $lib is not the list:
$(diff <(echo "$want") <(echo "$got") || true)"
    got=$(cd "$lib/${DATA%%/*}" && find . -mindepth 1 -maxdepth 1 -printf '%P\n')
    [ "$got" = "${DATA#*/}" ] || die 1 "${DATA%%/*}/ holds more than ${DATA#*/}: $got"
    [ -f "$lib/$DATA/TensileLibrary_lazy_${GFX}.dat" ] || die 1 "no $DATA/TensileLibrary_lazy_${GFX}.dat"
    [ -z "$(find "$lib" -path "*hipblaslt*" -type d)" ] || die 1 "a hipblaslt directory was extracted"
    [ -z "$(find "$lib" -type l -lname '*/*')" ] || die 1 "a symlink points outside its directory"
}

cmd_build() {
    local dir="" image="$IMAGE" version tmp
    while [ $# -gt 0 ]; do
        case $1 in
            --image) [ $# -ge 2 ] || usage; image=$2; shift 2 ;;
            -*) usage ;;
            *) [ -z "$dir" ] || usage; dir=$1; shift ;;
        esac
    done
    [ -n "$dir" ] || usage
    check_image_ref "$image"
    [ "$(id -u)" != 0 ] || die 2 "run as the user who owns the services, not root (the files are the caller's)"
    # Resolve first, then test: a symlink to a non-empty directory, or
    # "<dir>/new/..", must be refused like the directory itself.
    local given=$dir parent first
    dir=$(realpath -m -- "$given") || die 2 "cannot resolve $given"
    [ "$dir" != / ] || die 2 "refusing /"
    parent=$(dirname -- "$dir")
    [ -d "$parent" ] || die 2 "refusing $given: its parent directory $parent does not exist (create it first)"
    if [ -e "$dir" ]; then
        [ -d "$dir" ] || die 2 "refusing $given: $dir exists and is not a directory"
        first=$(find "$dir" -mindepth 1 -maxdepth 1 -print -quit) ||
            die 2 "refusing $given: cannot read $dir"
        [ -z "$first" ] ||
            die 2 "refusing $given: $dir exists and is not empty (build into a new directory)"
    fi

    command -v docker >/dev/null || die 3 "docker not found"
    local id
    id=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null) ||
        die 3 "$IMAGE is not on this host. This script never pulls; to fetch it on purpose: docker pull $IMAGE"
    [ "$id" = "$IMAGE_DIGEST" ] || die 3 "local image id $id is not $IMAGE_DIGEST"

    local run=(docker run --rm --network none --pull never --read-only --cap-drop ALL
               --security-opt no-new-privileges)
    version=$("${run[@]}" --entrypoint cat "$IMAGE" "/opt/rocm-${ROCM_VERSION}/.info/version") ||
        die 3 "could not read the image's ROCm version"
    [ "$version" = "$ROCM_VERSION" ] || die 3 "image reports ROCm '$version', pinned $ROCM_VERSION"

    [ -d "$dir" ] || mkdir -- "$dir"
    mkdir -- "$dir/lib"
    echo "build_rocm_libs: extracting from $IMAGE into $dir/lib" >&2
    "${run[@]}" --entrypoint tar "$IMAGE" -C "$SRC" -cf - "${LIBS[@]}" "$DATA" |
        tar -x -p --no-same-owner -f - -C "$dir/lib" ||
        die 3 "extraction failed; $dir/lib is incomplete: remove $dir and run again"

    check_extracted "$dir/lib"
    tmp="$dir/.MANIFEST.tmp"
    manifest "$dir/lib" >"$tmp"
    mv -- "$tmp" "$dir/MANIFEST"
    echo "build_rocm_libs: wrote $dir/MANIFEST ($(sed -n 's/^totals\t//p' "$dir/MANIFEST"))" >&2
    cmd_verify "$dir"
}

cmd_verify() {
    [ $# -eq 1 ] || usage
    local dir=$1 want got
    [ -f "$dir/MANIFEST" ] || die 1 "no manifest: $dir/MANIFEST"
    [ -d "$dir/lib" ] || die 1 "no directory: $dir/lib"
    want=$(cat -- "$dir/MANIFEST")
    got=$(manifest "$dir/lib") || die 1 "could not read $dir/lib"
    if [ "$want" = "$got" ]; then
        echo "build_rocm_libs: $dir/lib matches its manifest ($(sed -n 's/^totals\t//p' "$dir/MANIFEST"))"
        return 0
    fi
    # Header lines (image, version, totals) and entries, reported apart.
    awk -F'\t' '
        FNR == 1 { part = 0 }
        /^entries$/ { part = 1; next }
        part == 0 && /^[^#]/ { key = $1; line = $0; if (FILENAME == ARGV[1]) hw[key] = line; else hg[key] = line; next }
        part == 1 { if (FILENAME == ARGV[1]) w[$1] = $0; else g[$1] = $0 }
        END {
            for (k in hw) if (hw[k] != hg[k]) print "header:  manifest says \"" hw[k] "\", directory/script give \"" hg[k] "\""
            for (k in hg) if (!(k in hw)) print "header:  not in manifest: " hg[k]
            for (p in w) if (!(p in g)) print "missing: " p
            for (p in g) if (!(p in w)) print "extra:   " p
            for (p in w) if ((p in g) && w[p] != g[p]) print "changed: " p
        }' <(printf '%s\n' "$want") <(printf '%s\n' "$got") | LC_ALL=C sort >&2
    echo "build_rocm_libs: $dir/lib does NOT match $dir/MANIFEST" >&2
    return 1
}

[ $# -ge 1 ] || usage
cmd=$1; shift
case $cmd in
    build)    cmd_build "$@" ;;
    verify)   cmd_verify "$@" ;;
    manifest) [ $# -eq 1 ] || usage; manifest "$1" ;;
    list)     [ $# -eq 0 ] || usage; list_members ;;
    -h|--help|help) usage ;;
    *) usage ;;
esac
