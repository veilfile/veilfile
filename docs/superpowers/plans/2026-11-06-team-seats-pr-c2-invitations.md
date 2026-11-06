# Team seats, PR C2: Invitations and the Members Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an administrator invite people by email, set their allowances and choose who sees new files, and let an invited person join from a Free account, all behind a switch that is off in production.

**Architecture:** A setting, `TEAM_SEATS`, read from `VEILFILE_TEAM_SEATS`, hides every new address and every new line on a page. `drop_core/teams.py` holds the rules: seats, invitations, joining, allowances and the visibility setting. Every seat change takes the workspace row first, the lock `upload_artifact` and billing's `switch` already take. A new `Invitation` row holds a seat for 7 days, and its token is stored only as a hash. The pages are built from the dashboard's existing pieces and add no script.

**Tech Stack:** Django 6.1.1, Django REST framework, django-oauth-toolkit, SQLite in tests, Postgres 16 in the rehearsal, Postgres in production. Playwright for the site checks.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md` (decisions 1 to 3, 6, 8, 9, 14 to 16; calls 3, 6, 9, 12; section 9 questions 1 and 4). The plan for C1, `docs/superpowers/plans/2026-11-05-team-seats-pr-c1-one-place.md`, holds the table of the three pull requests that replace row C, and its last section, "Carried to C2 and C3", is this plan's starting list.

## Decisions this plan takes

The owner reviews these with the plan. Each says what it costs if wrong.

1. **The switch is a setting read at start-up,** `TEAM_SEATS = os.environ.get("VEILFILE_TEAM_SEATS", "") == "on"`. Turning it on takes a restart, which Render does on any environment change. With it off, a GET of every new address answers the shell's 404, signed in or out, and no existing page changes by a byte. A POST without a CSRF token still answers "Form not sent", and an address with no trailing slash still redirects, because that middleware runs first. That reveals only that a route exists, in a public repository. If wrong: a restart to flip it, nothing more.
2. **Accepting needs a signed-in account whose verified address equals the invited one.** A new account is signed in before it verifies and can type any address, so the link alone proves nothing about who is signing up, and a forwarded link must not let someone else join. A new person therefore signs up, verifies, and opens the link again. That is one more step than a link that signs you in. If wrong: a shorter path can be added later without weakening anything.
3. **The Members page hangs off Overview, and adds no sixth menu section.** The menu has no way to show a link only to administrators, and seven tests pin five sections. An administrator on a paid plan gets a Team section on Overview that links to it. This is the one change call 3 allows before someone invites anyone, because without it nobody could find the page. Free accounts see nothing new. If wrong: a menu link is a small change once the menu can filter by role.
4. **There is no team name yet.** `Workspace.name` is `<username>'s workspace`, which no person chose, and a free-text name would put an attacker's words in an email from us. Pages and emails say "the team that ada@rivetlabs.dev administers". If wrong: a name field later.
5. **The email carries only the inviter's verified address, and only paid plans can invite.** With no free text from the sender, the invitation cannot carry a spammer's message or link, and an inviter must have verified their own address. If wrong: nothing is lost, because a message field can come later.
6. **Rate limits, counted from the `Invitation` rows:** per team, 10 an hour and 30 a day; per invited address, 3 a day across all teams; at most 25 waiting at once on any plan, including Scale; and at most 100 a day across the whole site, with a WARNING in the log when that is reached. The site-wide cap protects the shared SMTP account, which signup verification also depends on, against someone paying for many teams. That account has a small daily sending limit, and going over it can stop sending for up to a day, so the cap leaves room for verification and password reset mail. Counting rows is exact, survives a cache reset, and needs no new table. A send that fails deletes its row, so a mail outage spends nobody's limit. For comparison, password reset allows 3 an hour per address and 10 an hour per IP, and verification allows 5 a day per address. If wrong: a team onboarding fast waits a day or asks support, and each number is one constant.
7. **A pending invitation holds a seat for 7 days,** and the token is hashed with sha256, like an API key, so a read of the database cannot accept anyone's invitation. The administrator can cancel a waiting invitation and send a new one. Sending again without cancelling is refused, so a seat is never held twice. A team also cannot invite an address that declined its invitation in the last 30 days, because a person who said no should be able to stop the email. That tells the inviter they declined, which they would see on the Members page anyway. If wrong: a resend button later, and one constant for the 30 days.
8. **Joining refuses a paid individual until PR D,** with its own sentence, and it refuses an administrator of a team with other people, a person already on a team, and a person whose own account is suspended, so that suspension cannot be escaped by joining. PR D replaces the first with the credit, and C3 adds the ways out for the next two. The switch stays off until then, so no customer meets a dead end. Every refusal also offers Decline.
9. **Joining closes every current administrator row the person holds, sets those keys inactive, marks their unused upload URLs used, cancels open invitations those workspaces sent, and revokes their OAuth grants,** all in one transaction, with the workspace rows locked in primary-key order. An approved device login needs no write here, because `_redeem` already refuses when the approver's row has closed, and writing it would deadlock with a poll, which holds that row while it waits for the workspace lock. The spec names keys, and its reason, that a team never inherits what it did not set up, covers connected apps too. If wrong: a person reconnects an app they could have kept.
10. **Decline needs only the link.** Nobody should have to make an account in order to say no. A forwarded link could decline, which costs the inviter one more invitation.
11. **The person who uploaded a file can change its visibility after upload, in both directions, only while the administrator lets members choose.** The owner asked for this on 2026-11-05: "they should be able to decide whether an item is visible only to them or to the team". Until PR E lets agents choose, new files start as "only me", so without this a team could share nothing. Changing the administrator's setting never relabels a file (section 9, question 1). If wrong: one view goes away.
12. **A teammate's address appears beside a file shared with the team, as "Uploaded by".** People on a team can see each other's shared files, and the spec's first customer wants to know whose screenshot it is. The address appears only on files they can already see. A member sees how many people are on the team and who administers it, not the list of members. If wrong: one column goes away.
13. **Allowances are typed in GB, and stored in bytes with the same `1024**3` the plans use.** Leaving a field empty means no allowance. A value above the plan's limit is stored as set, and the Members page shows it as capped by the team's limit, which is call 9.
14. **A plain member's Billing page says who manages it,** with no buttons, and its POST addresses and its switch confirmation answer with that same sentence and log at WARNING. The dashboard views test the role before calling Stripe's code, so the answer does not depend on Stripe's configuration, and the paid signup path sends a member to that page. Today these show a misleading sentence and write an ERROR line, which the launch runbook sends the owner to. The upgrade wording a member can still reach in REST errors goes to PR E, because this pull request changes no JSON answer.
15. **The browser checks run with the switch on, in `site-dashboard` and `site-forms` only.** Django tests prove the off state byte for byte, and the browser proves the on state. If wrong: a browser regression in the off state would go unseen, but the off state is the code as it stands today.

## Words

These are for the owner's review with this plan. Only a person on a team, or someone invited to one, can see them, and only with the switch on.

**The email.** Subject: "You are invited to a team on Veilfile".

```
{{ inviter }} invited you to join their team on Veilfile, which hosts files for AI agents behind private links that expire.

To accept or decline, open this link. It stops working on {{ expires }}.

{{ url }}

If you were not expecting this, you can ignore this email, and nothing will happen.

veilfile
```

**The invitation page**, whose heading is "You are invited to a team".

