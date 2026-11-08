#!/bin/sh
# Put the screenshots an end-to-end run captured somewhere a pull request can show them.
#
#   scripts/pr-screenshots.sh 621 e2e/screenshots/agenda.png e2e/screenshots/report.png
#
# It creates a secret gist, pushes the images into it, checks each raw url the way a
# browser will, and prints the markdown to paste into the pull request.
#
# The roundabout route is forced. The host GitHub uses for a dragged-in image
# (github.com/user-attachments/...) is authenticated by a browser session and has no API,
# so nothing that is not a browser can put an image there. A gist is a git repository,
# which means binaries go in by push - the REST API only takes text.
#
# Two other routes look right and are not, which is why this verifies rather than trusts.
# On a private repository raw.githubusercontent.com serves a file only to a request
# carrying an Authorization header, which an <img> cannot send, and
# github.com/<owner>/<repo>/raw/... wants a session cookie and 404s without one. Both
# answer 200 on a public repository, which is the trap: they work until the repository
# they are tried on is not public.
#
# A gist raw url is readable by anyone holding it even though the gist is secret. That is
# the same exposure a dragged-in image already has, but do not put anything in one that
# you would not attach by hand. Say in the pull request which gist holds them, so it can
# be deleted when the pull request closes.

set -eu

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <pr-number> <image>..." >&2
    exit 2
fi

for tool in gh git curl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "$tool is needed and was not found" >&2
        exit 1
    fi
done

pr=$1
shift

for image in "$@"; do
    if [ ! -f "$image" ]; then
        echo "no such file: $image" >&2
        exit 1
    fi
done

repository=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
owner=$(gh api user --jq .login)

work=$(mktemp -d)
gist=""
published=0

# A run that fails after creating the gist would otherwise leave a secret gist of somebody's
# screenshots behind, with nothing pointing at it and nobody expecting it - and the failure
# it left it behind from is the moment you are least likely to go looking. Only a run that
# printed its urls keeps its gist.
cleanup() {
    rm -rf "$work"
    if [ -n "$gist" ] && [ "$published" -eq 0 ]; then
        if gh api -X DELETE "/gists/$gist" >/dev/null 2>&1; then
            echo "gist $gist deleted: nothing was published from it" >&2
        else
            # Saying it was deleted when it was not is worse than not trying: the whole
            # point of deleting it is that nobody is left holding a secret gist they do not
            # know about.
            echo "gist $gist could not be deleted - remove it by hand:" >&2
            echo "  gh api -X DELETE /gists/$gist" >&2
        fi
    fi
}
trap cleanup EXIT INT TERM

# The README is here because a gist must be created with a file, and the REST API can
# only be handed a text one. The images arrive by push, below.
gist=$(gh api -X POST /gists \
    -F public=false \
    -f "description=screenshots for $repository#$pr" \
    -f "files[README.md][content]=Screenshots for $repository#$pr. Delete this gist when the pull request closes." \
    --jq .id)

echo "gist $gist created (secret)" >&2

# git, authenticated the way gh already is. A token pasted into the remote url would
# reach the process list, the clone's own .git/config, and any error git prints about the
# remote; asking gh for it at the moment git wants it keeps it out of all three.
git_as_gh() {
    git -c 'credential.helper=' -c 'credential.helper=!gh auth git-credential' "$@"
}

git_as_gh clone -q "https://gist.github.com/$gist.git" "$work/gist"

# Push over https, like the clone, whatever the caller's git would rather do. Fetching
# over https and pushing over ssh is a common setup -
#
#   url.ssh://git@gist.github.com/.pushInsteadOf = https://gist.github.com/
#
# and it rewrites this push into an ssh one, which then wants a key, an agent and a known
# host, and fails with "Host key verification failed" three steps from anything this script
# said it was doing. A pushurl set on the remote is the one thing pushInsteadOf does not
# rewrite.
git -C "$work/gist" remote set-url --push origin "https://gist.github.com/$gist.git"

for image in "$@"; do
    name=$(basename -- "$image")
    if [ -e "$work/gist/$name" ]; then
        echo "two images are both named $name; rename one so both survive" >&2
        exit 1
    fi
    cp -- "$image" "$work/gist/$name"
done

# The push has to happen from inside the clone, and the caller's directory is where the
# image paths were resolved, so this is the first point at which moving is safe.
cd "$work/gist"
for image in "$@"; do
    git add -- "$(basename -- "$image")"
done
git commit -qm "screenshots for $repository#$pr"
git_as_gh push -q origin HEAD:main
sha=$(git rev-parse HEAD)

# What a browser and GitHub's image proxy do, and all they do: an unauthenticated GET,
# which has to answer 200 with an image content type. A url that needs a header or a
# cookie fails here rather than in the pull request.
status=0
markdown=""
for image in "$@"; do
    name=$(basename -- "$image")
    url="https://gist.githubusercontent.com/$owner/$gist/raw/$sha/$name"
    answer=$(curl -sSL -o /dev/null -w '%{http_code} %{content_type}' "$url" || echo "000 -")
    case $answer in
        200\ image/*)
            markdown="$markdown![${name%.*}]($url)
"
            ;;
        *)
            echo "$url answered '$answer', which is not an unauthenticated image" >&2
            status=1
            ;;
    esac
done

if [ "$status" -ne 0 ]; then
    exit "$status"
fi

# After the urls are out and before the note about them: what decides whether the gist is
# worth keeping is whether anybody can now reach it. A printf that fails - stdout closed by
# a `| head`, a full disk - takes the gist with it, which is right, because nothing was
# printed to keep it for.
printf '\n%s\n' "$markdown"
published=1
printf 'Add to the pull request description: screenshots are in secret gist %s, delete it when this closes.\n' \
    "$gist"
