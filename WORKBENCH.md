# ASF Secretary Workbench

The Secretary Workbench is the web application the ASF Secretary and
Secretarial Team use to process the paperwork that arrives at
`secretary@apache.org`: ICLAs, CCLAs, software grants, membership
applications, emeritus and withdrawal requests. It turns each incoming email
with attachments into a work item, lets the operator inspect and clean up the
attachments, and then files the document in Subversion, updates the relevant
index files and LDAP, and sends the acknowledgement or rejection email, all
from one screen.

It lives at `www/secretary/workbench/` in the Whimsy repository and is served
at `https://whimsy.apache.org/secretary/workbench/`.

This document describes the application as of commit `ed90fa60`
(2026-09-30, "Handle merged projects").

---

## 1. Overview

| Aspect | Value |
|---|---|
| Server | Ruby, Sinatra via Wunderbar, run under Passenger (`config.ru`) |
| Front end | Vue.js components written in Ruby and transpiled with ruby2js (`views/*.js.rb`, bundled into `app.js`) |
| Storage | Mail archive on disk (`/srv/mail/secretary` in production); one mbox or `.mail/` directory plus one YAML index per month |
| Access | `ASF::Auth::MembersAndOfficers` (members and officers may log in) |
| Writes to | Subversion (`documents/`, `foundation/officers/*.txt`, `foundation/members.txt`, `Meetings/*/memapp-received.txt`, `infra/acreq`, `subreq`), LDAP, outgoing SMTP |
| External tools | `gpg`/`gpg2`, `pdfseparate`, `pdfunite` (poppler), `pdfjam`, `img2pdf`, ImageMagick `convert`, `rsync`, `svn` |

Design principles stated in the HOWTO:

- Nothing is written to SVN until the operator does it, so there is no
  working copy to get wedged and no pending state to clean up.
- Every action has its own form and its own server script; parameters are
  passed explicitly rather than hidden in the DOM or SVN properties.
- Emails are stored and indexed at delivery time, not by a cron job, and the
  index page updates live as new mail arrives.

---

## 2. Architecture

```
 secretary@apache.org
        │  (procmail)
        ▼
  deliver.rb ──► ARCHIVE/YYYYMM.mail/<hash>   (raw email)
             ──► ARCHIVE/YYYYMM.yml           (parsed headers + attachment index)
                         │
                         │ Listen (inotify)            browser
                         ▼                               ▲
  models/events.rb ──► GET /events (SSE) ───────────────┘
                                                         │
  server.rb (Sinatra) ◄─── GET/POST/PATCH/DELETE ────────┘
     │
     ├─ views/*.html.rb      page shells (index, message frameset, parts, body, …)
     ├─ views/*.js.rb        Vue components (Index, Parts, CheckSignature, forms)
     ├─ views/actions/*.json.rb   one script per action (dry-run and execute)
     ├─ tasks.rb             the task/form/complete DSL used by action scripts
     ├─ templates/*.erb      outgoing email bodies
     └─ models/              Mailbox, Message, Attachment, Events
```

### 2.1 Ingestion

`deliver.rb` is invoked by procmail with the raw email on stdin. It:

1. Computes the message hash (first 10 hex chars of SHA1 of the Message-ID,
   or of the whole message if there is no Message-ID).
2. Parses the message with `Message.parse` (see section 3.2).
3. Writes the raw email to `ARCHIVE/YYYYMM.mail/<hash>` and merges the parsed
   headers into `ARCHIVE/YYYYMM.yml` under a file lock.
4. If parsing failed, still records an entry containing the exception, then
   re-raises so procmail logs it.

An optional `YYYYMM` argument overrides the target month, used when merging
mail later.

`parsemail.rb` (via `rake parse`, `rake fetch`, `rake fetch1`) is the batch
equivalent for local development: it rsyncs the mbox archive from the mail
server and parses any mailbox whose mtime changed. `Mailbox#parse` only adds
entries that are not already in the YAML, so re-running is cheap.

### 2.2 Archive layout

```
ARCHIVE/
  202609            mbox file (optional; may be 202609.gz)
  202609.mail/      one raw email per file, named by hash
  202609.yml        index: {hash => headers, :mtime => ...}
```

`config.rb` chooses the paths:

- Production (when `/srv/mail` exists): `ARCHIVE = /srv/mail/secretary`,
  `GNUPGHOME = /srv/gpg` if present.
- Otherwise: `ARCHIVE = ./officers-secretary`, fetched from
  `minotaur.apache.org:/home/apmail/private-arch/officers-secretary`.

### 2.3 Live updates

`models/events.rb` watches `ARCHIVE` with the `listen` gem. Whenever a
`.yml` file changes it pushes that month's full client header list onto a
queue. `GET /events` streams the queue to the browser as Server-Sent Events,
with a heartbeat comment every 25 seconds (5 in development). The index page
merges each event into its list, so deletions made in another browser session
and newly delivered mail show up without a reload. SIGUSR2 (puma) and SIGHUP
(thin) close all streams so the server can restart cleanly.

---

## 3. Data model

### 3.1 `Mailbox` (`models/mailbox.rb`)

One instance per month. Key methods:

| Method | Purpose |
|---|---|
| `Mailbox.fetch(mailboxes)` | rsync the archive (all, a list, or one month) |
| `Mailbox.allmailboxes` | sorted list of `YYYYMM` names with a YAML index, excluding future months |
| `Mailbox.prev_next(month)` | neighbours for the deleted/pending/all listing pages |
| `#update { \|yaml\| … }` | locked read-modify-write of the YAML index |
| `#messages` | split the mbox (plain or gzip) on `^From ` lines |
| `#find(hash)` | build a `Message` from the `.mail/` file, falling back to the mbox |
| `Mailbox.find("/…/YYYYMM/hash/")` | same, from a URL path |
| `Mailbox.revert(path)` | re-parse the raw email and overwrite the YAML entry (discards all attachment edits) |
| `#headers` | server view of all entries |
| `#client_headers(listall:)` | trimmed view for the browser: `time`, `href`, `from`, `date`, `subject`, `status`, `secmail`. By default only messages that still have at least one undeleted, relevant attachment; with `listall` every message that ever had attachments |
| `#parse` | add any new mbox messages to the YAML |

### 3.2 `Message` (`models/message.rb`)

`Message.parse(raw)` produces the YAML entry for a message. It is defensive
about malformed mail (bad encodings, unparsable From headers, missing
Message-IDs) and records:

- `envelope_from`, `envelope_date` (delivery date; falls back to now)
- `from` (first address), `name` (display name), `time` (Date header as ISO
  8601, falling back to envelope date)
- `cc`: everyone on the original To/Cc minus `secretary@apache.org` and the
  sender; used to copy the same people on replies
- every header field (grouped when repeated)
- `attachments`: one entry per MIME attachment with `name`, `length`, `mime`
  and the part's headers. `application/octet-stream` is re-typed from the
  file extension. Parts with no filename use their Content-ID.

Special case: a message with no attachments whose subject contains `CLA` but
not `ICLA`/`iCLA` and is not a reply gets a synthetic attachment named
`rawmessage.txt` so that CCLA requests sent as plain text appear in the
workbench.

`Message.attachments(headers)` is the filter used everywhere a list of
attachments is shown. It drops entries marked `:deleted`, drops
`application/pgp-signature` and `application/pkcs7-signature` parts unless
they are named `*.pdf.asc` or `*.pdf.sig`, and drops anything named
`signature.asc` (mail client signatures).

Attachment mutation helpers, all of which rewrite the YAML:

- `update_attachment(name, values)`: merge new `content`/`name`/`mime` in
  place (edited content is stored inline in the YAML).
- `replace_attachment(name, [values…])`: swap one entry for several (burst).
- `replace_all_attachments(value)`: collapse to one (join).
- `delete_attachment(name)`: mark `:deleted`; if nothing is left, mark the
  whole message `:deleted`.

Filing helpers:

- `write_svn(repos, filename, *attachments)`: write one attachment as
  `filename.ext`, or several into a directory `filename/`, then `svn add` and
  set `svn:mime-type` plus the email properties.
- `write_att(tmpdir, *names)`: extract attachments to temp files for
  `svn_multi`.
- `add_email_details(path)` / `propval`: set SVN properties `email:addr`,
  `email:id`, `email:name`, `email:subject`, `envelope:from`,
  `envelope:date` on filed documents.
- `reply(fields)`: build a reply with `In-Reply-To`/`References` set,
  `Re:` subject, and To/Cc/Bcc merged from the original message and the
  caller with duplicates removed across all three fields.
- `cc=` / `bcc=`: stored per message, editable from the Mail tab.

### 3.3 `Attachment` (`models/attachment.rb`)

Wraps one attachment entry plus the corresponding MIME part.

- `content_type`: corrects `octet-stream`/`text/plain` from the extension and
  normalises `application/jpeg|gif|png` to `image/*`.
- `body`: edited content from the YAML if present, otherwise the MIME body.
- `as_file`: temp file with the raw body.
- `as_pdf`: temp PDF. Images (`.gif .jpg .jpeg .png` or any `image/*`) are
  converted with `img2pdf`; if that refuses because of an alpha channel the
  image is flattened with ImageMagick `convert` first.
- `write_svn(repos, file, path)`: write to a checkout and `svn add`.

### 3.4 Message status values

| Status | Meaning |
|---|---|
| (none) | awaiting processing |
| `:deleted` | removed from the index (spam, processed, or all attachments deleted) |
| `:deletePending` | client-side only, while a delete request is in flight |
| `:emeritusPending` / `:emeritusReady` | synthetic rows for pending emeritus requests, under / over 10 days old |
| `:withdrawalPending` / `:withdrawalReady` | same for withdrawal requests |

Per-message free text lives under `secmail`: `secmail.status` (set by
pubkey / clarify-intent actions) and `secmail.notes` (set by the Note form).
Both are displayed in the parts pane, and the status also shows in the index
row.

---

## 4. User interface

### 4.1 Index page (`/`)

`views/index.html.rb` + `views/index.js.rb` (Vue `Index` component).

- Lists unprocessed messages with attachments for the latest month, newest
  first, as Timestamp / From / Subject rows. Deleted rows are hidden.
- On load it fetches the latest month as JSON; during the first week of a
  month it also fetches the previous month. A **download previous month**
  button appears when an earlier month with pending items exists.
- Subscribes to `/events` and merges incoming lists by time and href.
- Pending **emeritus requests** and **withdrawal requests** from SVN are
  appended as rows linking to the member's roster page and the request file,
  coloured differently once they are 10 days old (the waiting period).
- Selection is remembered in `sessionStorage` (`Status` class) so returning
  from a message reselects the same row.
- Keyboard: up/down move the selection, Enter or right arrow opens the
  message, Ctrl/Cmd+Backspace or Delete deletes it (spam), Ctrl/Cmd+Z undoes
  the last delete. An **undo delete** button mirrors the undo stack.
- A **check for new mail** button (non-production hosts only) runs
  `actions/check-mail`, which rsyncs and parses the current month.
- Header links: **Deleted messages**, **All messages**, **Pending messages**
  (see 4.4). A status banner from `ASF::Status` is shown when present.

### 4.2 Message page (`/YYYYMM/hash/`)