| State | Words |
|---|---|
| Every state | "{{ inviter }} invited {{ address }} to join their team on Veilfile." |
| Signed out | "Sign in with that address, or sign up with it, to accept." Links: Sign in, Sign up. Button: Decline. A new account must verify its address first, then open this link again. |
| Signed in as someone else | "You are signed in as {{ email }}. This invitation is for {{ address }}, so sign out and sign in with that address to accept it." Button: Sign out, which returns to this page. |
| Address not verified | "Verify {{ address }} first, then open this link again." Link: Verify email. |
| Can join | "What joining means" as a list: "You share the team's limits with everyone on it."; "Your API keys stop working, and your connected apps are disconnected. Connect your agents again with veilfile login."; "Files you already have keep working until they expire. Only you can see them, and they do not count toward the team's limits."; "The administrator chooses whether your new files are shared with the team or stay only yours."; "The administrator can see how much you use, but not the names of files that only you can see."; "If you leave the team or are removed, this account is a Free account again." Buttons: Join the team, Decline. |
| Has a paid plan | "This account has its own paid plan, so it cannot join a team yet." |
| Administers people | "You administer a team with other people on it, so you cannot join another one until you hand the role to someone else." |
| On another team | "You are already on a team. Leave it before you join another." |
| On this team | "You are already on this team." |
| No free seat | "This team has no free seat. Ask {{ inviter }} to make room, then open this link again." |
| Own account suspended | "This account is suspended, so it cannot join a team. Contact support@veilfile.com." |
| Not valid (404) | Heading "This invitation is no longer valid". "Ask the person who invited you for a new one." |
| After declining | Heading "You declined the invitation". "Nothing has changed on your account." |

After joining, the person lands on Overview with the notice: "You have joined the team. Connect your agents again with veilfile login."

**The Members page.**

| Where | Words |
|---|---|
| Seats, limited | "{{ used }} of {{ seats }} seats {{ is or are }} in use, counting invitations that are still waiting." |
| Seats, Scale | "{{ used }} {{ seat or seats }} {{ is or are }} in use, and this plan has no limit on seats." |
| Under the table | "An allowance is the most one person can use. The team's limits still apply to everyone together." |
| A cell above the plan's limit | "{{ used }} of {{ allowance }}, capped at {{ limit }} by the team's limit" |
| Table headers | Person, Role, Uploads this month, Storage, API keys, Actions (Set allowance) |
| Invite | Heading "Invite someone". Help: "They get an email with a link that works for 7 days." Button: Send invitation. |
| Waiting | Heading "Waiting invitations". Headers: Address, Expires, Actions (Cancel). |
| Setting | Heading "Who can see new files". Options: "Each person decides" with "New files start as only them."; "Everyone on the team"; "Only the person who uploaded them". Note: "This applies to new files. Files that already exist keep the visibility they have." Button: Save. |
| Allowance page | Heading "Set an allowance". "You are setting the allowance for {{ email }}." Fields: "Uploads a month", "Storage in GB", "API keys", each with the help "Leave this empty for no allowance." Button: Save. Link: Go back. |
| Sent | "The invitation is on its way to {{ address }}." |
| Saved | "The invitation is canceled."; "The allowance is saved."; "The setting is saved." |

**Refusing an invitation.**

| Code | Words |
|---|---|
| `email_verification_required` | "Verify your email before you invite anyone." |
| `no_free_seat` | "Every seat is in use, so no invitation was sent. Cancel a waiting invitation, or change plans." |
| `already_on_team` | "That person is already on this team." |
| `already_waiting` | "An invitation to that address is already waiting. Cancel it to send a new one." |
| `team_rate_limited` | "This team has sent as many invitations as it can for now. Try again later." |
| `address_rate_limited` | "That address has had too many invitations today. Try again tomorrow." |
| `too_many_waiting` | "This team has too many invitations waiting. Cancel some to send more." |
| `declined_recently` | "That address declined an invitation from this team recently, so no invitation was sent." |
| `site_rate_limited` | "Invitations are paused for now. Try again tomorrow." |
| `send_failed` | "The invitation could not be sent. Try again in a moment." |
| `workspace_suspended` | "This account is suspended, so no invitation was sent. Contact support@veilfile.com." |

**Elsewhere.**

| Where | Words |
|---|---|
| Overview, administrator on a paid plan | Heading "Team". "You are the administrator. {{ used }} of {{ seats }} seats {{ is or are }} in use." Link: Members. On Scale, the second sentence becomes "{{ used }} {{ seat or seats }} {{ is or are }} in use." |
| Overview, member | Heading "Team". "You are on the team that {{ admin }} administers, which has {{ count }} {{ people }}." Then the headings "Your use" and "The team's use". |
| Overview, plan line for a member | "Your team is on the {{ plan }} plan." |
| Overview, an invitation waits | "An invitation to a team is waiting for this address. Open the link in the email to accept or decline it." |
| Billing, member | "Your administrator manages billing for this team." |
| Files page, on a team | Caption: "Your files and your team's, newest first". Headers added: "Visible to", "Uploaded by". Values: "Only you", "The team", "You", or the person's address. Buttons: "Share with the team", "Make it only mine". |
| Files page, changed | "Your team can now see this file."; "Only you can see this file now." |
| Keys page, administrator on a team | Header added: "Person". Revoke page: "This key belongs to {{ email }}." |

## Global Constraints

- **With the switch off, nothing changes.** A GET of every new address answers the shell's 404, signed in or out. No page changes by a byte for any account, on any plan. Every new helper in `teams.py` returns its empty value before any query when the switch is off. `SwitchOffTests` compares pages with bytes captured from `main`, because comparing an edited template with itself proves nothing. `drop_core/tests_json_answers.py` and `drop_billing/tests_json_answers.py` pass unchanged, and they keep passing with the switch on, because no JSON answer changes in this pull request.
- **With the switch on, a Free one-person account sees no change, and a paid one-person account sees only the Team section on Overview** (decision 3). Their keys, files, limits and billing behave as before.
- **Every seat change takes the workspace row first, with `select_for_update()`.** That means invite, cancel, accept and anything later that adds or removes a person. Resolve the person with `current_membership` before the transaction. Nothing here calls `administrator_of` or `current_membership` under a lock. When two workspace rows are needed, lock them in primary-key order. Anything that locks a `Membership` row uses `select_for_update(no_key=True)`. A plain `UPDATE` of `removed_at` or `role` needs no extra lock.
- **Mail is sent after the transaction commits, never under a lock.** Any exception from rendering or sending deletes the row, which frees the seat and spends no limit, and answers `send_failed`.
- **An `IntegrityError` is caught outside the `atomic` block,** or the transaction is poisoned on Postgres, which SQLite does not show.
- **Writers use `update()` with the fields they change, never a loaded row's `save()`,** because a full save would write back stale `plan` and Stripe values. Invite, cancel and every Members writer re-read the caller's administrator row under the workspace lock.
- **Nothing reads `workspace.owner`.** The administrator's address comes from their `Membership` row, because `OwnerReadsTests` keeps that read out.
- **Templates add no whitespace with the switch off.** Use the repository's idiom: the newline goes inside the block, as in `overview.html`, with `{% endif %}` opening the next line. A line holding only `{% if x %}...{% endif %}` still renders its newline.
- **Every new test class sets `longMessage = False`,** and every new test module joins `READING_TEST_MODULES`. The test client skips CSRF, so each new form has one test with `Client(enforce_csrf_checks=True)` and a scraped token.
- **Only `drop_core/file_access.py` lists or changes a file's visibility.** A new read of `Artifact` or `PendingUpload` must be declared in `OnlyFileAccessReadsFilesTests.REMAINING`, with a comment, and must be a count or a sum. The Members page reads counts and sums and no file names.
- **An administrator never sees a member's "only me" file name,** on any page this adds (spec section 6).
- **No new script.** The shell's budget has 6 bytes left. Confirmations are separate pages, and forms post and redirect.
- **Pages follow DESIGN.md (local, in the checkout) and AGENTS.md's dashboard recipe:** one column, no cards, no colour for state, one primary action per form, no disabled controls, `table.rows` with `data-label` and a `caption`, every interactive element `interactive` or `action`, and tokens only. Times use `utc_time`, and sizes use `bytes`.
- **Migrations are additive and safe on a live Postgres table,** and the release on main keeps serving while they run. A new column has `db_default` where the previous release inserts rows.
- **Never name a plan and then a figure on one line** (`<Plan> ... N keys|GB|uploads|days`) in a file `drop_core/tests_fix_docs.py` scans. `PLAN_COPY_GLOBS` includes `backend/drop_core/account.py`, `backend/drop_core/views.py` and `backend/templates/**/*.html`. Seat figures come from `PLANS`, never typed. `seats` gets a pattern in `_cap_facts` in Task 4, before any page states a figure.
- AGENTS.md invariants hold, notably 4 (published clients keep working), 8 (no edits to `backend/legal/documents/*`) and 9 (no package versions). No new runtime dependencies.
- Words are the ones in this plan's Words section, verbatim, and any others follow AGENTS.md's Voice section: complete sentences, semicolons only between independent clauses, no em dashes, no exclamation marks, and American spelling.
- **This Mac panicked from memory exhaustion on 2026-11-05 and is shared.** Run heavy checks one at a time, and check `memory_pressure | tail -1` first. Never signal a process you did not start.
- Commits: `Veilfile <support@veilfile.com>`, `TZ=UTC git commit`, plain-sentence subjects, no attribution lines of any kind, no personal names. Branch: `seats/invitations`, from the latest `origin/main`. Pushing, opening the pull request and merging each need the owner's go-ahead, through Muse's account.

