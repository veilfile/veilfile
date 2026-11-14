# Team seats PR F2b: forget old invitations, keep held files

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The sweep deletes each invitation 30 days after it ends and no longer deletes a held file at its expiry, so that the Privacy Policy can promise both (the owner's F1 decisions 1 and 3, 2026-11-14).

**Architecture:** Both changes live in the one sweep, `drop_core/sweep.py`. Invitations get `teams.purge_ended_invitations()`, run after the file loop the way `purge_stale_device_codes()` runs. Held files are left out of the sweep's query, so a held file waits until staff use the existing "Release the selected files" action, and the next sweep deletes it.

**Tech Stack:** Django 6.1.1.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md`, with the F plan (`2026-11-14-team-seats-pr-f-switch-on.md`, "Owner decisions raised by F1") and the owner's answers of 2026-11-14, which this plan records in Task 3. Branch `seats/keep-and-forget` from `main` at fa1168d.

## Questions for the owner (answered 2026-11-14: both as recommended)

1. **The Promises page.** Its "Gone means deleted" item says that a sweep removes an expired file from storage, usually within a day. That stops being true for a held file. I recommend adding one sentence after "It is not hidden; it is gone.": "The one exception is a file we hold after a report, which we keep until we have dealt with the report or for as long as the law requires." **Answer:** add it.
2. **The order of the two pull requests.** Keeping held files is a safety rule, so it runs with the switch off too, from this merge. Until the legal pull request lands, the Terms still say "Files are deleted when their TTL expires", and the Privacy Policy says "Deletion is TTL-based". I recommend merging this first and the legal pull request soon after, because holds are rare and only staff make them. The other choice is to keep held files only while the switch is on. **Answer:** from this merge, with the legal pull request soon after.

## Words

- The Promises page sentence in question 1.
- The release action's message gains one sentence when some released files are past their expiry: "1 file was past its expiry and will be deleted by the next sweep." or "N files were past their expiry and will be deleted by the next sweep."

## Global Constraints

- An invitation ends at the first of `accepted_at`, `declined_at`, `revoked_at` and `expires_at`. It is deleted once that end is `INVITATION_KEEP = timedelta(days=30)` old or more. `INVITATION_KEEP` is never shorter than `DECLINE_WAIT`, and the per-hour and per-day limits look back less than it does.
- Both rules run whether the switch is on or off. With the switch off, nothing a visitor sees changes except the Promises sentence.
- No new way to delete a file. Staff release a held file with the existing action, and the sweep does the rest.
- Agents never contact Stripe, production or GitHub, apart from Muse's push and pull request on the owner's go-ahead. Nobody opens a `.env` file or prints a secret value. Agents do not edit `backend/legal/documents/`.
- Commits are made as `Veilfile <support@veilfile.com>` with `TZ=UTC`, with no Co-Authored-By, session link or model name. PRODUCT.md and DESIGN.md stay uncommitted.
- Tests run from `backend/` with `DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test <module>`, one module at a time, and the whole suite once at the end, in the background. New test classes set `longMessage = False`.

## Review Focus

1. **Held files filling a batch.** `maybe_sweep` visits at most 50 rows. If held rows were skipped inside the loop instead of left out of the query, 50 held files would stop every other expired file from going. `test_held_files_do_not_fill_a_batch`.
2. **An expired invitation closed late.** `invite()` marks an expired invitation revoked only when its team next invites, which can be months later. It ended at its expiry, so it goes 30 days after `expires_at`. `test_a_late_close_counts_from_the_expiry`.
3. **The decline block's edge.** A declined invitation must outlast the 30-day block on re-inviting that address. `test_the_decline_block_ends_before_the_row_goes`.
4. **A failed purge.** It must not stop the file deletes or fail the command. `test_a_failed_purge_does_not_fail_the_sweep`.
5. **A kept file and account deletion.** A held file past its expiry still stops staff deleting its owner's account. `test_a_kept_file_still_blocks_account_deletion`.

---

### Task 1: Delete invitations 30 days after they end

**Files:**
- Modify: `backend/drop_core/teams.py`, adding the constant after `DECLINE_WAIT` (line 47) and the function after `_open` (line 106).
- Modify: `backend/drop_core/sweep.py:126-133`, adding a second guarded call after `purge_stale_device_codes()`. The import goes inside the function, so tests can patch `drop_core.teams.purge_ended_invitations`. It logs `"sweep: deleting ended invitations failed"` with `logger.exception`.
- Modify: `backend/drop_core/models.py:750-753`, where the `Invitation` docstring gains "The sweep deletes it 30 days after it ends."
- Test: create `backend/drop_core/tests_invitation_keep.py`.

**Interfaces:**
- Produces: `teams.INVITATION_KEEP` and `teams.purge_ended_invitations(now=None) -> int`, the number of invitations deleted. It is one `delete()` on `Q(accepted_at__lte=cutoff) | Q(declined_at__lte=cutoff) | Q(revoked_at__lte=cutoff) | Q(expires_at__lte=cutoff)`, where `cutoff = now - INVITATION_KEEP`. It logs `"sweep: deleted %d invitation(s) that ended 30 or more days ago"` at INFO when the number is not 0.

- [ ] **Step 1: Write the failing tests.** Use `InvitationKeepTests(tests_invitations.Base)` with `@override_settings(TEAM_SEATS=True)`.
  - The helper `row(email, *, ended_days, how)` makes an `Invitation` for `self.team` with `invited_by=None` and `token_hash` set to the email's sha256.
    - For `accepted`, `declined` or `revoked`, it sets that field to `now - ended_days`, `created_at` one day earlier, and `expires_at = created_at + Invitation.TTL`.
    - For `expired`, it sets `expires_at = now - ended_days` and `created_at = expires_at - Invitation.TTL`.
    - `created_at` is set with `update()` after the create.
  - `test_each_ending_is_deleted_after_30_days`: one row of each kind at 31 days. The purge returns 4, and no row is left.
  - `test_each_ending_is_kept_before_30_days`: one row of each kind at 29 days, plus a waiting invitation from `self.send()`. The purge returns 0, and all 5 rows remain.
  - `test_a_late_close_counts_from_the_expiry`: an `expired` row at 31 days with `revoked_at = now - 2 days` is deleted.
  - `test_the_decline_block_ends_before_the_row_goes`:
    - A `declined` row for `grace@example.com` at 29 days survives the purge, and `self.send("grace@example.com")` raises `InviteRefused` with code `declined_recently`.
    - With `declined_at` moved to 31 days ago, the purge deletes it, and `self.send` succeeds.
  - `test_the_keep_covers_the_limits`: `INVITATION_KEEP == timedelta(days=30)`, `INVITATION_KEEP >= DECLINE_WAIT`, and `INVITATION_KEEP > timedelta(days=1)`.
  - `test_the_sweep_deletes_them`: a row at 31 days is gone after `sweep.sweep_expired()`.
  - `test_a_failed_purge_does_not_fail_the_sweep`:
    - `drop_core.teams.purge_ended_invitations` is patched to raise `RuntimeError`, and an ordinary file is 2 days past its expiry.
    - `sweep_expired()` returns `swept == 1`.
    - `assertLogs("drop_core.sweep", "ERROR")` holds the failure line.
- [ ] **Step 2: Run** `manage.py test drop_core.tests_invitation_keep`. Expected: an ImportError for `INVITATION_KEEP`.
- [ ] **Step 3: Implement** the constant, `purge_ended_invitations`, the sweep call and the docstring.
- [ ] **Step 4: Run** `drop_core.tests_invitation_keep` (7 pass), then `drop_core.tests_invitations` and `drop_core.tests_teams`. Expected: all pass.
- [ ] **Step 5: Commit** "The sweep deletes an invitation 30 days after it ends".

### Task 2: Keep held files until staff release them

**Files:**
- Modify: `backend/drop_core/sweep.py:81`, where the expired query gains `.exclude(safety_status="held")`. The module docstring gains: "A held file is not deleted at its expiry. It waits until staff release it, and the next sweep deletes it."
- Modify: `backend/drop_core/file_access.py:165-166`, where the docstring's "until it is gone" becomes "until they release it".
- Modify: `backend/drop_core/admin.py`.
  - At line 65, `list_filter = ("mime_type", "safety_status")`, so staff can find held files.
  - In `release_selected_files`, the sentence from Words is added when any released file has `expires_at <= now`.
- Modify: `backend/templates/chapters/promises.html:9`, adding the sentence from question 1.
- Test: create `backend/drop_core/tests_held_keep.py`, and add one test to `drop_core/tests_holds.AdminActionTests`.

**Interfaces:** Consumes `file_access.mark_clear(ids)` and `file_access.held_files_at_stake(owned_workspace_ids, row_ids)`, both unchanged.

- [ ] **Step 1: Write the failing tests.**
  - The new module uses `HeldKeepTests(TestCase)`, set up like `tests_invitations.Base`: `_use_local_dir`, then `team_of("ada")` and `second_member(self.team, "grace")`.
  - The helper `expired_file(name, *, held, days=2)` makes Grace's file `days` past its expiry, with `safety_status` `"held"` or `"clear"`.
  - `test_a_held_file_past_its_expiry_is_kept`: one held and one ordinary file. After `sweep_expired()`, the held row exists and the ordinary one is gone, with `swept == 1` and `complete` true.
  - `test_held_files_do_not_fill_a_batch`: three held files 5 days past their expiry and one ordinary file 2 days past. After `sweep_expired(limit=2)`, the ordinary one is gone.
  - `test_a_released_file_goes_at_the_next_sweep`: after `mark_clear` on a held expired file, `sweep_expired()` deletes it.
  - `test_a_kept_file_still_blocks_account_deletion`: after a sweep, `held_files_at_stake([self.team.pk], [self.grace.pk])` is true.
  - `test_the_promise_names_the_exception`: the Promises page text contains the approved sentence.
  - In `AdminActionTests`, `test_releasing_an_expired_file_says_the_sweep_deletes_it`:
    - With the file updated to held and 1 day past its expiry, the release message reads "1 file released. The keys that uploaded them stay stopped. 1 file was past its expiry and will be deleted by the next sweep."
    - Releasing a live held file says nothing about expiry.
- [ ] **Step 2: Run** `drop_core.tests_held_keep`, then `drop_core.tests_holds`. Expected: the held row is deleted, so the first test fails; the release test and the promise test fail too.
- [ ] **Step 3: Implement** the four changes.
- [ ] **Step 4: Run** `drop_core.tests_held_keep`, `drop_core.tests_holds`, `drop_core.tests_fix_storage`, `drop_core.tests`, `dashboard.tests_files` and `pages`. Expected: all pass.
- [ ] **Step 5: Commit** "A held file waits for staff to release it before the sweep deletes it".

### Task 3: Record the decisions and the test card

- [ ] **Step 1:** In the F plan, replace the three bullets under "Owner decisions raised by F1" with the four answers of 2026-11-14, and add "F2b, this plan" to the slices before F3. Commit "Record the owner's F1 decisions".
- [ ] **Step 2:** Write the card `~/dev/veilfile-test-cards/f2b-local.md`, outside the repository, on the E1 card's database.
  - It adds `e1` commands `oldinvite ADMIN_EMAIL ADDRESS DAYS`, which makes an invitation that ended DAYS ago, and `heldfile EMAIL DAYS`, which makes a held file DAYS past its expiry.
  - Then `manage.py sweep_expired` runs, and the admin's Invitations and Files lists, the release action and a second sweep are checked.
  - The card is dry-run on a throwaway copy of the database.
- [ ] **Step 3:** Run the whole suite in the background, after `memory_pressure | tail -1`, then `makemigrations --check --dry-run`. Expected: OK, and "No changes detected".