`views/message.html.rb` is a two-frame layout: the **parts pane** on the left
(`_index_`) and a **content** frame on the right that starts on the message
body (`_body_`).

#### Parts pane (`views/parts.html.rb` + `views/parts.js.rb`, Vue `Parts`)

Top of pane:

- `ASF Secretary Mail` header linking back to the index.
- Links that load into the content frame: **text** (rendered body with
  selected headers, inline `cid:` images resolved), **headers** (YAML dump of
  the parsed entry), **raw** (the original email), **reparse** (re-run
  `Message.parse` on the raw mail and rewrite the YAML).
- **undelete this email** and a reminder that Ctrl/Cmd+Backspace deletes it.
- Submitter name and address.

Attachment list:

- One row per attachment from `Message.attachments`. PDFs, text and images
  open directly in the content frame; any other type goes through the
  **danger** page (`_danger_/name`), which shows the part's headers and
  requires a second click to open it.
- Clicking selects an attachment. If a detached signature matches (`x.asc`
  or `x.sig`, or the only other attachment when there are exactly two and it
  ends in `.asc`/`.sig`) it is highlighted and verified immediately (see 4.3).
- Right-click opens a **context menu**: burst, join, rotate right / flip /
  left, revert, delete, pdf-ize, parse pdf. See section 7.
- **Drag and drop** one attachment onto another to staple the dragged pages
  after the target (`actions/drop`).
- `secmail.status` and `secmail.notes` are displayed as alerts.

Below the list, once an attachment is selected, three tabs:

- **Categorize** (default). Radio buttons choose the document type and
  replace the tab with the matching form: `icla`, `additional icla`, `ccla`,
  `software grant`, `membership application` (only while applications are
  open for the current meeting), `emeritus request`, `withdrawal request`,
  `forward email`, `add note`/`edit note`. Below a rule are the reply-only
  actions that post straight to a tasklist: `late membership application`
  (only when applications are closed), `upload public key`, `clarify intent`,
  `reject document (select reasons below)` with its checklist and a
  **Cc project** selector, and `resubmitted form`.
- **Edit**: the same operations as the context menu, as a list.
- **Mail**: edit the per-message **cc** and **bcc** lists used by every reply
  (`actions/update-mail`).

The pane ends with a **Links** list (response time, mail search and browse,
committer index, ICLA/CCLA/grant SVN directories, incubator, project
proposals, ICLA lint, public names, board subscriptions, mail aliases,
members.txt, foundation-commits, this HOWTO) and, when the legacy
`/var/tools/secretary/secmail` exists, an **Upload email** link.

Keyboard in the parts pane: Ctrl/Cmd+Backspace or Delete deletes the message
and returns to the index; plain Backspace, Delete or up arrow return to the
index without deleting.

Navigation in the content frame uses `history.replaceState`, and `popstate`
is intercepted so the browser back button returns all the way to the index.

#### Content frame

Shows whatever was last requested: body, headers, raw, an attachment, the
danger interstitial, the ICLA parser output, or a **tasklist** page (section
5).

### 4.3 Signature checking (`views/check-signature.js.rb`, `actions/check-signature`)

When an attachment with a matching detached signature is selected, the
`CheckSignature` component posts both names to the server, which:

1. Runs `gpg --keyid-format long --verify sig doc`.
2. Extracts the key id from gpg's output and, unless `/srv/gpg/whimsy_use_db`
   exists (local testing switch), always fetches the current key from
   `keyserver.ubuntu.com` (max 125 000 bytes), imports it, and commits a copy
   to `documents/iclas/__keys__/<keyid>` in SVN with the email properties
   attached, so every key ever used to sign a filed document is preserved.
3. Re-runs the verify, strips the two standard "not certified" warnings, and
   returns stdout, stderr and the exit code.

The pane shows a green alert if the output contains `Good signature`,
otherwise red. The script can also be run from the command line for testing
(`ruby check-signature.json.rb YYYYMM/hash [doc] [sig]`).

### 4.4 Listing pages

| URL | Content |
|---|---|
| `/deleted` → `/YYYYMM/deleted` | messages with status `:deleted` for the month, with Previous/Next month links |
| `/pending` → `/YYYYMM/pending` | messages still to be processed |
| `/all` → `/YYYYMM/all` | every message that ever had attachments, with a Status column |

Each row links to the message page so a deleted message can be reopened and
undeleted.

---

## 5. The tasklist mechanism

Every filing or reply action follows the same two-phase protocol, implemented
by `tasks.rb` and `public/tasklist.js`.

1. The form posts (as a normal HTML form, target `content`) to
   `/tasklist/<action>`. The server runs `views/actions/<action>.json.rb` in
   **dry-run** mode: each `task "title" do … end` block records its title and
   the `form do … end` contents (input values, the email that would be sent)
   but skips `complete do … end`. Any `_warn` / `_info` messages are
   collected.
2. `views/tasklist.html.rb` renders "Operations to be performed": each task's
   title and disabled form fields, notes, and warnings. Warnings turn the
   button into **proceed anyway**; otherwise **proceed** / **cancel**.
3. On proceed, `tasklist.js` posts the same parameters plus `task: <title>`
   to `/tasklist/<action>` as JSON, one task at a time, in order. The server
   re-runs the script and executes only the `complete` block whose title
   matches. The transcript (svn commands and output), messages and any
   exception are appended under the task. If a task fails the operator is
   asked whether to continue.
4. When all tasks finish, the page posts a `status` message to the parts
   frame: `complete` (default) causes the parts pane to delete the processed
   attachment and its signature, and return to the index if nothing is left;
   `keep` (set by `_disposition :keep`) leaves the attachment in place and
   refreshes the headers. `cancelled`, `aborted`, `warning` and `exit` reset
   the form state.

