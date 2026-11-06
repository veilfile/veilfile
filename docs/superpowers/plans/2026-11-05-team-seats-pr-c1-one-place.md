# Team seats, PR C1: One Place per Person, and Private Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every request find one place for each person, and make every read of a file or key honour who is asking, with no change anyone on a one-person account can see.

**Architecture:** `current_membership(user)` replaces every owner-based lookup, so a session, an OAuth token and the device flow all land where the person is now. `drop_core/file_access.py` becomes the only way to list or revoke a file, so "only me" holds on REST, MCP and the dashboard from one rule. Keys follow the same idea. A staff-only suspension and a re-check under the workspace lock close the two gaps that a second person would open. Nothing here creates a second person: invitations come in C2, and until then a member row exists only in tests.

**Tech Stack:** Django 6.1.1, Django REST framework, django-oauth-toolkit, SQLite in tests, Postgres 16 in the rehearsal, Postgres in production.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md` (decisions 2, 8, 10 and 11; calls 6, 7, 8 and 11; section 6). This plan covers the first third of row C in the table at the end of `docs/superpowers/plans/2026-11-05-team-seats-pr-a-members.md`. AGENTS.md's "People inside an account" section describes what PR A and PR B built. The description of pull request #28 and the "Carried to PR C" list in `docs/superpowers/plans/2026-11-05-team-seats-pr-b-allowances.md` say what they carried here.

## Row C becomes three pull requests

Row C is about fourteen tasks, twice the size of PR A or PR B. Everything after this one sits behind a switch, so each can merge alone.

| PR | Scope | Visible? |
|---|---|---|
| **C1 (this plan)** | One place per person; every session and OAuth lookup moves to it; "only me" enforced on every list and revoke; keys and device login limited to the person's own keys; staff-only team suspension, which also stops the team's links; a re-check under the lock. | No |
| C2 | The switch. Invitations (a pending one holds a seat), their rate limits and email; joining from a Free account; the Members page; allowances set by the administrator; the visibility setting and the Files page's control; a member's first view; whose key it is on the Keys page. | Behind the switch |
| C3 | Removal with its choice; leaving; the administrator transfer; a removed member's remaining files; staff deletion of a member's account; the administrator told of a held file. | Behind the switch |

D, E and F keep their rows. Each of C2 and C3 gets its own plan after the one before it merges.

## Decisions this plan takes

The owner reviews these with the plan. Each says what it costs if wrong.

1. **A person's one place is their one current `Membership`.** When someone joins a team, C2 will close the administrator row of their old workspace, so `removed_at` already means "not here now" and `APIKey.verify`, upload URLs and device login all stop there with no new code. A new unique constraint allows one current member row per person. It covers `role="member"` only, because the schema has always allowed an owner with two workspaces, and such a person keeps landing on the oldest one, as today. If wrong: a person could have two current rows and the lookup would have to choose, which it does deterministically (a team beats an own workspace, then oldest first). If production held two current member rows, `0016` would fail with Postgres naming the duplicate key, and the deploy would stop with the previous release serving. Only a staff edit could have made such a pair.
2. **A person with no current row gets their own workspace back, at the lookup.** Reopening it there means removal and leaving need no separate step and nobody is ever stranded. Old keys do not come back to life, because C2's join sets them inactive. This costs a write on a read path, for one request, for someone who has just left a team. It also means that closing an owner's row no longer refuses them: staff suspend a person with `is_active` and a team with `suspended_at`. Two PR A tests pin the old refusal, and they change in Tasks 2 and 4.
3. **What a person can see is three things:** their files here, their files left behind under a membership that has ended, and files shared with the team here. "Left behind" reads closed rows only, so an owner's second workspace stays hidden as it is today, which keeps `dashboard/tests_files.py`'s rule that the list and the revoke read the same workspace. The query resolves the closed rows first and then uses two indexed conditions, with no join, because an `OR` across a join would scan the whole file table on every list. It costs one small extra query per list.
4. **A file with no member is shown to nobody.** PR B let a stray file still list. `0014` filled every such row and no code writes a new one, and a file nobody can claim should fail closed, because "only me" is the promise at stake. Staff can still see it in the admin, it still serves, and it still expires. Thirty `Artifact.objects.create` calls in the tests name no member, and those that then list or revoke the file must name one. `AGENTS.md`'s sentence changes. This costs test churn, and a stray file would stop listing if one somehow existed.
5. **Revoking follows decision 11.** A person can revoke their own files, wherever they are. The administrator can also revoke files shared with the team here. A file you cannot see answers exactly as a missing one does. A file you can see but may not revoke answers 403 `not_your_file` on REST, `{"revoked": false}` on MCP, and 404 on the dashboard. The Files page prints a Revoke link on every row, which is still honest here because no file can be shared yet, and C2 must hide it for a file the person cannot revoke.
6. **Keys:** a key, and any caller of REST `GET /api/v1/keys`, sees only that person's own keys. Only the administrator's dashboard lists the team's. A session can revoke its own keys, and the administrator's session can revoke any key on the team. A rotation replaces only your own key, for everyone including the administrator. The CLIs find their own key by prefix in that list, and it stays there. "Whose key it is" arrives on the Keys page in C2.
7. **A plain member is refused checkout, plan switch and the billing portal** with 403 `administrator_only`, because the moved lookup would otherwise act on the team's Stripe customer. `status()` reads the team, so meters stay honest. PR D owns the rest of billing, and C2 owns what the Billing page says to a member.
8. **Team suspension is a nullable `Workspace.suspended_at`, set by a staff action in the admin, and it stops the team's links as well (the owner's decision, 2026-11-05).** It is an action, not a form field, because a full form save would write back stale `plan` and Stripe values. It refuses every key, OAuth token, upload URL and device login on that team, and a session's upload or new key. Every link of that team answers exactly as a missing file does, with no step per file, and lifting the suspension brings back the ones that have not expired. A suspended team's members can still sign in, but they cannot revoke its files, so that a report's evidence stays. Every account is a workspace, so this works on a one-person account too. Suspending one person with `is_active` stays as it is, and stops only that person's credentials, because call 11 says each person's state governs only their own. The Terms already say we may suspend an account for abuse. If wrong: a false suspension takes a whole team's links down until staff lift it.
9. **An upload and a key mint re-read the person's membership under the workspace lock.** Removal in C3 will take the same lock, so either the upload commits first and "revoke all" sees it, or removal commits first and the upload is refused with 403 `member_removed`. This adds one small query to every upload for every customer, and it is the only way to keep decision 12's "stop at once" true under concurrency.
10. **OAuth grants are not touched here.** With the lookup moved, a token follows its person. C2 and C3 will revoke a person's grants when they join, leave or are removed, because the spec's reason for stopping old keys, that a team never inherits what it did not set up, applies to connected apps too.

**Changed during the build, after security reviews.** Decision 2 is narrower than it first read: only a signed-in session reopens a row, and it switches off that row's keys first, in one transaction. A credential never reopens one: `resolve_oauth_credential` passes `reopen=False` and refuses, so Task 2's rename of `test_an_oauth_token_of_a_removed_member_is_refused` is undone. `services.revoke_artifact` itself refuses a suspended team's file, so no route can skip decision 8's evidence rule. The suspend and lift actions need change permission and are recorded in the admin's history. The guard on file reads became an exact table of every read of `Artifact` and `PendingUpload`.

**Found while planning:** the design says a hold "lands on the file and on the key that sent it, as it does now". The code does not do that: nothing in the repository sets `safety_status="held"` except a staff edit in the admin, a safety block has no file, and no email of any kind follows a hold. C3's plan has to build that notice from nothing, and it will say so.

## Global Constraints

- **No visible change for a one-person account.** No JSON answer, status code, error code, MCP result, dashboard page or email changes for any account with one person. The assertions in `drop_core/tests_json_answers.py` and `drop_billing/tests_json_answers.py` stay unchanged. The only allowed edit there is a fixture naming a member (`make_files`, and the two files in `test_expired_and_held_files_are_listed`). No assertion or expected string changes.
- **No code creates a member row.** Tests create them with `second_member` from `drop_core/tests_members.py`.
- **Migrations are additive and safe on a live Postgres table.** `migrate` runs at boot while the release on main keeps serving, and that release knows nothing of the new column or constraint.
- **`current_membership` can write, so it runs before any transaction and never under a lock,** exactly like `administrator_of`.
- **Lock order is fixed:** resolve the member first; then the team counter, the member counter and the workspace row, with the re-checks read after the workspace lock. Nothing here locks a `Membership` row.
- **`serve_artifact` changes in one way only:** a suspended team's link answers as a missing file does. Its answer for every other link is unchanged, byte for byte.
- **Unchanged:** the per-credential throttles, the funnel, billing's plan logic, the writers' defaults (`member or administrator_of(workspace)`), and `get_or_create_workspace` itself, which only signup and the lookup still call.
- **Never name a plan and then a figure on one line** (`<Plan> ... N keys|GB|uploads|days`) in a file `drop_core/tests_fix_docs.py` scans. `PLAN_COPY_GLOBS` includes `backend/drop_core/account.py` and `backend/templates/**/*.html`.
- AGENTS.md invariants hold, notably 4 (published clients keep working), 8 (no edits to `backend/legal/documents/*`) and 9 (no package versions). No new runtime dependencies. The shell's script budget has 6 bytes left, and nothing here adds script.
- Words follow AGENTS.md's Voice section: complete sentences, semicolons only between independent clauses, no em dashes, no exclamation marks.
- **This Mac panicked from memory exhaustion on 2026-11-05 and is shared.** Run heavy checks one at a time, and check `memory_pressure | tail -1` first. Never signal a process you did not start.
- Commits: `Veilfile <support@veilfile.com>`, `TZ=UTC git commit`, plain-sentence subjects, no attribution lines of any kind, no personal names. Branch: `seats/one-place`, from the latest `origin/main` once #31 has merged. Pushing, opening the pull request and merging each need the owner's go-ahead, through Muse's account.

## Review Focus

1. **An administrator reaching for a member's "only me" file** through every surface gets what a missing file gets, and the name appears in no response. Test: `PrivacyMatrixTests` (Task 3).
2. **Pages of a list** must not skip, repeat or reveal hidden files through a cursor or a `Link` header. Test: `test_a_members_pages_show_only_what_they_may_see` (Task 3).
3. **A person with old files somewhere else** can still see and revoke them, and those files count only where they live. Test: `test_files_left_behind_stay_their_uploaders` (Task 3).
4. **A member at a full team with no keys of their own** runs `veilfile login` and is told before approving that nothing can be replaced. Test: `test_a_member_with_no_key_to_replace_is_told_before_approving` (Task 4).
5. **An upload in flight when its person is removed** is refused and leaves no count behind. Test: `test_an_upload_is_refused_when_the_member_was_removed_after_it_began` (Task 5).

---

### Task 1: One place for each person

**Files:**
- Modify: `backend/drop_core/models.py` (`Membership`, `administrator_of`, `current_member`; new `current_membership`, `has_other_members`, `NoCurrentAdministrator`)
- Create: `backend/drop_core/migrations/0016_membership_one_team.py`
- Create: `backend/drop_core/tests_one_place.py`
- Modify: `backend/drop_core/tests_members.py`, only if a test there closes an administrator row and then expects a write to succeed

**Interfaces:**
- Produces: `current_membership(user) -> Membership`, with `.workspace` already loaded. It can write, so it never runs inside a transaction.
- Produces: `Membership.is_administrator` (property, `role == ADMINISTRATOR`), `has_other_members(workspace) -> bool` (more than one current row), and `class NoCurrentAdministrator(Exception)`.
- Produces: constraint `membership_one_team`: `UniqueConstraint(fields=["user"], condition=Q(role="member", removed_at__isnull=True))`.

- [ ] **Step 1: Write the failing tests in `tests_one_place.py`**

Use `make_user` and `second_member` from `tests_members.py`. Each asserts the row returned, by primary key.

```python
class CurrentMembershipTests(TestCase):
    def test_one_person_lands_on_their_workspace(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        self.assertEqual(current_membership(ada), administrator_of(workspace))

    def test_a_member_lands_on_the_team_and_not_their_old_workspace(self):
        team = get_or_create_workspace(make_user("ada"))
        grace = second_member(team, "grace")
        old = get_or_create_workspace(grace.user)
        Membership.objects.filter(workspace=old).update(removed_at=timezone.now())
        self.assertEqual(current_membership(grace.user), grace)
        self.assertEqual(current_membership(grace.user).workspace, team)

    def test_a_team_beats_an_own_workspace_when_both_rows_are_current(self):
        team = get_or_create_workspace(make_user("ada"))
        grace = second_member(team, "grace")
        get_or_create_workspace(grace.user)
        self.assertEqual(current_membership(grace.user), grace)

    def test_an_owner_of_two_workspaces_lands_on_the_oldest(self):
        ada = make_user("ada")
        first = get_or_create_workspace(ada)
        Workspace.objects.create(owner=ada, name="second")
        self.assertEqual(current_membership(ada).workspace, first)

    def test_a_person_with_no_workspace_gets_one(self):
        ada = make_user("ada")
        self.assertEqual(Workspace.objects.filter(owner=ada).count(), 0)
        self.assertEqual(current_membership(ada).workspace.owner_id, ada.pk)

    def test_a_person_with_no_current_row_gets_their_own_workspace_back(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        row = administrator_of(workspace)
        Membership.objects.filter(pk=row.pk).update(removed_at=timezone.now())
        again = current_membership(ada)
        self.assertEqual(again.pk, row.pk)
        self.assertIsNone(again.removed_at)
        self.assertIsNone(Membership.objects.get(pk=row.pk).removed_at)

    def test_reopening_a_workspace_does_not_revive_a_deactivated_key(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        key, raw = APIKey.create_key(workspace, "old")
        APIKey.objects.filter(pk=key.pk).update(is_active=False)
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        current_membership(ada)
        self.assertIsNone(APIKey.verify(raw))

    def test_a_second_current_team_row_is_refused(self):
        grace = second_member(get_or_create_workspace(make_user("ada")), "grace")
        other = get_or_create_workspace(make_user("lin"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(workspace=other, user=grace.user)

    def test_a_person_can_hold_a_closed_row_and_a_current_one(self):
        grace = second_member(get_or_create_workspace(make_user("ada")), "grace")
        Membership.objects.filter(pk=grace.pk).update(removed_at=timezone.now())
        other = get_or_create_workspace(make_user("lin"))
        Membership.objects.create(workspace=other, user=grace.user)
        self.assertEqual(current_membership(grace.user).workspace, other)


class AdministratorGuardTests(TestCase):
    def test_administrator_of_refuses_a_closed_row(self):
        workspace = get_or_create_workspace(make_user("ada"))
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        with self.assertRaises(NoCurrentAdministrator):
            administrator_of(workspace)

    def test_current_member_answers_none_for_a_closed_owner_row(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        self.assertIsNone(current_member(workspace, ada))

    def test_a_writer_with_no_member_refuses_rather_than_writing_to_a_closed_row(self):
        workspace = get_or_create_workspace(make_user("ada"))
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        with self.assertRaises(NoCurrentAdministrator):
            create_key_within_cap(workspace, "agent")


class OtherMembersTests(TestCase):
    def test_it_counts_only_current_people(self):
        workspace = get_or_create_workspace(make_user("ada"))
        self.assertFalse(has_other_members(workspace))
        grace = second_member(workspace, "grace")
        self.assertTrue(has_other_members(workspace))
        Membership.objects.filter(pk=grace.pk).update(removed_at=timezone.now())
        self.assertFalse(has_other_members(workspace))
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_one_place`
Expected: ImportError for `current_membership`.

- [ ] **Step 3: Implement in `models.py`**

`get_or_create_workspace` is unchanged. Its docstring gains the sentence that only signup and `current_membership` call it. Write the two lookups as follows, because the tests leave the order open.

```python
def _administrator_row(workspace):
    membership, _ = Membership.objects.get_or_create(
        workspace=workspace,
        user_id=workspace.owner_id,
        defaults={"role": Membership.ADMINISTRATOR},
    )
    return membership


def administrator_of(workspace):
    """The workspace's current administrator, created if the row is missing."""
    membership = _administrator_row(workspace)
    if membership.removed_at is not None:
        raise NoCurrentAdministrator(workspace.pk)
    return membership


def current_membership(user):
    """The one place this person is now: a team beats an own workspace, then the oldest first.

    A person with no current row gets their own workspace back, so that leaving
    or being removed strands nobody. It can write, so call it before any
    transaction, as with administrator_of.
    """
    rows = list(
        Membership.objects.filter(user=user, removed_at__isnull=True)
        .select_related("workspace")
        .order_by("workspace__created_at", "pk")
    )
    for row in rows:
        if row.role == Membership.MEMBER:
            return row
    if rows:
        return rows[0]
    workspace = get_or_create_workspace(user)
    row = _administrator_row(workspace)
    if row.removed_at is not None:
        Membership.objects.filter(pk=row.pk).update(removed_at=None)
        row.removed_at = None
    row.workspace = workspace
    return row
```

`current_member` keeps its shape, and its owner branch reads `_administrator_row`, because a closed row is an answer there, not an error. `Membership`'s docstring changes to say that a workspace's owner is its current administrator, unless they have moved to another place.

- [ ] **Step 4: Write the migration**

`0016_membership_one_team.py` has one `AddConstraint` and nothing else. Run `makemigrations --check` afterward: Expected "No changes detected".

- [ ] **Step 5: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_one_place drop_core.tests_members`
Expected: OK. If a test in `tests_members.py` closes an administrator row and then expects a write to succeed, the guard is right and that test changes to pass a member explicitly.

- [ ] **Step 6: Commit**

```bash
git add backend/drop_core/models.py backend/drop_core/migrations/0016_membership_one_team.py backend/drop_core/tests_one_place.py backend/drop_core/tests_members.py
TZ=UTC git commit -m "Each person has one current place, and a closed administrator row refuses writes"
```

### Task 2: Every session and OAuth lookup uses it

**Files:**
- Modify: `backend/drop_core/account.py` (`_workspace_of`, `create_key`)
- Modify: `backend/drop_core/views.py` (`_request_workspace`, `_request_member`, the paid pick in `signup`)
- Modify: `backend/drop_core/oauth.py` (`resolve_oauth_credential`)
- Modify: `backend/drop_core/device.py` (`_render_consent`, `device_approve`)
- Modify: `backend/dashboard/keys.py`, `backend/dashboard/views.py`, `backend/dashboard/file_rows.py`
- Modify: `backend/drop_billing/account.py` (`status`, `checkout`, `switch`, `portal`)
- Modify: `backend/dashboard/tests_recipe.py` (`NAMES`), `AGENTS.md` (the recipe's step 8, and line 628's sentence), `docs/API.md` (billing section)
- Modify: `backend/drop_core/tests_members.py` (`test_an_oauth_token_of_a_removed_member_is_refused`)
- Test: `backend/drop_core/tests_one_place.py`, `backend/drop_billing/tests_account.py`

**Interfaces:**
- Consumes: `current_membership(user)` from Task 1.
- Produces: `_workspace_of(user, workspace)` in `account.py` returns `workspace` if given, else `current_membership(user).workspace`. `_request_member(request)` returns the credential's member, or `current_membership(request.user)`, and never None for a signed-in person. It keeps the answer on the request, and `_request_workspace` reads `_request_member(request).workspace` for a session, so one request cannot see two places.
- Produces: a billing refusal `Answer(403, {"error": "administrator_only"})` from `checkout`, `switch` and `portal`, tested before any Stripe call.

- [ ] **Step 1: Write the failing tests**

In `tests_one_place.py`, class `LookupTests`, with a team, an administrator Ada and a member Grace whose old workspace row is closed:

- `test_a_members_session_reads_the_teams_usage`: `self.client.force_login(grace.user)`, then `GET /api/v1/usage` has the team's `uploads_used`.
- `test_a_members_oauth_token_acts_on_the_team`: `resolve_oauth_credential(raw)` has `.workspace == team` and `.acting_member == grace`.
- `test_a_key_made_in_a_members_session_lands_on_the_team`: `account.create_key(grace.user, "agent")` makes a key with `workspace=team` and `member=grace`.
- `test_device_approval_stores_the_team`: approving as Grace leaves `device_code.workspace == team`, and the poll mints a key with `member=grace`.
- `test_a_removed_persons_session_upload_lands_on_their_own_account`: close Grace's team row, sign in, upload through `POST /api/v1/artifacts/`. The new file has `workspace` equal to Grace's own workspace and `member.user == grace.user`, and no team row names Grace's upload.
- `test_a_removed_persons_token_acts_on_their_own_account`: the same token resolves to their own workspace, not the team.
- `test_signup_paid_pick_reads_the_persons_place`: a signed-in member whose team has `stripe_subscription_id` set, following `GET /accounts/signup/?plan=team`, is redirected to `/app/?billing=already_subscribed`.

In `tests_one_place.py`, class `LookupOutsideTransactionTests(TransactionTestCase)`:

- `test_the_lookup_runs_before_any_transaction`: patch `drop_core.views.current_membership` and `drop_core.account.current_membership` with a wrapper that records `connection.in_atomic_block`, then drive a session `POST /api/v1/artifacts/` and `account.create_key(user, "agent")`. At least one call is recorded, and every recorded value is False.

In `tests_members.py`, `test_an_oauth_token_of_a_removed_member_is_refused` closes an owner's own row and expects a refusal, which Decision 2 reverses. Rename it `test_a_closed_owner_row_comes_back_for_an_oauth_token`, and assert that the token resolves with `acting_member.removed_at` None. A suspended person's refusal stays pinned by the existing tests of `is_active`.

In `tests_one_place.py`, class `OnlySignupAndTheLookupTests`, in the shape of `OwnerReadsTests`:

```python
ALLOWED_CALLS = {"drop_core/models.py": 2, "drop_core/views.py": 1}

def test_only_signup_and_the_lookup_call_get_or_create_workspace(self):
    pattern = re.compile(r"\bget_or_create_workspace\(")
    # Same walk as OwnerReadsTests: non-test, non-migration .py under backend/.
    self.assertEqual(found, ALLOWED_CALLS, "Use current_membership(user): AGENTS.md, People inside an account.")
```

In `drop_billing/tests_account.py`, class `MemberBillingTests`:

- `test_a_member_cannot_reach_checkout_switch_or_portal`: each returns `Answer(403, {"error": "administrator_only"})`, with Stripe patched to fail the test if it is called.
- `test_status_reads_the_team_for_a_member`: `status(grace.user).body["plan"]` is the team's plan.
- `test_the_administrator_still_reaches_them`: `checkout(ada, "team")` gets past the refusal, reaching the existing Stripe mock.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_one_place drop_billing.tests_account`
Expected: the new tests FAIL, most with the person's old workspace in place of the team.

- [ ] **Step 3: Implement**

Replace each call as the Interfaces block says. `resolve_oauth_credential` becomes `member = current_membership(token.user)`, then `OAuthCredentialAdapter(token, member.workspace, member)`. `account.create_key` passes `member=membership` and `membership.workspace`. In `device.py`, both functions read `member = current_membership(request.user)` and `workspace = member.workspace`; Task 4 changes what they do with it. The billing refusal comes first in `checkout`, `switch` and `portal`, after each function's existing checks on `plan` and Stripe configuration, so a one-person account's answers keep their order. `signup`'s line 916 stays, because it is what creates the workspace. The guard's regex also matches prose, so reword the docstring of `_own_file` in `dashboard/views.py`, and remove imports that are now unused. Add `administrator_only` to `docs/API.md`'s billing section.

`dashboard/tests_files.py`'s comment and failure message in `test_only_the_workspace_the_list_reads_is_revoked_from` say `get_or_create_workspace(user)`. Change them to `current_membership(user)`; the assertions stay. In `tests_recipe.py`, `NAMES` names `current_membership` at `backend/drop_core/models.py`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core dashboard drop_billing`
Expected: OK. Check `memory_pressure | tail -1` first. `drop_core/tests_json_answers.py` and `drop_billing/tests_json_answers.py` pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend AGENTS.md
TZ=UTC git commit -m "Sessions, OAuth tokens and device login find a person through their current place"
```

### Task 3: "Only me" holds on every list and revoke

**Files:**
- Create: `backend/drop_core/file_access.py`
- Create: `backend/drop_core/tests_file_access.py`
- Modify: `backend/drop_core/account.py` (`list_artifacts`, `revoke_artifact`)
- Modify: `backend/drop_core/views.py` (`ArtifactListCreateView.get`, `ArtifactDeleteView.delete`)
- Modify: `backend/drop_mcp/views.py` (`_tool_list_artifacts`, `_tool_revoke_artifact`)
- Modify: `backend/dashboard/views.py` (`_own_file`), `backend/dashboard/file_rows.py` (`held_ids`)
- Modify: every test fixture that creates a file with no member and then lists or revokes it, out of the thirty such `Artifact.objects.create` calls (including `drop_core/tests_json_answers.py`'s `make_files` and `test_expired_and_held_files_are_listed`)
- Modify: `backend/dashboard/views.py` (`file_revoke`)
- Modify: `AGENTS.md` ("People inside an account"), `docs/API.md` (the revoke section)

**Interfaces:**
- Consumes: `current_membership`, `Membership.is_administrator`.
- Produces: in `drop_core/file_access.py`, `files_visible_to(member) -> QuerySet[Artifact]`, `file_to_revoke(member, artifact_id) -> Artifact | None`, and `class NotYourFile(Exception)`.
- Produces: `account.list_artifacts(user, *, cursor=None, limit=None, member=None)` and `account.revoke_artifact(user, artifact_id, *, member=None)`. Both replace their `workspace=` parameter with `member=`, which defaults to `current_membership(user)`. `tests_account.py`'s calls with `workspace=` at lines 500 and 544 change to `member=administrator_of(...)`. `usage` keeps its `workspace=`.
- Produces: `revoke_artifact` answers 403 `{"error": "Only the person who uploaded a file, or the administrator of a team it is shared with, can revoke it.", "code": "not_your_file"}` on `NotYourFile`, and its existing 404 when `file_to_revoke` returns None.

- [ ] **Step 1: Write the failing tests in `tests_file_access.py`**

```python
class PrivacyMatrixTests(TestCase):
    """Administrator Ada, members Grace and Lin. Each person has one "only me" file and one "team" file."""

    # (viewer, file, may they see it, may they revoke it)
    TABLE = [
        ("ada", "ada-private", True, True),
        ("ada", "ada-shared", True, True),
        ("ada", "grace-private", False, False),
        ("ada", "grace-shared", True, True),
        ("grace", "ada-private", False, False),
        ("grace", "ada-shared", True, False),
        ("grace", "grace-private", True, True),
        ("grace", "grace-shared", True, True),
        ("grace", "lin-private", False, False),
        ("grace", "lin-shared", True, False),
    ]
```

For each row, one test method per surface, driven by `subTest`:

- `test_rest_list_with_a_key` and `test_rest_list_with_a_session`: the file name is in the body only if it may be seen, and when hidden, its name appears nowhere in `response.content`.
- `test_rest_revoke_with_a_key` and `test_rest_revoke_with_a_session`: hidden gives the same status and body as a random UUID; visible but not revocable gives 403 `not_your_file`; revocable gives 204. The file is still there unless it was revocable.
- `test_mcp_list_with_a_key` and `test_mcp_list_with_an_oauth_token`: as for the REST list.
- `test_mcp_revoke`: `{"revoked": false}` unless revocable, and the file remains.
- `test_dashboard_files_page`: the name is in the HTML only if it may be seen.
- `test_dashboard_revoke_page`: `GET` and `POST` of the confirmation are 404 unless revocable, a hidden name never appears, and the file remains.
- `test_dashboard_held_label`: a held "only me" file of a teammate does not appear, and `held_ids` does not return its id even when asked for it.

```python
class FileAccessTests(TestCase):
    def test_a_members_pages_show_only_what_they_may_see(self):
        # Twelve files alternating Grace's and Ada's "only me". Walk Grace's pages with limit=2, once through X-Next-Cursor and once through Link.
        # Every page has only Grace's files, the walk ends, and no id repeats or goes missing.

    def test_files_left_behind_stay_their_uploaders(self):
        # Grace has a file in her old workspace under a closed row, and joins the team. She sees and revokes it.
        # workspace_storage_bytes(team) does not count it, and workspace_storage_bytes(old) does.
        # Then her team row closes, her own workspace comes back, and her "only me" file in the team stays hers to see and revoke,
        # while Ada still cannot see it. Her "team" file stays visible to Ada, and Ada can still revoke it.

    def test_a_file_from_an_upload_url_belongs_to_the_person_who_minted_it(self):
        # Grace mints a URL and PUTs to it. The file names Grace, Grace sees it, and Ada does not.

    def test_the_page_answers_404_for_any_refused_revoke(self):
        # Patch account.revoke_artifact to answer 403 not_your_file: file_revoke's POST is a 404 and raises no KeyError.

    def test_a_second_workspace_of_one_owner_stays_hidden(self):
        # The owner's second workspace has a current row, so its file is not "left behind": the list and revoke agree it is not here.

    def test_a_file_with_no_member_is_shown_to_nobody(self):
        # member=None, visibility only_me: no list has it, revoke is the missing answer, and its link still serves.

    def test_a_one_person_list_is_byte_identical(self):
        # With three files, the REST body equals what list_artifacts built from a plain workspace filter.

    def test_the_list_runs_no_join(self):
        # with CaptureQueriesContext: the Artifact SELECT in files_visible_to has no JOIN in its SQL.


class OnlyFileAccessReadsFilesTests(SimpleTestCase):
    def test_no_other_code_reads_files_by_workspace(self):
        # The walk from OwnerReadsTests, with pattern r"Artifact\.objects\.\w+\(\s*(?:id=[^,]+,\s*)?workspace|\.artifacts\b". Compare with an exact
        # table of what remains: each entry is a count, a sum, an exists, or services.revoke_artifact's own lookup, and none returns a name.

    def test_only_file_access_callers_reach_the_service_revoke(self):
        # The same walk: only drop_core/account.py and drop_mcp/views.py import services.revoke_artifact, and each calls file_to_revoke first.
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_file_access`
Expected: ImportError for `drop_core.file_access`.

- [ ] **Step 3: Implement `file_access.py`**

```python
def files_visible_to(member):
    """The files this person can see: their own here, their own left behind
    under a membership that has ended, and those shared with the team here.

    It resolves the closed rows first, so the query has two indexed conditions
    and no join."""
    closed = Membership.objects.filter(
        user_id=member.user_id, removed_at__isnull=False
    ).values_list("pk", flat=True)
    return Artifact.objects.filter(
        Q(member_id__in=[member.pk, *closed])
        | Q(workspace_id=member.workspace_id, visibility=VISIBILITY_TEAM)
    )


def file_to_revoke(member, artifact_id):
    """The file if this person may revoke it, None if they cannot see it. Raises NotYourFile when they can see it and may not."""
    artifact = files_visible_to(member).filter(id=artifact_id).select_related("member", "workspace").first()
    if artifact is None:
        return None
    mine = artifact.member is not None and artifact.member.user_id == member.user_id
    shared_here = artifact.workspace_id == member.workspace_id and artifact.visibility == VISIBILITY_TEAM
    if mine or (member.is_administrator and shared_here):
        return artifact
    raise NotYourFile()
```

Call `services.revoke_artifact(artifact.workspace, artifact.id)` for the actual revoke: the file's own workspace, which can differ from the person's. `_own_file` turns None and `NotYourFile` into `Http404`, and `file_revoke` treats any answer other than 204 as `Http404`, because it reads `storage_deleted` from the body. `held_ids(user, body)` filters `files_visible_to(current_membership(user))`. The views pass `member=_request_member(request)`. MCP uses `key.acting_member`. Add `not_your_file` to `docs/API.md`'s revoke section. The site's docs, `docs/llms.txt` and the new scope of the list wait for PR E, as PR B's decision 5 left its codes.

- [ ] **Step 4: Name a member in the fixtures**

Run the suite, and for each listing test that now finds no file, add `member=administrator_of(workspace)`, or use the test's own member. Do not change an assertion. In `tests_members.py`, a test that expects a stray file to list changes to expect that nobody sees it. In `AGENTS.md`, replace "a stray file still lists, serves and revokes, but it counts toward nobody's allowance" with "a stray file still serves and expires, but no list shows it and only staff can reach it", and "`Artifact.visibility` is stored ... and nothing reads it yet" with a sentence naming `drop_core/file_access.py` as the only way to list or revoke a file.

- [ ] **Step 5: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core dashboard drop_mcp drop_billing`
Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add backend AGENTS.md
TZ=UTC git commit -m "Only me holds on every list and revoke, through one module"
```

### Task 4: Keys and device login are the person's own

**Files:**
- Modify: `backend/drop_core/key_caps.py`
- Modify: `backend/drop_core/account.py` (`list_keys`, `revoke_key`)
- Modify: `backend/drop_core/views.py` (`KeyListCreateView.get`, `KeyDeleteView.delete`)
- Modify: `backend/drop_core/device.py` (`_render_consent`, `device_approve`, `_redeem`)
- Modify: `backend/templates/device/device_consent.html`, `backend/templates/device/includes/grant_summary.html`
- Modify: `backend/dashboard/keys.py` (`_key_rows`, `_own_key`, `_refusal`)
- Modify: `backend/drop_core/tests_account.py` (lines 249 and 400 pass `workspace=`), `backend/drop_core/tests_members.py` (`test_a_device_login_approved_by_a_removed_member_is_refused`)
- Test: `backend/drop_core/tests_one_place.py`, `backend/drop_core/tests_allowances.py`, `backend/drop_core/tests_device_pages.py`

**Interfaces:**
- Produces: `needs_replacement(workspace, member) -> bool` (the team is at its cap, or the member is at their `key_allowance`), and `replaceable_keys(workspace, member) -> QuerySet[APIKey]` (the member's own active keys, oldest first).
- Produces: `create_key_within_cap(...)` treats `replacing` as a replacement only if it is an active key of `member`. A teammate's key is ignored, so the caps then apply as for a new key.
- Produces: `account.list_keys(user, *, member=None, everyone=False)`, where `everyone` is honoured only for an administrator, and `account.revoke_key(user, key_id, *, member=None, only_key_id=None)`. Both replace `workspace=`.
- Consumes: `has_other_members` from Task 1.

The new words, for the owner's review with this plan. Only a person on a team of two or more can see them. A one-person account's pages stay byte-identical.

| Where | Words |
|---|---|
| Device consent, team at its limit | "Your team's plan allows {{ key_cap }} active API key{{ key_cap\|pluralize }}, and the team has {{ active_total }}. Choose one of your own keys to replace. The key you choose is revoked when the terminal receives its new key; anything still using it stops working." |
| Device consent, member at their allowance | "Your administrator allows you {{ key_allowance }} active API key{{ key_allowance\|pluralize }}. Choose one of your keys to replace. The key you choose is revoked when the terminal receives its new key; anything still using it stops working." |
| Device consent, nothing to replace, member | "This login needs one of your keys to replace, and you have none. Ask your administrator to make room, then run `veilfile login` again." |
| Device consent, nothing to replace, administrator | "This login needs one of your keys to replace, and you have none. Revoke a key on the Keys page to make room, then run `veilfile login` again." |
| Grant summary, on a team | "List your files and the files shared with your team, and download each one through its link"; "Revoke your own files", or for the administrator "Revoke your own files and files shared with the team"; "List your own API keys and revoke only the key in use" |
| Keys page, member at the team's limit | "Your team's plan allows {cap} active {keys}, and the team has reached that limit. Ask your administrator to make room." |
| Device poll, the person has moved | "The account that approved this login has since moved to or left a team. Run veilfile login again." |

- [ ] **Step 1: Write the failing tests**

In `tests_one_place.py`, class `KeysOfAPersonTests`, with Ada as administrator and Grace and Lin as members:

- `test_a_members_key_lists_only_their_own_keys`: `GET /api/v1/keys` with Grace's key has Grace's keys, including that key's prefix, and neither Ada's nor Lin's name.
- `test_an_administrators_key_lists_only_their_own_keys`.
- `test_the_administrators_dashboard_lists_every_key` and `test_a_members_dashboard_lists_only_their_own`.
- `test_a_session_cannot_revoke_a_teammates_key`: Grace's session gets the 404 a missing key gets, and the key stays active.
- `test_the_administrators_session_can_revoke_any_key`.
- `test_a_key_still_cannot_revoke_a_sibling`: 403 `session_required` for the same person's other key, unchanged.
- `test_a_members_key_probing_a_teammates_key_gets_the_missing_answer`: the same status and body as a key id that does not exist.
- `test_the_dashboard_revoke_page_follows_the_same_rule`: GET and POST of `app-key-revoke` are 404 for Grace on Lin's key and work for Ada.
- `test_a_member_is_not_shown_the_billing_link_at_the_cap`: the Keys page at the team's cap has the member's sentence and no Billing link.
- `test_the_administrator_still_gets_the_plan_sentence_at_the_cap`.

In `tests_device_pages.py`:

- `test_consent_offers_only_a_members_own_keys`: at the team's cap, the page lists Grace's keys and not Lin's.
- `test_a_posted_teammates_key_is_refused`: POST with Lin's key id answers 400 and the grant stays pending.
- `test_a_member_at_their_allowance_is_asked_before_approving`: the team has room, `key_allowance=1` and Grace has one key, so the page asks for a replacement.
- `test_a_member_with_no_key_to_replace_is_told_before_approving`: at the team's cap with no keys of Grace's, the page has the sentence and no Approve button, and a POST answers 400 with the same sentence.
- `test_a_one_person_consent_page_keeps_its_words`: render at the cap and below it. The body has the sentence "Your plan allows", the grant summary reads "List every artifact in the workspace", and no blank line is added. Task 7 Step 3 compares these pages byte for byte across releases.
- `test_a_person_who_moved_gets_the_moved_sentence_at_the_poll`: approve, close the row, poll. Answer `access_denied` with the moved sentence.
- `test_an_inactive_approver_keeps_the_old_sentence`.

In `tests_members.py`, `test_a_device_login_approved_by_a_removed_member_is_refused` expects the old sentence for an active approver with a closed row. Change it to expect the moved sentence.

In `tests_allowances.py`, replace the test that a teammate's key counts as new with:

- `test_a_rotation_never_replaces_a_teammates_key`: `create_key_within_cap(team, "x", replacing=lins_key, member=grace)` leaves Lin's key active. At the team's cap, it raises `KeyCapReached`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_one_place drop_core.tests_device_pages drop_core.tests_allowances`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

`list_keys` reads `member.workspace.api_keys` when `everyone and member.is_administrator`, else `member.api_keys`. The rows' fields stay the pinned six. `revoke_key` looks in `member.workspace`, then answers its existing 404 if the caller is not an administrator and the key is not theirs, and only then runs the `only_key_id` branch, so that a teammate's key id and a missing one answer alike. `_own_key` applies the same rule. `_key_rows` passes `everyone=True`.

In `_render_consent`, pass `active_total=active_key_count(workspace)`, `key_allowance=member.key_allowance`, `at_allowance`, `no_key_to_replace=replacement_required and not own_keys` and `on_team=has_other_members(workspace)`. The template's "and this workspace has" prints `active_total`, which a one-person account shows as the same number. `device_approve` looks up the posted key in `replaceable_keys(workspace, member)`. Its `elif` branch refuses with the "nothing to replace" sentence when there are no keys to choose from. `grant_summary.html` receives `on_team` and `is_administrator`, and its `only` keeps that explicit. Write each new branch as a single-line `{% if %}...{% else %}...{% endif %}`, so a one-person page gains no blank line.

`_redeem` keeps its sentence for a missing or inactive approver, and uses the moved sentence when the approver is active but `current_member` is None. `_refusal` reads `current_membership(user)`. For a plain member, it returns `{"kind": "other", "message": ...}` with the member's sentence, because the `cap` kind prints a Billing link. The PR B carry list says `_refusal` needs a branch for `member_key_allowance_reached`, but that code already falls through to `other` with the correct sentence, so it needs none.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core dashboard`
Expected: OK. `drop_core/tests_json_answers.py` passes with no change to its assertions.

- [ ] **Step 5: Commit**

```bash
git add backend
TZ=UTC git commit -m "Keys and device login reach only a person's own keys"
```

### Task 5: Team suspension, and the re-check under the lock

**Files:**
- Modify: `backend/drop_core/models.py` (`Workspace.suspended_at`, `APIKey.verify`)
- Create: `backend/drop_core/migrations/0017_workspace_suspended_at.py`
- Modify: `backend/drop_core/admin.py` (`WorkspaceAdmin`)
- Modify: `backend/drop_core/oauth.py`, `backend/drop_core/views.py` (`_minting_credential_active`, `ERROR_STATUS`, `ERROR_MESSAGES`), `backend/drop_core/device.py` (`_redeem`)
- Modify: `backend/drop_core/views.py` (`serve_artifact`), `backend/dashboard/file_rows.py` (`held_ids`), `backend/dashboard/views.py` (`file_revoke`)
- Modify: `backend/drop_core/services.py` (`upload_artifact`), `backend/drop_core/key_caps.py`, `backend/drop_core/account.py` (`create_key`, `revoke_artifact`)
- Modify: `docs/API.md`
- Test: `backend/drop_core/tests_one_place.py`

**Interfaces:**
- Produces: `Workspace.suspended_at`, a nullable `DateTimeField`.
- Produces: `class KeyNotAllowed(Exception)` in `key_caps.py`, with `.code` of `member_removed` or `workspace_suspended`. It is not a `KeyCapReached`.
- Produces: `upload_artifact` raises `ValueError("workspace_suspended")` or `ValueError("member_removed")`, and `ERROR_STATUS` maps both to 403.

The new words:

| Code | Words |
|---|---|
| `workspace_suspended` | "This account is suspended, so nothing was stored. Contact support@veilfile.com." |
| `member_removed` | "You are no longer on this team, so nothing was stored." |
| Device poll, suspended | "This account is suspended, so no key was made. Contact support@veilfile.com." |
| Revoking a file, suspended | "This account is suspended, so the file was not revoked. Contact support@veilfile.com." |

- [ ] **Step 1: Write the failing tests**

In `tests_one_place.py`, class `SuspensionTests`:

- `test_a_suspended_teams_keys_are_refused`: every member's key answers `APIKey.verify(raw) is None`.
- `test_a_suspended_teams_oauth_tokens_are_refused`: `resolve_oauth_credential(raw)` is None.
- `test_a_suspended_teams_upload_urls_are_dead`: a `PendingUpload` PUT answers 404 and marks the row used.
- `test_a_suspended_teams_device_login_is_refused`: the poll answers `access_denied` with the suspended sentence, and no key exists.
- `test_a_session_upload_is_refused_with_its_code`: `POST /api/v1/artifacts/` answers 403 `workspace_suspended`, with no file and both counters unchanged.
- `test_a_session_key_is_refused_with_its_code`: `POST /api/v1/keys` answers 403 `workspace_suspended`.
- `test_people_can_still_sign_in`: the Overview page answers 200.
- `test_a_suspended_teams_links_answer_like_missing_ones`: status, headers and body equal those of a token that never existed, for a live file of every member, and the stored bytes remain.
- `test_lifting_a_suspension_brings_back_the_links`: with `suspended_at=None` again, the same link serves its bytes, and a file that expired meanwhile stays a miss.
- `test_a_left_behind_file_stops_with_the_team_it_lives_in`: a former member's file in the suspended team is a miss, and that person's file in their own workspace still serves.
- `test_suspending_a_person_leaves_their_links_serving`: `is_active=False` on one person is unchanged, and their link serves.
- `test_the_files_page_shows_a_suspended_teams_files_as_held`: the state reads Held, and the page offers no link or Revoke.
- `test_a_suspended_teams_files_cannot_be_revoked`: a session `DELETE` answers 403 `workspace_suspended`, the dashboard's POST changes nothing and goes back to Files, and the file remains.
- `test_suspending_one_team_touches_no_other`: another workspace's key still verifies.
- `test_the_admin_action_writes_only_the_suspension`: with the workspace's `plan` changed in the database after the admin loaded the page, the action leaves `plan` as it now is.
- `test_lifting_a_suspension_restores_access`: with `suspended_at=None` again, the same raw key verifies.
- `test_suspending_a_person_still_touches_only_that_person`: the existing person rule is unchanged, so Ada's `is_active=False` leaves Grace's key working.

Class `InFlightTests`, using a stale `member` object loaded before the row closed:

- `test_an_upload_is_refused_when_the_member_was_removed_after_it_began`: `upload_artifact(workspace, None, "a.txt", b"hi", "text/plain", member=stale)` raises `ValueError("member_removed")`, with no `Artifact` row, the team's `UsageCounter` and the `MemberUsageCounter` both unchanged, and nothing in storage.
- `test_an_upload_is_refused_when_the_team_was_suspended_after_it_began`.
- `test_a_key_mint_is_refused_when_the_member_was_removed_after_it_began`: `create_key_within_cap` raises `KeyNotAllowed` with code `member_removed`, and no key exists.
- `test_each_code_is_documented`: `test_every_upload_error_code_is_documented` already requires both rows, so run it.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_one_place`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement the suspension**

`APIKey.verify`'s filter gains `workspace__suspended_at__isnull=True`. `resolve_oauth_credential` returns None if `member.workspace.suspended_at` is set. `_minting_credential_active` returns False if `pending.workspace.suspended_at` is set, because `select_related` has already loaded the workspace. `serve_artifact` reads the file with `select_related("workspace")`, so it adds a join and no query, and it returns `artifact_miss()` straight after the existing `safety_status` test when `artifact.workspace.suspended_at` is set. `held_ids` also returns files whose workspace is suspended, and `file_revoke` counts a suspended team's file as held in `state_of`, so the page keeps its rule that it never offers a dead link. `account.revoke_artifact` answers 403 with code `workspace_suspended` and the revoking sentence when `artifact.workspace.suspended_at` is set, before it revokes anything. MCP's `_tool_revoke_artifact` needs no change, because a suspended team's credentials are refused before it runs. `WorkspaceAdmin` shows `suspended_at` in `list_display` and as read-only on the form, and gains two actions, suspend and lift, each using `update(suspended_at=...)`. `0017_workspace_suspended_at.py` has one nullable `AddField`.

- [ ] **Step 4: Implement the re-check, in this order**

In `upload_artifact`, replace the existing `Workspace.objects.select_for_update().only("id").get(pk=workspace.pk)` with this. The test is positive, so a row that has gone or belongs elsewhere also refuses:

```python
locked = Workspace.objects.select_for_update().only("id", "suspended_at").get(pk=workspace.pk)
if locked.suspended_at is not None:
    raise ValueError("workspace_suspended")
if not Membership.objects.filter(pk=member.pk, workspace_id=workspace.pk, removed_at__isnull=True).exists():
    raise ValueError("member_removed")
```

`create_key_within_cap` makes the same two tests straight after taking `locked`, and raises `KeyNotAllowed`. `account.create_key` answers it with 403 and the code. `_redeem` turns it into its refused sentence. Add both codes to `ERROR_STATUS` and `ERROR_MESSAGES`, add rows for them to `docs/API.md`'s upload table, and add them to its MCP sentence.

- [ ] **Step 5: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core`
Expected: OK. `ResolveMemberBeforeLockTests` still passes, because nothing here calls `administrator_of` under the lock.

- [ ] **Step 6: Commit**

```bash
git add backend docs/API.md
TZ=UTC git commit -m "Staff can suspend a team, and an upload or key mint re-reads the person under the lock"
```

### Task 6: AGENTS.md and the full suite

**Files:**
- Modify: `AGENTS.md` ("People inside an account")

- [ ] **Step 1: Update the section**

Add, as bullets in the section's existing voice:

- A person has one place, and `current_membership(user)` finds it. Every session, OAuth token and device login goes through it. Only signup and that function call `get_or_create_workspace`, and a test keeps it so.
- It can write, so it runs before any transaction, as `administrator_of` does.
- A person with no current row gets their own workspace back at the lookup. A key does not come back with it, because a join sets the person's old keys inactive.
- `drop_core/file_access.py` is the only way to list or revoke a file. A person sees their files here, their files left behind under a membership that has ended, and files shared with the team here. A test keeps every other read of files by workspace to a count, sum or exists.
- A key and REST `GET /api/v1/keys` see only that person's keys. Only the administrator's dashboard lists the team's. A rotation replaces only your own key.
- `Workspace.suspended_at`, set by a staff action, stops every credential and every new upload or key on that team, and every link of that team answers as a missing file does. Its people can still sign in, but they cannot revoke its files, so that a report's evidence stays. Lifting it brings back the links that have not expired. Suspending one person with `is_active` is separate, and stops only that person's credentials.
- An upload and a key mint re-read the person's row and the team's suspension under the workspace lock, and removal must take the same lock.
- A plain member is refused checkout, plan switch and the portal, and PR D owns the rest of billing.

Change the "`Workspace.owner` stays ..." bullet so that it stops saying `get_or_create_workspace` is unchanged, and change the sentence "A member's allowances ... only staff can set them until PR C" to say PR C2.

- [ ] **Step 2: Run the full suites, one at a time**

Run: `memory_pressure | tail -1`, then `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: OK. Then run `cd cli && ../venv/bin/python -m pytest -q` and `cd npm && npm test`, which should show no change, because no client changed. Then run the dashboard and forms parts of the site checks as AGENTS.md describes, one at a time, because Task 4 changes the consent templates.

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
TZ=UTC git commit -m "AGENTS.md explains one place per person and who can read what"
```

### Task 7: Rehearse on Postgres, then stop for the owner

This is the only evidence on locks and query plans, because the suite runs on SQLite. Nothing here commits unless the rehearsal finds a defect.

**Files:** none in the repository. The kit is `$HOME/dev/veilfile-rehearsal-kit`, the throwaway cluster is on `127.0.0.1:8779` at `$HOME/dev/veilfile-pg-scratch`, and the old release is a worktree of `origin/main` at `$HOME/dev/veilfile-pg-old`. The server for a real HTTP run is `runserver --noreload` on `127.0.0.1:8778`, with the old release on `8777`.

- [ ] **Step 0: Bring the kit up to date.** It has no script to create the cluster, it hard-codes migrations `0014` and `0015`, the rollback target `0013` and the old scratchpad path, and it has no team seed. Write or change these: a script for `initdb` and the old worktree, with `log_lock_waits=on` and `deadlock_timeout=200ms`; parameters for the migration names and the rollback target; a seed of one team of three people with 50,000 files; a matrix and fuzz driver that speaks HTTP to the two servers; an `EXPLAIN` script; a race script for Step 6; and a comparator that masks `last_used_at` across REST, MCP and the consent page's HTML.

- [ ] **Step 1: Rebuild the cluster and seed it** at the same volume as PR B: 5,146 workspaces, 20,046 keys and 200,207 files, with the old release's migrations applied.

- [ ] **Step 2: Migrate under load.** Run the old release's readers and writers during `migrate`, and record the duration, probe errors, lock waits and longest stall. Then repeat `0017` with a transaction holding one workspace row for 5 s, as a webhook waiting on Stripe would, and record how long reads of the table queue behind it. **Bar:** 0 probe errors, and no stall over 1 s that a lock explains, apart from that deliberate hold, whose queue goes in the deploy notes.

- [ ] **Step 3: Compare one-person responses.** Run the old and new releases against the same rows, across REST, MCP and the device consent page at the cap and below it. **Bar:** byte-identical once `last_used_at` is masked. Also compare `GET /a/<token>` for live, expired and held files, status, headers and bytes.

- [ ] **Step 4: Attack the privacy rule over real HTTP.** Using a new seed with one team of three people holding 50,000 files, run the `PrivacyMatrixTests` table through a real server, and also try every id, cursor and `limit` value that could reveal a hidden file. **Bar:** no hidden name, id or link appears in any response. Then suspend the team and fetch every link: all answer as a missing file does, and lifting the suspension brings them back.

- [ ] **Step 5: Measure the list.** Compare `GET /api/v1/artifacts?limit=50` on the old and new releases, for a one-person account of 50,000 files and for that team, and record `EXPLAIN` for both queries. At this size a sequential scan can be the planner's right answer for either query, so its absence is not the test. **Bar:** the plan is no worse than the old release's on the same rows, and p95 is within twice the old release's plus 10 ms.

- [ ] **Step 6: Race the lock.** At 30 threads, run uploads and key mints while another thread takes the workspace lock, sets `removed_at`, deactivates the member's keys and sets `expires_at=now` on the member's files, which is what C3's removal will do. It touches no counter, which is why it cannot form a cycle with an upload's order of counters and then workspace. Separately, send 30 concurrent first requests for a person whose only row is closed. **Bar:** 0 deadlocks; after the removal commits, no file of that member is live and no key of theirs verifies; each month's upload counter equals the uploads committed this month, and storage equals the sum of live files; the 30 first requests leave one current row and no new workspace.

- [ ] **Step 7: Run both releases together for 65 s.** **Bar:** 0 errors.

- [ ] **Step 8: Roll back** the code first, then run `migrate drop_core 0015`, and record the time. A rollback loses any suspension.

- [ ] **Step 9: Run the full suite on Postgres.** **Bar:** the same failures as `main` on Postgres, and no new ones.

- [ ] **Step 10: Remove the cluster and the worktree, and confirm** that port 8779 is closed and that port 5432 is untouched.

- [ ] **Step 11: Stop for the owner.** Report the results and the rulings. The pull request's description carries these deploy notes: `0016` stops the deploy if two current member rows exist; `0017`'s `ALTER TABLE` waits for any transaction holding a workspace row, including one waiting on Stripe, and new reads of the table queue behind it for that time; a rollback removes the code first and loses suspensions; staff should not suspend anyone until the rollout finishes, because the previous release ignores `suspended_at`; `docs/API.md` has rows for codes no customer can reach yet, as PR B's did, and a test requires them; staff suspend with `is_active` or `suspended_at`, never by closing an owner's row. Pushing, opening the pull request and merging each need the owner's go-ahead.

## Carried to C2 and C3

These are not part of this plan. They are listed so that nothing is lost, and each proposal gets the owner's review with its own plan.

- **C2, the switch:** a setting read from an environment variable, off unless set. A `VEILFILE_*` variable is scrubbed from test runs and needs a README row, which `EnvVarDocsTests` enforces. A view-level 404 must run before `login_required`, because `dashboard/tests_app.py` requires the shell's 404 signed in or signed out.
- **C2, joining:** close the old administrator row, set the old keys inactive, revoke the person's OAuth grants, refuse a paid individual until D, and lock the two workspaces in primary-key order.
- **C2, an administrator with other members** cannot accept another team's invitation until they transfer the role (spec 9.4). `has_other_members` from this plan answers it. The join re-reads the person's rows after taking its locks, because `current_membership` can reopen a row without one.
- **C2, what a member is told:** hide the Revoke link for a file the person cannot revoke, and replace the upgrade wording a member still reaches on REST errors and the paid signup path.
- **C2, the seat mutex:** the workspace row, which `upload_artifact` and `switch` already take, so that D's "refuse fewer seats than members" needs no second lock. Every membership and invitation write goes through it, and `administrator_of` never runs under it.
- **C2, proving the address:** the signed-in account's verified address must equal the invited one, because a new account signs in before verifying and can type any address. A notice on Overview finds a pending invitation for a verified address, so the verification link needs no route back.
- **C2, rate limits (a proposal for the owner):** per team, 10 an hour and 30 a day; per invited address, 3 a day across all teams; at most 25 pending at once on any plan, including Scale. For comparison, password reset allows 3 an hour per address and 10 an hour per IP, and verification allows 5 a day per address.
- **C2, the Members page** hangs off Overview and adds no sixth menu section, because the menu has no role audience and seven tests pin five sections. It adds no script. Confirmations are separate pages, as they are for revoking a file.
- **C2, `upload_artifact` writes `visibility`** from the team's setting, read once under the lock and copied to the row. The Files page gets a per-file control, for the uploader only, when the administrator lets members choose. `tests_fix_docs._cap_facts` gets its `seats` pattern before any page states a seat count.
- **C2, the invitation link:** a `SECRET_PATH_PREFIXES` entry in `config/log_redaction.py`, and `X-Robots-Tag`, because `robots.txt` does not cover a new top-level path.
- **C3, "revoke all of them now":** one `UPDATE` setting `expires_at=now`, under the workspace lock, sparing held files. Links die and meters free at once, and the bytes go at the next sweep, within 24 hours. The owner should say whether that meets "now".
- **C3, removal** sets the member's keys inactive so they stop using the shared cap and a re-invite cannot revive them. A re-invite reopens the same row, with its allowances cleared. Anything that locks a `Membership` row uses `select_for_update(no_key=True)` or takes the counters first (PR B's carry). An administrator with other members cannot leave or delete their account (spec 9.3).
- **C3, email change for a member** works as it does today, and an invitation matches the verified address at the time it is accepted (row C, call 13).
- **C3, the transfer** moves `Workspace.owner` and both roles in one transaction, demoting first because the unique index is not deferrable, and uses `update(owner=...)` so a stale `plan` cannot be written back. Stripe's customer email and the card wait for D.
- **C3, account deletion stays with staff.** No self-service deletion exists, and the Terms and Privacy Policy promise it by emailing support, so a service function makes the admin path safe rather than a new page. `APIKey.member` is `RESTRICT` too, so keys go first.
- **C3, the hold notice** has no hook today. It becomes a staff action in the admin, with a plain-text email to the administrator and a count on the Members page, and it never shows a name.
- **Everything listed under "What the later pull requests must carry" for PR C in #28** that this plan has not done: handling a removed session user's account deletion, and tests for an inactive non-owner member's upload URL.
- **PRODUCT.md** still says a workspace has one member and that seats are not promised. It is local and uncommitted, and needs a refresh when the switch turns on.
