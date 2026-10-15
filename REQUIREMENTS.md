# Secretary Workbench Replacement: Requirements

*DRAFT*

This document captures the requirements for a replacement of the Whimsy
Secretary Workbench. The replacement takes the same technological shape as
the Board Agenda Tool (BAT, `apache/tooling-agenda`): a Python/Quart service
with a Svelte front end, ASF OAuth, SVN as the source of record, and a
per-role in-app documentation page.

Two companion documents:

- `WORKBENCH.md` in this repository describes the current tool in detail, and
  is the baseline for every "preserve" requirement below.
- `CONTRIBUTING.md`, `AGENTS.md` and `docs/` in `apache/tooling-agenda`
  describe the conventions the replacement inherits.

Each requirement carries a tag:

- **preserve**: the current workbench does this and the replacement must too.
- **change**: the current behaviour exists but must be done differently.
- **new**: requested by the Secretary; not in the current tool.

---

## 1. Purpose and scope

The tool processes the paperwork that arrives at `secretary@apache.org`:
ICLAs, CCLAs, software grants, membership applications, emeritus requests and
withdrawal requests. For each document it must get from "email received" to
"filed in SVN, indexes updated, directory updated, submitter answered" with
as few operator decisions as possible. About 90% of incoming documents are
ICLAs for new contributors, so that path is the one to optimise.

In scope: intake, normalisation, classification, verification, filing,
replies, the work queue, audit, and the secretarial reports that today live as
separate CGI scripts beside the workbench (candidates listed in section 3.11).

Out of scope: the roster tool, the board agenda, and the public submission
page on `apache.org`, except where section 3.12 asks for changes to the form
it serves.

---

## 2. Audience and roles

| Role | Who | What they do |
|---|---|---|
| Secretary | the officer | everything; signs outgoing mail with the office title |
| Assistant Secretary | the officer | everything; signs with the office title |
| Secretarial team | members appointed to help | everything except maintaining configuration; signs as "Secretarial Team" |
| ASF member | any member | read-only view of the queue and history (open question 1) |
| Admin | Tooling/Infra | configuration, server status, data browser, read-only mode |
| Submitter | the person who sent the document | never logs in; interacts only by email |

- **R-1 (change)** Mutating actions are limited to the secretary roles above.
  Today anyone who passes the members-and-officers login can file documents.
- **R-2 (preserve)** The signature and From line of outgoing mail depend on
  the role of the logged-in operator.
- **R-3 (new)** Roles come from `committee-info.txt` (the Secretary and
  Assistant Secretary offices) and from a configured list for the secretarial
  team, in the same way BAT derives `Role.SECRETARY`.

---

## 3. Functional requirements

### 3.1 Intake

- **F-1 (preserve)** Every email delivered to `secretary@apache.org` is
  stored raw, byte for byte, and indexed by a stable id derived from its
  Message-ID (falling back to a hash of the whole message).
- **F-2 (preserve)** Intake is idempotent: redelivery of the same message does
  not create a second work item.
- **F-3 (preserve)** Intake survives malformed mail: bad encodings,
  unparsable From headers, missing Message-IDs, invalid dates. A message that
  cannot be parsed still appears in the queue with the error attached.
- **F-4 (preserve)** Only messages with attachments become work items. A
  plain-text message whose subject mentions a CLA (but not an ICLA, and not a
  reply) is treated as a work item with the message body as its document.
- **F-5 (preserve)** The parsed entry records envelope date, envelope sender,
  Date header, sender name and address, and the list of other recipients, so
  replies can copy the same people.
- **F-6 (change)** Intake is decoupled from the web process and replayable:
  the raw store plus the pipeline must be enough to rebuild all derived state.
  Today intake is a procmail hook into the web application's own code.
- **F-7 (new)** Intake records the document's origin where it can be
  determined: fillable PDF form, scan or photo, DocuSign envelope, online
  submission form.
- **F-8 (preserve)** The queue updates live as messages arrive and as other
  operators act, without reloading.

### 3.2 Normalisation (new)

Goal: every document reaches the operator as one portrait PDF, with the
originals kept.