Helpers available to action scripts:

- `_personalize_email(user)`: sets `@from` and `@sig`. The Secretary and
  Assistant Secretary get their office title; anyone else is signed as
  "Apache Software Foundation Secretarial Team".
- `_extract_project`: resolves the `project` form field to `@pmc` and
  `@podling` (a podling maps to the Incubator PMC plus the podling), warning
  if it is not an active PMC or podling or has no private list yet.
- `_status(text)`: store `secmail.status` on the message.
- `svn!`: run an svn command through `ASF::SVN` with the user's credentials.
- `svn_multi(index_dir, index_name, docdir, doc, sig, outname, ext, message, commit_msg) { |index_text| … }`:
  in one commit, add the document (and signature) to `documents/<docdir>/`
  as `outname.ext` or into a directory `outname/` when there is a signature,
  set `svn:mime-type` and (for ICLAs) the email properties, and update the
  index file with the block's result. Refuses to overwrite an existing path.
- `template(name)`: render `templates/<name>` with the script's instance
  variables.

---

## 6. Filing actions

Each subsection lists the form fields and the tasks executed. All replies are
built with `Message#reply`, so the original correspondents are copied unless
they are already on To/Bcc.

### 6.1 ICLA (`forms/icla.js.rb`, `actions/icla.json.rb`)

Form: E-mail, Real Name, Public Name, Family First (reorders the generated
file name), File Name (pattern `[a-zA-Z][-\w]+(\.[a-z]+)?`, auto-generated by
asciizing the real name), User ID (checked live against `actions/check-id` for
ICLA and mail-alias collisions), Project, Vote Link. Buttons **clear form**,
**Parse/use PDF data** (runs the ICLA PDF parser, section 8, and fills the
form; fields the PDF could not supply are highlighted yellow, an email
mismatch between PDF and sender is flagged red) and **Use mail data**. The
parser is run automatically when the form opens. Project and vote link become
required when a user id is given.

Dry-run checks: file name validity, existing ICLA with that name, email
already in `iclas.txt`, possible duplicate ICLAs by name, email at
`@apache.org`, invalid From address.

Tasks:

1. `svn commit documents/iclas/<file> and iclas.txt`: add the document (plus
   signature, into a directory, renamed `icla.<ext>` / `icla.<ext>.asc`) and
   insert `notinavail:<real>:<public>:<email>:Signed CLA;<file>` into
   `officers/iclas.txt`, sorted.
2. `email <submitter>`: template chosen by context: `icla.erb` (no project),
   `icla-pmc-notified.erb` (project but no account request),
   `icla-account-requested.erb` (valid id and project),
   `icla-invalid-id.erb` (id taken or malformed). The PMC/podling private list
   is copied and set as Reply-To; the secretary is Bcc'd when the PMC takes
   over.
3. If a valid user id, project and vote link were supplied:
   `svn commit infra/acreq/new-account-reqs.txt` (append the account request
   line) and `email root@apache.org` (`acreq.erb`, copying the submitter and
   the project).

### 6.2 Additional ICLA (`forms/icla2.js.rb`, `actions/icla2.json.rb`)

For a committer who already has an ICLA on file (name or email change). Form:
a search box over `/iclas.json` (id, name, legal name); selecting a match
shows the existing Full Name, Public Name, E-mail, File Name and AvailId and
lets the operator edit Public Name and E-mail.

Tasks:

1. `svn mv <existing> <stem>/icla.<ext>`: if the existing ICLA is a single
   file, move it into a directory of the same stem (only when needed).
2. `svn commit documents/iclas/<stem>/iclaN.<ext>`: add the new document
   (and signature) with the next sequence number.
3. `svn commit foundation/officers/iclas.txt`: replace the person's line with
   the updated public name and email.
4. `change public name in LDAP` (only if changed and the id is real).
5. `email <submitter>` (`icla2.erb`), copying all of the person's known
   addresses and the secretary.
6. `change email address in LDAP` (if the id is real).

### 6.3 CCLA (`forms/ccla.js.rb`, `actions/ccla.json.rb`)

Form: Corporation, Product, Contact, E-mail, Employees (textarea), File Name
(generated from corporation and product), Project.

Tasks:

1. `svn commit documents/cclas/<file> and update cclas.txt`: add document
   and signature, append
   `notinavail:<Company> - <Contact>:<email>:Signed Corp CLA for <employees> for <product>`.
2. `email <contact>` (`ccla.erb`), Reply-To secretary, copying the project
   lists.

### 6.4 Software grant (`forms/grant.js.rb`, `actions/grant.json.rb`)

Form: From (company), For (description), Signed By, E-mail, File Name,
Project.

Tasks:

1. `svn commit documents/grants/<file> and update grants.txt`: add document
   and signature, and insert a `company / file: / for:` block after the
   "registering. documents on way to Secretary." marker in
   `officers/grants.txt`.
2. `email <signer>` (`grant.erb`).

### 6.5 Membership application (`forms/memapp.js.rb`, `actions/memapp.json.rb`)

Only offered while the current meeting's application window is open
(`ASF::MeetingUtil.applications_valid`, or the email's envelope date was in
time). Form: a drop-down of invitees from `memapp-received.txt` who have not
yet applied (`/memapp.json`), Full Name, File Name.

Dry-run checks: existing form under `documents/member_apps/`, valid availid.

Tasks:

1. `svn commit documents/member_apps/<file> and update members.txt`: add the
   document and insert a new entry (built by `ASF::Member.make_entry`) into
   the Active section of `foundation/members.txt`, update the "We now number
   N active members" count and re-sort.
2. `update cn=member,ou=groups,dc=apache,dc=org in LDAP`: add the person to
   the `member` group (skipped if already present).
