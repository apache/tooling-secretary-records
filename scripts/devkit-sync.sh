#!/bin/sh
# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
# Take the shared files from apache/tooling-devkit, and check that a repository still has them as the
# kit has them.
#
#   scripts/devkit-sync.sh <ref>     copy the files of the kit at <ref> here, and write that commit to
#                                    devkit.lock. Nothing is committed. <ref> is a branch, a tag or a full
#                                    commit id: git does not fetch an abbreviated one.
#   scripts/devkit-sync.sh --check   compare each file with the kit at the commit in devkit.lock. Prints
#                                    what differs and each override, and fails on drift, on an override
#                                    past its date, and where there is no usable devkit.lock.
#
# What the kit lists is in its devkit.files: `path`, or `source destination` where a file lives
# somewhere else here. This repository keeps what it names in DEVKIT_SKIP (devkit.conf, paths
# separated by spaces) as its own, and neither the sync nor the check touches it.
#
# devkit.lock is this repository's, and so is devkit.conf. The sync rewrites only the `commit` line of
# the first, and refuses a kit that lists either. The other lines of devkit.lock are kept:
#
#   commit 0123456789abcdef0123456789abcdef01234567
#   override scripts/e2e-container.sh tooling-service#12 until 2026-12-31
#
# An override says that this copy differs from the kit on purpose: with its issue, and the day it ends.
# The sync leaves an overridden file as it is, and the check lets it differ until that day.
#
# One file is not a byte copy: .claude/settings.json is the base of the kit with the entries this
# repository adds in .claude/settings.additions.json, if there is one. It may add to `permissions.allow`,
# `permissions.deny` and `permissions.ask`, and to nothing else, so the hook that registers the guard stays
# the kit's and a deny of the kit's stays. The merge is made by python3, inline below, and only where the
# additions file exists; the check compares the file with that merge.
#
# Where the kit comes from: DEVKIT_SOURCE, else its repository on GitHub. Nothing the kit holds is run
# here: the files are copied, and their paths are checked first. DEVKIT_TODAY (YYYY-MM-DD) and
# DEVKIT_SYNC_PYTHON are for the tests.
#
# This script is a kit file like the others, so the first copy of it is made by hand.

set -euf

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
conf="$repo_root/devkit.conf"
lock="$repo_root/devkit.lock"
kit_url=${DEVKIT_SOURCE:-https://github.com/apache/tooling-devkit.git}
settings_dst=.claude/settings.json
settings_additions=.claude/settings.additions.json

die() {
    echo "devkit-sync: $*" >&2
    exit 1
}

usage() {
    echo "usage: scripts/devkit-sync.sh <ref> | --check" >&2
    exit 2
}

# What devkit.conf gives $1: the first line that names it, read with sed and never run.
conf_value() {
    sed -n "s/^$1=//p" "$conf" 2>/dev/null | head -n 1
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Fetch the kit at $1 into the directory $2, and print the commit it is.
fetch_kit() {
    case $1 in
        '' | -* | *[!A-Za-z0-9._/~^@-]*) die "not a ref to fetch: '$1'" ;;
        *) : ;;
    esac
    git init -q "$2" || die "cannot make a directory to fetch the kit into"
    git -C "$2" fetch -q --depth 1 "$kit_url" "$1" 2> /dev/null || die "cannot fetch '$1' from $kit_url"
    git -C "$2" -c advice.detachedHead=false checkout -q FETCH_HEAD || die "cannot check out '$1'"
    git -C "$2" rev-parse HEAD
}