## Review Focus

1. **Two administrators, or one with two tabs, inviting into the last seat:** one succeeds and one is refused, and the seat count never goes over. Test: `test_the_last_seat_goes_to_one_invitation` (Task 2), and Task 8 on Postgres.
2. **A person accepting two teams' invitations at once, or clicking twice:** they end on one team. Tests: `test_accepting_twice_is_harmless` and `test_a_person_ends_on_one_team_after_two_accepts` (Task 3), and Task 8.
3. **Someone with a forwarded link:** they cannot join under another address, and they learn nothing about whether the invited address has an account. Tests: `test_a_forwarded_link_cannot_join_as_someone_else` and `test_the_page_does_not_say_whether_an_account_exists` (Task 3).
4. **The moment of joining:** an old key, an unused upload URL, an approved device login and an OAuth token all stop in the same commit, and an upload already in flight is refused. Tests: `test_joining_stops_everything_the_old_account_had` and `test_an_upload_in_flight_is_refused_after_joining` (Task 3).
5. **The switch off:** an address that exists only behind it, and every existing page, are as they were. Test: `SwitchOffTests` (Task 1, kept green by every later task), and Task 8's comparison with main.

---

### Task 1: The switch, and files get their visibility at upload

**Files:**
- Modify: `backend/config/settings.py`, `README.md` (its environment table), `render.yaml` (a comment beside the other optional settings)
- Modify: `backend/drop_core/models.py` (`Workspace.file_visibility`, constants)
- Create: `backend/drop_core/migrations/0018_workspace_file_visibility.py`
- Create: `backend/drop_core/teams.py`
- Modify: `backend/drop_core/services.py` (`upload_artifact`)
- Modify: `backend/config/test_runner.py` (set `TEAM_SEATS` off for every test run, because it scrubs `VEILFILE_*` only after settings have read them)
- Create: `backend/drop_core/tests_teams.py`, `backend/dashboard/tests_switch_off.py`, and golden pages under `backend/dashboard/golden/`

**Interfaces:**
- Produces: `settings.TEAM_SEATS` (bool), from a small function that a test can call with each value.
- Produces: in `teams.py`, `team_seats_only(view)`, a decorator that returns `pages.errors.not_found(request)` when the switch is off, before anything else runs, and uses `functools.wraps`.
- Produces: `Workspace.file_visibility`, a `CharField(max_length=16)` with `db_default` and `default` of `FILE_VISIBILITY_MEMBERS_CHOOSE`. Constants: `FILE_VISIBILITY_MEMBERS_CHOOSE = "members_choose"`, `FILE_VISIBILITY_ALWAYS_TEAM = "always_team"`, `FILE_VISIBILITY_ALWAYS_ONLY_ME = "always_only_me"`.

- [ ] **Step 1: Write the failing tests in `tests_teams.py`**

```python
class SwitchTests(SimpleTestCase):
    def test_it_is_off_unless_set_to_on(self):
        # Run settings' expression through the three values: unset, "off" and "on".

    @override_settings(TEAM_SEATS=False)
    def test_a_view_behind_it_is_the_shells_404_signed_out_and_signed_in(self):
        # A RequestFactory view wrapped by team_seats_only and a login_required stand-in: both answers have status 404 and
        # <title>Not found | Veilfile</title>, and the wrapped view never runs.


class SwitchOffTests(TestCase):
    # In dashboard/tests_switch_off.py. With fixed fixtures, render Overview, Files, Keys, Billing and the key revoke page for accounts
    # on each plan and for a member, mask the CSRF token and times, and compare with dashboard/golden/. Capture those files in this
    # task, from main's templates, before any later task edits one. A failure names the file, and says how to regenerate the golden
    # after a deliberate change to a page.

class VisibilityAtUploadTests(TestCase):
    def test_a_new_file_starts_as_only_me_when_members_choose(self):
    def test_a_new_file_is_shared_when_the_team_always_shares(self):
    def test_a_new_file_is_only_me_when_the_team_never_shares(self):
    def test_changing_the_setting_relabels_nothing(self):
        # A file uploaded under always_team stays team after the setting becomes always_only_me, and the reverse.
    def test_the_setting_is_read_under_the_lock(self):
        # Change file_visibility after the stale workspace object was loaded: the upload uses the new value.
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_teams`
Expected: ImportError for `drop_core.teams`.

- [ ] **Step 3: Implement**

In `upload_artifact`, the existing lock line becomes `.only("id", "suspended_at", "file_visibility")`, and the `Artifact(...)` call passes `visibility=VISIBILITY_TEAM if locked.file_visibility == FILE_VISIBILITY_ALWAYS_TEAM else VISIBILITY_ONLY_ME`. `WorkspaceAdmin` shows `file_visibility` as read-only, because a full form save could write back stale values. Add `VEILFILE_TEAM_SEATS` to README's table: not required, default off, "Turns on team seats: invitations and the Members page. Leave it unset until they launch." `EnvVarDocsTests` requires the row.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_teams drop_core.tests_fix_docs drop_core.tests_allowances`
Expected: OK. Run `makemigrations --check`: "No changes detected".

- [ ] **Step 5: Commit**

```bash
git add backend README.md render.yaml
TZ=UTC git commit -m "A switch hides team seats, and a new file takes the team's visibility setting"
```

### Task 2: Invitations hold a seat

**Files:**
- Modify: `backend/drop_core/models.py` (`Invitation`)
- Create: `backend/drop_core/migrations/0019_invitation.py`
- Modify: `backend/drop_core/teams.py`, `backend/drop_core/admin.py`
- Create: `backend/templates/registration/invitation_email.txt`
- Modify: `backend/config/log_redaction.py` (`SECRET_PATH_PREFIXES`: `/accounts/invitations/`)
- Test: `backend/drop_core/tests_teams.py`