3. `subscribe to members@apache.org` and
4. `subscribe to members-notify@apache.org`: drop a JSON subscription request
   into the `subreq` SVN directory (same format as `committers/subscribe`).
5. `svn commit memapp-received.text`: flip the person's `no` flags to `yes`
   in the meeting's `memapp-received.txt`.
6. `email <applicant>` (`mem.erb`, the welcome letter), with `members@`
   removed from the Cc list.

### 6.6 Emeritus request (`forms/emeritus-request.js.rb`, `actions/emeritus-request.json.rb`)

Form: search box over `/members.json` (official members list, including
inactive); selecting a member fixes the availid, which is also the file name.

Tasks:

1. `svn commit documents/emeritus-requests-received/<availid>.<ext>`: add the
   request (and signature). The index page then shows it as a pending request
   until it is processed from the roster tool after the 10-day waiting
   period.
2. `email <member>` (`emeritus-request.erb`).

### 6.7 Withdrawal request (`forms/withdrawal-request.js.rb`, `actions/withdrawal-request.json.rb`)

Identical shape to the emeritus request, filing into
`documents/withdrawn/pending/` and refreshing the cached listing. Reply uses
`withdrawal-request.erb`.

---

## 7. Attachment editing actions

All of these are available from the context menu and the Edit tab, post to
`/actions/<name>` as JSON, and return the updated attachment list plus the
attachment to select. Edited content is stored inline in the YAML entry, so
the original email is never modified and **revert** can always restore it.

| Action | Script | What it does |
|---|---|---|
| burst | `burst.json.rb` | `pdfseparate` the PDF into `name-01.pdf`, `name-02.pdf`, … replacing the original entry |
| join | `join.json.rb` | `pdfunite` every remaining attachment (converted to PDF) into a single `pages.pdf`, replacing all entries |
| drop (drag and drop) | `drop.json.rb` | `pdfunite` target + source into the target (renamed `.pdf`), then delete the source |
| right / flip / left | `rotate-attachment.json.rb` | `pdfjam` with 270° / 180° / 90°, `--fitpaper`; output renamed `.pdf` |
| pdf-ize | `pdfize.json.rb` | convert an image attachment to PDF via `Attachment#as_pdf` |
| revert | `revert.json.rb` | re-parse the raw email, discarding all edits and deletions |
| delete | `delete-attachment.json.rb` | mark the attachment deleted; marks the message deleted if none remain |
| parse pdf | (client only) | open `/secretary/icla-parse/<attachment>` in the content frame to show the ICLA parser's extracted fields |

Message-level operations:

| Operation | Request | Effect |
|---|---|---|
| delete message | `DELETE /YYYYMM/hash/` | set `status: :deleted` (used for spam and from the parts pane) |
| undelete | `PATCH /YYYYMM/hash/` with `status: null` and optionally `attachment_status: true` | clear the message status and, if asked, clear every attachment's deleted flag |
| update cc/bcc | `POST /actions/update-mail` | store `cc` and `bcc` lists on the message |
| note | `POST /actions/note` | store `secmail.notes` |
| reparse | `GET /YYYYMM/hash/_reparse_` | re-parse raw mail and rewrite the YAML entry |

---

## 8. Reply-only and forwarding actions

These send an email and, except where noted, leave the attachment in place
via `_disposition :keep` or by the operator deleting it afterwards.

| Action | Trigger | Template | Notes |
|---|---|---|---|
| incomplete | "reject document" + reason checkboxes | `incomplete.erb` | Builds a bullet list from the selected reasons (section 8.1). Copies the Cc project's private list. |
| resubmit | "resubmitted form" | `resubmit.erb` | Tells the submitter the resubmission was received and names the (P)PMC. |
| pubkey | "upload public key" | `pubkey.erb` | Asks for the key to be uploaded to a keyserver; sets `secmail.status` and keeps the attachment. |
| clarify_intent | "clarify intent" | `clarify-intent.erb` | Asks what the sender wants; sets `secmail.status` and keeps the attachment. |
| memapplate | "late membership application" | `memlate.erb` | Sent from `secretary@apache.org`; only shown when applications are closed. |
| generic_reject | (server script, no UI button at present) | `<doctype>.erb` | Generic rejection using a template named by the posted doctype. |
| forward | "forward email" form | none | Builds a new message `Fwd: <subject>` with the original text quoted and the selected attachment converted to PDF, to one of: accounting, board-chair, legal-internal, operations, president, trademarks (all `@apache.org`). |

### 8.1 Rejection reasons

The checklist in the Categorize tab and the `REASONS`/`OTHERS` tables in
`actions/incomplete.json.rb` must be kept in step. Current reasons:

- missing or partial postal address
- missing email address
- incorrect email address (must match the sender)
- corporate residence address
- invalid public name (must be a real name or pen name, not a user id)
- invalid legal name
- invalid language (English only)
- invalid form filler (use a PDF form filler and attach a signature image)
- employee or grant is needed (CCLA)
- separate document and signature (must be in the same email)
- unauthorized signature (CCLA signer must be an authorised person)
- empty form
- unreadable or partial scan
- key data does not match email
- gpg signature validation failed
- gpg signature not armored (must be detached and ASCII-armoured)
- unsigned
- script font (typed name is not a signature)
- undated
- grant missing (Exhibit A not filled)

Two further items are appended as "also need to be fixed" rather than
rejection reasons: **upload signature** (public key to
`keyserver.ubuntu.com`) and **invalid availid** (provide a preferred user id).

---

## 9. ICLA PDF parsing (`actions/parse-icla.json.rb`)