- **F-10 (new)** The pipeline runs automatically on arrival, before anyone
  opens the message.
- **F-11 (new)** Inputs it must handle: several single-page PDFs, possibly out
  of order; one PDF with pages in reverse order; several images (JPEG, PNG,
  HEIC where feasible) that become pages; a mix of the above.
- **F-12 (new)** Page order is decided from file names, sequence numbers,
  detected page numbers on the page, and a reversed-order check. Orientation
  is detected and corrected (rotate right, left, flip).
- **F-13 (new)** The result is one PDF per logical document (document and
  signature file stay separate). Mail-client signature parts and inline
  images are not treated as documents.
- **F-14 (new)** Every step is recorded (what was done, in what order, by
  which tool) so the operator can see why the document looks the way it does.
- **F-15 (preserve)** The operator can override: burst into pages, join,
  staple one attachment onto another, rotate, convert to PDF, delete, and
  revert to the original as received. These act on the derived copy; the raw
  email is never modified.

### 3.3 Classification and extraction (new)

- **F-20 (new)** The document type is identified automatically: ICLA, CCLA,
  software grant, membership application, emeritus request, withdrawal
  request, or unknown. Identification uses form fields when present and OCR
  or layout fingerprinting otherwise.
- **F-21 (new)** OCR and extraction must work with Chinese characters (and
  other non-Latin scripts) in names and addresses.
- **F-22 (new)** Fields extracted per type, at least: full legal name, public
  name, family name (and whether family name comes first), email, residence
  address, project, desired apache.org id with alternatives (split on space,
  comma, semicolon or pipe), signature date, signer and company for
  CCLAs and grants, and the list of covered employees or the grant
  description.
- **F-23 (new)** Each extracted value carries a confidence and its source
  (form field, OCR, mail header). The form shows which values could not be
  checked against the document, as the current ICLA form highlights them.
- **F-24 (preserve)** The extracted project is normalised to a current PMC or
  podling, with the original wording kept for display.
- **F-25 (preserve)** Candidate ids are checked for availability against
  existing ICLAs and mail aliases, and the first available candidate is
  proposed.
- **F-26 (new)** The extracted email is compared with the sender; a mismatch
  is flagged before filing.
- **F-27 (preserve)** Duplicate detection against `iclas.txt` by file name,
  email and name, run before filing.
- **F-28 (new)** When a potential duplicate is found, the operator gets a
  side-by-side view of the existing filed document and the new one, with the
  extracted fields compared.

### 3.4 Signature verification

- **F-30 (preserve)** A detached PGP signature (`.asc`/`.sig`) is matched to
  its document and verified with GPG; the result is shown with the output.
- **F-31 (preserve)** The signing key is fetched from the configured
  keyserver (currently `keyserver.ubuntu.com`, with a size limit) on every
  verification, and a copy is committed to `documents/iclas/__keys__/` with
  the email properties attached, so the key is kept for the record.
- **F-32 (new)** Verification runs on arrival, not when the operator selects
  the attachment.
- **F-33 (new)** When the key cannot be found, the "please upload your public
  key" reply is sent automatically and the message is marked as awaiting the
  submitter. The operator is told, and can suppress automatic replies per
  message.
- **F-34 (new)** DocuSign envelopes are recognised, their completion
  certificate validated (certificate parsing, or the DocuSign API if an
  integration is approved), and a valid envelope counts as a signed document.
- **F-35 (preserve)** A document with a good hand signature and a bad digital
  signature can be filed without the signature file.

### 3.5 Work queue

- **F-40 (preserve)** The queue lists unprocessed work items newest first
  with timestamp, sender and subject, spanning months as needed (the current
  tool shows the previous month during the first week of a month).
- **F-41 (new)** Queues by state, in the BAT style: ready to file (classified,
  verified, no warnings), needs attention (unknown type, failed
  verification, duplicate, mismatch), awaiting submitter (reply sent),
  pending emeritus and withdrawal requests (with their 10-day timers), and
  deleted.
