# Team seats, PR A: People Inside an Account Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a membership row underneath every account, so that every key, upload URL, file and safety event records the person behind it and every credential check tests that person, with no change anyone can see.

**Architecture:** A new `Membership` model joins a user to a workspace with a role (administrator or member). Every existing workspace gets one administrator membership for its owner, from a signal for new workspaces and a backfill for old ones. Keys, upload URLs, files and safety events gain a nullable `member`; files gain a stored `visibility`. Credentials expose `acting_member` and `person`, and the four checks that read `workspace.owner` today read the person instead, with the owner as the fallback for rows the previous release writes during a deploy.

**Tech Stack:** Django 6.1.1, Django REST framework, django-oauth-toolkit, SQLite in tests, Postgres in production.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md` (approved 2026-11-05). This plan covers the first of six pull requests (see "The six pull requests" at the end).

## Global Constraints

- **No visible change.** No JSON response, status code, error code, MCP tool result, dashboard page, email or doc changes. `drop_core/tests_json_answers.py` and `drop_billing/tests_json_answers.py` must pass unchanged. Nothing in this PR reads `Artifact.visibility`.
- **Migrations are additive and safe on a live Postgres table** (AGENTS.md, Deploy facts): every new column is nullable or has `db_default`; the backfill is idempotent and its reverse is `migrations.RunPython.noop`. `migrate` runs at every boot, and the previous release keeps inserting rows without the new columns for a few minutes.
- **A row with a NULL `member` belongs to the workspace's administrator.** Every reader treats it that way, permanently.
- **`Workspace.owner` stays the administrator and the payer.** Billing, Stripe lookups, `get_or_create_workspace` and the funnel are unchanged.
- **Each person's account state governs only their own credentials** (spec, call 11): a key, OAuth token or upload URL tests the person who made it, never the owner unless the row's member is NULL.
- **Email verification stays per person** and gates exactly what it gates today.
- **Unchanged:** `serve_artifact` (no membership or visibility check on `/a/<token>`), the upload counter, storage check and `select_for_update` order in `upload_artifact`, the per-credential throttle buckets (`key-<pk>`, `oauth-token-<pk>`), the key cap (`create_key_within_cap` still locks the workspace row and counts the workspace's keys).
- **Membership rows are never deleted by application code.** Removal (a later PR) sets `removed_at`.
- AGENTS.md invariants hold, notably 4 (published clients keep working), 8 (no edits to `backend/legal/documents/*`), 9 (no package versions) and "no new runtime dependencies".
- Commits: `Veilfile <support@veilfile.com>`, `TZ=UTC git commit`, plain-sentence subjects, no attribution lines of any kind, no personal names (the `names` check refuses them). Branch: `seats/members-underneath`. Merging needs the owner's go-ahead; Muse's account opens and merges the pull request.

## Review Focus

1. **Deploy overlap:** a key, upload URL or file inserted with a NULL `member` after the migration keeps working exactly as today. Tests: `test_a_key_with_no_member_verifies_for_an_active_owner` (Task 3), `test_an_upload_url_with_no_member_tests_the_owner` (Task 4).
2. **An owner with two workspaces** (the schema allows it; tests create one): the backfill gives each its own administrator, and every lookup still lands on the oldest. Test: `test_an_owner_of_two_workspaces_gets_an_administrator_in_each` (Task 2).
3. **Deleting a user still deletes everything** it deletes today, while deleting a membership that keys or files point to is refused. Tests: `test_deleting_a_user_still_deletes_their_account` and `test_a_membership_with_files_cannot_be_deleted` (Task 2).
4. **Suspension moves from the owner to the person:** a suspended owner of a team of one still loses every key, OAuth token, upload URL and device login; a second member's key keeps working while the owner is suspended. Tests in Tasks 3 and 4, listed there.
5. **A leftover owner read** would pass every single-person test. Test: `OwnerReadsTests.test_only_the_fallbacks_read_the_workspace_owner` (Task 5).

---

### Task 1: The membership model

**Files:**
- Modify: `backend/drop_core/models.py` (new model and helpers after `get_or_create_workspace`, line 40)
- Modify: `backend/drop_core/signals.py` (new receiver)
- Modify: `backend/drop_core/admin.py` (register `Membership`)
- Create: `backend/drop_core/tests_members.py`
- Create: `backend/drop_core/migrations/0011_members.py` (generated)

**Interfaces:**
- Produces, in `drop_core/models.py`:
  - `class Membership(models.Model)` with `ADMINISTRATOR = "administrator"`, `MEMBER = "member"`, `ROLE_CHOICES`; fields `workspace = ForeignKey(Workspace, CASCADE, related_name="memberships")`, `user = ForeignKey(AUTH_USER_MODEL, CASCADE, related_name="memberships")`, `role = CharField(max_length=16, choices=ROLE_CHOICES, default=MEMBER)`, `created_at = DateTimeField(auto_now_add=True)`, `removed_at = DateTimeField(null=True, blank=True)`; property `is_current -> bool` (`removed_at is None`).
  - Constraints: `UniqueConstraint(fields=["workspace", "user"], name="membership_one_per_person")` and `UniqueConstraint(fields=["workspace"], condition=Q(role="administrator", removed_at__isnull=True), name="membership_one_administrator")`. No constraint on `user` alone: an owner of two workspaces has two memberships.
  - `administrator_of(workspace) -> Membership`: `get_or_create(workspace=workspace, user_id=workspace.owner_id, defaults={"role": Membership.ADMINISTRATOR})`. It heals a workspace that has no membership.
  - `current_member(workspace, user) -> Membership | None`: None for an anonymous or missing user; the administrator (through `administrator_of`) when `user.pk == workspace.owner_id`; otherwise the user's membership of that workspace with `removed_at` NULL, or None.
  - `VISIBILITY_ONLY_ME = "only_me"`, `VISIBILITY_TEAM = "team"`, `VISIBILITY_CHOICES` (used in Task 2).
- Produces, in `drop_core/signals.py`: a `post_save` receiver on `Workspace`, `dispatch_uid="drop_core.workspace_administrator"`, that calls `administrator_of(instance)` when `created` is true. It covers `get_or_create_workspace`, sign-up, the admin and every direct `Workspace.objects.create(...)` in tests.

- [ ] **Step 1: Write the failing tests** in `backend/drop_core/tests_members.py`, class `MembershipTests(TestCase)`, with module helpers `make_user(name, email="")` (a user with a verified `EmailVerification`, as `tests_account.make_user` does) and `second_member(workspace, name, email="") -> Membership` (a user made by `make_user`, plus `Membership(workspace, user, role=MEMBER)`):

```python
def test_a_new_workspace_gets_its_owner_as_administrator(self):
    owner = make_user("ada")
    workspace = get_or_create_workspace(owner)
    member = Membership.objects.get(workspace=workspace)
    self.assertEqual((member.user, member.role, member.removed_at), (owner, Membership.ADMINISTRATOR, None))

def test_a_workspace_made_directly_gets_an_administrator_too(self):
    owner = make_user("ada")
    workspace = Workspace.objects.create(owner=owner, name="direct")
    self.assertEqual(administrator_of(workspace).user, owner)
    self.assertEqual(Membership.objects.filter(workspace=workspace).count(), 1)

def test_a_workspace_has_one_current_administrator(self):
    workspace = get_or_create_workspace(make_user("ada"))
    with self.assertRaises(IntegrityError), transaction.atomic():
        Membership.objects.create(workspace=workspace, user=make_user("grace"), role=Membership.ADMINISTRATOR)

def test_a_person_belongs_to_a_workspace_once(self):
    workspace = get_or_create_workspace(make_user("ada"))
    with self.assertRaises(IntegrityError), transaction.atomic():
        Membership.objects.create(workspace=workspace, user=workspace.owner)

def test_administrator_of_heals_a_workspace_with_no_membership(self):
    workspace = get_or_create_workspace(make_user("ada"))
    Membership.objects.filter(workspace=workspace).delete()
    self.assertEqual(administrator_of(workspace).role, Membership.ADMINISTRATOR)

def test_current_member_finds_the_person_and_ignores_removed_ones(self):
    workspace = get_or_create_workspace(make_user("ada"))
    grace = second_member(workspace, "grace")
    self.assertEqual(current_member(workspace, workspace.owner), administrator_of(workspace))
    self.assertEqual(current_member(workspace, grace.user), grace)
    Membership.objects.filter(pk=grace.pk).update(removed_at=timezone.now())
    self.assertIsNone(current_member(workspace, grace.user))
    self.assertIsNone(current_member(workspace, make_user("stranger")))
    self.assertIsNone(current_member(workspace, AnonymousUser()))
```

- [ ] **Step 2: Run them to see them fail.** Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_members` Expected: ImportError on `Membership`.

- [ ] **Step 3: Add the model, the two helpers, the constants and the receiver.** Register `Membership` in `drop_core/admin.py` with `list_display = ("workspace", "user", "role", "created_at", "removed_at")`, `list_filter = ("role",)` and `raw_id_fields = ("workspace", "user")`. Generate the schema migration: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations drop_core --name members` (expected file: `drop_core/migrations/0011_members.py`, holding only the new table).

- [ ] **Step 4: Run the tests.** Run the Step 2 command. Expected: 6 tests pass. Then run `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core dashboard drop_billing drop_mcp` Expected: pass (the receiver gives every test workspace an administrator; nothing reads it yet).

- [ ] **Step 5: Commit.**

```bash
git add backend/drop_core/models.py backend/drop_core/signals.py backend/drop_core/admin.py backend/drop_core/tests_members.py backend/drop_core/migrations/0011_members.py
TZ=UTC git commit -m "Every account has its owner as its administrator, in a new membership table"
```

---

### Task 2: Who made each key, upload URL, file and safety event

**Files:**
- Modify: `backend/drop_core/models.py` (`APIKey` line 50, `Artifact` line 106, `PendingUpload` line 140, `SafetyEvent` line 214)
- Create: `backend/drop_core/migrations/0012_member_columns.py` (generated)
- Create: `backend/drop_core/migrations/0013_backfill_members.py`
- Modify: `backend/drop_core/admin.py` (`APIKeyAdmin`, `ArtifactAdmin`)
- Test: `backend/drop_core/tests_members.py`

**Interfaces:**
- Consumes: `Membership`, `administrator_of`, `VISIBILITY_*` (Task 1).
- Produces:
  - `APIKey.member = ForeignKey(Membership, null=True, blank=True, on_delete=RESTRICT, related_name="api_keys")`
  - `Artifact.member = ForeignKey(Membership, null=True, blank=True, on_delete=RESTRICT, related_name="artifacts")`
  - `Artifact.visibility = CharField(max_length=8, choices=VISIBILITY_CHOICES, default=VISIBILITY_ONLY_ME, db_default=VISIBILITY_ONLY_ME)` (spec, section 9 question 1: files from before an account is a team are "only me")
  - `PendingUpload.member = ForeignKey(Membership, null=True, blank=True, on_delete=CASCADE, related_name="pending_uploads")` (a URL dies with its minter, as `api_key` does)
  - `SafetyEvent.member = ForeignKey(Membership, null=True, blank=True, on_delete=SET_NULL, related_name="safety_events")`
  - `backfill_members(apps, schema_editor)` in `0013_backfill_members.py`.

`RESTRICT`, not `PROTECT`, on keys and files: deleting a user still cascades through the workspace and takes its keys and files with it, while deleting a membership row on its own is refused, so a file never loses its uploader.

- [ ] **Step 1: Write the failing tests** in class `BackfillTests(TestCase)`. Load the function with `importlib.import_module("drop_core.migrations.0013_backfill_members").backfill_members` and call it as `backfill(django.apps.apps, None)`. A helper `legacy_rows(workspace)` makes one key, one file (`Artifact.objects.create` as in `dashboard/tests_files.make_file`, with no storage write), one upload URL and one `SafetyEvent`, then sets `member=None` on all four with `.update()`.

```python
def test_rows_with_no_member_go_to_the_administrator(self):
    workspace = get_or_create_workspace(make_user("ada"))
    legacy_rows(workspace)
    backfill(apps, None)
    admin = administrator_of(workspace)
    for model in (APIKey, Artifact, PendingUpload, SafetyEvent):
        self.assertEqual(set(model.objects.values_list("member", flat=True)), {admin.pk}, model.__name__)

def test_a_workspace_with_no_membership_gets_one(self):
    workspace = get_or_create_workspace(make_user("ada"))
    legacy_rows(workspace)
    Membership.objects.filter(workspace=workspace).delete()  # allowed: nothing points at it after legacy_rows
    backfill(apps, None)
    self.assertEqual(Membership.objects.get(workspace=workspace).role, Membership.ADMINISTRATOR)
    self.assertFalse(APIKey.objects.filter(member__isnull=True).exists())

def test_the_backfill_can_run_twice(self):
    workspace = get_or_create_workspace(make_user("ada"))
    legacy_rows(workspace)
    backfill(apps, None); backfill(apps, None)
    self.assertEqual(Membership.objects.filter(workspace=workspace).count(), 1)

def test_an_owner_of_two_workspaces_gets_an_administrator_in_each(self):
    ada = make_user("ada")
    first = get_or_create_workspace(ada)
    second = Workspace.objects.create(owner=ada, name="second")
    Membership.objects.all().delete()
    backfill(apps, None)
    self.assertEqual(Membership.objects.filter(user=ada, role=Membership.ADMINISTRATOR).count(), 2)
    self.assertEqual(get_or_create_workspace(ada), first)

def test_new_files_are_only_me(self):
    workspace = get_or_create_workspace(make_user("ada"))
    artifact = Artifact.objects.create(workspace=workspace, filename="a.txt", size_bytes=1, expires_at=timezone.now() + timedelta(days=1))
    self.assertEqual(artifact.visibility, VISIBILITY_ONLY_ME)

def test_deleting_a_user_still_deletes_their_account(self):
    ada = make_user("ada")
    workspace = get_or_create_workspace(ada)
    APIKey.create_key(workspace, "agent")
    Artifact.objects.create(workspace=workspace, member=administrator_of(workspace), filename="a.txt", size_bytes=1, expires_at=timezone.now() + timedelta(days=1))
    ada.delete()
    for model in (Workspace, Membership, APIKey, Artifact):
        self.assertFalse(model.objects.exists(), model.__name__)

def test_a_membership_with_files_cannot_be_deleted(self):
    workspace = get_or_create_workspace(make_user("ada"))
    Artifact.objects.create(workspace=workspace, member=administrator_of(workspace), filename="a.txt", size_bytes=1, expires_at=timezone.now() + timedelta(days=1))
    with self.assertRaises(RestrictedError):
        administrator_of(workspace).delete()
```

The user-deletion test makes a file through `Artifact.objects.create` with no stored bytes; patch or clean up storage as `dashboard/tests_files.py` does if the `post_delete` receiver needs a local directory (`tests.py:_use_local_dir`).

- [ ] **Step 2: Run them to see them fail.** Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_members.BackfillTests` Expected: errors on the missing fields and module.

- [ ] **Step 3: Add the four fields and the two migrations.** Generate the columns with `makemigrations drop_core --name member_columns` (expected: `0012_member_columns.py`), then the backfill with `makemigrations drop_core --empty --name backfill_members` (expected: `0013_backfill_members.py`), and give the backfill this function and `migrations.RunPython(backfill_members, migrations.RunPython.noop)`:

```python
def backfill_members(apps, schema_editor):
    """Gives every workspace its owner as administrator, then gives that administrator every key, upload URL,
    file and safety event that names no member. Runs at every deploy without harm."""
    Workspace = apps.get_model("drop_core", "Workspace")
    Membership = apps.get_model("drop_core", "Membership")
    for workspace in Workspace.objects.filter(memberships__isnull=True).only("id", "owner_id").iterator():
        Membership.objects.get_or_create(
            workspace_id=workspace.id, user_id=workspace.owner_id, defaults={"role": "administrator"}
        )
    administrator = Membership.objects.filter(
        workspace=OuterRef("workspace"), role="administrator", removed_at__isnull=True
    ).values("pk")[:1]
    for name in ("APIKey", "Artifact", "PendingUpload", "SafetyEvent"):
        apps.get_model("drop_core", name).objects.filter(member__isnull=True).update(member=Subquery(administrator))
```

Add `raw_id_fields = ("member",)` and `"member"` to `list_display` in `APIKeyAdmin` and `ArtifactAdmin`.

- [ ] **Step 4: Run the tests and the migration check.** Run the Step 2 command, then `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run` Expected: 7 tests pass; "No changes detected".

- [ ] **Step 5: Commit.**

```bash
git add backend/drop_core/models.py backend/drop_core/admin.py backend/drop_core/migrations/ backend/drop_core/tests_members.py
TZ=UTC git commit -m "Keys, upload URLs, files and safety events record their member, and old rows go to the administrator"
```

---

### Task 3: A key or OAuth token acts as its person

**Files:**
- Modify: `backend/drop_core/models.py` (`APIKey.create_key` line 74, `APIKey.verify` line 87, two new properties)
- Modify: `backend/drop_core/oauth.py` (`OAuthCredentialAdapter` line 128, `resolve_oauth_credential` line 174)
- Modify: `backend/drop_core/key_caps.py` (`create_key_within_cap` line 45)
- Modify: `backend/drop_core/account.py` (`create_key` line 152)
- Modify: `backend/drop_core/device.py` (`_redeem` line 413)
- Test: `backend/drop_core/tests_members.py`

**Interfaces:**
- Consumes: `Membership`, `administrator_of`, `current_member`, `APIKey.member` (Tasks 1 and 2).
- Produces:
  - `APIKey.create_key(cls, workspace, name, member=None) -> (APIKey, str)`: stores `member or administrator_of(workspace)`. Every existing positional caller keeps working.
  - `APIKey.acting_member` (property) `-> Membership`: `self.member` when set, else `administrator_of(self.workspace)`.
  - `APIKey.person` (property) `-> User`: `self.acting_member.user`.
  - `APIKey.verify(cls, raw)`: the lookup becomes

    ```python
    cls.objects.select_related("member__user").get(
        Q(member__isnull=True, workspace__owner__is_active=True)
        | Q(member__user__is_active=True, member__removed_at__isnull=True),
        key_hash=digest, is_active=True,
    )
    ```

    Update its docstring: a key verifies while its person is active and still a member; a key with no member follows the owner.
  - `OAuthCredentialAdapter.__init__(self, access_token, workspace, member)`, with properties `acting_member -> Membership` (the `member` passed in) and `person -> User` (`member.user`).
  - `resolve_oauth_credential(raw_token)`: after `workspace = get_or_create_workspace(token.user)`, `member = current_member(workspace, token.user)`; return None when `member` is None.
  - `create_key_within_cap(workspace, name, device_issued=False, replacing=None, member=None)`: passes `member` to `APIKey.create_key`; the lock, count and replacement rules are unchanged.
  - `account.create_key(...)`: passes `member=current_member(workspace, user)`.
  - `device._redeem(device_code)`: computes `member = current_member(device_code.workspace, approver)` for an active approver and treats `member is None` exactly as an inactive approver (the same refusal sentence and DENIED status), then passes `member=member` to `create_key_within_cap`.

Both credential kinds now share the duck type `.workspace`, `.acting_member`, `.person`; Tasks 4 and 5 use only those.

- [ ] **Step 1: Write the failing tests** in class `CredentialPersonTests(TestCase)`, using `second_member` from Task 1 and the OAuth helpers of `tests_fix_auth.py` (`_make_app`, `_issue_token`; copy them, as each test module keeps its own helpers):

```python
def test_a_new_key_belongs_to_the_person_who_made_it(self):
    workspace = get_or_create_workspace(make_user("ada"))
    grace = second_member(workspace, "grace")
    key, _ = APIKey.create_key(workspace, "agent", member=grace)
    self.assertEqual((key.member, key.person), (grace, grace.user))
    self.assertEqual(APIKey.create_key(workspace, "other")[0].member, administrator_of(workspace))

def test_a_key_with_no_member_verifies_for_an_active_owner(self):
    workspace = get_or_create_workspace(make_user("ada"))
    key, raw = APIKey.create_key(workspace, "agent")
    APIKey.objects.filter(pk=key.pk).update(member=None)
    self.assertEqual(APIKey.verify(raw).pk, key.pk)
    self.assertEqual(APIKey.verify(raw).person, workspace.owner)
    User.objects.filter(pk=workspace.owner.pk).update(is_active=False)
    self.assertIsNone(APIKey.verify(raw))

def test_a_suspended_owner_loses_their_own_keys(self):
    workspace = get_or_create_workspace(make_user("ada"))
    _, raw = APIKey.create_key(workspace, "agent")
    User.objects.filter(pk=workspace.owner.pk).update(is_active=False)
    self.assertIsNone(APIKey.verify(raw))

def test_a_members_key_follows_that_member_not_the_owner(self):
    workspace = get_or_create_workspace(make_user("ada"))
    grace = second_member(workspace, "grace")
    _, raw = APIKey.create_key(workspace, "agent", member=grace)
    User.objects.filter(pk=workspace.owner.pk).update(is_active=False)
    self.assertIsNotNone(APIKey.verify(raw))
    User.objects.filter(pk=grace.user.pk).update(is_active=False)
    self.assertIsNone(APIKey.verify(raw))

def test_a_removed_members_key_stops(self):
    workspace = get_or_create_workspace(make_user("ada"))
    grace = second_member(workspace, "grace")
    _, raw = APIKey.create_key(workspace, "agent", member=grace)
    Membership.objects.filter(pk=grace.pk).update(removed_at=timezone.now())
    self.assertIsNone(APIKey.verify(raw))

def test_an_oauth_token_acts_as_the_person_who_approved_it(self):
    ada = make_user("ada")
    raw, _, _ = _issue_token(ada, _make_app())
    credential = resolve_oauth_credential(raw)
    self.assertEqual((credential.acting_member, credential.person), (administrator_of(get_or_create_workspace(ada)), ada))

def test_an_oauth_token_of_a_removed_member_is_refused(self):
    ada = make_user("ada")
    raw, _, _ = _issue_token(ada, _make_app())
    Membership.objects.filter(user=ada).update(removed_at=timezone.now())
    self.assertIsNone(resolve_oauth_credential(raw))

def test_a_device_login_key_belongs_to_the_approver(self):
    # Drive the flow as tests_fix_device._DeviceTestBase does: start, approve as self.user, redeem.
    ...  # assert the minted key's member == current_member(workspace, self.user)

def test_a_device_login_approved_by_a_removed_member_is_refused(self):
    # Approve, then set removed_at on the approver's membership, then redeem.
    ...  # assert the same refusal body as an inactive approver and status DENIED, and no new key
```

The removed-administrator case cannot happen in the product (an administrator is never removed), but `current_member` returns None for it, so the two refusal tests use `.update(removed_at=...)` directly.

- [ ] **Step 2: Run them to see them fail.** Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_members.CredentialPersonTests` Expected: failures on `member=` and `acting_member`.

- [ ] **Step 3: Make the changes listed in Interfaces.**

- [ ] **Step 4: Run the tests and the credential suites.** Run the Step 2 command, then `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core drop_mcp dashboard` Expected: all pass, including `tests_fix_auth`, `tests_oauth*`, `tests_fix_device` and `tests_device_pages` unchanged.

- [ ] **Step 5: Commit.**

```bash
git add backend/drop_core/models.py backend/drop_core/oauth.py backend/drop_core/key_caps.py backend/drop_core/account.py backend/drop_core/device.py backend/drop_core/tests_members.py
TZ=UTC git commit -m "A key or OAuth token is checked against the person behind it, not the account's owner"
```

---

### Task 4: Every file and upload URL records its uploader

**Files:**
- Modify: `backend/drop_core/services.py` (`mint_upload_url` line 259, `upload_artifact` line 313, `_record_safety_block` line 510)
- Modify: `backend/drop_core/views.py` (new `_request_member` beside `_request_workspace` line 120; `_minting_credential_active` line 274; `ArtifactListCreateView.post` call at line 393; `PendingUploadView.put` lines 458-502)
- Modify: `backend/drop_mcp/views.py` (`_tool_upload_artifact` call at line 583, `_tool_create_upload_url` call at line 656)
- Test: `backend/drop_core/tests_members.py`

**Interfaces:**
- Consumes: `acting_member`, `person`, `current_member`, `administrator_of`, the `member` columns (Tasks 1 to 3).
- Produces:
  - `upload_artifact(workspace, api_key, filename, data, mime_type, ttl_days=None, uploader_ip="", user_agent="", on_secret="flag", member=None)`: stores `member or administrator_of(workspace)` on the new `Artifact` (inside the existing `Artifact(...)` constructor; nothing else in the transaction changes) and passes it to `_record_safety_block`.
  - `mint_upload_url(workspace, api_key, filename, ttl_days=None, on_secret="flag", oauth_application_id=None, member=None)`: stores `member or administrator_of(workspace)` on the `PendingUpload`.
  - `_record_safety_block(*, workspace, api_key, digest, verdict, uploader_ip, user_agent, enforce, member=None)`: stores `member` on the `SafetyEvent`. It still suspends only the key.
  - `_request_member(request) -> Membership`: `request.auth.acting_member` for a credential (`_has_credential(request.auth)`), else `current_member(_request_workspace(request), request.user)`.
  - Callers pass `member=`: the REST POST passes `_request_member(request)`; the PUT passes `pending.member` (NULL falls back inside `upload_artifact`); the MCP upload and the MCP upload URL pass `key.acting_member`.
  - `_minting_credential_active(pending) -> bool`, with `PendingUploadView.put` loading `select_related("workspace__owner", "api_key", "member__user")`:

    ```python
    member = pending.member
    if member is not None and member.removed_at is not None:
        return False
    person = member.user if member is not None else pending.workspace.owner
    if not person.is_active:
        return False
    # then exactly today's three branches, with person in place of owner:
    # an API-key URL follows pending.api_key.is_active; an OAuth URL needs is_email_verified(person)
    # and a live access or refresh token for {"user_id": person.pk, "application_id": ...}; a legacy row is True.
    ```

- [ ] **Step 1: Write the failing tests** in class `UploaderTests(TestCase)`. Use `tests.py:_use_local_dir` for storage and the REST and MCP request shapes of `drop_core/tests.py` (`_upload`) and `drop_mcp/tests.py` (`_rpc`).

```python
def test_a_rest_upload_records_the_keys_member(self):
    workspace = get_or_create_workspace(make_user("ada"))
    grace = second_member(workspace, "grace")
    _, raw = APIKey.create_key(workspace, "agent", member=grace)
    response = self.client.post("/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", b"hi")}, HTTP_AUTHORIZATION=f"Bearer {raw}")
    self.assertEqual(response.status_code, 201)
    artifact = Artifact.objects.get()
    self.assertEqual((artifact.member, artifact.visibility), (grace, VISIBILITY_ONLY_ME))

def test_a_dashboard_upload_records_the_signed_in_person(self): ...      # session POST as the owner: member == administrator_of(workspace)
def test_an_oauth_upload_records_the_token_person(self): ...             # OAuth bearer POST: member == current_member(workspace, token user)
def test_an_mcp_upload_records_the_keys_member(self): ...                # tools/call upload_artifact with grace's key: member == grace
def test_an_upload_url_carries_its_minter_to_the_file(self): ...         # MCP create_upload_url with grace's key, then PUT: PendingUpload.member and Artifact.member == grace
def test_a_blocked_upload_records_its_member(self): ...                  # VEILFILE_SAFETY_MODE=log with a BlockedHash match: SafetyEvent.member == grace

def test_an_upload_url_with_no_member_tests_the_owner(self):
    workspace = get_or_create_workspace(make_user("ada"))
    key, _ = APIKey.create_key(workspace, "agent")
    pending = mint_upload_url(workspace, key, "a.txt")
    PendingUpload.objects.filter(pk=pending.pk).update(member=None)
    self.assertEqual(self.client.put(f"/api/v1/uploads/{pending.token}", b"hi", content_type="application/octet-stream").status_code, 201)
    # and with the owner suspended, a second such URL answers 404

def test_an_upload_url_follows_its_members_grant_not_the_owners(self):
    # An OAuth URL minted for grace (member=grace, oauth_application_id=app.pk, api_key=None):
    # with a live token for grace and none for ada it is active; with a token only for ada it is not.
    ...

def test_a_removed_members_upload_url_stops(self): ...                   # removed_at set on grace: PUT answers the same 404 as an unknown token
```

Take the exact PUT path and success status from `tests_fix_web.py` (the PUT tests near its lines 200-370) rather than the guesses above.

- [ ] **Step 2: Run them to see them fail.** Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_members.UploaderTests` Expected: failures on `member`.

- [ ] **Step 3: Make the changes listed in Interfaces.**

- [ ] **Step 4: Run the tests and the upload suites.** Run the Step 2 command, then `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core drop_mcp dashboard pages` Expected: all pass, including `tests_fix_upload`, `tests_fix_storage`, `tests_fix_web`, `tests_sec_authz`, `tests_json_answers` and `pages.tests_artifact_miss` (whose query counts stay as they are, because `serve_artifact` does not change).

- [ ] **Step 5: Commit.**

```bash
git add backend/drop_core/services.py backend/drop_core/views.py backend/drop_mcp/views.py backend/drop_core/tests_members.py
TZ=UTC git commit -m "Every upload and upload URL records the member behind it"
```

---

### Task 5: The last owner reads, and a guard against new ones

**Files:**
- Modify: `backend/drop_core/views.py` (`OAuthGrantRevokeView.post` line 645, `UsageView.get` line 693)
- Modify: `backend/dashboard/file_rows.py` (`held_ids` line 53)
- Test: `backend/drop_core/tests_members.py`

**Interfaces:**
- Consumes: `person` on both credential kinds (Task 3).
- Produces:
  - `OAuthGrantRevokeView.post`: an OAuth caller revokes `revoke_user_oauth_grants(request.auth.person, application=application)`.
  - `UsageView.get`: a credential caller's `account_user` is `request.auth.person`, so the response reports the key's or token's person (for a team of one, the same email as today).
  - `held_ids(user, body)`: `Artifact.objects.filter(workspace=get_or_create_workspace(user), id__in=ids, safety_status="held")`.
  - `OwnerReadsTests` with `ALLOWED = {"drop_core/models.py": 1, "drop_core/views.py": 2}`: the occurrences of `workspace__owner` or `\bworkspace\.owner\b` in non-test, non-migration Python under `backend/` are exactly the two NULL-member fallbacks (`APIKey.verify`; `_minting_credential_active` and its `select_related`).

- [ ] **Step 1: Write the failing tests** in classes `PersonReadsTests(TestCase)` and `OwnerReadsTests(SimpleTestCase)`:

```python
def test_usage_reports_the_keys_person(self):
    workspace = get_or_create_workspace(make_user("ada", email="ada@example.com"))
    grace = second_member(workspace, "grace", email="grace@example.com")
    _, raw = APIKey.create_key(workspace, "agent", member=grace)
    body = self.client.get("/api/v1/usage", HTTP_AUTHORIZATION=f"Bearer {raw}").json()
    self.assertEqual(body["email"], "grace@example.com")

def test_an_oauth_logout_revokes_only_the_callers_grants(self): ...
    # grace and ada each hold a token for the same app; grace's OAuth caller posts the revoke:
    # grace's tokens are revoked and ada's are not.

def test_only_the_fallbacks_read_the_workspace_owner(self):
    pattern = re.compile(r"workspace__owner|\bworkspace\.owner\b")
    found = {}
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if "/migrations/" in rel or path.name.startswith("tests"):
            continue
        count = len(pattern.findall(path.read_text()))
        if count:
            found[rel] = count
    self.assertEqual(found, ALLOWED, "A credential or file check reads workspace.owner. Use .acting_member or .person "
                     "(AGENTS.md, People inside an account), or, for a new NULL-member fallback, add it to ALLOWED.")
```

Take the usage path and the OAuth revoke path from `tests_json_answers.py` and `tests_fix_auth.py`.

- [ ] **Step 2: Run them to see them fail.** Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_members.PersonReadsTests drop_core.tests_members.OwnerReadsTests` Expected: the usage test reports the owner's email; the guard lists the three reads this task removes.

- [ ] **Step 3: Make the changes listed in Interfaces.** If the guard's count for a file differs from `ALLOWED` only because a fallback line was split, keep the code and correct `ALLOWED`; any other extra occurrence is a read to move to `.person`.

- [ ] **Step 4: Run the tests and the whole backend.** Run the Step 2 command, then `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test` Expected: all pass. `dashboard/tests_files.py`'s `test_held_is_looked_for_among_the_owners_own_files_only` passes unchanged.

- [ ] **Step 5: Commit.**

```bash
git add backend/drop_core/views.py backend/dashboard/file_rows.py backend/drop_core/tests_members.py
TZ=UTC git commit -m "Usage, OAuth logout and held files read the person, and a test refuses new owner reads"
```

---

### Task 6: AGENTS.md, the full checks, and the pull request

**Files:**
- Modify: `AGENTS.md` (a new section `## People inside an account` between "Invariants: do not break these" and "Pricing changes", line 622; the "Repo map" row for `drop_core`, if it lists models)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the AGENTS.md section,** in the voice rules (complete sentences, no em dashes). It says, in this order: a workspace is an account, and `Membership` joins a person to it as administrator or member; `Workspace.owner` stays the administrator and the payer; every workspace gets its administrator from the `post_save` receiver and from the backfill; keys, upload URLs, files and safety events record `member`, and a NULL `member` means the administrator, because the previous release writes rows without it during a deploy; credentials expose `.acting_member` and `.person`, and no check reads `workspace.owner` except the two NULL-member fallbacks that `OwnerReadsTests` allows; membership rows are never deleted by the application (removal sets `removed_at`), which is why keys and files point at them with `RESTRICT`; `Artifact.visibility` is stored ("only_me" for every file so far) and nothing reads it yet. Name the spec and this plan.

- [ ] **Step 2: Run every local check.**

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run
cd npm && node --test
cd cli && python -m unittest discover -s tests -t .
```

Expected: everything passes; "No changes detected".

- [ ] **Step 3: Run the dashboard and forms site checks locally,** because the fixture scripts build keys and files through the changed models. Follow AGENTS.md "Site checks" with the server on port 8777 (the owner uses 8000): migrate, `make_staff_session.py`, `make_form_fixtures.py`, `make_dashboard_fixtures.py`, `runserver 127.0.0.1:8777 --noreload`, then `site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts --part dashboard` and the same with `--part forms`. Expected: both parts report no problems. Stop the server afterwards.

- [ ] **Step 4: Commit AGENTS.md.**

```bash
git add AGENTS.md
TZ=UTC git commit -m "AGENTS.md explains people inside an account"
```

- [ ] **Step 5: Stop for the owner.** Report the branch, the commits and the check results, and that no legal text became inaccurate (nothing visible changed). Push the branch and open the pull request as Muse's account only after the owner says so, verifying the login in the same command; merge, with a merge commit, only after all 15 checks pass and the owner says so again.

---

## The six pull requests

Each later pull request gets its own plan, written after the one before it merges, so that it starts from the code as it is.

| PR | Scope | Visible? |
|---|---|---|
| A (this plan) | Membership underneath every account; every key, upload URL, file and safety event records its person; credentials check the person. | No |
| B | Seat counts in `plans.py`; per-member allowances for uploads, storage and keys, enforced in the same transaction as the team's limits; the two error codes; a second run of the backfill for rows written during PR A's deploy. | No |
| C | A switch, off in production, that hides everything below. Invitations by email (a pending one holds a seat for 7 days; rate limits whose numbers the plan states); joining from an individual Free account (its old keys stop; a paid one is in D); the Members page; allowances set by the administrator; the visibility setting and the Files page's "only me" and "team"; members see their own and the team's files; removal with its choice; leaving; the administrator transfer, accepted by the new administrator; a removed member's account as its own Free account, still able to see and revoke its remaining files; email change and account deletion for members (a member's files point at their membership with `RESTRICT`, so deleting a member's account revokes their files first); the administrator told of a member's held file without its name; a person's lookup moves from `get_or_create_workspace` to their current membership in one place. | Behind the switch |
| D | Billing: a switch to fewer seats than members is refused; members are barred when the team ends up on Free (they can still sign in only to see and revoke their own files) and keep access while Stripe retries; access returns with payment; billing email and portal follow the administrator after a transfer; joining from a paid individual plan ends it with the unused part credited. | Behind the switch |
| E | Agents choose visibility on the REST upload, both MCP upload tools and both CLIs (server first, per invariant 4); `docs/API.md`, `docs/llms.txt` and `backend/docs/pages/`. | Behind the switch |
| F | The owner's Terms and Privacy Policy wording, the Plans page's "up to 5 people", "up to 10" and "unlimited", and turning the switch on. | Yes |