Uses `www/secretary/iclaparser.rb` to read the form fields from a filled
ICLA PDF: FullName, PublicName, FamilyFirst, EMail, ApacheID, Project, and
so on. The project string is normalised (`Apache X (podling) project` → `x`),
mapped to a current PMC or podling, with `commons-*` → `commons`, `log*` →
`logging` and an alias table (`solr` → `lucene`). The original value is kept
as `PDFProject` and shown in the ICLA form when it differs from the selection.

---

## 10. HTTP routes

### Pages

| Method | Path | Returns |
|---|---|---|
| GET | `/` | index page (latest month, plus pending emeritus/withdrawal rows) |
| GET | `/YYYYMM` | JSON: `{mbox: <previous month with pending items or null>, messages: [...]}` |
| GET | `/deleted`, `/YYYYMM/deleted` | deleted-message listing |
| GET | `/pending`, `/YYYYMM/pending` | pending-message listing |
| GET | `/all`, `/YYYYMM/all` | all-message listing with status |
| GET | `/YYYYMM/hash/` | message frameset |
| GET | `/YYYYMM/hash/_index_` | parts pane |
| GET | `/YYYYMM/hash/_body_` | rendered message body |
| GET | `/YYYYMM/hash/_headers_` | YAML dump of the parsed entry |
| GET | `/YYYYMM/hash/_raw_` | raw email (text/plain) |
| GET | `/YYYYMM/hash/_reparse_` | re-parse and rewrite the entry |
| GET | `/YYYYMM/hash/_danger_/<name>` | interstitial for non-PDF/image/text parts |
| GET | `/YYYYMM/hash/<name>` | the attachment with its content type |

### Mutations

| Method | Path | Effect |
|---|---|---|
| DELETE | `/YYYYMM/hash/` | mark message deleted |
| PATCH | `/YYYYMM/hash/` | merge JSON fields into the entry; `attachment_status` clears attachment deletions |
| POST | `/tasklist/<action>` | dry run (form post → HTML) or execute one task (JSON post with `task`) |
| POST | `/actions/<action>` | JSON action (section 7, 8, 11) |

Every mutating route first checks `Status.updates_disallowed_reason` and
returns 503 while the server is in a read-only state (for example during a
migration). `check-mail` and `check-signature` are exempt (`SAFE_ACTIONS`).

### Data endpoints

| Path | Content |
|---|---|
| `/memapp.json` | parsed `memapp-received.txt` for the current meeting |
| `/email.json?id=` | primary email for an availid |
| `/iclas.json` | every ICLA: claRef, id, name, legal name, email |
| `/members.json` | official members list (id, name), including inactive |
| `/icla/<claRef>` | redirect to the ICLA document in SVN |
| `/events` | Server-Sent Events stream of index updates |
| `/actions/check-id` | `{message: 'userid is already taken'}` or empty |
| `/actions/check-mail` | rsync + parse the current month, return its headers |
| `/actions/check-signature` | gpg verification result |
| `/actions/parse-icla` | parsed ICLA fields |

---

## 11. Email composition

- `Message#reply` always threads on the original Message-ID, prefixes `Re:`
  once, and merges addresses: explicit Bcc first (so those addresses are
  suppressed from To/Cc), explicit To, then the original From (moved to Cc
  when an explicit To was given), the original To, the stored per-message Cc
  list, and explicit Cc. Duplicates across fields are removed.
- `_personalize_email` provides the From line (`Name <availid@apache.org>`)
  and signature block.
- Templates live in `templates/` and are plain ERB over the action script's
  instance variables (`@pubname`, `@email`, `@cttee`, `@notify`, `@sig`, and
  so on).
- Delivery is via the `mail` gem configured by `ASF::Mail.configure`.
- Several scripts refuse to proceed when the target address is
  `@apache.org`, since confirmation emails must go to the submitter's own
  address.

---

## 12. Running and operating

### Production

Deployed as a Passenger application from `config.ru`, with the middleware
stack `ASF::HTTPS_workarounds`, `ASF::Auth::MembersAndOfficers`,
`ASF::AutoGC`, `ASF::DocumentRoot`. Mail arrives through procmail into
`deliver.rb`. GPG state lives in `/srv/gpg`.

### Local development

```
rake bundle      # install gems
rake fetch       # rsync the whole archive and parse (first run can take an hour, ~11 GB)
rake fetch1      # only the current month
rake sync        # rsync /srv/mail from whimsy.apache.org instead
rake parse       # re-parse without fetching
rake server      # run under puma with Wunderbar::Listen auto-reload
rake clean       # remove all parsed YAML files
```

Required tools: `gpg`, poppler utilities (`pdfseparate`, `pdfunite`),
`pdfjam`, `img2pdf` (the bundled `img2pdf` wrapper runs the Python module
directly), ImageMagick, `rsync`, `svn`.

Local testing switches: create `/srv/gpg/whimsy_use_db` to stop
`check-signature` fetching keys from the keyserver; the **check for new mail**
button is only rendered when the hostname is not `whimsy*.apache.org`.

### File layout

```
Gemfile, Rakefile, config.ru     dependencies, rake tasks, Rack entry point
config.rb                        ARCHIVE / SOURCE / GNUPGHOME selection
server.rb                        Sinatra routes
deliver.rb                       procmail entry point
parsemail.rb                     batch fetch/parse
tasks.rb                         task / form / complete DSL, svn_multi, template
helpers.rb                       _extract_project, _status, fixup_images, String#fix_encoding
personalize.rb                   _personalize_email
img2pdf                          wrapper around the Python img2pdf module
models/mailbox.rb, message.rb, attachment.rb, events.rb
views/*.html.rb                  page shells
views/*.js.rb                    Vue components (Index, Parts, CheckSignature, HTTP, Status, asciize)
views/forms/*.js.rb              one Vue form per document type
views/actions/*.json.rb          one server script per action
templates/*.erb                  outgoing email bodies
public/secmail.css, tasklist.js, fetch.js, spinner.gif, HOWTO.html
tmp/                             scratch space
```