- **F-42 (preserve)** Pending emeritus and withdrawal requests filed in SVN
  appear in the queue with their age and a link to the roster page, coloured
  differently once the waiting period has passed.
- **F-43 (preserve)** Keyboard model: up/down select, Enter opens,
  Ctrl/Cmd+Backspace deletes (spam), Ctrl/Cmd+Z undoes the last delete, with
  the selection remembered across navigation.
- **F-44 (preserve)** A work item can be deleted and undeleted, including
  restoring attachments deleted along with it.
- **F-45 (preserve)** Per-item notes and a status line, visible in the queue
  and on the item.
- **F-46 (preserve)** Per-item editable Cc and Bcc lists used by every reply.
- **F-47 (preserve)** Listing pages for deleted, pending and all items per
  month, with navigation between months.

### 3.6 Viewing a work item

- **F-50 (preserve)** Views of the original: rendered body with selected
  headers (inline images resolved), parsed headers, raw message.
- **F-51 (preserve)** Attachments of safe types (PDF, text, images) open
  inline. Any other type goes through an interstitial that shows the part's
  headers before it can be opened.
- **F-52 (preserve)** HTML mail is sanitised before rendering.
- **F-53 (new)** The normalised PDF, the extracted fields, the verification
  result and the pipeline log are shown together, so the common case is "read
  the summary, confirm, file".

### 3.7 Filing workflows (preserve, with changes noted)

The replacement must carry out exactly the side effects the current tool
does, listed here per document type. Index files in SVN remain the source of
record.

| Type | SVN documents | Index update | Directory | Mail |
|---|---|---|---|---|
| ICLA | `documents/iclas/<stem>.<ext>`, or `<stem>/icla.<ext>` + `icla.<ext>.asc` when signed; email properties set | `officers/iclas.txt` line `notinavail:<legal>:<public>:<email>:Signed CLA;<file>`, sorted | none | confirmation to submitter; template depends on whether a project was named and an account requested; (P)PMC private list copied and set as Reply-To |
| ICLA with account request | as above | as above plus `infra/acreq/new-account-reqs.txt` line | none | as above plus account request to `root@` copying submitter and project |
| Additional ICLA | move existing file into `<stem>/`, add `<stem>/iclaN.<ext>` | replace the person's `iclas.txt` line | today: update `cn` and `mail` in LDAP; replacement: emit a person-changed event (F-64) | confirmation to all of the person's addresses |
| CCLA | `documents/cclas/...` | append to `officers/cclas.txt` | none | confirmation to contact, project copied |
| Software grant | `documents/grants/...` | insert block in `officers/grants.txt` after the registering marker | none | confirmation to signer, project copied |
| Membership application | `documents/member_apps/...` | new entry in `foundation/members.txt`, count and sort updated; flags in `Meetings/<date>/memapp-received.txt` set to yes | today: add to `cn=member` group; replacement: emit a membership-granted event (F-64) | welcome letter; subscription requests for `members@` and `members-notify@` dropped in `subreq` |
| Emeritus request | `documents/emeritus-requests-received/<availid>.<ext>` | none (processed later from the roster tool) | none | acknowledgement |
| Withdrawal request | `documents/withdrawn/pending/<availid>.<ext>` | none | none | acknowledgement |

- **F-60 (preserve)** File names are validated (`[a-zA-Z][-\w]+`), generated
  from the asciized legal name (family-first aware) and must not collide with
  an existing document.
- **F-61 (preserve)** Membership application filing is offered only while the
  meeting's application window is open; outside it, the late-application
  reply is offered instead.
- **F-62 (preserve)** Every filed document gets the SVN properties
  `email:addr`, `email:id`, `email:name`, `email:subject`, `envelope:from`,
  `envelope:date` and `svn:mime-type`.
- **F-63 (change)** Commits are made through the tool's SVN role account with
  `asf:uid` set to the operator, as BAT does, rather than with the operator's
  own password.