**Interfaces:**
- Produces: `Invitation(workspace FK CASCADE related_name="invitations", email, token_hash unique, invited_by FK user SET_NULL, created_at, expires_at, accepted_at, declined_at, revoked_at)`, with constraint `invitation_one_waiting`: unique on `workspace, email` where all three of `accepted_at`, `declined_at` and `revoked_at` are null. `Invitation.TTL = timedelta(days=7)`.
- Produces in `teams.py`: `seat_limit(workspace) -> int | None`; `seats_in_use(workspace) -> int` (current rows plus unexpired open invitations); `class InviteRefused(Exception)` with `.code`; `invite(member, email, *, build_url) -> Invitation`; `cancel_invitation(member, invitation_id) -> bool`; `find_invitation(raw_token) -> Invitation | None` (open and unexpired); `same_address(a, b) -> bool`, which compares after `unicodedata.normalize("NFKC", ...)` and `casefold()`; the constants `INVITES_PER_HOUR = 10`, `INVITES_PER_DAY = 30`, `INVITES_PER_ADDRESS_PER_DAY = 3`, `MAX_WAITING = 25`, `SITE_INVITES_PER_DAY = 100`, `DECLINE_WAIT = timedelta(days=30)`.
- `build_url` is a callable from raw token to absolute URL, so that `teams.py` does not need a request.

- [ ] **Step 1: Write the failing tests**

In `tests_teams.py`, class `InviteTests`, on a Team-plan workspace with an administrator whose address is verified:

- `test_an_invitation_is_mailed_and_only_its_hash_is_stored`: `mail.outbox` has one message, to the address, with the Words section's subject; its link's token appears nowhere in the row, and `hashlib.sha256(token)` equals `token_hash`.
- `test_the_email_names_only_the_inviters_address`: the body has the inviter's address and no `Workspace.name`.
- `test_it_expires_in_seven_days`, using `Invitation.TTL`.
- `test_a_waiting_invitation_holds_a_seat`: on a plan with 5 seats, after 4 invitations, `seats_in_use` is 5 and the next raises `no_free_seat`.
- `test_an_expired_invitation_frees_its_seat`: set `expires_at` in the past; the next invitation succeeds, and the old row has `revoked_at` set.
- `test_cancelling_frees_its_seat`.
- `test_the_last_seat_goes_to_one_invitation`: with one seat left and two threads in a `TransactionTestCase`, exactly one succeeds. On SQLite this checks the logic, and Task 8 checks the lock.
- `test_scale_has_no_seat_limit_but_a_waiting_limit`: on Scale, with 25 waiting rows created directly and older than an hour, `invite` raises `too_many_waiting`.
- `test_the_site_wide_limit_refuses_and_logs`: with 100 rows from other teams today, `invite` raises `site_rate_limited`, and `assertLogs` sees a WARNING.
- `test_a_decline_stops_this_team_for_thirty_days`: `declined_recently` for this team, and a different team can still invite.
- `test_a_bad_address_is_refused_before_any_row`: an address `validate_email` refuses, or one holding a newline.
- `test_the_team_limits_count_every_row`: the 11th invitation in an hour raises `team_rate_limited`, even with earlier ones cancelled; with rows moved back over an hour, the 31st in a day raises it too.
- `test_the_address_limit_spans_teams`: three teams invite one address; the fourth raises `address_rate_limited`, and a different address is fine.
- `test_an_address_is_matched_without_regard_to_case`: `Grace@Example.com` and `grace@example.com` are one address, for the waiting check and the limits, and the stored form is lower case.
- `test_someone_on_the_team_cannot_be_invited`: `already_on_team`, for the administrator and for a member.
- `test_a_second_invitation_to_one_address_is_refused`: `already_waiting`.
- `test_only_the_administrator_can_invite`: a member gets `PermissionDenied`.
- `test_an_unverified_inviter_is_refused`: `email_verification_required`, with no mail.
- `test_a_free_plan_has_no_seat_to_offer`: `no_free_seat`, with no mail.
- `test_a_suspended_team_cannot_invite`: `workspace_suspended`.
- `test_a_failed_send_frees_the_seat_and_spends_no_limit`: patch `send_mail` to raise `SMTPException`, and also `ValueError`: `send_failed`, the row is gone, and `seats_in_use` and the counts drop.
- `test_an_apostrophe_in_an_address_survives_the_email`: the `.txt` template uses `{% autoescape off %}`.
- `test_mail_is_sent_after_commit_and_outside_the_lock`: the patched `send_mail` records `connection.in_atomic_block` as False.
- `test_a_token_in_a_path_is_hidden_from_logs`: `redact_path("/accounts/invitations/abc")` hides it.

In the same file, class `CancelTests`: an administrator of another team, and a member, get False and change nothing.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_teams`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement `invite` in this order**

```python
def invite(member, email, *, build_url):
    if not member.is_administrator:
        raise PermissionDenied
    email = email.strip().lower()
    if not is_email_verified(member.user):
        raise InviteRefused("email_verification_required")
    raw = secrets.token_urlsafe(32)
    now = timezone.now()
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(pk=member.workspace_id)
        if workspace.suspended_at is not None:
            raise InviteRefused("workspace_suspended")
        # Close expired rows first, so that the one-waiting constraint and the count agree.
        _open(workspace).filter(expires_at__lte=now).update(revoked_at=now)
        # Then, in this order: already_on_team, already_waiting, declined_recently, too_many_waiting, team_rate_limited,
        # address_rate_limited, site_rate_limited, no_free_seat. Then create the row.
    # After the commit: render invitation_email.txt and send_mail. On any Exception, delete the row and raise send_failed.
```

`already_on_team` reads `Membership` with `workspace`, `removed_at__isnull=True` and `user__email__iexact`. The `IntegrityError` from `invitation_one_waiting` cannot occur under the lock; if it does, catch it outside the `atomic` block and answer `already_waiting`. `invite` validates the address with `validate_email` before anything else. `cancel_invitation` is one `UPDATE ... WHERE` all three outcome columns are null, under the workspace lock, and it returns whether a row changed. `InvitationAdmin` lists `email`, `workspace`, `created_at`, `expires_at`, and the three outcomes, and excludes `token_hash`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_teams config.tests_fix_platform`
Expected: OK. `makemigrations --check` shows no changes.

- [ ] **Step 5: Commit**

```bash
git add backend
TZ=UTC git commit -m "An invitation holds a seat for seven days, within limits on every plan"
```

### Task 3: The invitation page, and joining

**Files:**
- Modify: `backend/drop_core/teams.py`
- Create: `backend/drop_core/invitation_views.py`
- Modify: `backend/config/urls.py` (`accounts/invitations/<str:token>/`, name `invitation`)
- Create: `backend/templates/registration/invitation.html`, `invitation_invalid.html`, `invitation_declined.html`
- Modify: `backend/pages/tests_site_rules.py` (`READING_TEST_MODULES`)
- Test: `backend/drop_core/tests_invitations.py`

**Interfaces:**
- Consumes: `find_invitation`, `current_membership`, `is_email_verified`, `revoke_user_oauth_grants`, `has_other_members`, `team_seats_only`.
- Produces in `teams.py`: `why_not_join(user, invitation) -> str | None`, which returns the code that would refuse, for the page, without writing; `class JoinRefused(Exception)` with `.code`; `accept_invitation(user, raw_token) -> Membership`; `decline_invitation(raw_token) -> bool`.
- The refusal codes, checked in this order: `wrong_person`, `email_verification_required`, `on_this_team`, `on_another_team`, `administers_people`, `account_suspended`, `has_paid_plan`, `no_free_seat`.
- `wrong_person` uses `same_address`, and the person must match on `user.email`, on their `EmailVerification.email`, and on the invitation, with `is_verified` true.