# What the kit at $1 lists, one `source destination` to a line. A path the sync must not write is
# refused: absolute, upward, inside .git, with an odd character, or this repository's own devkit.conf
# and devkit.lock.
manifest() {
    [ -f "$1/devkit.files" ] || die "the kit has no devkit.files"
    while IFS= read -r _line || [ -n "$_line" ]; do
        case $_line in '' | '#'*) continue ;; *) : ;; esac
        # shellcheck disable=SC2086 # the words of the line are the point; globbing is off (set -f)
        set -- $_line
        [ "$#" -le 2 ] || die "devkit.files: more than a source and a destination: $_line"
        _src=$1
        _dst=${2:-$1}
        for _path in "$_src" "$_dst"; do
            case $_path in
                /* | ../* | */../* | */.. | .. | .git | .git/* | */.git/* | devkit.conf | devkit.lock | .claude/settings.additions.json)
                    die "devkit.files lists a path the sync does not write: $_path"
                    ;;
                *[!A-Za-z0-9._/-]*) die "devkit.files lists a path with an odd character: $_path" ;;
                *) : ;;
            esac
        done
        printf '%s %s\n' "$_src" "$_dst"
    done < "$1/devkit.files"
}

# Whether this repository keeps $1 as its own: it is in DEVKIT_SKIP.
skipped() {
    for _kept in $(conf_value DEVKIT_SKIP); do
        [ "$_kept" = "$1" ] && return 0
    done
    return 1
}

# The `override` lines of devkit.lock, as `path issue day`. One that is not
# `override <path> <issue> until <YYYY-MM-DD>` fails the run.
overrides() {
    [ -f "$lock" ] || return 0
    while IFS= read -r _line || [ -n "$_line" ]; do
        case $_line in
            override\ *)
                # shellcheck disable=SC2086
                set -- $_line
                if [ "$#" -ne 5 ] || [ "$4" != until ]; then
                    die "devkit.lock: an override is 'override <path> <issue> until <YYYY-MM-DD>': $_line"
                fi
                printf '%s\n' "$5" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' ||
                    die "devkit.lock: the day an override ends is YYYY-MM-DD: $_line"
                printf '%s %s %s\n' "$2" "$3" "$5"
                ;;
            *) : ;;
        esac
    done < "$lock"
}

# Whether the files $1 and $2 are the same: the same bytes, and the same executable bit. git is here
# anyway, and cmp is not in every container.
same_file() {
    git diff --no-index --quiet -- "$1" "$2" > /dev/null 2>&1
}

# What a destination is made from: the file of the kit, except .claude/settings.json where there is a merge.
merged_settings=''
source_file() {
    if [ "$2" = "$settings_dst" ] && [ -n "$merged_settings" ]; then
        printf '%s\n' "$merged_settings"
    else
        printf '%s\n' "$work/kit/$1"
    fi
}

# The base of the kit and the additions of this repository, made into one file, when there are additions.
# Python reads both as data and prints the result; a service adds only to the three lists of permissions.
prepare_settings() {
    merged_settings=''
    [ -f "$repo_root/$settings_additions" ] || return 0
    _base=$(awk -v d="$settings_dst" '$2 == d { print $1; exit }' "$work/manifest")
    [ -n "$_base" ] || return 0
    skipped "$settings_dst" && return 0
    _python=${DEVKIT_SYNC_PYTHON:-python3}
    command -v "$_python" > /dev/null 2>&1 ||
        die "$settings_additions has to be merged into $settings_dst, which needs python3, and there is none"
    if ! "$_python" -I - "$work/kit/$_base" "$repo_root/$settings_additions" \
        > "$work/settings.merged" 2> "$work/settings.errors" << 'PYTHON'
import json
import sys

KEYS = ("allow", "deny", "ask")
base_path, additions_path = sys.argv[1], sys.argv[2]
problems = []


def read(path, what):
    try:
        with open(path, encoding="utf-8") as file:
            document = json.load(file)
    except (OSError, ValueError) as error:
        problems.append(f"{what} cannot be read: {error}")
        return None
    if not isinstance(document, dict):
        problems.append(f"{what} is a JSON object")
        return None
    return document


base = read(base_path, "the settings of the kit")
added = read(additions_path, "the file")
permissions = {}
if added is not None:
    problems += [f"{key} is not something a repository adds: only `permissions`" for key in sorted(set(added) - {"permissions"})]
    permissions = added.get("permissions", {})
    if not isinstance(permissions, dict):
        problems.append("permissions is an object")
        permissions = {}
    problems += [
        f"permissions.{key} is not something a repository adds: only {', '.join(KEYS)}" for key in sorted(set(permissions) - set(KEYS))
    ]
    for key in KEYS:
        entries = permissions.get(key, [])
        if not isinstance(entries, list) or not all(isinstance(entry, str) and entry.strip() for entry in entries):
            problems.append(f"permissions.{key} is a list of non-empty strings")
if problems:
    print("\n".join(problems), file=sys.stderr)
    sys.exit(1)
merged = base.setdefault("permissions", {})
for key in KEYS:
    entries = merged.setdefault(key, []) if permissions.get(key) else None
    for entry in permissions.get(key, []):
        if entry not in entries:
            entries.append(entry)
sys.stdout.reconfigure(encoding="utf-8")
sys.stdout.write(json.dumps(base, indent=2, ensure_ascii=False) + "\n")
PYTHON
    then
        { echo "devkit-sync: $settings_additions cannot be used:"; sed 's/^/  /' "$work/settings.errors"; } >&2
        exit 1
    fi
    chmod 644 "$work/settings.merged"
    merged_settings=$work/settings.merged
}

# Whether the overrides in the file $2 name the path $1.
overridden() {
    awk -v p="$1" '$1 == p { found = 1 } END { exit !found }' "$2"
}

# The commit of devkit.lock, which has to be one.
locked_commit() {
    [ -f "$lock" ] || die "there is no devkit.lock: run scripts/devkit-sync.sh <ref> first"
    _commit=$(sed -n 's/^commit //p' "$lock" | head -n 1)
    printf '%s\n' "$_commit" | grep -Eq '^[0-9a-f]{40}$' || die "devkit.lock has no commit line of 40 hex digits"
    printf '%s\n' "$_commit"
}

short() {
    printf '%.12s' "$1"
}

check() {
    _locked=$(locked_commit)
    fetch_kit "$_locked" "$work/kit" > /dev/null
    overrides > "$work/overrides"
    manifest "$work/kit" > "$work/manifest"
    prepare_settings
    _today=$(printf '%s' "${DEVKIT_TODAY:-$(date +%Y-%m-%d)}" | tr -d '-')
    _problems=0
    _checked=0
    while read -r _path _issue _day; do
        echo "override: $_path ($_issue) until $_day"
        if [ "$_today" -gt "$(printf '%s' "$_day" | tr -d '-')" ]; then
            echo "devkit-sync: the override of $_path ended on $_day" >&2
            _problems=$((_problems + 1))
        fi
    done < "$work/overrides"
    while read -r _src _dst; do
        skipped "$_dst" && continue
        _checked=$((_checked + 1))
        overridden "$_dst" "$work/overrides" && continue
        if [ ! -f "$repo_root/$_dst" ]; then
            echo "missing: $_dst" >&2
            _problems=$((_problems + 1))
        elif ! same_file "$(source_file "$_src" "$_dst")" "$repo_root/$_dst"; then
            if [ "$(source_file "$_src" "$_dst")" != "$work/kit/$_src" ]; then
                echo "drift: $_dst differs from the kit at $(short "$_locked") with $settings_additions" >&2
            else
                echo "drift: $_dst differs from the kit at $(short "$_locked")" >&2
            fi
            _problems=$((_problems + 1))
        fi
    done < "$work/manifest"
    if [ "$_problems" -ne 0 ]; then
        die "$_problems problem(s) in $_checked file(s). Change a copy in the kit and sync, or say why it differs in devkit.lock."
    fi
    echo "devkit-sync: $_checked file(s) are as the kit has them at $(short "$_locked")"
}

sync() {
    _new=$(fetch_kit "$1" "$work/kit")
    overrides > "$work/overrides"
    manifest "$work/kit" > "$work/manifest"
    prepare_settings
    # what the kit listed at the commit in the lock, so that a file it has dropped since can go
    : > "$work/old"
    if [ -f "$lock" ]; then
        _old=$(sed -n 's/^commit //p' "$lock" | head -n 1)
        if [ -n "$_old" ] && fetch_kit "$_old" "$work/old-kit" > /dev/null 2>&1; then
            manifest "$work/old-kit" > "$work/old"
        else
            echo "devkit-sync: cannot read the kit at the commit in devkit.lock, so a file it dropped stays" >&2
        fi
    fi
    _added=0
    _changed=0
    _same=0
    _removed=0
    while read -r _src _dst; do
        skipped "$_dst" && continue
        if overridden "$_dst" "$work/overrides"; then
            echo "kept (override): $_dst"
            continue
        fi
        if [ ! -e "$repo_root/$_dst" ]; then
            _added=$((_added + 1))
            echo "added: $_dst"
        elif same_file "$(source_file "$_src" "$_dst")" "$repo_root/$_dst"; then
            _same=$((_same + 1))
            continue
        else
            _changed=$((_changed + 1))
            echo "changed: $_dst"
        fi
        mkdir -p "$(dirname -- "$repo_root/$_dst")"
        cp "$(source_file "$_src" "$_dst")" "$repo_root/$_dst"
    done < "$work/manifest"
    while read -r _src _dst; do
        skipped "$_dst" && continue
        overridden "$_dst" "$work/overrides" && continue
        grep -q -x -F -- "$_src $_dst" "$work/manifest" && continue
        if [ -f "$repo_root/$_dst" ]; then
            rm -f "$repo_root/$_dst"
            _removed=$((_removed + 1))
            echo "removed: $_dst"
        fi
    done < "$work/old"
    # the commit line of devkit.lock, and nothing else of it
    if [ -f "$lock" ] && grep -q '^commit ' "$lock"; then
        sed "s/^commit .*/commit $_new/" "$lock" > "$work/lock"
    elif [ -f "$lock" ]; then
        { printf 'commit %s\n' "$_new"; cat "$lock"; } > "$work/lock"
    else
        printf 'commit %s\n' "$_new" > "$work/lock"
    fi
    cp "$work/lock" "$lock"
    echo "devkit-sync: kit at $(short "$_new"): $_added added, $_changed changed, $_removed removed, $_same the same"
}

[ -f "$conf" ] || die "there is no devkit.conf in $repo_root"
case ${1:-} in
    --check)
        [ "$#" -eq 1 ] || usage
        check
        ;;
    '' | -*) usage ;;
    *)
        [ "$#" -eq 1 ] || usage
        sync "$1"
        ;;
esac
