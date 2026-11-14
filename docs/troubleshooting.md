# When something behaves strangely

An index by symptom - what you are actually looking at - of the things in this
repository that mislead. Each entry says what it usually means and where the full
account lives, because most of them are documented somewhere already; what was
missing is the route from the symptom to that page.

Nearly every entry here was worked out more than once, by somebody who had no way
of knowing it had been worked out before. That is the cost this file exists to
stop paying. The entries came with the scaffolding from BAT
([apache/tooling-agenda](https://github.com/apache/tooling-agenda)), so an issue or
pull request number in them is BAT's.

## A pull request has no checks at all

Not a broken runner, almost never a queue. Two causes, and both are invisible
from the checks tab:

**Its base is not `main`.** `.github/workflows/build.yml` triggers on
`pull_request: branches: [main]`, so a pull request stacked on another branch -
which is a reasonable way to split a large change - fires nothing whatsoever. It
will sit at "no checks reported" for as long as it stays stacked.

**It conflicts with `main`.** The run would check out the merge of the branch and
`main`, and there isn't one. Resolving the conflict is what starts CI.

In both cases the workflow can still be asked directly, because it also has a
`workflow_dispatch` trigger:

```sh
gh workflow run build.yml --ref <branch>
```

That tests the branch itself rather than its merge with `main`, which is a
different question - and the more useful one while the branch is stacked or
conflicted, since the merge is exactly what does not exist yet.

## Every job waits for a runner, and is cancelled

The checks are listed, and each sits at "queued" until it is cancelled, without a
step having run. Read `runs-on` in the workflow: a job runs only where a runner
answers to every label it names. Every job here names one GitHub-hosted image,
`ubuntu-24.04`, which any public repository has. A job that names the labels of the
ASF's self-hosted runners instead - `runs-on=${{ github.run_id }}`, `tag=infra` and
the rest, as BAT's workflows do - waits for a runner that this repository is not
given, and is cancelled when its time runs out. A workflow copied from BAT keeps
those labels until somebody changes them.

## The quality job says every meter is unmeasured

Measuring the base failed. The ratchet compares against the commit your branch
would merge into, and in CI it takes those readings from the actions cache when
they are there and measures the base itself when they are not - so a cold cache is
not the cause. Look for the "Measure the base ..." step in the job: it is allowed
to fail, because a base nobody can measure is not your branch's fault, and the
check then reports unmeasured and passes rather than going red.

The usual causes are a base commit whose own suite does not pass, or a worktree
that could not be created. Rebasing onto a current `main` gives you a base whose
readings are cached, which skips the measuring entirely.

## `make container-quality` says the same thing locally

Locally the base is a file, and nothing has written it yet:

```sh
make container-quality-base
```

That checks the merge base out into a throwaway worktree and measures it into
`state/quality-base.json`, which is not committed. Run it again after pulling -
the file describes the commit you branched from at the time it was written.

It measures the seven meters that read source. The two coverage meters need a
suite to have run, which `quality-base` will not do against another commit, so
locally they are skipped and CI is where they are checked.

[docs/quality-ratchet.md](quality-ratchet.md) has the whole picture, including
how to allow a regression that is the point of the change.

## The end-to-end suite fails a random handful of specs at login

Every failure before any assertion, on a page reading `OAuth origin domain ... is
not allowed to use this service`: the request arrived with no session, and the
redirect to sign in went to the live ASF OAuth service, which refuses the
container's address. It is not your change, and each affected spec passes when
re-run on its own.

BAT found one cause: a wall clock that steps backwards. A session cookie carries
the second it was signed at, and a signature from the future is refused exactly as
an expired one is, so a step that crosses a second boundary loses whoever had just
signed in ([issue #612](https://github.com/apache/tooling-agenda/issues/612)). BAT's
`app/session_cookie.py` measures the age itself and honours a cookie signed ahead of
now, and its `app/client/handlers.py` answers a missing session in the suite with a
page saying where the cookie stopped. Neither is ported here yet; port both when
this first appears.

The OAuth error is the symptom of a missing session, not an authentication
problem to go and fix: a session that exists but lacks a role renders the 403 page
instead.

## Signing in fails with "Invalid redirect URI specified"

On a page from `oauth.apache.org`: `Invalid redirect URI specified. MUST be of
format https://foo.bar/baz.html and MUST be https`, with a `redirect_uri` that ends
in `:4500/auth`.

ASF OAuth refuses a redirect URI that has a port, and the app builds that URI from
the host the browser used. Open the app at <https://localhost>, not
<https://localhost:4500>. `make dev` starts a Caddy reverse proxy
(`scripts/Caddyfile`) for it, so the container has to publish port 443 and have
`caddy` installed (`docker compose build` after pulling the change that added it).
Without `caddy`, `make dev` says so and the app stays at `:4500`, where signing in
cannot work. `127.0.0.1` makes no difference: it is the port that is refused.

The end-to-end suite and `/e2e/login/<uid>` do not go through ASF OAuth and are not
affected.

### `caddy` exits with `listen tcp :443: bind: permission denied`

Port 443 is privileged. The dev container runs as root and binds it, so this is `caddy`
run outside the container, or in a rootless one. On Linux, either let the binary bind
low ports, which lasts until `caddy` is reinstalled:

```shell
sudo setcap cap_net_bind_service=+ep "$(command -v caddy)"
```

or lower the machine's limit, which also lets a rootless Docker or Podman publish host
port 443 (put the setting in `/etc/sysctl.d/` to keep it across reboots):

```shell
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443
```

Avoid `sudo make dev`: it leaves root-owned files in the repository and in your home
directory, which is what the next section is about.

### `could not clean default/global storage ... storage_clean.lock: permission denied`

`caddy` keeps a lock under `~/.local/share/caddy`, and an earlier run as root left that
directory owned by root. Nothing in it matters here, since the Caddyfile uses the
certificate `make certs` made. Give it back, or delete it:

```shell
sudo chown -R "$USER": ~/.local/share/caddy ~/.config/caddy
```

The error is Caddy's cleanup of its certificate storage and is logged without stopping
it, so if `https://localhost` answers, it is only noise.

## A test passes alone and fails in the suite

Two causes that look identical from the failure alone, and it is worth saying
which one it turned out to be:

- The specs share one server, so a spec can be perturbed by whatever ran before
  it, as they did in BAT ([issue #594](https://github.com/apache/tooling-agenda/issues/594)).
- The machine is shared with the other sessions working in this repository. A
  full BAT end-to-end suite has taken anywhere from 8 to 15 minutes on the same
  commit, and a timing-sensitive assertion can fail under that load.

Re-run a lone failure before believing it.

## The suite tests a change you did not make

`SR_KEEP=1` leaves the app container up so the next narrow run skips most of a
minute of startup. The `e2e/` directory is bind-mounted, so spec edits are picked up
live - but everything under `app/` and `assets/` is inside the running server,
and a change there needs the container restarted. Drop `SR_KEEP` (or remove the
server container) after one.

`scripts/e2e-container.sh` prints a warning when it reuses a container. It is one
line, and it scrolls.

## pytest appears to hang

Run it serially before anything else:

```sh
scripts/container.sh make test PYTEST_WORKERS=1
```

The suite runs across up to six worker processes, whose output interleaves, and a
hang under that is unreadable - you cannot tell which test stopped, or whether
anything stopped at all. Serial is the run to reach for whenever a failure is
easier to read without the interleaving.

If it still hangs, ask the process where it is. That has to happen inside the
container the suite is running in, because the hung process is in there rather
than on the host: `exec` into the container `scripts/container.sh` started, which
`podman ps` will name (`docker` throughout, if that is your runtime).

```sh
podman exec --privileged <container> uv run --with py-spy py-spy dump --pid <pid>
```

py-spy is not in the image; `uv` fetches it in about a second. `--privileged` is
there for `SYS_PTRACE` - `--cap-add=SYS_PTRACE` does as well - and without it
py-spy cannot read another process's stack, and says so rather than hanging in
turn.

## The first container command in a new worktree takes minutes

It is copying, once. Each checkout gets its own virtualenv and `node_modules`
volumes so that two sessions cannot sync one another's dependencies mid-test, and
a new worktree's volumes are seeded from the primary checkout's rather than built
from nothing. The copy is paid once per volume; a full `uv sync` and
`yarn install` would be paid once per worktree.

## A container command is killed, or the machine is short of memory

Look for a neighbour that is stuck before blaming the command that died. On
2026-09-11 this machine held eleven dev containers, the oldest 39 hours old, and
three `make container-verify` runs were killed by the host's OOM killer - which
shoots a victim rather than the offender, so the run that died was not the one at
fault.

Two of those eleven were spinning a core apiece. **Check the CPU, rather than
whether a process exists**: a container with a live `make` in it is not necessarily
working.

```sh
podman exec <container> ps -eo etime=,time=,pcpu=,comm=
```

A gate takes about ten minutes. A `make` whose **CPU time tracks its elapsed time**
is spinning rather than working - one had burned 24h39m of CPU in 24h48m of wall
clock with no children at all, another 10h16m while its five pytest workers sat at
0.3% each, which is the hung-pytest case above. Removing both took the load average
from 2.39 to 1.42 within a minute.

**A container whose `/run/secretary-records` mount points at a worktree that no longer exists can
go**, whatever it is doing: nothing can be referring to it, and any work it is
still doing is against a tree that was deleted.

```sh
for c in $(podman ps -q); do
  src=$(podman inspect "$c" --format '{{range .Mounts}}{{if eq .Destination "/run/secretary-records"}}{{.Source}}{{end}}{{end}}')
  [ -n "$src" ] && [ ! -d "$src" ] &&
    echo "orphan: $(podman inspect "$c" --format '{{.Name}}')"
done
```

Do not try to tell a working container from a stuck one by its process list.
Two traps, both hit while writing this: `podman exec` **cannot enter a container
whose mount source is gone** - it fails with "current working directory is outside
of container mount namespace root", and that error, swallowed, reads as "no
processes", so the busiest container on the machine is reported as idle. `-w /`
gets in. And the names are not what you would guess: a container spinning at 99.9%
showed `sh`, an idle one showed `sleep`, so any filter over `comm` classifies them
backwards. CPU is the signal, as above.

Since that day `scripts/container.sh` gives the containers **it** starts a name, a
label, a memory cap and an hour's deadline, so a hang stops by itself and an
overrun is charged to itself rather than to a bystander.
`podman ps --filter label=secretary-records.checkout` lists those, and says which checkout each
came from. It does not list them all: `scripts/e2e-container.sh` and
`scripts/e2e-typecheck.sh` build their own invocations and have none of it, and the
e2e app server outlives its run under `SR_KEEP` by design. A container started by
hand has none of it either - use `make container-keep` for one you mean to keep,
and `SR_TIMEOUT` or `SR_MEMORY` for what that does not cover.

## The ratchet says `python-cognitive: could not be read`, exited 139

complexipy crashed: 139 is a segmentation fault. complexipy 8.3.0 does that on every
input in the dev container's Alpine image, which is why `uv.lock` holds it at 6.2.0,
the version BAT pins. A lock refreshed from nothing resolves the newest release and
brings the crash back; start a relock from the current `uv.lock` rather than without
one, and check this meter before trusting the new lock.

## `make check` fails on formatting alone

Prettier is a `make check` hook and the easiest red in CI to avoid:

```sh
scripts/container.sh sh -c 'cd assets && yarn format'
```

`svelte-check` and eslint are deliberately not in `make check` - they check the
whole project rather than the files that changed, which is too slow for every
commit. They run as a `pre-push` hook, in `make lint`, and in the `frontend` job.

## A browser API is undefined in the end-to-end suite but works in the dev server

The suite reaches the app by container ip over plain http, which is deliberately
not a secure context. `crypto.randomUUID`, the clipboard and service workers are
absent there by design, so a feature depending on one fails in the suite rather
than in front of someone running the dev server on a LAN address.

Do not "fix" this by pointing the suite at localhost. `utils.uniqueId()` is the
wrapper for the one case this repository hit, and a pre-commit hook forbids the
raw call.

## Reading a Copilot review, and asking for another

The summary line is not the review. One that says "generated no new comments" has
often found something and collapsed it into a `Suppressed comments (N)` block, which
the PR page folds away and `gh pr view` shows in full:

```sh
gh pr view <pr> --json reviews -q '.reviews[-1].body'
gh api --paginate repos/apache/tooling-secretary-records/pulls/<pr>/comments -q '.[] | .path + ": " + .body'
```

`--paginate` because the second one answers thirty comments a page, and a pull
request that has been round a few times has more than that.

A review is tied to the commit it ran against and does not carry forward. Compare
each review's `commit.oid` with `headRefOid` before treating one as covering the
branch:

```sh
gh pr view <pr> --json headRefOid,reviews -q '.headRefOid as $headRefOid | .reviews[] | select(.commit.oid == $headRefOid) | .state'
```

## Asking Copilot for a review appears to work and nothing arrives

Two obvious routes fail, one of them silently.

`gh pr edit <pr> --add-reviewer Copilot` fails loudly, with a message about
Projects (classic) being deprecated. That is a `gh` bug unrelated to what you
asked for; it breaks `gh pr edit --body` the same way, so edit a description with
`gh api repos/.../pulls/<pr> -X PATCH -F body=@file.md` instead.

`gh api repos/:owner/:repo/pulls/<pr>/requested_reviewers -X POST -f
"reviewers[]=Copilot"` is worse: when this was tried it answered `200` with the whole
pull request object and no review was ever requested. Copilot is a Bot, and that
field takes Users - so check for the review rather than trusting the status.

What works is the GraphQL mutation, which takes bots separately:

```sh
gh api graphql -f query='mutation { requestReviews(input: {
  pullRequestId: "<PR node id>", botIds: ["BOT_kgDOCnlnWA"], union: true
}) { pullRequest { number } } }'
```

`BOT_kgDOCnlnWA` is `copilot-pull-request-reviewer`. It is absent from
`suggestedActors`, so it cannot be looked up that way - lift it off any existing
Copilot review instead:

```sh
gh api graphql -f query='query { repository(owner:"apache",name:"tooling-secretary-records"){
  pullRequest(number:663){ reviews(last:20){ nodes { author { login __typename
  ... on Bot { id } } } } } }'
```

A review is tied to the commit it ran against, so this has to be re-done after
every push. `gh pr view <pr> --json reviews` reports each review's `commit.oid`;
compare it with `headRefOid` before treating a review as covering the branch.

## A Copilot review arrives, but says it could not run its full review

The body opens with a warning rather than a finding:

> Copilot couldn't run its full agentic review because it didn't start before the
> timeout. Make sure your repository has a runner available, or add a
> `copilot-code-review.yml` file specifying one with the `runs-on` attribute.

The review that follows is the degraded pass: it reads the diff hunks without the
repository around them. It is worth reading - it still finds real defects - but expect
it to raise things the surrounding code already answers, and to raise them again after
you have said so: a guard sitting three lines above the hunk, or an import that the
module's `from __future__ import annotations` makes moot.

What it wants is `.github/workflows/copilot-code-review.yml`, whose
`copilot-setup-steps` job names the runner. Without that file the review falls back to
`ubuntu-latest`; the file names `ubuntu-24.04`, the image every check runs on. A review
that still did not start is waiting for a runner, as in "Every job waits for a runner,
and is cancelled" above.

Two things about that file surprise people:

- Only `steps`, `permissions`, `runs-on`, `services`, `snapshot` and `timeout-minutes`
  are read from the job, and the job must be named `copilot-setup-steps`. Rename it and
  the review goes back to `ubuntu-latest` without saying so.
- **It is read from the default branch only.** A change to it does nothing until it
  merges, and the review round of the pull request that changes it still runs under the
  old configuration. There is no way to test it on the branch.