A paid plan means `workspace.plan != "free"` or a non-empty `stripe_subscription_id`, on any workspace the person currently administers. `no_free_seat` means the current rows alone are at the limit, because the invitation's own hold becomes the person's seat.

- [ ] **Step 1: Write the failing tests in `tests_invitations.py`**

Every class has `longMessage = False`, and each failure message names the file and the fix. Add the module to `READING_TEST_MODULES` in `pages/tests_site_rules.py`.

Class `PageTests`, with `override_settings(TEAM_SEATS=True)`:

- `test_it_is_the_shells_404_with_the_switch_off`, with `TEAM_SEATS=False`.
- `test_a_bad_expired_used_or_cancelled_link_gets_one_answer`: the same status (404) and the same body for all of them, for a person signed in and signed out.
- `test_signed_out_it_offers_sign_in_sign_up_and_decline`, with `next=` on both links.
- `test_the_page_does_not_say_whether_an_account_exists`: byte-identical signed out for an invited address with an account and one without, apart from the address itself.
- `test_signed_in_as_someone_else_it_says_so_and_offers_no_join`.
- `test_an_unverified_address_is_asked_to_verify_and_offers_no_join`.
- `test_a_pending_change_of_address_does_not_count`: `EmailVerification.email` holds the invited address unverified, and `user.email` holds another: refused.
- `test_sign_out_returns_here`: the wrong-person page's button signs out and comes back to the page, signed out.
- `test_every_refusal_offers_decline`.
- `test_a_person_who_can_join_reads_what_it_means`: every sentence of the Words table's "Can join" row is present.
- One test per refusal code, each showing its sentence and no Join button.
- `test_a_get_changes_nothing`.
- `test_it_is_not_indexed_or_cached`: `X-Robots-Tag: noindex, nofollow` and `Cache-Control: no-store`.
- `test_a_post_without_its_token_is_form_not_sent`.

Class `JoinTests`, with a person who owns a Free workspace holding one key, one file and one unused upload URL, one approved device login and one OAuth token:

- `test_joining_moves_the_person_to_the_team`: `current_membership(person)` is a member row of the team, with no allowances, and the invitation has `accepted_at`.
- `test_joining_stops_everything_the_old_account_had`: after the POST, `APIKey.verify(old_raw)` is None and the key's `is_active` is False; the upload URL is `used`; the device login answers `access_denied` at the poll; `resolve_oauth_credential(old_token)` is None.
- `test_an_upload_in_flight_is_refused_after_joining`: `upload_artifact(old_workspace, ..., member=stale_row)` raises `ValueError("member_removed")` after the join.
- `test_old_files_keep_working_and_stay_the_persons`: the link still serves, the person still lists and revokes the file, the team's administrator cannot see it, and `workspace_storage_bytes(team)` does not count it.
- `test_a_new_key_lands_on_the_team`: `account.create_key(person, "agent")` makes a key with `workspace=team` and that member.
- `test_joining_cancels_invitations_the_old_workspace_sent`.
- `test_a_forwarded_link_cannot_join_as_someone_else`: signed in and verified with another address, the POST is refused with `wrong_person` and nothing changes.
- `test_an_unverified_account_cannot_join`.
- `test_accepting_twice_is_harmless`: the second POST finds the invitation used, answers the "not valid" page, and leaves one current row.
- `test_a_person_ends_on_one_team_after_two_accepts`: accept team A, then open team B's link: `on_another_team`, and nothing changes.
- `test_a_person_with_a_closed_row_on_this_team_comes_back`: an existing closed row is reopened, not duplicated, with its allowances cleared and its old keys still inactive.
- `test_an_expired_or_cancelled_invitation_cannot_be_accepted_at_the_last_moment`: `accept_invitation` re-reads under the lock.
- `test_a_suspended_team_cannot_be_joined`: the "not valid" page.
- `test_a_suspended_person_cannot_join`: `account_suspended`, and nothing changes.
- `test_a_forced_integrity_error_is_refused_not_a_500`: patch the insert to raise `IntegrityError`: refused with `on_another_team`, and the transaction is not poisoned.
- `test_the_lock_set_is_rechecked`: a current row that appears between the first read and the lock causes a retry, not a join with an unlocked workspace.
- `test_the_person_lands_on_overview_with_the_notice`.

Class `DeclineTests`:

- `test_declining_needs_only_the_link`: signed out, the POST sets `declined_at`, frees the seat, and shows the declined page.
- `test_declining_touches_nothing_else`.
- `test_declining_works_in_a_real_browser_session`: with `Client(enforce_csrf_checks=True)`, signed out: GET, scrape the token, POST.
- `test_declining_cannot_undo_an_acceptance`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_invitations`
Expected: the tests FAIL, most with a 404 for the missing route.

- [ ] **Step 3: Implement `accept_invitation` in this order**

```python
def accept_invitation(user, raw_token):
    current_membership(user)  # before any lock: it can write
    digest = _digest(raw_token)
    team_id = Invitation.objects.filter(token_hash=digest).values_list("workspace_id", flat=True).first()
    if team_id is None:
        raise JoinRefused("not_valid")
    with transaction.atomic():
        own = Membership.objects.filter(user=user, removed_at__isnull=True).values_list("workspace_id", flat=True)
        ids = sorted({team_id, *own})
        locked = {w.pk: w for w in Workspace.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
        # Re-read own under the locks. If it names a workspace not in locked, leave the block and start again, at most three times.
        invitation = _open(locked[team_id]).select_for_update().filter(token_hash=digest, expires_at__gt=timezone.now()).first()
        if invitation is None or locked[team_id].suspended_at is not None:
            raise JoinRefused("not_valid")
        # Re-read the person's rows under the locks, and raise why_not_join's code if it has one.
        # Close each current administrator row of the person: set its keys inactive, its unused upload URLs used,
        # and that workspace's open invitations revoked, then set removed_at. Do not write DeviceCode here: see Decision 9.
        # Reopen this team's row for the person if one exists, or create one, as a member with no allowances. Set any
        # old keys on it inactive.
        # Set accepted_at, and call revoke_user_oauth_grants(user).
    return row
```

`Membership` writes here are plain updates and one insert, all under the workspace lock, so that this cannot deadlock with `upload_artifact`, whose order is counters and then the workspace row. A person-level `IntegrityError` from `membership_one_team` is caught outside the `atomic` block and answers `on_another_team`. `decline_invitation` is one `UPDATE ... WHERE` all three outcome columns are null, under the workspace lock.

`invitation_views.invitation` is `team_seats_only`, never cached, with `X-Robots-Tag`. A GET renders from `find_invitation` and `why_not_join`. A POST takes `action=accept` or `action=decline`. `accept` needs a signed-in person, and a signed-out POST goes to sign-in with `next=`. On `JoinRefused`, re-render with the refusal's sentence and status 400. All pages use `layouts/form.html`, with their own `title` and `meta` blocks.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_invitations drop_core.tests_teams drop_core.tests_one_place pages.tests_forms_on_the_shell pages.tests_site_rules`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend
TZ=UTC git commit -m "An invited person with a verified address can join from a Free account, or decline"
```

### Task 4: The Members page

**Files:**
- Create: `backend/dashboard/members.py`
- Create: `backend/templates/dashboard/members.html`, `backend/templates/dashboard/member_allowance.html`, `backend/templates/dashboard/invitation_cancel.html`
- Modify: `backend/dashboard/urls.py`, `backend/dashboard/forms.py`, `backend/pages/registry.py` (`DASHBOARD_SECTIONS`)
- Modify: `backend/drop_core/teams.py`
- Modify: `backend/drop_core/tests_fix_docs.py` (`_cap_facts`)
- Modify: `backend/drop_core/tests_file_access.py` (`REMAINING`)
- Modify: `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (the table of addresses in section 4.8)
- Modify: `backend/pages/tests_site_rules.py` (`READING_TEST_MODULES`)
- Test: `backend/dashboard/tests_members.py`

**Interfaces:**
- Produces: routes `app-members` (`/app/members/`, GET and POST to invite), `app-member-allowance` (`/app/members/<int:member_id>/allowance/`, GET and POST), `app-invitation-cancel` (`/app/members/invitations/<int:invitation_id>/cancel/`, GET and POST), `app-file-visibility-setting` (`/app/members/visibility/`, POST). Each view is `team_seats_only` outside `dashboard_view`, and each key in `DASHBOARD_SECTIONS` marks `app`.
- Produces in `teams.py`: `member_rows(workspace) -> list[dict]`, with `person`, `role`, `uploads_used`, `upload_allowance`, `storage_used`, `storage_allowance`, `keys_used`, `key_allowance`, from one query per figure across the team and never one per person; `set_allowances(administrator, member_id, *, uploads, storage_bytes, keys) -> bool`; `set_file_visibility_setting(administrator, value)`.
- Produces in `teams.py`: `has_team_features(workspace) -> bool`, the one rule for whether a team has these pages: the switch is on, and `seat_limit` is None or above 1, or someone else is on it, or an invitation is waiting. The Members page, the Team section and every helper use it.
- A page for an administrator of such a team. Anyone else gets the shell's 404.
- `members.html` uses `layouts/app.html` for the dashboard's column, though it is not a section. It needs its table's width, which the form layout's 26rem cannot give.

- [ ] **Step 1: Write the failing tests in `dashboard/tests_members.py`**

With `override_settings(TEAM_SEATS=True)`, unless a test says otherwise:

- `test_every_address_is_the_shells_404_with_the_switch_off`: GET and POST, signed in and signed out.
- `test_signed_out_it_goes_to_sign_in`.
- `test_a_member_and_a_free_administrator_get_the_shells_404`.
- `test_another_teams_member_or_invitation_is_the_shells_404`.
- `test_it_lists_each_person_with_their_use_and_allowance`.
- `test_it_counts_only_files_still_live`.
- `test_no_file_name_appears_on_the_page`: a member's "only me" file name and its link are absent from the HTML, in a test that first proves the fixture holds them.
- `test_it_runs_one_query_per_figure`: `assertNumQueries` stays the same for two people and for six.
- `test_it_states_seats_from_the_plan` for Team and Scale, reading `PLANS`.
- `test_inviting_sends_and_says_so`; one test per refusal code, showing its sentence through `components/form_errors.html` with status 400; `test_a_bad_address_is_a_field_error`.
- `test_a_resent_form_does_not_invite_twice`: the second POST is refused with `already_waiting`.
- `test_cancelling_asks_first_and_frees_the_seat`: a GET changes nothing.
- `test_an_allowance_saves_and_an_empty_field_clears_it`.
- `test_gb_become_bytes`, using `plans.GB`, and accepting a fraction such as `0.5`.
- `test_bad_allowances_are_field_errors`: negative numbers, words, uploads or keys above 2,147,483,647, and GB whose bytes reach `2**63`, parsed with `Decimal`, because a too-large value is a 500 on Postgres that SQLite hides.
- `test_an_allowance_above_the_plans_limit_shows_as_capped`.
- `test_zero_is_a_real_allowance`.
- `test_the_setting_saves_and_relabels_nothing`.
- `test_a_bad_setting_value_is_refused`.
- `test_a_post_without_its_token_is_form_not_sent`, and one test of the invite form with `Client(enforce_csrf_checks=True)`.
- `test_every_table_has_a_caption_and_data_labels`, which `RowsMarkupTests` also covers.

In `tests_fix_docs.py`: `_cap_facts` gets a `seats` pattern, with `(\d[\d,]*)\s+(?:seats|people)` after a plan name, and a test that a wrong figure next to a plan name fails.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard.tests_members`
Expected: ImportError or 404.

- [ ] **Step 3: Implement**

`members.html` extends `layouts/app.html`, and needs its own `title` and `meta` blocks, because a page off a section has no shell entry. Use `components/field.html`, `components/form_errors.html`, `table.rows`, `.check` radios as in `device/device_consent.html`, and `.notice`. Refusals are a module-level `REFUSALS` dictionary from code to sentence, as `billing_page.FIXED` does. Add rows to section 4.8's address table so that `dashboard/tests_recipe.py` passes. `member_rows` adds `drop_core/teams.py` entries to `REMAINING`, each a sum or count, with a comment. `set_allowances` uses one `Membership.objects.filter(pk=..., workspace=...).update(...)`, and `set_file_visibility_setting` one `Workspace.objects.filter(pk=...).update(file_visibility=value)`. Allowance fields are prefilled with strings, because `field.html` prints a value only when it is truthy, which a zero is not. The Members page links to no other page that names a file.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard drop_core.tests_fix_docs drop_core.tests_file_access pages.tests_voice`
Expected: OK. The five sections and their tests are unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend docs/superpowers/specs/2026-10-08-site-redesign-design.md
TZ=UTC git commit -m "An administrator can invite people, set allowances and choose who sees new files"
```

### Task 5: A member's first view, and Billing for a member

**Files:**
- Modify: `backend/dashboard/overview_page.py`, `backend/templates/dashboard/overview.html`
- Modify: `backend/dashboard/billing_page.py`, `backend/dashboard/views.py` (`billing`, `billing_checkout`, `billing_portal`, `billing_switch`), `backend/templates/dashboard/billing.html`
- Modify: `backend/drop_core/teams.py`
- Test: `backend/dashboard/tests_members.py`, `backend/dashboard/tests_billing.py`

**Interfaces:**
- Produces in `teams.py`: `team_summary(member) -> dict | None`, with `is_administrator`, `admin_email`, `people`, `seats_used`, `seat_limit`, and, for a member, `own` with `uploads_used`, `upload_allowance`, `storage_used`, `storage_allowance`, `keys_used`, `key_allowance`. It answers None with the switch off, and for an administrator on a plan with one seat and nobody else.
- Produces: `waiting_invitation_for(user) -> bool`: a verified person's address has an open, unexpired invitation.

- [ ] **Step 1: Write the failing tests**

In `tests_members.py`, class `OverviewTests`:

- `test_a_free_one_person_account_sees_no_change_with_the_switch_on`: the same bytes on and off.
- `test_a_paid_administrator_gets_the_team_section_and_a_link`, on Team and on Scale.
- `test_a_member_sees_the_team_their_allowances_and_the_totals`: the sentence, both headings, and a meter each for own uploads, storage and keys with the allowance as the cap.
- `test_a_member_with_no_allowance_sees_their_use_without_a_cap`.
- `test_a_zero_allowance_shows_as_a_limit_of_zero`: `meter.html` tests `{% if cap %}`, so change it to test for none, which leaves every existing meter unchanged.
- `test_a_member_sees_no_list_of_people`: a teammate's address is absent.
- `test_a_member_gets_no_billing_link_or_notice`: the plan line is the member's sentence, `over_limits` is not drawn, by a flag of its own, and the page does not print "Plan limits could not be checked" as it would for a missing `status`.
- `test_get_connected_follows_a_members_own_keys`: a member with no keys sees it while the team has keys.
- `test_a_waiting_invitation_shows_a_notice_only_when_verified`, with no token in the page.

In `tests_billing.py`, class `MemberBillingPageTests`:

- `test_a_member_reads_who_manages_it_and_gets_no_buttons`.
- `test_each_post_answers_with_that_sentence_and_logs_a_warning`: `assertLogs("drop_billing", "WARNING")`, and no ERROR line, for checkout, portal and switch, with Stripe unconfigured and configured.
- `test_the_switch_confirmation_says_the_same_to_a_member`.
- `test_the_paid_signup_path_sends_a_member_to_billing`: GET and POST of `/accounts/signup/?plan=team`, with no checkout started and no ERROR line.
- `test_an_administrators_page_is_unchanged`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard.tests_members.OverviewTests dashboard.tests_billing.MemberBillingPageTests`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `overview.html`, follow the Global Constraint on whitespace. `FIXED` gains `(403, None): MEMBER_BILLING`, with `MEMBER_BILLING = "Your administrator manages billing for this team."`, and the explained-refusal path logs at WARNING. The dashboard's billing views test the person's role before they call `billing_account`, so that the sentence does not depend on Stripe's configuration. `billing` renders the sentence with no buttons when the person is not an administrator. In `signup`, a signed-in plain member choosing a paid plan is redirected to `app-billing`. `team_summary` takes the administrator's address from their `Membership` row.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard drop_billing`
Expected: OK. `drop_billing/tests_json_answers.py` passes unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend
TZ=UTC git commit -m "A member sees their team and their allowances, and who manages billing"
```

### Task 6: The Files and Keys pages on a team

**Files:**
- Modify: `backend/drop_core/file_access.py`
- Modify: `backend/dashboard/file_rows.py`, `backend/dashboard/views.py` (`files`, new `file_visibility`), `backend/dashboard/urls.py`, `backend/pages/registry.py`
- Modify: `backend/templates/components/file_list.html`, `backend/pages/catalog.py`
- Modify: `backend/dashboard/keys.py`, `backend/templates/dashboard/keys.html`, `backend/templates/dashboard/key_revoke.html`
- Modify: `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (section 4.8)
- Test: `backend/dashboard/tests_files.py`, `backend/dashboard/tests_keys.py`, `backend/drop_core/tests_file_access.py`

**Interfaces:**
- Produces in `file_access.py`: `set_visibility(member, artifact_id, visibility) -> bool`: True only for the person's own file in their current workspace, while `file_visibility` is `members_choose`, with the file still live. `file_details(member, ids) -> dict[id, dict]`, with `visibility`, `uploaded_by`, `can_revoke` and `can_change_visibility`, for the ids on the page.
- Produces: route `app-file-visibility` (`/app/files/<uuid:artifact_id>/visibility/`, POST only), `team_seats_only` outside `dashboard_view`.
- `file_rows` gains the keys from `file_details` when `show_team` is true. `show_team` is `settings.TEAM_SEATS and has_other_members(workspace)`. The list's JSON body is unchanged.

- [ ] **Step 1: Write the failing tests**

In `tests_files.py`, class `TeamFilesTests`, with `TEAM_SEATS=True`:

- `test_a_one_person_page_is_byte_identical_with_the_switch_on`.
- `test_on_a_team_it_has_the_two_columns_and_the_caption`.
- `test_a_teammates_shared_file_shows_their_address_and_no_revoke`.
- `test_the_administrator_gets_revoke_on_a_shared_file`.
- `test_a_teammates_only_me_file_is_not_there`.
- `test_a_file_left_behind_is_there_and_cannot_change_visibility`.
- `test_sharing_and_unsharing_work_for_the_uploader`: the POST changes the row, and the notice is the Words table's.
- `test_only_the_uploader_can_change_it`: the administrator's POST on a member's file is the shell's 404, whichever visibility it has.
- `test_it_cannot_change_when_the_team_decides`: under `always_team` and `always_only_me`, no button appears and a POST changes nothing.
- `test_an_expired_or_held_file_offers_no_change`.
- `test_sharing_works_with_csrf_enforced`: with `Client(enforce_csrf_checks=True)`, GET the page, scrape the token from the row's form, POST, and the file changes. This is the test that a real browser's request would pass.
- `test_a_post_without_its_token_is_form_not_sent`.
- `test_the_route_is_the_shells_404_with_the_switch_off`.
- `test_a_get_is_not_allowed`.

In `tests_file_access.py`, the matrix gains `set_visibility` across the people in `PrivacyMatrixTests`.

In `tests_keys.py`, class `TeamKeysTests`:

- `test_a_one_person_page_is_byte_identical_with_the_switch_on`.
- `test_the_administrator_sees_whose_each_key_is`, and the revoke page's sentence.
- `test_a_member_sees_only_their_own_with_no_person_column`.
- `test_the_rest_list_is_unchanged`: `GET /api/v1/keys` stays six fields.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard.tests_files.TeamFilesTests dashboard.tests_keys.TeamKeysTests`
Expected: FAIL.

- [ ] **Step 3: Implement**

`file_list.html` takes `show_team` and `csrf_token`, and `files.html` and the catalog entry pass both, because the component is included with `only` and has no token otherwise. A one-person page stays unchanged under the Global Constraint on whitespace. The catalog gets an entry showing `show_team` with the two new columns. `keys.py` reads the people's addresses in one bulk lookup, only when `show_team` is true. The visibility change uses `Artifact.objects.filter(pk=..., member=member).update(visibility=...)` and writes nothing else.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard drop_core.tests_file_access pages.tests_catalog`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend docs/superpowers/specs/2026-10-08-site-redesign-design.md
TZ=UTC git commit -m "On a team, Files shows who uploaded what and lets people share, and Keys shows whose"
```

### Task 7: The browser checks, a look at the pages, and AGENTS.md

**Files:**
- Modify: `.github/workflows/ci.yml` (`site-dashboard` and `site-forms`: `VEILFILE_TEAM_SEATS: "on"` on "Start the app")
- Modify: `site-tests/make_dashboard_fixtures.py`, `site-tests/make_form_fixtures.py`, `site-tests/run_site_checks.py`
- Modify: `backend/pages/tests_dashboard_fixtures.py`, `backend/pages/tests_site_parts.py`, `backend/pages/tests_form_fixtures.py`
- Modify: `AGENTS.md` ("People inside an account")

**Interfaces:**
- Consumes: every page from Tasks 3 to 6.
- Produces: a new team in the dashboard fixtures, under its own new users, an administrator, a member with allowances, and a waiting invitation, leaving the owner's pinned 53 files and 4 keys alone. Each script deletes its own `Invitation` and non-owner `Membership` rows first, because the tests run it twice, and creates invitation rows directly, so that it knows the raw token. Update the pinned key lists in `tests_dashboard_fixtures.py` and `tests_form_fixtures.py`. A new function `dashboard_members_problems`, run by `run_dashboard`. `FormState`s for the invitation page's states. `DASHBOARD_PAGES` stays the five sections.

- [ ] **Step 1: Add the fixtures and checks.** `dashboard_members_problems` runs the pages part's checks on the Members page, the allowance page and a member's Overview: at 375 and 1280, in Paper and Ink, with motion on and off, with axe, no sideways scroll, no console error, and fields at 16 px on phones. It also walks the invite form by keyboard. `FORM_STATES` gain the invitation page signed out, can join, wrong person, unverified, and not valid. `tests_site_parts.py`'s `RunnerTests` gain a pin that both jobs set the switch.

- [ ] **Step 2: Run the parts locally, one at a time,** as AGENTS.md describes, on a scratch SQLite database, with the switch on: first `dashboard`, then `forms`.
Expected: 0 problems.

- [ ] **Step 3: Update AGENTS.md's section.** Add, as bullets in its voice: the switch, what it hides, and that F turns it on; `drop_core/teams.py` holds the seat rules, every seat change takes the workspace row first, and mail goes after the commit; what joining does and that a person proves their address by being signed in and verified; the numbers live in `teams.py`; the Members page is off Overview because the menu has no role audience; and the first sentence's "Nobody can invite a second person yet" changes to say that only the switch stands in the way. Also change "only staff can set them" about allowances, the sentence about what nothing reads, and the "Site checks" description of `site-dashboard` and `site-forms`, which now run with the switch on.

- [ ] **Step 4: Run the full suites, one at a time.**

Run: `memory_pressure | tail -1`, then `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: OK. Then run `cd cli && ../venv/bin/python -m unittest discover -s tests -t .` and `cd npm && npm test`, which should show no change.

- [ ] **Step 5: Commit**

```bash
git add .github site-tests backend AGENTS.md
TZ=UTC git commit -m "The site checks cover the team pages with the switch on, and AGENTS.md explains them"
```

- [ ] **Step 6: The controller looks at the pages.** With the switch on, capture the Members page, the allowance page, a member's Overview, the Files page on a team, and the invitation page in its main states, at 375 and 1280, in Paper and Ink, and read them against DESIGN.md, because spec section 6 says that looking like a different product is a failure. Fix what the look shows. Show the owner the screenshots with the results.

### Task 8: Rehearse on Postgres, then stop for the owner

This is the only evidence on locks, because the suite runs on SQLite. Nothing here commits unless the rehearsal finds a defect.

**Files:** none in the repository. The kit is `$HOME/dev/veilfile-rehearsal-kit`, with C1's scripts under `prc1/`. The throwaway cluster is on `127.0.0.1:8779` at `$HOME/dev/veilfile-pg-scratch`, the old release is a worktree of `origin/main` at `$HOME/dev/veilfile-pg-old`, and the servers are on `8777` and `8778`.

- [ ] **Step 0: Bring the kit up to date.** Parameterize the migrations `0018` and `0019` and the rollback target `0017`. Add a seed of teams on each plan, and a driver for the invitation page over real HTTP. `run.sh` starts servers with `env -i` and a fixed list, so pass `VEILFILE_TEAM_SEATS=on` to the new server only, and restart it between Steps 3 and 4. Read raw tokens from the server's console email output, or create rows directly. Set a plan directly in the database for the opposite-join case.

- [ ] **Step 1: Rebuild the cluster and seed it** at the same volume as C1: 5,146 workspaces, 20,046 keys and 200,207 files.

- [ ] **Step 2: Migrate under load** while the old release reads and writes. **Bar:** 0 probe errors, and no stall over 1 s that a lock explains.

- [ ] **Step 3: Compare the switch off with the old release.** Compare REST, MCP, the device consent page, `/a/<token>`, and all five dashboard sections for accounts on each plan. **Bar:** byte-identical once `last_used_at` and CSRF tokens are masked, and every new address is the shell's 404, signed in and signed out.

- [ ] **Step 4: Compare the switch on for one-person accounts.** **Bar:** a Free account is byte-identical, and a paid account differs only by the Team section on Overview.

- [ ] **Step 5: Race the seats, with the switch on, over real HTTP.** With 30 threads: inviting into the last seat; accepting against inviting; accepting twice; one person accepting two teams; two people's opposite joins, each holding the other's invitation; cancelling against accepting. **Bar:** 0 deadlocks, the seats never go over, each person holds one current place, and each invitation has one outcome.

- [ ] **Step 6: Race the join against the person's own work.** With uploads, key mints, device polls, upload URL PUTs and OAuth requests under the old account while it joins, and the team's own members uploading. Also hold a workspace row for 5 s, as billing does across a Stripe call, and measure how long an invite and a join wait. **Bar:** 0 deadlocks; after the commit, no old key verifies, no old token resolves, no device poll mints, and no new file lands in the old workspace; counters equal what was committed; the wait is recorded for the deploy notes.

- [ ] **Step 7: Attack privacy again, with real invitations and the visibility control.** Run C1's matrix on a team made through the pages, and add the Members page, Overview, Files columns and the invitation page. Also try a stranger guessing tokens, a member reaching administrator addresses, and an administrator reaching another team's. **Bar:** no hidden name, id or link appears in any response, and no address reveals whether an account exists.

- [ ] **Step 8: Measure the Members page** for a Scale team of 200 people. **Bar:** the same number of queries as for 2 people, and a p95 under 200 ms on this machine.

- [ ] **Step 9: Run both releases together for 65 s, then roll back** the code first and `migrate drop_core 0017`. **Bar:** 0 errors, and the time recorded.

- [ ] **Step 10: Run the full suite on Postgres.** **Bar:** the same failures as `main` on Postgres, compared by test name.

- [ ] **Step 11: Remove the cluster and the worktree, and confirm** that port 8779 is closed and that port 5432 is untouched.

- [ ] **Step 12: Stop for the owner.** Report the results and the rulings, and show the screenshots. The pull request's description repeats C1's deploy note: `0018`'s `ALTER TABLE` on the workspace table waits for any transaction holding a workspace row, including one waiting on Stripe, and new reads queue behind it. `0019` is a new table. Pushing, opening the pull request and merging each need the owner's go-ahead.

## Carried to C3 and D

These are not part of this plan.

- **C3 must merge before F turns the switch on:** the ways out that this plan's sentences promise: leaving, removal with its choice, and the transfer. Until C3 merges, someone who has joined cannot leave, which is safe only because the switch is off.
- **C3:** "Can join" says "If you leave the team or are removed, this account is a Free account again." `current_membership` already reopens the row, and C3 must test the whole path from a real join.
- **C3:** removal takes the workspace lock and sets `removed_at`, so that C1's re-check holds. An invitation's reopening of a closed row assumes removal has set the keys inactive, and this plan sets them inactive again as a guard.
- **C3:** the hold notice, staff deletion of a member's account, and an invitation matching the verified address at the time it is accepted, which this plan's check already does.
- **D:** replaces `has_paid_plan` with ending the subscription and crediting the unused part; refuses a plan switch below the seats in use, using the workspace lock this plan's seat changes already take; bars members on Free; moves the billing email at a transfer.
- **D:** a Stripe checkout started before a person joined can still complete afterward, and pay for the workspace they left, because `_sync_workspace` has no membership check. D must test that path.
- **E:** agents choose visibility on upload, and `upload_artifact` honours it only under `members_choose`. The REST and MCP error sentences a member can reach still say "upgrade your plan", and E words them for a member.
- **F, once the switch is on for good:** remove `dashboard/golden/` and `SwitchOffTests`.
- **F, before the switch turns on:** move the site's mail to a dedicated sending service under veilfile.com, and then raise `SITE_INVITES_PER_DAY`. Invitations go to strangers, so spam reports would count against the account that sends the site's mail now, and a suspension there would also stop signup verification.
- **F:** `render.yaml`'s comment becomes a real setting, the Plans page states seats, and the owner's Terms and Privacy words cover invitations, stored invited addresses and how long they are kept, and what an administrator sees.
- **PRODUCT.md** still says a workspace has one member. It is local and uncommitted, and needs a refresh when the switch turns on.