- **F-64 (change)** The tool does not write to LDAP. Where the workbench
  today updates a person's `cn` and `mail` or adds them to the `member`
  group, the replacement records the change in SVN (the source of record)
  and emits a record-change event for the roster and directory service to
  apply, in the same way the Board Agenda Tool is being split from direct
  LDAP writes. LDAP is read-only for this tool. The event carries the
  acting operator, the SVN revision and the document that justifies the
  change, so the receiving service can audit it.
- **F-65 (preserve)** Confirmation mail is never redirected to an
  `@apache.org` address.
- **F-66 (change)** A membership application is only filed for a person on
  the elected list for the current meeting
  (`Meetings/<date>/memapp-received.txt`, built from `nominated-members.txt`
  and the vote). Today the form's drop-down enforces this; the replacement
  additionally matches the extracted name and email of the application to
  the nominee and flags a mismatch, offers the nominee's invitation record
  for comparison, and refuses to file an application for someone whose
  `invite` flag is not set. An application from someone not elected gets a
  reply explaining that, instead of being filed.

### 3.8 Replies and rejections

- **F-70 (preserve)** Replies thread on the original message, prefix `Re:`
  once, and merge To/Cc/Bcc from the original and the action with duplicates
  removed across fields. The secretary address is copied or blind-copied
  according to who owns the next step.
- **F-71 (preserve)** Reply kinds: incomplete (with reasons), resubmitted
  form received, upload public key, clarify intent, late membership
  application, and forward to accounting, board-chair, legal-internal,
  operations, president or trademarks with the document attached as PDF.
- **F-72 (preserve)** A rejection lists every reason that applies, plus the
  "also needs fixing" items (upload signature key, provide an id).
- **F-73 (new)** The list of rejection reasons, their short labels and the
  explanation text that goes into the email are data the Secretary maintains
  in the tool (or in a file in SVN the tool reads), not code. Checklist and
  email are rendered from the same source.
- **F-74 (new)** An unreadable or unclassifiable document gets a reply that
  recommends using the online submission form, and can be sent automatically
  when extraction confidence is below a configured threshold.