Related tools under `www/secretary/` that the workbench links to or calls:
`iclaparser.rb` / `icla-parse.cgi` (PDF field extraction), `icla-lint.cgi`,
`public-names.cgi`, `response-time.cgi`, `upload_file.cgi`.

---

## 13. Requirements gathered from the Secretary

Context from discussion with the Secretary: roughly 90% of incoming
documents are ICLAs for new users, so the goal is to make that path as close
to zero-touch as possible. The table maps each request to what the workbench
does today and what is missing.

| # | Request | Today | Gap |
|---|---|---|---|
| 1 | **Normalise every incoming document to one portrait PDF automatically.** Inputs seen: several single-page PDFs (possibly out of order), one PDF with pages reversed, several images (JPEG etc.) that must become correctly ordered pages. | Manual, per attachment: burst, join, drag-and-drop staple, rotate, pdf-ize (section 7). Join converts images to PDF and concatenates in attachment-list order. Nothing detects orientation or page order. | A normalisation step, ideally run at delivery time in `deliver.rb` so the result is ready when the message is opened: convert images, order pages (filename/sequence, OCR'd page numbers, reversed-order detection), detect and fix orientation, emit one PDF and keep the originals available via revert. |
| 2 | **Categorise the document automatically** (ICLA, CCLA, grant, membership application) by OCR or fingerprinting, and extract the unique fields: family name, full name, desired apache.org id with alternatives (separated by space, comma, semicolon or pipe), and the document's origin. Must work with Chinese characters. | `iclaparser.rb` reads AcroForm fields from a fillable ICLA only, on demand from the ICLA form (section 9). No OCR, no classifier, scanned forms yield nothing, the doctype radio is always chosen by hand, and the id field is a single value. | OCR pipeline (for example Tesseract with `chi_sim`/`chi_tra` plus Latin scripts), a document classifier (fingerprint of the layout or title text), field extraction into the YAML entry (`secmail.parsed`), splitting of the id field into a candidate list that is checked with `check-id`, and recording origin (fillable form, scan, DocuSign). The Categorize tab can then preselect the doctype and the form can prefill from the stored data. |
| 3 | **PGP-signed documents: verify, request the key automatically when it is missing, and keep a copy of keys found on a keyserver.** | `check-signature` already fetches the key from `keyserver.ubuntu.com` on every check and commits it to `documents/iclas/__keys__/` (section 4.3). A missing key only shows a red alert; the "upload public key" reply is sent manually. | Run verification at delivery time rather than on selection, and when the key cannot be found send the `pubkey.erb` reply automatically (and record `secmail.status`), so the operator only sees documents that are ready to file. |
| 4 | **Support DocuSign documents; consider a DocuSign integration.** | None. DocuSign PDFs arrive as ordinary attachments with a completion certificate; only detached PGP signatures are verified. | Detect DocuSign envelopes (certificate page or metadata), validate them (certificate parsing or the DocuSign API), and treat a valid envelope as a signed document in the signature check and in the "unsigned" rejection logic. |
| 5 | **Rejection email lists every reason that applies.** | Done: the Categorize tab has a checklist and `incomplete.erb` lists all selected reasons (section 8.1). | None. |
| 6 | **The Secretary maintains the list of rejection reasons and the explanation text that goes into the email.** | Hard-coded twice, in `views/parts.js.rb` (checkboxes) and `views/actions/incomplete.json.rb` (`REASONS`/`OTHERS`), which must be kept in sync by a code change. | Move reasons to a single data file (YAML in SVN or in the repo) that drives both the checkboxes and the email, with a small edit page for the Secretary. |
| 7 | **Unreadable documents get a reply recommending the submission form.** | The "unreadable or partial scan" reason exists, and `incomplete.erb` already links to `https://www.apache.org/licenses/contributor-agreements.html#submitting`. | A dedicated template or wording that explicitly recommends the online form, and possibly automatic sending when OCR confidence is too low. |
| 8 | **Side-by-side viewer when a potential duplicate is found.** | The ICLA dry run warns "Found possible duplicate ICLAs" with links to the existing files in SVN (section 6.1); the operator opens them separately. | Split the content frame to show the existing ICLA next to the new one, with the extracted fields (name, email, id, address) compared. |
| 9 | **A PDF form with built-in validation**: reject apache ids that are not lowercase or contain special characters, and refuse submission with missing required fields. | Outside the workbench. The ICLA form is published at `https://www.apache.org/licenses/contributor-agreements.html#submitting`; the workbench validates the id only after the fact (`check-id`, pattern `^[a-z][a-z0-9]{2,}$`). | Add AcroForm JavaScript validation to the published form (field patterns, required fields). Not every PDF viewer runs form scripts, so server-side validation in item 2 remains necessary. |

Common thread: items 1 to 4 and 7 all benefit from a processing step at
delivery time whose results are stored in the message's YAML entry. The
existing UI would then preselect the doctype, prefill the form, show the
signature result, and only ask the operator to confirm.

## 14. Around the workbench: submission, invitation, accounts, membership

The workbench is the middle of a longer chain. This section describes the
pieces on either side of it as they exist today, because the replacement
has to fit, or change, that chain.

### 14.1 How documents are submitted today

There is no online submission form. The public page
https://www.apache.org/licenses/contributor-agreements.html#submitting tells
the submitter to:

- fill in the PDF (`icla.pdf`, `cla-corporate.pdf`,
  `software-grant-template.pdf`) with a PDF form filler;
- sign electronically, by attaching a graphic signature, or with a detached
  PGP signature (`gpg --armor --detach-sign`, public key uploaded to
  `keyserver.ubuntu.com`, never sent to the ASF);
- email one document per message, as an attachment named `icla.pdf`,
  `ccla.pdf` or `software-grant.pdf` (plus `.asc`), from the address written
  in the document, to `secretary@apache.org` with nobody copied.

Postal mail, fax, print-and-scan, zip files and links are refused. The only
validation rule stated is for the preferred Apache id (a letter followed by
at least two alphanumerics, nothing else). The PDF itself does not enforce
it; the workbench rejects invalid ids at filing time (section 6).

Everything else the Secretary asked for in section 13 (normalisation,
classification, OCR) exists because this submission path is free-form email.

### 14.2 The invitation demo at `www/project/icla/`

Whimsy contains a Sinatra/Vue application titled "ASF ICLA demo" that
models the committer invitation process end to end. It is wired up in the
Docker development configuration (`/project/icla`) but every page says it is
a demo: state is written to `/srv/icla/<token>.json`, nothing is committed
to SVN, and the final page lists what a real application would have done.

The flow it demonstrates:

1. **Invite** (`pages/invite.js.rb`). A PMC or PPMC member picks one of
   their projects, enters the contributor's name and email, and walks
   through three phases, `[DISCUSS]`, `[VOTE]` and invite, each with a
   previewed email to the project's private list. The invite phase requires
   a link to the `[RESULT][VOTE]` thread and, for podlings, a link to the
   board or incubator NOTICE thread.
2. **Discuss** and **vote** (`pages/discuss.js.rb`, `pages/vote.js.rb`,
   `actions/update.json.rb`). Members of the project comment and vote
   (`+1 +0 -0 -1`, a `-1` needs a comment) on a page addressed by the
   token. The tally is mailed to the private list as `[RESULT]`.
3. **Interview** (`pages/interview.js.rb`). The invitee follows the emailed
   link, confirms name and email, answers three questions about the
   ownership of their contributions, then fills in address, country,
   telephone, preferred Apache ids and the project to notify.
4. **Draft ICLA** (`actions/draft-icla.json.rb`). The server rejects Apache
   ids already in use, records the submitter's IP address, and renders the
   full ICLA text with the answers filled in for review.
5. **Complete** (`pages/complete.js.rb`). States that a real application
   would commit the file to SVN with the IP address in the commit message,
   email the PMC, and submit an account request when an id was given.

No link from the production site points at it, and the account request,
SVN commit and signature steps were never built. It is nonetheless the only
existing design for a submitter-facing form and for tying an ICLA to a
project's vote.

### 14.3 From filed ICLA to account

An ICLA on file does not create an account. The two paths that do:

- **At filing time** (`actions/icla.json.rb`). If the operator fills in both
  a project and an Apache id on the ICLA form, and pastes a vote link, the
  workbench appends a line to `infra/acreq/new-account-reqs.txt` and mails
  `root@apache.org` with the submitter and the project's private list
  copied. The confirmation sent to the submitter is then
  `icla-account-requested.erb`. If only a project is given, the
  confirmation is `icla-pmc-notified.erb`, which copies the project's
  private list and asks the submitter to tell the project their preferred
  id. With neither, the plain `icla.erb` goes out and no project hears
  anything.
- **Later, by the project** (`www/officers/acreq.cgi`). A chair or member
  opens the account request helper, which lists people whose `iclas.txt`
  line still starts with `notinavail` (derived from the recent diff of the
  file, `ASF::ICLA.unlisted_name_by_email`), picks one, chooses an Apache
  id that is not taken in `iclas.txt`, the mail aliases or the pending
  requests, selects the project and supplies a vote link or comment. The
  script appends the same `new-account-reqs.txt` line and sends the same
  `[FORM] Account Request` mail, copying root, secretary, the project's
  private list and the person.

So the signal from the Secretary back to the project is a copy of the
acknowledgement email when the ICLA names a project. If the submitter left
the "notify project" field empty or named it in a way the parser did not
recognise, the project only finds out by asking the person or by watching
the `acreq` helper's list. Nothing links the project's vote, the invitation
email, the ICLA and the account request to each other except free-text
vote links.

### 14.4 Membership applications and the meeting

Membership applications are checked against the meeting, but indirectly:

- After the members meeting, the Secretary creates
  `Meetings/<date>/memapp-received.txt` from `nominated-members.txt` and the
  vote results, one line per elected nominee with four flags (`invite`,
  `apply`, `mail`, `karma`), an Apache id and a name. The runbook in the
  Meetings directory describes this; `www/members/check_invitations.cgi`
  cross-checks nominations, invitations sent to `members@` and the file.
- The workbench membership application form (`forms/memapp.js.rb`) loads
  that file through `/memapp.json` (`ASF::MeetingUtil.parse_memapp_to_h`)
  and offers only the listed people in its applicant drop-down. If the file
  is missing, the form refuses with a message pointing at the runbook. The
  operator must therefore match the incoming application to an elected
  nominee by hand, but cannot file one for anyone not on the list.
- Filing (`actions/memapp.json.rb`) sets the `apply`, `mail` and `karma`
  flags for that line to `yes`, adds the person to `members.txt` and to the
  LDAP `member` group, and drops subscription requests.
- Section 2 of this document describes the application window: the form
  is only offered while `ASF::MeetingUtil.applications_valid` is true for
  the message's envelope date; afterwards the late-application reply is the
  only option.

The verification is thus "is this person on the elected list", enforced by
the drop-down. There is no check that the name or email in the application
matches the nominee, only the operator's judgement.
