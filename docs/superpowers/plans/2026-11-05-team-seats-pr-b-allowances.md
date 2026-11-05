# Team seats, PR B: Closing NULL Members, Seat Counts and Per-Member Allowances Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every key, upload URL and file name its person for good, record seat counts, and enforce per-member allowances for uploads, storage and keys underneath the team's limits, with no change anyone can see.

**Architecture:** A second backfill fills the NULL-member rows left from PR A's deploy and stops the deploy if any remain, so the code can stop treating NULL as the administrator. Three nullable columns on `Membership` hold a member's allowances, and NULL there means no allowance. A new `MemberUsageCounter` counts each member's uploads per month, because swept and revoked files make that count underivable. `upload_artifact` and `create_key_within_cap` test the team's limits first and then the member's, in the same transaction and lock order they use today.

**Tech Stack:** Django 6.1.1, Django REST framework, SQLite in tests, Postgres 16 in the rehearsal, Postgres in production.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md` (decisions 1, 5, 6 and 7; calls 2 and 9; section 9). This is the second of the pull requests in the table at the end of `docs/superpowers/plans/2026-11-05-team-seats-pr-a-members.md`. AGENTS.md's "People inside an account" section describes what PR A built, and the description of pull request #28 lists what it carried forward.

## Decisions this plan takes

The owner reviews these with the plan. Each says what it costs if wrong.

1. **Every upload is counted for its member, whether or not an allowance is set.** A count kept only under an allowance would restart at zero when an administrator sets one mid-month, and decision 15's Members page needs every member's usage. This costs one extra read and one extra update on every upload, for every customer, before anyone can use the result. A reviewer suggested moving the counter and the enforcement to PR C for that reason. This plan keeps them here, so that the enforcement gets its own rehearsal and PR C stays reviewable.
2. **The team's limit is tested first.** When both limits are reached, the answer is the team's existing code and sentence, so a one-person account can never see a new one. Precedence is guaranteed by the early checks and by the order of the reservations. In a race, a member's monthly code can appear when the team's storage has also just filled, and the pool stays the hard ceiling (decisions 5 and 6).
3. **Member counts start at this deploy,** and existing `UsageCounter` figures are not copied. Only a workspace's administrator can have earlier uploads, because every other member joins after PR C. So the undercount can only let an administrator exceed an allowance they set on themselves, in the launch month. A rollback window also undercounts, because the release on main does not count.
4. **Three new codes, one more than the pull-request table says, with statuses chosen for published clients.** `member_monthly_allowance_reached` is 429 with no `Retry-After`, like `monthly_cap_reached`. `member_storage_allowance_reached` and `member_key_allowance_reached` are 400. Both CLIs print the server's sentence for 429 and for 400, but any 402 prints their fixed "or upgrade" sentence, which is wrong here because upgrading is not the member's remedy, and call 2 exists so that a person knows whom to ask. No CLI release is needed.
5. **Only `docs/API.md` names the codes now, and only the two in `ERROR_STATUS`,** because `test_every_upload_error_code_is_documented` requires them. It is public in the repository and linked from the site. The site's docs, `docs/llms.txt`, the plugin and the Plans page wait for PR E and PR F.
6. **A member with a key allowance may rotate a key only by replacing one of their own.** Replacing a teammate's key counts as a new key against their allowance.
7. **The NULL branches close in this pull request, and the deploy proves it is safe.** The new backfill raises if any key, file or upload URL still names no member. `backend/entrypoint.sh` has `set -e`, so a failed migration stops the new release booting and the release on main keeps serving. Nobody has to read a log line. The release on main writes no NULL member from application code, so nothing new can appear during the overlap except through its admin forms, which is staff only. This replaces the separate follow-up that PR A's notes suggested. It must land before PR C's transfer, because a NULL key follows the account's owner and would flip to a new administrator. A stray NULL row after this deploy fails closed: the key is refused.
8. **Zero is a real allowance,** meaning no new uploads. An allowance above the plan's limit is stored as set, and the team's limit caps it at use (call 9). A request in flight honours the allowance it loaded when it started, which is accepted and documented: the window is one request, and the team's limit still holds.

## Global Constraints

- **No visible change.** No existing JSON answer, status code, error code, MCP result, dashboard page or email changes for any rows the backfill can fill. `drop_core/tests_json_answers.py` and `drop_billing/tests_json_answers.py` pass unchanged. `seats` and the allowance fields reach no response, plan card, `status()` caps dict or `_over_plan_limits`.
- **Only staff can set an allowance,** in the Django admin. No view, API or MCP tool reads or writes one until PR C.
- **Migrations are additive and safe on a live Postgres table.** New columns are nullable, and new tables are additive. A data migration is non-atomic, batched and never shares a file with a schema change. `migrate` runs at boot while the release on main keeps serving.
- **Lock order is fixed:** resolve the member before any transaction; then the team counter; then the member counter; then the workspace row. The give-back takes the team counter, then the member counter. The member counter's insert takes a key-share lock on the `Membership` row only, and nothing in this pull request locks a `Membership` row for update. Never create a row with a foreign key to `Workspace` under the workspace lock, and never call `administrator_of` inside a transaction in `services.py`, `models.py` or `key_caps.py` (`ResolveMemberBeforeLockTests` pins this; a Postgres rehearsal found 848 deadlocks from the opposite).
- **Unchanged:** `serve_artifact`, the per-credential throttles, the funnel, billing's plan logic, and `mint_upload_url`, which checks no cap today and will check no allowance. The writers' defaults stay: `APIKey.create_key`, `create_key_within_cap`, `upload_artifact` and `mint_upload_url` still fill in the administrator when a caller passes no member.
- **Never name a plan and then a figure on one line** (`<Plan> ... N keys|GB|uploads|days`) in a file `drop_core/tests_fix_docs.py` scans, because `_cap_facts` reads that as a plan's limit. AGENTS.md's "Pricing changes" lists those files.
- AGENTS.md invariants hold, notably 4 (published clients keep working), 8 (no edits to `backend/legal/documents/*`) and 9 (no package versions). No new runtime dependencies.
- Words follow AGENTS.md's Voice section: complete sentences, semicolons only between independent clauses, no em dashes, no exclamation marks.
- **This Mac panicked from memory exhaustion on 2026-11-05 and is shared.** Run heavy checks one at a time, and check `memory_pressure | tail -1` first. Never signal a process you did not start.
- Commits: `Veilfile <support@veilfile.com>`, `TZ=UTC git commit`, plain-sentence subjects, no attribution lines of any kind, no personal names. Branch: `seats/allowances`, from the latest `origin/main`. Pushing, opening the pull request and merging each need the owner's go-ahead, through Muse's account.

## Review Focus

1. **A one-person account is untouched:** the same answers, codes and limits, at the cap and under it. Test: `test_a_one_person_account_gets_the_teams_codes_at_every_limit` (Task 4), plus the unchanged JSON pins.
2. **A row the backfill cannot fill** stops the deploy with a message saying what to do, instead of letting a key start failing. Test: `test_it_stops_the_deploy_when_a_row_cannot_be_filled` (Task 1).
3. **Both limits reached at once:** the team's code wins, and nothing is half-counted. Tests: `test_the_team_limit_wins_when_both_are_reached` and `test_a_refusal_after_the_reservation_rolls_back_both_counters` (Task 4).
4. **A storage failure after the reservation** gives back both counters and never goes negative. Test: `test_a_storage_failure_gives_back_both_counters` (Task 4).
5. **An allowance set below what a member already holds:** new use is refused, nothing already made stops working, and a key can still rotate. Tests: `test_an_allowance_below_current_use_refuses_new_uploads_only` (Task 4) and `test_a_member_over_their_allowance_can_still_replace_their_own_key` (Task 5).

---

### Task 1: Every key, file and upload URL names its person

**Files:**
- Create: `backend/drop_core/migrations/0014_backfill_members_again.py`
- Modify: `backend/drop_core/models.py` (`APIKey.acting_member`, `APIKey.verify`)
- Modify: `backend/drop_core/views.py` (`_minting_credential_active`, near line 296; the PUT view's `select_related`, near line 477)
- Modify: `backend/drop_core/admin.py` (`APIKeyAdmin`, `ArtifactAdmin`)
- Modify: `backend/drop_core/tests_members.py`, `AGENTS.md` ("People inside an account")
- Create: `backend/drop_core/tests_allowances.py`

**Interfaces:**
- Produces: `backfill_members_again(apps, schema_editor)`, a frozen copy of `0013`'s function with its own `BATCH_SIZE = 1000`, `atomic = False` and `migrations.RunPython(backfill_members_again, migrations.RunPython.noop)`. It imports nothing from live models or from `0013`. It differs from `0013` in two ways:
  - The update filter adds `member__isnull=True`, so a row changed between the select and the update is never overwritten.
  - After the batches, it counts rows with a NULL member in `APIKey`, `Artifact` and `PendingUpload`, and raises `RuntimeError` if any remain: `f"The member backfill stopped this deploy. Rows that still name no member: keys {k}, files {f}, upload URLs {u}. Each row's workspace needs a current administrator. Fix that and deploy again."` `SafetyEvent` is filled too but not counted, because its column is `SET_NULL` by design.
  A comment says this migration runs once, before any code can delete a membership, and that the function must not be reused after that, because a NULL `SafetyEvent.member` will then mean a deleted membership.
- Produces, in `models.py`:
  - `APIKey.acting_member` returns `self.member`.
  - `APIKey.verify` looks up `cls.objects.select_related("member__user").get(key_hash=digest, is_active=True, member__user__is_active=True, member__removed_at__isnull=True)`. Its docstring says that a key verifies while its person is active and still a member. `Q` stays imported only if something else uses it.
- Produces, in `views.py`: `_minting_credential_active` starts `member = pending.member`, `if member is None or member.removed_at is not None: return False`, `person = member.user`, then today's checks. The PUT view's `select_related` is `("api_key", "member__user", "workspace")`.
- Produces, in `admin.py`: `get_form` on `APIKeyAdmin` and `ArtifactAdmin` sets `form.base_fields["member"].required = True`, so staff cannot save a new NULL member. The model field stays `blank=True`.

- [ ] **Step 1: Write the failing tests.** In `backend/drop_core/tests_allowances.py`, class `SecondBackfillTests(TestCase)`, load the function with `importlib.import_module("drop_core.migrations.0014_backfill_members_again")`, isolate storage as `tests_members.BackfillTests` does, and import `legacy_rows` and `make_user` from `tests_members`.

```python
def test_rows_with_no_member_go_to_the_administrator(self):
    workspace = get_or_create_workspace(make_user("ada"))
    legacy_rows(workspace)
    backfill(apps, None)
    admin = administrator_of(workspace)
    for model in (APIKey, Artifact, PendingUpload, SafetyEvent):
        self.assertEqual(set(model.objects.values_list("member", flat=True)), {admin.pk}, model.__name__)

def test_it_can_run_twice(self):
    legacy_rows(get_or_create_workspace(make_user("ada")))
    backfill(apps, None); backfill(apps, None)
    self.assertEqual(Membership.objects.count(), 1)

def test_it_does_not_overwrite_a_row_that_names_a_member(self):
    workspace = get_or_create_workspace(make_user("ada"))
    grace = Membership.objects.create(workspace=workspace, user=make_user("grace"))
    key, _ = APIKey.create_key(workspace, "agent", member=grace)
    backfill(apps, None)
    key.refresh_from_db()
    self.assertEqual(key.member, grace)

def test_it_stops_the_deploy_when_a_row_cannot_be_filled(self):
    workspace = get_or_create_workspace(make_user("ada"))
    legacy_rows(workspace)
    Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
    with self.assertRaisesMessage(RuntimeError, "keys 1, files 1, upload URLs 1"):
        backfill(apps, None)

def test_a_safety_event_with_no_member_does_not_stop_the_deploy(self):
    workspace = get_or_create_workspace(make_user("ada"))
    legacy_rows(workspace)
    APIKey.objects.all().delete(); Artifact.objects.all().delete(); PendingUpload.objects.all().delete()
    Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
    backfill(apps, None)
    self.assertTrue(SafetyEvent.objects.filter(member__isnull=True).exists())

def test_it_fills_more_rows_than_a_batch(self):
    # BATCH_SIZE patched to 2 on this module, with 5 keys set to NULL: no NULL key remains.
    ...

def test_the_migration_is_not_atomic(self):
    self.assertFalse(module.Migration.atomic)

def test_the_admin_requires_a_member_on_keys_and_files(self):
    # For APIKeyAdmin and ArtifactAdmin, with a superuser's request: form.base_fields["member"].required is True.
    ...
```

In `backend/drop_core/tests_members.py`, rewrite `test_a_key_with_no_member_verifies_for_an_active_owner` as `test_a_key_with_no_member_is_refused` (`APIKey.verify(raw)` is None with an active owner), and `test_an_upload_url_with_no_member_tests_the_owner` as `test_an_upload_url_with_no_member_is_refused` (the PUT answers the same 404 as an unknown token). In `OwnerReadsTests`, set `ALLOWED = {}`, and add:

```python
def test_no_code_treats_a_missing_member_specially(self):
    # Search every non-test, non-migration .py file under backend/ for "member__isnull": none.
    ...
```

- [ ] **Step 2: Run them to see them fail.** Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_allowances drop_core.tests_members` Expected: ImportError, then the rewritten tests failing.

- [ ] **Step 3: Create the migration with `makemigrations drop_core --empty --name backfill_members_again`, fill it in, and make the code changes.** Rewrite AGENTS.md's sentences in "People inside an account" that say a NULL member means the administrator permanently: after this deploy no key, file or upload URL names no member, and a stray one is refused; `SafetyEvent.member` can be NULL because it is `SET_NULL`; and the second backfill runs once and stops the deploy if it cannot finish.

- [ ] **Step 4: Run the tests, the credential suites and the migration check.** Run: `... manage.py test drop_core drop_mcp dashboard`, then `... manage.py makemigrations --check --dry-run`. Expected: all pass. A test that relied on a NULL member elsewhere is a finding to fix by giving its row a member, not by restoring the branch.

- [ ] **Step 5: Commit.** `TZ=UTC git commit -m "Every key, file and upload URL names its person, and the deploy stops if one cannot"`

---

### Task 2: Seat counts in the plans

**Files:**
- Modify: `backend/drop_core/plans.py` (`PLANS`, the module docstring)
- Modify: `backend/drop_core/tests_team_plus.py` (`TEAM_PLUS` and its equality tests), `backend/drop_core/tests_annual.py` (`test_the_monthly_plans_are_unchanged`), and any other literal plan dict that the search in Step 3 finds
- Modify: `AGENTS.md` ("Pricing changes")

**Interfaces:**
- Produces: `PLANS[slug]["seats"]`: `free` 1, `team` 5, `team_plus` 10, `scale` None. No code reads it yet; PR C and PR D do.

- [ ] **Step 1: Write the failing test** in `backend/drop_core/tests_team_plus.py`, in the existing class:

```python
def test_seats_match_the_key_counts(self):
    self.assertEqual({slug: caps["seats"] for slug, caps in PLANS.items()},
                     {"free": 1, "team": 5, "team_plus": 10, "scale": None})
    for caps in PLANS.values():
        self.assertEqual(caps["seats"], caps["keys"])
```

- [ ] **Step 2: Run it to see it fail.** Run: `... manage.py test drop_core.tests_team_plus` Expected: KeyError on `seats`.

- [ ] **Step 3: Add the field.** Update `plans.py`'s docstring to say `keys: None` and `seats: None` mean unlimited. Search the tests for `"storage_gb":` and add `"seats"` to every literal copy of a real plan. Add one sentence to AGENTS.md's "Pricing changes": seats live in `PLANS` as `seats`, None means unlimited, and they are not advertised until team seats launch. Do not add seats to `plan_cards()`, `status()` or `usage()`.

- [ ] **Step 4: Run the tests.** Run: `... manage.py test drop_core.tests_team_plus drop_core.tests_annual drop_core.tests_fix_docs pages.tests_chapters drop_billing.tests_json_answers drop_core.tests_json_answers` Expected: all pass; no JSON pin changed.

- [ ] **Step 5: Commit.** `TZ=UTC git commit -m "The plans record how many seats each one includes"`

---

### Task 3: Allowance columns and the member counter

**Files:**
- Modify: `backend/drop_core/models.py` (`Membership`; a new model after `UsageCounter`)
- Modify: `backend/drop_core/services.py` (a helper beside `workspace_storage_bytes`)
- Modify: `backend/drop_core/admin.py`
- Create: `backend/drop_core/migrations/0015_member_allowances.py` (generated)
- Test: `backend/drop_core/tests_allowances.py`

**Interfaces:**
- Produces, in `models.py`:
  - On `Membership`: `upload_allowance = PositiveIntegerField(null=True, blank=True)`, `storage_allowance_bytes = PositiveBigIntegerField(null=True, blank=True)`, `key_allowance = PositiveIntegerField(null=True, blank=True)`. NULL means no allowance.
  - `class MemberUsageCounter(models.Model)`: `member = ForeignKey(Membership, CASCADE, related_name="usage_counters")`, `year_month = CharField(max_length=7)`, `uploads = IntegerField(default=0)`, `unique_together = [("member", "year_month")]`. It has no foreign key to `Workspace`, so inserting one never touches the workspace row.
- Produces, in `services.py`: `member_storage_bytes(member) -> int`, which is `Artifact.objects.filter(member=member, expires_at__gt=timezone.now())` summed the way `workspace_storage_bytes` does.
- Produces, in `admin.py`: the three columns in `MembershipAdmin.list_display`; `MemberUsageCounter` registered with `list_display = ("member", "year_month", "uploads")` and `raw_id_fields = ("member",)`.
- Produces these helpers in `tests_allowances.py`, used by Tasks 3 to 5:

```python
def second_member(workspace, name, **allowances):
    return Membership.objects.create(workspace=workspace, user=make_user(name), role=Membership.MEMBER, **allowances)

def set_allowance(member, **fields):
    # On the same instance, because upload_artifact and create_key_within_cap read the instance they are given.
    for name, value in fields.items():
        setattr(member, name, value)
    member.save(update_fields=list(fields))

def live_file(workspace, member, size, **fields):
    fields.setdefault("expires_at", timezone.now() + timedelta(days=1))
    return Artifact.objects.create(workspace=workspace, member=member, filename="a.txt", size_bytes=size, **fields)

def team_count(workspace):
    row = UsageCounter.objects.filter(workspace=workspace, year_month=_current_month()).first()
    return row.uploads if row else 0

def member_count(member):
    row = MemberUsageCounter.objects.filter(member=member, year_month=_current_month()).first()
    return row.uploads if row else 0
```

- [ ] **Step 1: Write the failing tests** in class `AllowanceModelTests(TestCase)`:

```python
def test_a_new_membership_has_no_allowances(self):
    admin = administrator_of(get_or_create_workspace(make_user("ada")))
    self.assertEqual((admin.upload_allowance, admin.storage_allowance_bytes, admin.key_allowance), (None, None, None))

def test_a_member_has_one_counter_a_month(self):
    admin = administrator_of(get_or_create_workspace(make_user("ada")))
    MemberUsageCounter.objects.create(member=admin, year_month="2026-11")
    with self.assertRaises(IntegrityError), transaction.atomic():
        MemberUsageCounter.objects.create(member=admin, year_month="2026-11")

def test_a_members_storage_counts_only_their_live_files(self):
    workspace = get_or_create_workspace(make_user("ada"))
    grace = second_member(workspace, "grace")
    live_file(workspace, grace, 100)
    live_file(workspace, administrator_of(workspace), 7)
    live_file(workspace, grace, 50, expires_at=timezone.now() - timedelta(days=1))
    self.assertEqual(member_storage_bytes(grace), 100)

def test_deleting_a_user_removes_their_counters(self):
    ada = make_user("ada")
    admin = administrator_of(get_or_create_workspace(ada))
    MemberUsageCounter.objects.create(member=admin, year_month="2026-11", uploads=3)
    ada.delete()
    self.assertFalse(MemberUsageCounter.objects.exists())
```

- [ ] **Step 2: Run them to see them fail.** Run: `... manage.py test drop_core.tests_allowances.AllowanceModelTests` Expected: ImportError.

- [ ] **Step 3: Add the columns, the model, the helper and the admin changes,** then generate the migration: `manage.py makemigrations drop_core --name member_allowances` (expected file `0015_member_allowances.py`, with only `AddField` and `CreateModel`).

- [ ] **Step 4: Run the tests and the migration check.** Expected: 4 pass; `makemigrations --check --dry-run` reports no changes.

- [ ] **Step 5: Commit.** `TZ=UTC git commit -m "Memberships can hold allowances, and members have a monthly upload counter"`

---

### Task 4: Uploads respect a member's allowance

**Files:**
- Modify: `backend/drop_core/services.py` (`upload_artifact`; import `MemberUsageCounter`)
- Modify: `backend/drop_core/views.py` (`ERROR_STATUS`, `ERROR_MESSAGES`)
- Modify: `docs/API.md` (the error table)
- Test: `backend/drop_core/tests_allowances.py`

**Interfaces:**
- Consumes: `MemberUsageCounter`, `member_storage_bytes`, the allowance columns (Task 3).
- Produces: `upload_artifact` raising `ValueError("member_monthly_allowance_reached")` and `ValueError("member_storage_allowance_reached")`, with its signature unchanged. `ERROR_STATUS` maps them to 429 and 400. The messages, verbatim:
  - `member_monthly_allowance_reached`: "You have reached the monthly upload allowance your administrator set. Uploads work again next month, or your administrator can raise it."
  - `member_storage_allowance_reached`: "You have reached the storage allowance your administrator set. Revoke files you no longer need, or ask your administrator to raise it."

The order inside `upload_artifact` is the point of this task. After the team's early checks, and with `member` already resolved before them:

```python
    # Early and not authoritative, like the team's checks above; the team's are tested first.
    member_counter, _ = MemberUsageCounter.objects.get_or_create(member=member, year_month=_current_month())
    if member.upload_allowance is not None and member_counter.uploads >= member.upload_allowance:
        raise ValueError("member_monthly_allowance_reached")
    if (member.storage_allowance_bytes is not None
            and member_storage_bytes(member) + len(data) > member.storage_allowance_bytes):
        raise ValueError("member_storage_allowance_reached")
```

and inside the existing `transaction.atomic()`, after the team counter's reservation and before the workspace lock:

```python
        member_reserve = MemberUsageCounter.objects.filter(member=member, year_month=month)
        if member.upload_allowance is not None:
            member_reserve = member_reserve.filter(uploads__lt=member.upload_allowance)
        if not member_reserve.update(uploads=F("uploads") + 1):
            # The allowance is reached, or the month rolled over since the early check.
            MemberUsageCounter.objects.get_or_create(member=member, year_month=month)
            if not member_reserve.update(uploads=F("uploads") + 1):
                raise ValueError("member_monthly_allowance_reached")
```

and after the team's storage check, still under the workspace lock:

```python
        if (member.storage_allowance_bytes is not None
                and member_storage_bytes(member) > member.storage_allowance_bytes):
            raise ValueError("member_storage_allowance_reached")
```

The give-back after a storage failure becomes one `transaction.atomic()` that decrements the team counter and then the member counter, each with `uploads__gt=0` and the transaction's `month`. Update the function's docstring to list the new codes.

- [ ] **Step 1: Write the failing tests** in class `UploadAllowanceTests(TestCase)`:

```python
def setUp(self):
    patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": tempfile.mkdtemp()})
    patcher.start(); self.addCleanup(patcher.stop)
    self.workspace = get_or_create_workspace(make_user("ada"))
    self.workspace.plan = "team"; self.workspace.save(update_fields=["plan"])
    self.admin = administrator_of(self.workspace)
    self.grace = second_member(self.workspace, "grace")
    self.cap = PLANS["team"]["uploads"]

def upload(self, member, data=b"hi"):
    return upload_artifact(self.workspace, None, "a.txt", data, None, member=member)

def fill_team_counter(self, uploads):
    UsageCounter.objects.create(workspace=self.workspace, year_month=_current_month(), uploads=uploads)

def test_every_upload_is_counted_for_its_member(self):
    self.upload(self.grace)
    self.assertEqual((team_count(self.workspace), member_count(self.grace)), (1, 1))

def test_a_member_at_their_monthly_allowance_gets_their_own_code(self):
    set_allowance(self.grace, upload_allowance=2)
    self.upload(self.grace); self.upload(self.grace)
    with self.assertRaisesMessage(ValueError, "member_monthly_allowance_reached"):
        self.upload(self.grace)
    self.assertEqual((team_count(self.workspace), member_count(self.grace), Artifact.objects.count()), (2, 2, 2))

def test_a_member_with_no_allowance_can_use_the_whole_pool(self):
    self.fill_team_counter(self.cap - 1)
    self.upload(self.grace)
    with self.assertRaisesMessage(ValueError, "monthly_cap_reached"):
        self.upload(self.grace)

def test_the_team_limit_wins_when_both_are_reached(self):
    set_allowance(self.grace, upload_allowance=0, storage_allowance_bytes=0)
    self.fill_team_counter(self.cap)
    with self.assertRaises(ValueError) as caught:
        self.upload(self.grace)
    self.assertEqual(str(caught.exception), "monthly_cap_reached")

def test_an_allowance_above_the_plan_is_capped_by_the_plan(self):
    set_allowance(self.grace, upload_allowance=self.cap * 10)
    self.fill_team_counter(self.cap)
    with self.assertRaises(ValueError) as caught:
        self.upload(self.grace)
    self.assertEqual(str(caught.exception), "monthly_cap_reached")

def test_a_teammate_using_the_pool_stops_a_member_under_their_allowance(self):
    # Decision 6's example. grace has a large storage allowance, and the administrator holds a live file
    # as large as the team's whole cap: grace's upload gets exactly storage_cap_reached.
    ...

def test_a_refusal_after_the_reservation_rolls_back_both_counters(self):
    set_allowance(self.grace, storage_allowance_bytes=1000)
    with mock.patch("drop_core.services.member_storage_bytes", side_effect=[0, 1001]):
        with self.assertRaisesMessage(ValueError, "member_storage_allowance_reached"):
            self.upload(self.grace)
    self.assertEqual((team_count(self.workspace), member_count(self.grace), Artifact.objects.count()), (0, 0, 0))

def test_a_storage_failure_gives_back_both_counters(self):
    with mock.patch("drop_core.services.get_storage") as get_storage:
        get_storage.return_value.save.side_effect = OSError("down")
        with self.assertRaises(StorageError):
            self.upload(self.grace)
    self.assertEqual((team_count(self.workspace), member_count(self.grace), Artifact.objects.count()), (0, 0, 0))

def test_an_allowance_below_current_use_refuses_new_uploads_only(self):
    first = self.upload(self.grace); self.upload(self.grace)
    set_allowance(self.grace, upload_allowance=1)
    with self.assertRaisesMessage(ValueError, "member_monthly_allowance_reached"):
        self.upload(self.grace)
    self.assertEqual(self.client.get(f"/a/{first.token}").status_code, 200)

def test_the_count_starts_again_next_month(self):
    # With drop_core.services._current_month patched: at the allowance in "2026-11", allowed in "2026-12",
    # with a new MemberUsageCounter row for that month.
    ...

def test_a_one_person_account_gets_the_teams_codes_at_every_limit(self):
    # Through the real views, with a key and no allowance. At the monthly cap: 429, with exactly
    # {"error": ERROR_MESSAGES["monthly_cap_reached"], "code": "monthly_cap_reached"}. At the storage cap: 402,
    # with the same shape for storage_cap_reached.
    ...

def test_the_rest_answers(self):
    # POST /api/v1/artifacts/ with grace's key. At her monthly allowance: 429, no Retry-After header, and the
    # code with its exact sentence. At her storage allowance: 400, and the code with its exact sentence.
    ...

def test_a_refused_upload_url_can_be_retried(self):
    # PUT through an upload URL minted with member=grace, at her allowance: the refusal, then
    # PendingUpload.used is False; after raising the allowance on the row, the same URL gives 201.
    ...

def test_the_mcp_tool_reports_the_code(self):
    # tools/call upload_artifact with grace's key at her allowance: isError, with the code and the sentence.
    ...
```

Take the REST, PUT and MCP request shapes from `drop_core/tests.py`, `drop_core/tests_fix_web.py` and `drop_mcp/tests.py`.

- [ ] **Step 2: Run them to see them fail.** Run: `... manage.py test drop_core.tests_allowances.UploadAllowanceTests`

- [ ] **Step 3: Make the changes above.** Add the two rows to the error table in `docs/API.md`, in complete sentences, without naming a plan beside a figure. Do not change `backend/docs/pages/api.md`.

- [ ] **Step 4: Run the tests and the suites that pin this path.** Run: `... manage.py test drop_core.tests_allowances drop_core.tests_members drop_core.tests_fix_docs drop_core.tests_fix_upload drop_core.tests_fix_storage drop_core.tests_json_answers drop_mcp` Expected: all pass, with `ResolveMemberBeforeLockTests` and `ErrorCodeDocsTests` unchanged.

- [ ] **Step 5: Commit.** `TZ=UTC git commit -m "Uploads stop at a member's allowance, after the team's limits"`

---

### Task 5: Keys respect a member's allowance

**Files:**
- Modify: `backend/drop_core/key_caps.py` (`create_key_within_cap`; a new exception after `KeyCapReached`)
- Modify: `backend/drop_core/account.py` (`create_key`; import `MemberKeyCapReached`)
- Modify: `backend/drop_core/device.py` (`_redeem`; import `MemberKeyCapReached`)
- Test: `backend/drop_core/tests_allowances.py`

**Interfaces:**
- Consumes: `Membership.key_allowance` (Task 3).
- Produces:
  - `class MemberKeyCapReached(KeyCapReached)`, so that any bare `except KeyCapReached` still catches it.
  - `create_key_within_cap(workspace, name, device_issued=False, replacing=None, member=None)`, signature unchanged. Inside the existing lock, after the team check and before deactivating the replaced key:

    ```python
    if member.key_allowance is not None:
        own = active.filter(member=member)
        replaces_own = replaced_pk is not None and own.filter(pk=replaced_pk).exists()
        if not replaces_own and own.count() >= member.key_allowance:
            raise MemberKeyCapReached()
    ```

  - `account.create_key` answers 400 `{"error": "You have reached the API key allowance your administrator set. Revoke one of your keys, or ask your administrator to raise it.", "code": "member_key_allowance_reached"}`, catching `MemberKeyCapReached` before `KeyCapReached`. The `key_limit_reached` body stays byte-identical.
  - `device._redeem` catches `MemberKeyCapReached` in its own clause before `KeyCapReached`, with `refused = "You have reached the API key allowance your administrator set. Revoke one of your keys in the dashboard, or ask your administrator to raise it."`, and the same DENIED status and `access_denied` answer as the team's refusal.

`needs_replacement`, `active_keys` and the consent page stay team-level in this pull request, and `dashboard/keys.py::_refusal` already shows an unknown code's own sentence as kind "other". PR C makes them member-aware.

`account.create_key` and the device flow find the workspace through `get_or_create_workspace(user)`, so today only an administrator reaches them. Their tests set the allowance on the administrator, on the free plan, where allowance 0 is reached before the team's cap of 1. Tests with a second member call `create_key_within_cap` directly.

- [ ] **Step 1: Write the failing tests** in class `KeyAllowanceTests(TestCase)`, with `setUp` making `self.workspace` on the team plan, `self.admin` and `self.grace`, as `UploadAllowanceTests` does:

```python
def test_a_member_at_their_key_allowance_gets_their_own_refusal(self):
    set_allowance(self.grace, key_allowance=1)
    create_key_within_cap(self.workspace, "one", member=self.grace)
    with self.assertRaises(MemberKeyCapReached):
        create_key_within_cap(self.workspace, "two", member=self.grace)
    self.assertEqual(APIKey.objects.filter(member=self.grace, is_active=True).count(), 1)

def test_the_team_limit_wins_when_both_are_reached(self):
    for i in range(PLANS["team"]["keys"]):
        create_key_within_cap(self.workspace, f"k{i}", member=self.admin)
    set_allowance(self.grace, key_allowance=0)
    with self.assertRaises(KeyCapReached) as caught:
        create_key_within_cap(self.workspace, "more", member=self.grace)
    self.assertNotIsInstance(caught.exception, MemberKeyCapReached)

def test_a_member_over_their_allowance_can_still_replace_their_own_key(self):
    old, _ = create_key_within_cap(self.workspace, "old", member=self.grace)
    set_allowance(self.grace, key_allowance=0)
    new, _ = create_key_within_cap(self.workspace, "new", replacing=old, member=self.grace)
    old.refresh_from_db()
    self.assertEqual((old.is_active, new.is_active, new.member), (False, True, self.grace))

def test_replacing_a_teammates_key_counts_as_a_new_key(self):
    theirs, _ = create_key_within_cap(self.workspace, "theirs", member=self.admin)
    set_allowance(self.grace, key_allowance=0)
    with self.assertRaises(MemberKeyCapReached):
        create_key_within_cap(self.workspace, "mine", replacing=theirs, member=self.grace)
    theirs.refresh_from_db()
    self.assertTrue(theirs.is_active)

def test_no_allowance_changes_nothing(self):
    # With no allowance: a key under the cap succeeds, a key at the cap raises KeyCapReached and not the
    # subclass, and an over-cap workspace can still rotate a key.
    ...

def test_the_create_key_answer(self):
    # An administrator on the free plan with key_allowance=0, through account.create_key: status 400, with
    # the exact sentence and code, and no key made. With no allowance, at the cap: the existing
    # key_limit_reached body, byte-identical.
    ...
```

and, in a separate class `DeviceKeyAllowanceTests(_DeviceTestBase)`, imported from `tests_fix_device`:

```python
def test_a_device_login_at_the_allowance_is_refused_with_the_members_sentence(self):
    # set_allowance(administrator_of(self.workspace), key_allowance=0), start, approve, redeem:
    # access_denied, the exact sentence, STATUS_DENIED, and no new key.
    ...
```

- [ ] **Step 2: Run them to see them fail.** Run: `... manage.py test drop_core.tests_allowances.KeyAllowanceTests drop_core.tests_allowances.DeviceKeyAllowanceTests`

- [ ] **Step 3: Make the changes above.**

- [ ] **Step 4: Run the tests and the suites that pin this path.** Run: `... manage.py test drop_core.tests_allowances drop_core.tests_members drop_core.tests_fix_device drop_core.tests_device_pages drop_core.tests_account drop_core.tests_json_answers dashboard.tests_keys`

- [ ] **Step 5: Commit.** `TZ=UTC git commit -m "Key creation stops at a member's allowance, after the team's limit"`

---

### Task 6: AGENTS.md and the full checks

**Files:**
- Modify: `AGENTS.md` ("People inside an account")

- [ ] **Step 1: Add to the section,** in the voice rules:
  - A member's allowances are nullable columns on `Membership`, NULL means no allowance, and only staff can set them until PR C.
  - `MemberUsageCounter` counts every upload per member per month, from this deploy on.
  - The team's limits are tested first, so the team's code wins, apart from a race in which the pool still holds.
  - The lock order is the team counter, the member counter, then the workspace row, with the give-back in the same order.
  - The member counter's insert takes a key-share lock on `Membership`, so code that later locks a `Membership` row must use `select_for_update(no_key=True)` or take the counters first.
  - A request in flight honours the allowance it loaded when it started.
  - A release older than this one cannot delete a user, workspace or membership once member counters exist, because it does not know the table. This affects staff in the admin only, during the deploy or after a code-only rollback.

- [ ] **Step 2: Run the full backend suite and the migration check, one at a time.**

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run
```

The CLIs and the site's pages do not change, so CI covers them.

- [ ] **Step 3: Commit.** `TZ=UTC git commit -m "AGENTS.md explains closed member rows and member allowances"`

---

### Task 7: The Postgres rehearsal

SQLite hides every lock-order bug here, so this task decides whether the branch is ready. Stop for the owner if any pass criterion fails.

- [ ] **Step 0: Build the environment.** You start the cluster, so you may stop it; never touch the Postgres on port 5432.
  - Data directory `$HOME/dev/veilfile-pg-scratch/data`: `/opt/homebrew/opt/postgresql@16/bin/initdb -D <data> -U scratch --auth=trust -E UTF8`, then `pg_ctl -D <data> -l $HOME/dev/veilfile-pg-scratch/pg.log -o "-p 8779 -c listen_addresses=127.0.0.1 -k $HOME/dev/veilfile-pg-scratch -c log_lock_waits=on -c deadlock_timeout=200ms" -w start`, then create database `veilfile`.
  - The old release: `git worktree add --detach $HOME/dev/veilfile-pg-old origin/main`. Both releases use `$HOME/dev/veilfile/venv/bin/python`.
  - Copy the kit `$HOME/dev/veilfile-rehearsal-kit` to `$HOME/dev/veilfile-pg-scratch/kit`. Repoint it with `grep -rl pg-rehearsal-2 . | xargs sed -i '' 's#<old scratchpad path>#<kit path>#g'`.
  - Every Django command runs with `env -i HOME="$HOME" PATH="$PATH" DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key DATABASE_URL=postgres://scratch@127.0.0.1:8779/veilfile DROP_STORAGE=local DROP_LOCAL_DIR=$HOME/dev/veilfile-pg-scratch/files`, and scripts assert the host and port before writing.
  - Reuse `seed.py`, `checksum.py`, `probe.py`, `probe_hb.py`, `analyze_probe.py` and `atk/` where they fit. Write a new verifier for the counts below.

- [ ] **Step 1: Migration under load.** Seed the old release (`origin/main`, which writes members itself) at the kit's volume: at least 200,000 files, 20,000 keys and 5,000 workspaces. Then, by SQL, set `member_id` to NULL on 10 keys, 10 files, 10 upload URLs and 10 safety events, and remove the membership of one workspace whose rows are all NULL. Run the new release's `migrate --noinput` with the old release's probe overlapping it. Report the duration, the longest stall per operation, every lock wait from the server log and every error.
  **Pass:** 0 probe errors, no lock wait over 1,000 ms, 0 NULL members in keys, files and upload URLs, the removed membership healed, and checksums of pre-existing columns unchanged apart from the NULL rows that were filled. Stalls are reported but do not decide this: recheck any stall over 1 s with no lock wait using `probe_hb.py` on a restored copy before calling it a host pause.

- [ ] **Step 2: The deploy stops itself.** On a restored pre-migration copy, set `removed_at` on an administrator whose workspace has a NULL-member key. Run `migrate` while the probe runs.
  **Pass:** `migrate` exits non-zero with the message naming the counts, `0014` is not recorded, and the probe sees 0 errors. Then clear `removed_at` and run `migrate` again: it finishes, and 0 NULL members remain.

- [ ] **Step 3: Exact limits under concurrency.** Add `mkmember(workspace, name, **allowances)` to `atk/acommon.py`, creating a user and a `Membership`, and keys with `member=`. Add these scenarios to `atk/a.py`, each with 30 threads on the team plan:
  - **Monthly:** team counter 1,000, member allowance 5, member counter 0. **Pass:** 5 succeed, 25 get `member_monthly_allowance_reached`, team counter 1,005, member counter 5, 5 new files.
  - **Storage:** files of 2,000 bytes, and an allowance of the member's current bytes plus 10,000. **Pass:** 5 succeed, 25 get `member_storage_allowance_reached`, and the member's live bytes are exact.
  - **Two members, the team's last slot:** team counter one below its cap, 15 threads for each member. **Pass:** exactly 1 succeeds, 29 get `monthly_cap_reached`, and the member counters sum to 1.
  - **Forced storage failure:** `mock.patch("drop_core.services.get_storage")` in the worker, raising on save. **Pass:** all fail with `storage_unavailable`, and both counters end at their starting values.

- [ ] **Step 4: Deadlocks.** Add modes `allow_up`, `allow_mint` and `allow_mixed` to `atk/b.py` (25 rounds of 30 callers, across 3 allowance-limited members) and an allowance-limited member to `atk/c.py` (65 s). Add a mode that deletes a user through `user.delete()` while 30 threads upload and create keys for that account. Rerun PR A's modes. Read the count with `grep -c 'deadlock detected' pg.log` before and after each.
  **Pass:** the count stays 0, no call hangs, per-member counters equal their rows, and `c.py` reports no errors. If only the user-deletion mode deadlocks, report it instead of failing: AGENTS.md then says staff must not delete an account with live traffic.

- [ ] **Step 5: Both releases together.** Old and new writers run concurrently for at least 60 s. **Pass:** 0 errors and 0 NULL members written by either. Then, on a restored copy, record what the old release can no longer do: deleting a user, workspace or membership with counter rows should fail with a foreign key error. That is expected, and it goes in the deploy notes.

- [ ] **Step 6: One-person answers, old against new.** For 150 seeded accounts on the same database, compare the usage, keys and files JSON byte for byte with `last_used_at` masked, and compare the exact bodies at the monthly and storage caps. **Pass:** identical.

- [ ] **Step 7: Rollback.** On a dump copy, run `migrate drop_core 0013` and forward again. **Pass:** row counts in the other tables are equal before and after, the only losses are the three columns and the counters, and the old release's verify and uploads work in between.

- [ ] **Step 8: The full backend suite on Postgres.** Run main's suite from the old-release checkout, then the branch's, and compare the sorted failure lists. **Pass:** the branch adds no failure. Main has 12 here, from tests that assume SQLite or that close the connection.

- [ ] **Step 9: Clean up.** Copy new scripts back into the kit. Stop only your cluster with `pg_ctl -D <data> -m fast -w stop`, remove `$HOME/dev/veilfile-pg-scratch`, and remove the worktree with `git worktree remove` and no `--force`. Confirm nothing listens on 8779 and that 5432 still does.

- [ ] **Step 10: Stop for the owner.** Report the branch, commits, check results and deploy notes. Say that per-member upload counts are a new stored figure, in case the Privacy Policy should mention them (invariant 8: report it and do not edit it). Push and open the pull request through Muse's account only after the owner says so, verifying the login in the same command; merge, with a merge commit, only after all 15 checks pass and the owner says so again.

---

## Carried to PR C

- Today's device consent page offers every key on the team as replaceable, so a member could rotate away a teammate's key. `needs_replacement` and `replaceable_keys` must become member-aware, and limited to the member's own keys.
- `dashboard/keys.py::_refusal` needs a branch for `member_key_allowance_reached`, because the cap text it builds from the plan would show the wrong number.
- `tests_fix_docs._cap_facts` needs a `seats` pattern before any page states a seat count.
- Removal, transfer and anything else that locks a `Membership` row must use `select_for_update(no_key=True)` or take the counters first.
- Everything listed under "What the later pull requests must carry" for PR C in the description of pull request #28.