- **F-75 (preserve)** Every outgoing email is shown in full before it is
  sent, and the templates remain editable by the team (Jinja, in the
  repository, as BAT's email templates are).

### 3.9 Carrying out an action

- **F-80 (preserve)** Every filing or reply is shown first as the list of
  operations it will perform, with the exact lines to be added to index
  files and the full email text, and warnings that require "proceed anyway".
- **F-81 (preserve)** Operations run in order; each shows its transcript; a
  failure stops the run and the operator chooses to continue or abort.
- **F-82 (change)** Each operation is idempotent and resumable so a run
  interrupted after the SVN commit does not repeat the commit when resumed.
  Today a partial run leaves the operator to work out what happened.
- **F-83 (new)** Before any action is chosen, the item shows a projection of
  what filing it would do, in the way BAT's post-meeting checklist projects
  work before it is minuted.
- **F-84 (preserve)** When an action completes, the processed attachment and
  its signature are removed from the item, and the item leaves the queue once
  nothing is left.

### 3.10 Records and audit

- **F-90 (new)** Every action is recorded with operator, time, inputs, SVN
  revisions produced, events emitted and emails sent, and is viewable per item
  and per month, like BAT's revision logs.
- **F-91 (preserve)** Response-time reporting (today `response-time.cgi`):
  time from receipt to filing, per month.
- **F-92 (new)** Pipeline and verification results are kept with the item so
  the record shows why a document was accepted.

### 3.11 Related secretary tools (scope decision open)

The following CGI scripts live beside the workbench and are candidates to be
absorbed, since they share its data and audience: `icla-lint`,
`icla-dupes`, `public-names`, `ldap-check`, `ldap-check-banned`,
`ldap-names`, `memapp_check`, `emeritus_check`, `response-time`,
`upload_file`. See open question 2.

### 3.12 Submitter side

There is no online submission form today: `apache.org` publishes fillable
PDFs and asks for them by email (WORKBENCH.md section 14.1). Whimsy holds an
unfinished demo of a submitter-facing form at `www/project/icla/`
(section 14.2), which is the only existing design for one.

- **F-100 (new)** The ICLA PDF form published on `apache.org` gains built-in
  validation: apache id lowercase, letters and digits only, minimum length;
  required fields present; email well-formed. This is a change to the form,
  not to the tool, and server-side validation in section 3.3 stays necessary
  because not every PDF viewer runs form scripts.
- **F-102 (new)** The "notify project" field on the PDF form becomes a
  choice field (an AcroForm combo box) listing the current PMCs, podlings
  and other committees with LDAP groups, so the submitted value is a
  committee name rather than free text to normalise. Today that
  normalisation strips "Apache", "(podling)" and "project", maps
  `commons-*` and `log*` prefixes, and consults an alias table, and still
  fails often enough that the filing form shows the raw value beside the
  guess (WORKBENCH.md section 9). Consequences the tool has to own:
  - The form is generated, not hand-edited. The tool, or a job beside
    it, regenerates the PDF whenever `committee-info.txt` or the podling
    list changes and publishes it to the website, with a version or
    generation date in a hidden field so the parser knows which
    vocabulary a submitted form used.
  - The export value of each entry is the stable committee name; the
    display text is the display name. The parser reads the export value
    when the form data survived, and falls back to matching the display
    text when the PDF was flattened, printed to PDF or scanned.
  - The combo box stays editable so a stale copy of the form, which
    people keep for months, can still name a project established after
    it was generated; those values go through the old normalisation.
  - Projects terminated after the form was generated show up as unknown,
    which the work item flags the same way it does today.
  - The same generated list feeds the online submission form (F-101) and
    the invitation form (F-110), so all three agree on the vocabulary.
- **F-101 (new)** The tool offers an online submission form, reached from
  the invitation link in section 3.13 or from the public page. It collects
  the ICLA fields with validation, produces the agreement for the submitter
  to sign (electronically or by PGP), and delivers the result into the
  same queue as email, with origin and fields already known so the
  normalisation and OCR steps are skipped. Email submission stays supported
  for people who cannot or will not use the form.

### 3.13 Invitation and account request (new)

Today a project votes on a new committer on its private list, a member
sends an invitation by hand asking for an ICLA, and the Secretary files the
ICLA when it arrives. Nothing ties the three together, and the project
learns the ICLA is on file only if the submitter named the project on the
form, which makes the Secretary copy the project's private list. The
account itself is requested either by the Secretary at filing time, when
the form carries an id, a project and a vote link, or later by the project
through the `acreq` helper, which lists everyone with an ICLA and no id
(WORKBENCH.md section 14.3).

- **F-110** A PMC or PPMC member can register an invitation: project, the
  person's name and email, the vote result link, and for podlings the
  NOTICE link, as the Whimsy demo collects them. The tool records it as an
  expected ICLA and sends the invitation email with a personal link to the
  submission form (F-101).
- **F-111** An incoming ICLA, whether through the form or by email, is
  matched to open invitations by email address, name and the project named
  on the form (F-102), and the match is shown on the work item. The ICLA filing form is then prefilled with the
  project and vote link, so the operator does not retype them.
- **F-112** When an ICLA matched to an invitation is filed, the project is
  told, by email to its private list and by the invitation's status in the
  tool, that the agreement is on file. The notification is sent regardless
  of what the submitter wrote in the "notify project" field.
- **F-113** The invitation carries the preferred Apache id, chosen by the
  invitee on the form and validated for availability against `iclas.txt`,
  the mail aliases and pending requests, so that the account request can
  be prepared without a second round trip.
- **F-114** An ICLA filing does not itself request an account. The account
  request is a separate step the project triggers from the invitation
  record once the ICLA is on file, unless the project opted at invitation
  time to have it raised automatically. Either way the request is emitted
  as a record-change event (F-64) rather than written directly, with the
  `new-account-reqs.txt` line and `root@` mail kept only as long as the
  account service needs them.
- **F-115** Invitations that have not produced an ICLA after a configurable
  period are listed to the project with the option to remind or withdraw,
  replacing the project's current habit of polling the `acreq` list.
- **F-116** The `acreq` helper's function, requesting an account for an
  existing ICLA without an invitation, remains available for the cases an
  invitation did not precede, with the same validation.

---

## 4. Non-functional requirements

- **N-1 Performance** Opening a work item shows the summary within one
  second; the pipeline for a typical ICLA completes within a minute of
  delivery.
- **N-2 Availability and read-only mode** The tool can be put into a
  read-only state (today `Status.updates_disallowed_reason`) during
  maintenance; viewing keeps working, and the reason is shown.
- **N-3 Privacy** Documents contain residence addresses and other personal
  data. Access is limited to the roles in section 2, logs and telemetry never
  contain document contents or extracted fields, derived files are kept
  under the same access control as the raw mail, and retention of raw mail
  and derived files follows a documented policy (open question 3).
- **N-4 Security** Attachments are untrusted input: converters and OCR run
  with resource limits and no network; HTML mail is sanitised; non-safe
  attachment types need the interstitial; keyserver downloads are
  size-limited; file names written to SVN are validated.
- **N-5 Internationalisation** Names and addresses in any script are stored,
  displayed and OCR'd correctly; file names are asciized with the existing
  rules (`ASF::Person.asciize` equivalent).
- **N-6 Accessibility and devices** Keyboard-first operation as today;
  usable at tablet width; dark mode, as BAT's design book requires.
- **N-7 Observability** Server status page shows intake health (last message
  received, pipeline backlog, failed items), pubsub and keyserver health, and
  why an automatic action is waiting, as BAT's status page does.
- **N-8 Testability** The pipeline, extraction and verification are testable
  against a fixture corpus of synthetic emails and documents that contains no
  real personal data, so the dev instance never needs production mail.

---

## 5. Technology and infrastructure requirements

Mirrors the Board Agenda Tool so that the Tooling team can maintain both.

| Layer | Requirement |
|---|---|
| Language | Python 3.12+; no Ruby. The current tool's Ruby, Wunderbar and ruby2js stack is not maintainable by Tooling or Infra. |
| Web framework | Quart (async) via `asfquart`; routes under `app/api/routes/`, pages under `app/client/`, admin under `app/admin/`. |
| Authentication | ASF OAuth through `asfquart`; no passwords held by the app. A dev-only test login route for end-to-end tests, as BAT's `/e2e/login/<uid>`. |
| Authorisation | Role enum derived from LDAP and `committee-info.txt`; `require_role` on every mutating route. |
| Front end | Svelte 5 runes, TypeScript, Vite; server-rendered Jinja pages with Svelte islands; Bootstrap 5 with the BAT design book tokens and component set. |
| Live updates | WebSocket, replacing Server-Sent Events. |
| State | SQLite with SQLModel/SQLAlchemy and sequential Alembic migrations that auto-apply at startup; new tables registered in the admin data browser. The database is workflow state and cache; SVN files stay the source of record and the database must be rebuildable from raw mail plus SVN. |
| Files | Raw mail and derived PDFs under a configured `data_dir`/`state_dir`, never in the repository. |
| SVN | Commits through the tool's role account with `asf:tool` and `asf:uid` revision properties and "committed on behalf of" in the message; `svnmucc` for multi-path commits, as `app/svn.py` does. Working copies of `officers/`, `foundation/`, `documents/` and `Meetings/` refreshed by pubsub. |
| LDAP | `asfpy` async LDAP, read-only, for roles and lookups. Person and group changes are emitted as record-change events (F-64) for the roster and directory service; the event transport is to be shared with the Board Agenda Tool's split. |
| Mail out | `aiosmtplib` to the Foundation relay; no SMTP block in config means no mail is sent. |
| Mail in | A delivery path that does not require the web process to run on the mail host: pipe from the relay into an intake endpoint, LMTP, or IMAP polling of the `officers-secretary` archive (open question 4). |
| Pubsub | `asfpy.pubsub` listener for changes to the SVN paths above, with the reconnect and catch-up behaviour BAT has. |
| Scheduled work | `quart-tasks` cron jobs: emeritus and withdrawal timers, re-verification of pending signatures, reminder to submitters awaiting a reply, intake backlog checks. |
| External tools | GPG; poppler or `pikepdf`/`pypdf` for split, merge and rotate; `img2pdf`; Tesseract with CJK language packs (or an equivalent OCR engine, open question 5); all invoked with timeouts and resource limits. |
| Configuration | `config.yaml` with defaults in `app/config.py`, written by Puppet in production; feature flags for automatic replies and DocuSign. |
| Telemetry | OpenTelemetry route and database telemetry as in BAT, with document contents excluded. |
| Documentation | In-app Documentation page from markdown per role (`app/md/documentation/`), changed in the same PR as the behaviour; `docs/` for maintainers; the design book covers every new component. |
| Tests | pytest with coverage; vitest logic and component suites; Playwright end-to-end against a fixture mail corpus; the quality ratchet and pre-commit hooks from BAT; CI on every PR. |
| Development | Devcontainer and Compose with everything containerised; `make` targets matching BAT's; synthetic fixtures so a dev instance never holds production data. |
| Deployment | Puppet-managed hosts pulling from the repository, systemd `pipservice` unit, dev and prod instances, listening on localhost behind the Infra proxy. |
| Repository | `apache/tooling-secretary` (name to be confirmed), Apache License 2.0, `.asf.yaml` notifications to Tooling, all ASF members with push access as for BAT. |

---

## 6. Data model sketch

Workflow state the database holds; everything filed stays in SVN.

- **Submission**: id (from Message-ID), received at, envelope and header
  fields, sender, recipients to copy, origin, status (new, needs attention,
  awaiting submitter, filed, deleted), notes, cc/bcc overrides, raw file
  path.
- **Attachment**: submission, name, content type, size, original part
  reference, current derived file, deleted flag, pipeline log.
- **Classification**: attachment, document type, confidence, extracted
  fields with per-field source and confidence, candidate ids, duplicate
  matches.
- **Verification**: attachment, signature attachment, method (PGP,
  DocuSign), key id, result, output, key archived revision, checked at.
- **Action**: submission, kind (file ICLA, reject, forward, …), operator,
  started and finished at, steps with status and transcript, SVN revisions,
  events emitted, emails sent.
- **Invitation**: project, invitee name and email, inviting member, vote
  and NOTICE links, preferred Apache id, status (sent, ICLA received, ICLA
  filed, account requested, withdrawn), linked submission, reminders sent.
- **RejectionReason**: key, label, explanation, kind (reason or also-fix),
  active flag, order.
- **AutomaticReply**: submission, kind, sent at, suppressed by.

---

## 7. Migration and cutover

- **M-1** Import the existing month archives (`YYYYMM.mail/` and `YYYYMM.yml`)
  so history, deleted items and notes carry over.
- **M-2** Run beside the Whimsy workbench on the same mail feed until the
  secretarial team signs off, then switch delivery and retire the Whimsy
  tool with redirects from its URLs.
- **M-3** Move the `__keys__` archiving, email SVN properties and index file
  formats over unchanged, since other tools read them.
- **M-4** Decide the fate of the CGI tools in section 3.11 before the Whimsy
  secretary directory is retired.

---

## 8. Open questions

1. Do ASF members at large get read access to the queue, or only the
   secretarial roles? Today any member can log in and act.
2. Which of the related CGI tools move into the replacement, and which are
   retired or moved elsewhere?
3. Retention policy for raw mail, derived files and OCR text, given the
   personal data they hold.
4. Mail delivery path into the tool: pipe from the relay, LMTP, or IMAP
   polling of the existing archive.
5. OCR engine: Tesseract with CJK packs, or a hosted service; the choice
   affects where document contents travel.
6. DocuSign: certificate validation only, or an account and API integration?
7. The submission form (F-101) and invitation flow (section 3.13) are new
   user-facing surfaces for projects and invitees, outside the secretarial
   roles. Do they live in this tool, or in the roster tool, with this tool
   consuming the invitation record?
8. Record-change events (F-64, F-114): transport, schema and the consumer
   on the roster and directory side are to be defined together with the
   Board Agenda Tool's split, so the two tools emit the same kind of event.
