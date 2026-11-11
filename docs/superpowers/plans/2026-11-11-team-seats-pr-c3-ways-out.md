# Team seats, PR C3: The Ways Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an administrator remove a member, with the choice the spec gives, let a member leave, let the administrator hand the role to a member who accepts, let staff delete a member's account safely and hold a file with the administrator told, all behind the switch that is off in production.

**Architecture:** `drop_core/teams.py` gains the seat changes: removal, leaving and the administrator offer. Each takes the team's workspace row first, re-reads the people involved under it, and closes or flips `Membership` rows with `update()`. Files are expired, counted or deleted only in `drop_core/file_access.py`. Staff tools live in `drop_core/admin.py` and call two new services, `drop_core/holds.py` and `drop_core/account_deletion.py`. One additive migration stores a waiting offer on the chosen member's row.

**Tech Stack:** Django 6.1.1, Django REST framework, django-oauth-toolkit, SQLite in tests, Postgres 16 in the rehearsal, Postgres in production. Playwright for the site checks.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md` (decisions 4, 11, 12, 15; calls 6, 7, 10, 11, 13, 15; section 9 questions 2, 3 and 5). The C1 plan's last section, "Carried to C2 and C3", and the C2 plan's last two sections, "Changed during the build" and "Carried to C3 and D", are this plan's starting list. Code maps of the merged code at `bb787e2`, and the review of this plan, are in the session scratchpad: `seats-c3-map/` and `c3-plan-review.txt`.

## Questions for the owner

These four change what gets built. The owner answered all four on 2026-11-11 with the recommendation, so the plan builds each as recommended.

1. **What "revoke all of them now" means.** Recommended: the links stop working and the meters free up in the same moment as the removal, and the stored bytes are deleted at the next daily cleanup, normally within a day. The page says exactly that. The Privacy Policy already describes this order for a revoke when storage is down, and it keeps a removal fast for a person with thousands of files. The other choice deletes the bytes during the removal too, which makes a large removal slow and needs a fallback for storage errors anyway.
2. **What staff deletion does with held files.** Recommended: it refuses to delete an account while any held file would go with it, whether that account uploaded it or it sits in a workspace the account owns, and tells staff to release or delete the held file first, so that evidence is never lost by accident. The other choice deletes held files with the account.
3. **Whether a hold notice names the member.** Recommended: neither the email nor the Members page names the file or the person. The email says that a file someone on the team uploaded was held, and the Members page says how many of the team's files are held. The administrator can still work out who it was, because the hold stops the key that uploaded the file and the Keys page shows each key's person (call 6), and a file shared with the team already shows as held on Files. The other choice names the person outright, with a count beside each member on the Members page.
4. **Whether revoking a held file through the API may change for every account.** Recommended: yes. Today the dashboard refuses to revoke a held file, but REST and MCP delete it, which lets the uploader destroy the evidence of a report. After this change both refuse, for every account and with the switch off, so a one-person customer who uploaded a held file is the one person who sees a difference. Section 6 of the design counts any change for a one-person customer as a failure, which is why this is a question.

## Decisions this plan takes

The owner reviews these with the plan. Each says what it costs if wrong.

1. **Removal and leaving stop everything the person's row had, in one transaction.** It first denies the person's approved device logins on that team, before it takes the workspace lock, which is the order a device poll takes the two. Then, under the team's workspace lock, the row closes, its keys go inactive, its unused upload URLs are marked used, a waiting administrator offer to it ends, and the person's OAuth grants are revoked. Without the grant revocation, a removed person's old token would come back as a token for their own account at their next sign-in (`tests_one_place.py:216` shows this today), and call 7 says removal ends every credential. If wrong: a removed person reconnects an app they could have kept.
2. **"Revoke all of them now" is one `UPDATE` that sets `expires_at` to now** for the person's live files on that team, of either visibility, under the same lock (question 1). It reads no file name. It never touches a held file, which staff keep as evidence until it expires. If wrong: the bytes linger for up to a day.
3. **Nobody can be removed from, or leave, a suspended team.** Staff suspended the team, so its membership stays as staff found it until they lift the suspension, and the files stay as evidence. Without this, a member could leave and keep uploading on a new Free account while the team is stopped. This narrows call 15, a member leaving on their own, for as long as a suspension lasts. If wrong: a member of a suspended team waits for support.
4. **Leaving is for members.** An administrator with other people on the team is told to make someone else the administrator first (section 9, question 3). An administrator alone has no team to leave, so Overview offers them no Leave link and the page is the shell's 404 for them.
5. **The removal page starts on "Let them keep working until they expire".** The other choice cannot be undone, so it is never the default, and only the exact value `revoke` revokes.
6. **A person who left or was removed reads on Overview, for 7 days:** "You are no longer on a team, so this account is a Free account again." It is worked out from the closed row, so it needs no new field, and it names nobody, so that a person removed for abuse learns no one's address. They get no email. If wrong: the notice goes, or an email comes later.
7. **The administrator offer is a nullable `Membership.offered_administrator_at` on the chosen member's row,** with a partial unique index so that a team has one offer at a time. A new offer replaces a waiting one. It lasts 7 days, like an invitation. The chosen member finds it on Overview, so no email is sent and no token is needed, because they are already signed in. They must have a verified address and an active account. Accepting demotes the administrator, promotes the chosen member, clears the new administrator's allowances and moves `Workspace.owner`, all in one transaction under the workspace lock, demoting first because the unique index is not deferrable. The rows keep their primary keys, so keys, files and "only me" stay with their people. The Stripe customer's email and the prompt to check the card stay with PR D, as the C1 plan agreed. If wrong: an email can come later, and the column is one migration.
8. **`administrator_of` refuses a workspace whose owner's row is no longer the administrator,** with `NoCurrentAdministrator`. After a transfer, a `Workspace` loaded before the commit still holds the old owner, and the fallback would otherwise give a new file or key to the former administrator. In the admin, staff can no longer add, delete, or edit the owner of a workspace, and can no longer add, delete, or edit the person, team, role, `removed_at` or offer of a membership, because each of those could break the rules above. Workspaces come from signup, and memberships from signup and joining. A staff edit that is truly needed goes through a shell.
9. **Waiting invitations survive a transfer.** They still say who sent them, and the invitation page's "Ask … to make room" now names the team's current administrator, who is the one person who can.
10. **A hold is a staff action, "Hold the selected files", in the admin.** It sets the file's `safety_status` to held and, when the file has an `api_key`, sets that key inactive (section 9, question 5: "a hold lands on the file and on the key that sent it"). A file uploaded through a connected app has no key, and the hold stops nothing else; staff can suspend the person if they need to. Then, after the commit, it emails each team's current administrator once, unless the team has none or the uploader is that administrator. A "Release" action clears the hold and leaves the key stopped. `safety_status` becomes read-only on the admin form, so that the action is the only way to hold a file and nothing skips the notice. A failed email never undoes a hold: it is logged by its class, and staff are told in the admin. A blocked upload in the safety mode is not a hold, because no file is kept, so it sends no notice.
11. **No route revokes a held file** (question 4). REST answers 403 with the code `file_held` and "This file is held, so it was not revoked." The MCP revoke tool raises that sentence through its existing error path, as it does for a bad id, so its result shape and its description stay as they are. The REST row goes into the error tables of `docs/API.md` and `backend/docs/pages/api.md`. If wrong: one check comes out.
12. **Staff deletion is one account at a time, through the admin's Delete button, which runs a service outside any transaction.** The admin's stock delete cannot do it: it wraps everything in one transaction, it stops at the files and keys that point at a membership, and it lists every file name. So the user admin draws its own confirmation, which shows counts only, and drops the bulk delete. The service refuses an account that administers a team with other people on it (section 9, question 3), an account whose own workspace pays through Stripe, because nothing cancels the subscription, and an account any of whose held files would go with it (question 2). Otherwise it stops the person's credentials on every team as removal does, then deletes their files and keys there in batches, so that the bytes go after each batch, revokes the waiting invitations they sent, and deletes the account, which takes their own workspace with it. Deleting an administrator whose team has only removed people's files left in it takes those files too, which ends them early; the person closing the account owns the team.

## Words

These are for the owner's review with this plan. Only a person on a team can see them, and only with the switch on, apart from the staff pages and the one REST and MCP answer.

**The Members page.**

| Where | Words |
|---|---|
| Actions on a member's row | "Set allowance", "Make administrator", "Remove", each with the hidden " for {{ email }}". The administrator's own row keeps "Set allowance" only. |
| A request is waiting | "You asked {{ email }} to become the administrator. The request ends on {{ expires }}." Link: "Cancel the request". |
| Held files | "Veilfile has held {{ count }} {{ file or files }} that people on this team uploaded. This page does not say which {{ one or ones }}, or whose." |

**Removing someone**, whose heading is "Do you want to remove {{ email }} from the team?".

```
Their API keys and connected apps stop working at once, and this account becomes a Free account of their own.

They have {{ count }} live {{ file or files }} on this team, using {{ size }}.

What happens to their files
( ) Let them keep working until they expire
    They go on counting toward the team's storage until then, and {{ email }} can still see and revoke them.
( ) Revoke all of them now
    Their links stop working at once, and the files are normally deleted within a day. This cannot be undone.

[Remove from the team]   Keep them on the team
```

With no live files, the count line reads "They have no live files on this team." and the choice is not shown.

**Leaving**, whose heading is "Do you want to leave this team?".

```
You leave the team that {{ admin }} administers, and this account becomes a Free account again.

Your API keys and connected apps stop working at once. Files you uploaded here keep working until they expire, and you can still see and revoke them on Files. They count toward the team's storage until then.

[Leave the team]   Stay on the team
```

**Asking someone to become the administrator**, whose heading is "Do you want to make {{ email }} the administrator?".

```
They become the administrator when they accept, and you become a member of the team. The administrator manages who is on the team and pays for its plan. Files that only you can see stay that way.

They have 7 days to accept, on their Overview page.

[Ask them]   Go back
```

**Cancelling the request**, whose heading is "Do you want to cancel the request?": "{{ email }} can no longer accept the administrator role." Button: "Cancel the request". Link: "Keep the request".

**Accepting the role**, whose heading is "Do you want to become the administrator of this team?".

```
{{ admin }} asked you to take over the team. If you accept:
- You manage who is on the team, what each person can use and who can see new files.
- You pay for the team's plan, and Billing is yours to manage.
- {{ admin }} becomes a member, and the files that only they can see stay private.
- You cannot leave the team while other people are on it, until you hand the role on.

[Become the administrator]   Decline
```

If the chosen member has not verified their address, the page says instead: "Verify your email first, then open this page again." Link: "Verify email".

**Overview.**

| Where | Words |
|---|---|
| Member's Team section | Link after the sentence: "Leave the team" |
| A request waits for this person | "{{ admin }} asked you to become the administrator of this team. The request ends on {{ expires }}." Link: "Review the request" |
| Left or removed, for 7 days | "You are no longer on a team, so this account is a Free account again." |

**Notices after an action.**

| After | Words |
|---|---|
| Removed, files kept | "{{ email }} is no longer on the team." |
| Removed, files revoked | "{{ email }} is no longer on the team, and their files are revoked." |
| Left | "You have left the team. Connect your agents again with veilfile login." |
| Asked | "{{ email }} can now accept the request on their Overview page." |
| Request cancelled | "The request is canceled." |
| Accepted | "You are now the administrator of this team." |
| Declined | "You declined the request." |
| Not waiting | "That request is not waiting any more." |
| Person gone | "That person is not on the team any more." |
| Team suspended | "This team is suspended, so nobody can be removed or leave it until the suspension ends. Contact support@veilfile.com." |
| Administrator tries to leave | "You administer this team, so you cannot leave it while other people are on it. Make someone else the administrator first." |
| Chosen person suspended | "That person's account is suspended, so they cannot become the administrator." |
| Chosen person unverified | "That person has not verified their email address, so they cannot become the administrator yet." |

**The hold email.** Subject: "A file on your Veilfile team was held". The second sentence of the first paragraph appears only when at least one of the files had a key.

```
Veilfile held {{ count }} {{ file or files }} that someone on your team uploaded, because {{ it or they }} may break our Terms of Service, and {{ its link no longer opens or their links no longer open }}. The API {{ key that uploaded it has or keys that uploaded them have }} stopped working.

This email does not say which file was held or who uploaded it, to keep files private between the people on a team. If you have a question, write to support@veilfile.com.

veilfile
```

**The REST answer** for revoking a held file: 403, `{"error": "This file is held, so it was not revoked.", "code": "file_held"}`. The MCP revoke tool raises the same sentence.

**Staff pages in the admin.**

| Where | Words |
|---|---|
| Deletion confirmation | Heading "Delete {{ email }}?". "This deletes the account, its own workspace, and its files and keys on {{ teams }} {{ team or teams }}: {{ files }} {{ file or files }} and {{ keys }} {{ key or keys }} in all. It cannot be undone." Button: "Delete the account". Link: "Go back". |
| Deleted | "The account is deleted, with its files and keys." |
| Deletion refused, administrator | "This account administers a team with other people on it. Make someone else the administrator first." |
| Deletion refused, Stripe | "This account pays for a plan through Stripe. Cancel the subscription in Stripe first." |
| Deletion refused, held | "Deleting this account would delete files that are held. Release or delete them first." |
| Held | "{{ n }} {{ file or files }} held. {{ m }} {{ administrator was or administrators were }} told." |
| A notice failed | "The notice to {{ m }} {{ administrator or administrators }} could not be sent, so tell them another way." |
| Released | "{{ n }} {{ file or files }} released. The keys that uploaded them stay stopped." |

## Global Constraints

- **With the switch off, nothing changes for anyone but staff, apart from question 4.** A GET of every new address answers the shell's 404, signed in or out, before `login_required`. No page changes by a byte for any account, on any plan: `SwitchOffTests` compares pages with bytes captured from `main`. Every new helper in `teams.py` returns its empty value before any query when the switch is off. The admin changes, the hold email, the `file_held` answer and staff deletion work with the switch off, because staff and holds exist without teams.
- **The one JSON answer that changes is revoking a held file** (decision 11). `drop_core/tests_json_answers.py` and `drop_billing/tests_json_answers.py` otherwise pass unchanged, with the switch on and off.
- **Every seat change takes the team's workspace row first, with `select_for_update()`,** and re-reads the people involved under it: the caller with `_still_administrator` where the caller is the administrator, and the target's row by primary key, workspace, role and `removed_at`. A caller who is no longer the administrator under the lock raises `PermissionDenied`, as `invite` does. Resolve the caller with `current_membership` before the transaction. Nothing here calls `administrator_of` or `current_membership` under a lock. Anything that locks a `Membership` row uses `select_for_update(no_key=True)`; a plain `UPDATE` of `removed_at`, `role` or `offered_administrator_at` needs no extra lock.
- **A `DeviceCode` is written before the workspace lock, never under it.** A device poll holds its row and then waits for the workspace lock, so the same order everywhere means no deadlock.
- **Writers use `update()` with the fields they change, never a loaded row's `save()`.** `Workspace.owner` moves with `Workspace.objects.filter(pk=...).update(owner_id=...)`.
- **Nothing reads `workspace.owner`.** `OwnerReadsTests` keeps its `ALLOWED` table empty. The administrator's address comes from their `Membership` row.
- **Mail goes after the transaction commits, never under a lock.** A failed hold email is logged with the exception's class only and never undoes the hold.
- **An `IntegrityError` is caught outside the `atomic` block.**
- **`delete_account` never runs inside a transaction,** and says so with an assertion on `connection.in_atomic_block`, because its separate steps and the byte deletion after each batch depend on it.
- **Only `drop_core/file_access.py` lists, counts, expires, holds or deletes files, or marks upload URLs used, for this pull request's code.** `OnlyFileAccessReadsFilesTests.REMAINING` stays as it is. New modules must not contain the strings `Artifact.objects`, `PendingUpload.objects` or `.artifacts`, docstrings included, because the guard counts text.
- **An administrator never sees a member's "only me" file name** on any page this adds, and nothing this adds names a held file or its uploader to anyone but staff. What an administrator can infer from a stopped key is question 3.
- **Templates add no whitespace with the switch off.** The newline goes inside the block, as in `overview.html`, with `{% endif %}` opening the next line.
- **Every new test class sets `longMessage = False`,** and every new test module joins `READING_TEST_MODULES`. Each new form has one test with `Client(enforce_csrf_checks=True)` and a scraped token.
- **No new script.** Confirmations are separate pages on `layouts/form.html`, and forms post and redirect. A page whose object can no longer be acted on gives a sentence and a redirect, someone else's object is the shell's 404, and an unknown choice or action changes nothing.
- **New pages follow DESIGN.md and AGENTS.md's dashboard recipe:** one column, no cards, no colour for state, one primary action per form, no disabled controls, every interactive element `interactive` or `action`, and tokens only. Times use `utc_time`, and sizes use `bytes`. Each new route is in `DASHBOARD_SECTIONS` marking `app` and has a row in section 4.8 of `docs/superpowers/specs/2026-10-08-site-redesign-design.md`. A member's pages live under `/app/team/`, because a member's Overview must not contain `/app/members/`.
- **Removal and leaving never depend on the plan, the key cap, `has_team_features` or a verified address,** because PR D's bar on members of a Free team must still let them leave, and removal is how a team fits a smaller plan (section 9, question 2).
- **Migrations are additive and safe on a live Postgres table,** and the release on main keeps serving while they run. Rolling back is the code alone.
- **Never name a plan and then a figure on one line** in a file `drop_core/tests_fix_docs.py` scans.
- AGENTS.md invariants hold, notably 4 (published clients keep working), 8 (no edits to `backend/legal/documents/*`) and 9 (no package versions). No new runtime dependencies.
- Words are the ones in this plan's Words section, verbatim, and any others follow AGENTS.md's Voice section: complete sentences, semicolons only between independent clauses, no em dashes, no exclamation marks, and American spelling.
- **This Mac panicked from memory exhaustion on 2026-11-05 and is shared.** Run heavy checks one at a time, and check `memory_pressure | tail -1` first. Never signal a process you did not start.
- Commits: `Veilfile <support@veilfile.com>`, `TZ=UTC git commit`, plain-sentence subjects, no attribution lines of any kind, no personal names. Branch: `seats/ways-out`, from the latest `origin/main`. Pushing, opening the pull request and merging each need the owner's go-ahead, through Muse's account.

## Review Focus

1. **A removal racing the person's own upload, key mint, upload URL and device login:** nothing lands on the team after the commit, nothing deadlocks, and every credential is refused. Tests: `test_an_upload_in_flight_is_refused_after_removal` and `test_removal_stops_everything_the_row_had` (Task 1), and Task 8 on Postgres.
2. **A double POST, or two tabs, on remove, leave, accept or decline:** one outcome, a sentence for the second, and never a 500. Tests: `test_removing_twice_is_harmless` (Task 2) and `test_accepting_twice_is_harmless` (Task 4).
3. **After a transfer, everything follows the role:** the former administrator gets a member's Overview, Billing and Keys and the shell's 404 on Members, the new administrator gets the administrator's, and the former administrator's "only me" files stay out of the new administrator's lists. Test: `test_after_the_transfer_everything_follows_the_role` (Task 4).
4. **"Revoke all of them now" spares held files and names no file.** Tests: `test_revoking_all_spares_held_files` (Task 1) and `test_no_file_name_appears_on_the_remove_page` (Task 2).
5. **Staff deleting an administrator of other people, a paying account or an account with held files at stake, through the real admin page:** refused with nothing changed, and an allowed deletion goes all the way. Tests: `test_the_admin_deletes_a_member_with_keys_and_files` and the refusal tests (Task 6).

---

### Task 1: Removal and leaving

**Files:**
- Modify: `backend/drop_core/teams.py`, `backend/drop_core/file_access.py`
- Test: `backend/drop_core/tests_removal.py` (new)
- Modify: `backend/pages/tests_site_rules.py` (`READING_TEST_MODULES` gains `drop_core.tests_removal`)

**Interfaces:**
- Produces in `teams.py`: `class SeatChangeRefused(Exception)` with `code`; `remove_member(administrator, member_id, *, revoke_files: bool) -> int`, the number of files it revoked; `leave_team(member) -> None`. Codes: `person_gone` (the target is not a current member row of this team, or is the administrator), `team_suspended`, `administrator_has_people` (leave by an administrator of a team with other people), `not_on_a_team` (leave by anyone whose current row is not a member row). A caller who is no longer the administrator under the lock raises `PermissionDenied`.
- Produces in `teams.py`: `GONE_NOTICE = timedelta(days=7)` and `no_longer_on_team(member) -> bool`: the person's current row is an administrator row of their own workspace, and one of their member rows closed less than `GONE_NOTICE` ago. False with the switch off.
- Produces in `file_access.py`: `expire_files_of(row, now) -> int` (held files excluded), `live_files_of(row, now) -> tuple[int, int]` (count and bytes) and `end_upload_urls_of(row) -> None` (unused ones marked used), each for one `Membership` row in its own workspace.
- A private `_close_member_row(row, now)` in `teams.py` makes the writes of decision 1 that belong under the lock, and a private `_deny_device_logins(workspace_id, user_id)` runs first in the same transaction, before the lock.

- [ ] **Step 1: Write the failing tests in `drop_core/tests_removal.py`**

Import `team_of` from `drop_core.tests_teams` and `second_member` from `drop_core.tests_members`. With `override_settings(TEAM_SEATS=True)` unless a test says otherwise:

- `test_removal_stops_everything_the_row_had`: after `remove_member(admin, grace.pk, revoke_files=False)`, grace's key fails `APIKey.verify`, her unused upload URL answers as an unknown one, her approved device login on the team is `denied`, `resolve_oauth_credential` refuses her token, and `key_caps.active_keys(team)` no longer counts her key.
- `test_a_refused_removal_denies_no_device_login`: `person_gone` rolls the denial back with everything else.
- `test_removal_lets_files_run_out_with_the_first_choice`: with `revoke_files=False`, her files keep `expires_at`, still serve, and still count in `workspace_storage_bytes(team)`.
- `test_revoking_all_expires_every_live_file_of_the_person`: both visibilities, returns the count, links answer as missing, `workspace_storage_bytes(team)` drops, and the upload counters do not change.
- `test_revoking_all_spares_held_files`: a held file keeps its `expires_at`.
- `test_the_administrator_and_another_team_are_refused`: removing the administrator's own row, a closed row and another team's row raise `person_gone` and change nothing.
- `test_a_suspended_team_refuses_removal_and_leaving`.
- `test_a_caller_who_is_no_longer_administrator_cannot_remove`: the caller's row is demoted before the call; `assertRaises(PermissionDenied)`, and nothing changes.
- `test_removing_twice_raises_person_gone`.
- `test_an_upload_in_flight_is_refused_after_removal` and `test_a_key_mint_in_flight_is_refused_after_removal`, extending the pattern of `tests_one_place.InFlightTests`.
- `test_leaving_is_removal_with_files_kept`, `test_an_administrator_with_people_cannot_leave` and `test_a_person_on_their_own_cannot_leave`.
- `test_removal_works_on_a_free_team_and_for_an_unverified_person`: a team whose plan is Free with two people, and a member whose address change is pending (call 13).
- `test_the_whole_path_from_a_real_join`: `invite`, `accept_invitation`, then `leave_team`, then `current_membership(person)` reopens the person's own workspace, Free, with its old keys still inactive and its old files listed by `files_visible_to`; then a second invitation and join reopens the same team row with allowances cleared. Repeat with `remove_member` in place of leaving.
- `test_no_longer_on_team_lasts_seven_days`: True on day 6, False on day 8, False for someone who never left and for someone back on a team.
- `test_an_inactive_members_upload_url_dies`: carried from PR #28: a non-owner member with `is_active=False`, a URL minted by a key and one minted by an OAuth grant, and a PUT to each answers exactly as an unknown token.
- `test_with_the_switch_off_the_helpers_ask_nothing`: `remove_member`, `leave_team` and `no_longer_on_team` run no query with the switch off, in the pattern of `HelperTests` in `dashboard/tests_members.py`.
- `test_the_file_guard_table_is_unchanged`: run `OnlyFileAccessReadsFilesTests` from `drop_core/tests_file_access.py` as part of Step 4.

Every class sets `longMessage = False`. Add the module to `READING_TEST_MODULES`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_removal`
Expected: ImportError for `remove_member`.

- [ ] **Step 3: Implement**

`remove_member` reads the target row by primary key and the caller's workspace, with no lock, to learn its person; opens `transaction.atomic()`; calls `_deny_device_logins(workspace_id, target.user_id)`, which updates that person's `approved` codes on that team to `denied`; then locks `Workspace.objects.select_for_update().get(pk=administrator.workspace_id)`, re-checks `_still_administrator(administrator)`, raises `team_suspended` if the locked row has `suspended_at`, re-reads the target with `Membership.objects.filter(pk=member_id, workspace_id=..., role=MEMBER, removed_at__isnull=True)`, calls `_close_member_row`, then `expire_files_of` when asked, then `revoke_user_oauth_grants(target.user)`. A refusal raised inside the block rolls the denial back. `_close_member_row` is `APIKey ... update(is_active=False)`, `file_access.end_upload_urls_of(row)` and one `Membership.objects.filter(pk=row.pk).update(removed_at=now)`. Task 3 adds `offered_administrator_at=None` to that update, with its test. `leave_team` makes the same writes for the caller's own member row with no file choice, and refuses before the transaction when the caller is not on a team. `expire_files_of` is `Artifact.objects.filter(member=row, workspace_id=row.workspace_id, expires_at__gt=now).exclude(safety_status="held").update(expires_at=now)`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core dashboard.tests_switch_off pages.tests_site_rules`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend
TZ=UTC git commit -m "An administrator can remove a member, and a member can leave, with everything their row had stopped at once"
```

### Task 2: The pages for removing and leaving

**Files:**
- Modify: `backend/dashboard/members.py`, `backend/dashboard/urls.py`, `backend/dashboard/overview_page.py`, `backend/pages/registry.py`
- Create: `backend/dashboard/team.py` (a member's own team pages)
- Create: `backend/templates/dashboard/member_remove.html`, `backend/templates/dashboard/team_leave.html`
- Modify: `backend/templates/dashboard/members.html`, `backend/templates/dashboard/overview.html`
- Modify: `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (section 4.8's table of addresses)
- Modify: `backend/pages/tests_site_rules.py` (`READING_TEST_MODULES` gains `dashboard.tests_team`)
- Test: `backend/dashboard/tests_members.py`, `backend/dashboard/tests_team.py` (new)

**Interfaces:**
- Consumes: `remove_member`, `leave_team`, `no_longer_on_team`, `SeatChangeRefused`, `live_files_of` (Task 1).
- Produces: routes `app-member-remove` (`/app/members/<int:member_id>/remove/`, GET and POST, administrator) and `app-team-leave` (`/app/team/leave/`, GET and POST, member). Each is `team_seats_only` outside `dashboard_view`, and each key in `DASHBOARD_SECTIONS` marks `app`.
- Produces in `dashboard/team.py`: `_member(request)`, the signed-in person's current member row. An administrator of a team with other people gets the "Administrator tries to leave" notice and a redirect to Members. An administrator alone, a person with no current member row, and anyone else get the shell's 404. Task 4's accept page uses it too.
- The remove view first looks the row up by primary key and the caller's workspace, with no other filter. No row is the shell's 404. A closed row, or the administrator's own row, gives the "Person gone" notice and a redirect to Members. Only then does it call `remove_member`.

- [ ] **Step 1: Write the failing tests**

In `dashboard/tests_members.py`: add `/app/members/<id>/remove/` to `AccessTests.addresses()`; `test_removing_asks_first_and_changes_nothing_on_a_get`; `test_removing_with_files_kept_and_with_files_revoked` (each notice from the Words, verbatim); `test_the_remove_page_starts_on_keeping_the_files`; `test_an_unknown_choice_keeps_the_files`; `test_no_file_name_appears_on_the_remove_page` (first proves the fixture holds an "only me" name and link); `test_the_remove_page_states_count_and_size_only`; `test_a_person_with_no_live_files_gets_no_choice`; `test_removing_twice_is_harmless` (the second POST answers the "Person gone" notice and a redirect, never a 500); `test_another_teams_member_is_the_shells_404`; `test_the_administrators_own_row_has_no_remove_link`; `test_a_suspended_team_gets_the_sentence`; `test_removing_the_last_member_of_a_free_team_goes_to_overview`; `test_a_post_without_its_token_is_form_not_sent` and one test with `Client(enforce_csrf_checks=True)`; `FormPagesAreOnTheShellTests.check` on the page.

In `dashboard/tests_team.py`, with its own list of member addresses: the switch-off 404 signed in and out; signed out goes to `/accounts/login/?next=`; `test_leaving_asks_first_and_changes_nothing_on_a_get`; `test_leaving_lands_on_overview_with_the_notice`; `test_an_administrator_with_people_gets_the_sentence`; `test_an_administrator_alone_and_a_person_with_no_team_get_the_shells_404`; `test_the_member_overview_links_to_leave` (and the administrator's does not); `test_a_member_overview_never_contains_app_members` still passes; `test_a_person_who_left_reads_the_notice_on_overview` and stops reading it after 7 days; the token and shell checks as above.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard.tests_members dashboard.tests_team`
Expected: failures for the missing routes.

- [ ] **Step 3: Implement**

Both pages copy `invitation_cancel.html`: `layouts/form.html`, a `title` and a `noindex` `meta` block, one `{# #}` comment per line, one form with `{% csrf_token %}` and one `action interactive` button, and a quiet `form-links` link back. The choice is two `.check` radios in a `fieldset` with a visible `legend`, as on the Members page, with the first checked. A POST that the service refuses with `person_gone` or `team_suspended` gives `messages.info` and a redirect to Members, and `PermissionDenied` is the shell's 404. After a removal that leaves `has_team_features` false, the redirect goes to Overview. The Members table's Actions cell gains "Remove" after "Set allowance". Overview's member Team section gains the Leave link after its sentence, and the notice from `no_longer_on_team` sits with the other notices at the top, inside team-only blocks so that the golden pages do not change. Add both rows to section 4.8's table and both names to `DASHBOARD_SECTIONS`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard pages drop_core.tests_file_access`
Expected: OK, with `SwitchOffTests` unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend docs/superpowers/specs/2026-10-08-site-redesign-design.md
TZ=UTC git commit -m "The Members page can remove someone, with the choice for their files, and a member can leave from Overview"
```

### Task 3: The administrator offer, underneath

**Files:**
- Create: `backend/drop_core/migrations/0020_membership_offered_administrator_at.py`
- Modify: `backend/drop_core/models.py`, `backend/drop_core/teams.py`, `backend/drop_core/admin.py`, `backend/drop_core/invitation_views.py`, `backend/templates/registration/invitation.html`
- Test: `backend/drop_core/tests_transfer.py` (new, which also holds this task's admin tests), `backend/drop_core/tests_removal.py`, `backend/drop_core/tests_members.py`, `backend/drop_core/tests_invitations.py`
- Modify: `backend/pages/tests_site_rules.py` (`READING_TEST_MODULES` gains `drop_core.tests_transfer`)

**Interfaces:**
- Produces on `Membership`: `offered_administrator_at = models.DateTimeField(null=True, blank=True)`, and a constraint `membership_one_offer`, unique on `workspace` where `offered_administrator_at` is not null.
- Produces in `teams.py`: `OFFER_TTL = timedelta(days=7)`; `offer_administrator(administrator, member_id) -> Membership`; `cancel_administrator_offer(administrator) -> bool`; `waiting_administrator_offer(workspace) -> dict | None` with `email` and `expires`; `administrator_offer_for(member) -> dict | None` with `admin_email` and `expires`; `accept_administrator_offer(member) -> None`; `decline_administrator_offer(member) -> bool`. Codes added to `SeatChangeRefused`: `chosen_suspended`, `chosen_not_verified`, `offer_not_waiting`. An `IntegrityError` during acceptance becomes `offer_not_waiting`.
- Changes `administrator_of` so that it raises `NoCurrentAdministrator` when the owner's row is closed or is not an administrator row. `_administrator_row` still creates the row of a new workspace's owner as administrator, which `signals.workspace_administrator` relies on.

- [ ] **Step 1: Write the failing tests**

In `drop_core/tests_transfer.py`: `test_an_offer_waits_for_seven_days`; `test_a_new_offer_replaces_the_waiting_one`; `test_the_database_allows_one_offer_a_team` (an `IntegrityError` on a second row set directly); `test_an_offer_to_the_administrator_a_closed_row_or_another_team_is_refused`; `test_an_offer_to_a_suspended_or_unverified_person_is_refused`; `test_a_suspended_team_refuses_offers_and_acceptance`; `test_accepting_flips_the_roles_moves_the_owner_and_keeps_the_rows` (both primary keys unchanged, `Workspace.owner_id` is the new administrator, the offer is cleared, the new administrator's allowances are cleared, keys and files keep their `member`); `test_accepting_after_the_offer_expired_or_was_cancelled_is_refused`; `test_accepting_after_the_administrator_changed_is_refused`; `test_accepting_twice_is_harmless`; `test_declining_clears_the_offer`; `test_the_former_administrators_only_me_files_stay_out_of_the_new_administrators_list` (`files_visible_to`); `test_administrator_of_refuses_a_stale_workspace` (a `Workspace` loaded before the transfer); `test_a_new_workspace_still_gets_its_administrator_row`; `test_an_integrity_error_becomes_offer_not_waiting` (a former administrator who already holds a member row elsewhere, built directly); `test_with_the_switch_off_the_helpers_ask_nothing`; and, for the admin, `test_staff_cannot_edit_the_owner_or_a_memberships_fields` and `test_staff_cannot_add_a_membership_or_a_workspace` (the add views answer 403 for a superuser). In `tests_removal.py`: `test_removal_ends_a_waiting_offer_to_that_person`. In `tests_members.py`: `OwnerReadsTests` still passes, and `AdministratorGuardTests` in `tests_one_place.py` too. In `tests_invitations.py`: `test_no_free_seat_names_the_current_administrator` after a transfer.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_transfer`
Expected: errors for the missing field and functions.

- [ ] **Step 3: Implement**

The migration adds the column and the constraint, nothing else. `accept_administrator_offer` takes the workspace lock, re-reads the offer on the caller's own row (current, member, `offered_administrator_at` within `OFFER_TTL`), the administrator's row with `role=ADMINISTRATOR, removed_at__isnull=True`, the caller's `is_active` and verified address, and the suspension; then, in order, demotes the administrator's row, promotes the caller's row with its allowances and offer cleared, and moves the owner with `update(owner_id=...)`. Catch `IntegrityError` outside the block. `offer_administrator` clears every offer on the team and sets the new one, in that order, in one transaction. `_close_member_row` clears the offer. `WorkspaceAdmin` adds `owner` to `readonly_fields`; `MembershipAdmin` makes `workspace`, `user`, `role`, `removed_at` and `offered_administrator_at` read-only; both return False from `has_add_permission`. Their delete permission waits for Task 6, because Django's stock user delete checks it on every row a deletion cascades to. The invitation page's "make room" sentence takes `administrator`, the current administrator's address from their `Membership` row.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core dashboard.tests_switch_off pages.tests_site_rules`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend
TZ=UTC git commit -m "An administrator can offer the role to a member, who becomes the administrator by accepting"
```

### Task 4: The pages for the transfer

**Files:**
- Modify: `backend/dashboard/members.py`, `backend/dashboard/team.py`, `backend/dashboard/urls.py`, `backend/dashboard/overview_page.py`, `backend/pages/registry.py`
- Create: `backend/templates/dashboard/member_administrator.html`, `backend/templates/dashboard/administrator_offer_cancel.html`, `backend/templates/dashboard/team_administrator.html`
- Modify: `backend/templates/dashboard/members.html`, `backend/templates/dashboard/overview.html`
- Modify: `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (section 4.8)
- Test: `backend/dashboard/tests_members.py`, `backend/dashboard/tests_team.py`, `backend/dashboard/tests_billing.py`

**Interfaces:**
- Consumes: the Task 3 functions, and `_member` (Task 2).
- Produces: routes `app-member-administrator` (`/app/members/<int:member_id>/administrator/`, GET and POST), `app-administrator-offer-cancel` (`/app/members/administrator/cancel/`, GET and POST) and `app-team-administrator` (`/app/team/administrator/`, GET, and POST with `action=accept` or `action=decline`). Only those two exact values act; any other gives the "Not waiting" notice and changes nothing.

- [ ] **Step 1: Write the failing tests**

Add the two administrator addresses to `AccessTests.addresses()`, and `/app/team/administrator/` to `tests_team.py`'s list. `test_asking_asks_first_and_says_so`; `test_the_waiting_request_shows_on_members_with_a_cancel_link`; `test_cancelling_the_request_asks_first`; each refusal's sentence; `test_the_chosen_member_sees_the_notice_on_overview` and no one else does; `test_the_accept_page_says_what_the_role_means`; `test_an_unverified_chosen_member_is_asked_to_verify`; `test_accepting_lands_on_members_with_the_notice`; `test_accepting_twice_is_harmless`; `test_declining_lands_on_overview_with_the_notice`; `test_an_unknown_action_changes_nothing`; `test_a_page_for_a_request_that_is_not_waiting_gives_the_sentence`; `test_after_the_transfer_everything_follows_the_role` (the former administrator's Overview is a member's, Billing says "Your administrator manages billing for this team." and its POSTs call no Stripe code, Members is the shell's 404, and Keys shows only their own keys; the new administrator's are the administrator's; and the former administrator's "only me" file name appears on none of the new administrator's pages); a token test and `FormPagesAreOnTheShellTests.check` for each page.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard.tests_members dashboard.tests_team dashboard.tests_billing`
Expected: failures for the missing routes.

- [ ] **Step 3: Implement**

The asking and cancelling pages copy the removal page's pattern. The accept page follows the invitation page: one form for the main button with `action=accept`, and a second form whose `label interactive` button sends `action=decline`. The Overview notice for the chosen member sits with the waiting-invitation notice, inside team-only blocks. Members' Actions cell gains "Make administrator" between "Set allowance" and "Remove", and the waiting request is a paragraph above the table.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test dashboard pages drop_billing`
Expected: OK, with `SwitchOffTests` unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend docs/superpowers/specs/2026-10-08-site-redesign-design.md
TZ=UTC git commit -m "The Members page can ask someone to become the administrator, and they accept or decline from Overview"
```

### Task 5: Holding a file, and telling the administrator

**Files:**
- Create: `backend/drop_core/holds.py`, `backend/templates/registration/file_held_email.txt`
- Modify: `backend/drop_core/admin.py`, `backend/drop_core/file_access.py`, `backend/drop_core/services.py`, `backend/drop_core/account.py`, `backend/drop_mcp/views.py`
- Modify: `backend/dashboard/members.py`, `backend/drop_core/teams.py`, `backend/templates/dashboard/members.html`
- Modify: `docs/API.md` and `backend/docs/pages/api.md` (the `file_held` row in the error table, and one sentence in the revoke section), and `docs/llms.txt` if it lists the revoke errors
- Test: `backend/drop_core/tests_holds.py` (new), `backend/drop_core/tests_json_answers.py`, `backend/drop_mcp/tests.py`, `backend/dashboard/tests_members.py`
- Modify: `backend/pages/tests_site_rules.py` (`READING_TEST_MODULES` gains `drop_core.tests_holds`)

**Interfaces:**
- Produces in `holds.py`: `hold_files(artifact_ids) -> HoldResult` with `held` (files newly held), `told` (administrators emailed) and `not_told` (administrators whose email failed); `release_files(artifact_ids) -> int`.
- Produces in `file_access.py`: `uploads_of(artifact_ids) -> list[tuple[int, int, int | None]]`, the workspace, member and key of each file; `mark_held(workspace_id, artifact_ids) -> list[int]`, the ids it changed; `mark_clear(artifact_ids) -> int`; `held_file_count(workspace) -> int`, the team's live held files, of any uploader, current or removed.
- Produces in `teams.py`: `team_held_files(workspace) -> int`, zero with the switch off, for the Members page.

- [ ] **Step 1: Write the failing tests**

In `drop_core/tests_holds.py`: `test_holding_sets_the_file_and_stops_its_key`; `test_holding_a_file_with_no_key_holds_it_and_the_email_makes_no_claim_about_a_key` (a file uploaded through a connected app); `test_holding_an_already_held_file_does_nothing_and_sends_nothing`; `test_one_email_per_team_whatever_the_count`; `test_the_email_names_no_file_and_no_person` (no file name, token, size, uploader address or key name in the subject, the body or any log line); `test_the_email_goes_to_the_current_administrator` (after a transfer); `test_no_email_when_the_uploader_is_the_administrator`; `test_a_file_in_a_workspace_with_no_current_administrator_is_held_without_an_email` (a person who joined a team, and a file left in their own closed workspace); `test_a_failed_email_keeps_the_hold_and_logs_the_class_only`; `test_releasing_clears_the_hold_and_keeps_the_key_stopped`; `test_the_admin_form_cannot_edit_safety_status`; `test_the_actions_log_each_change`. In `tests_json_answers.py`: `test_revoking_a_held_file_is_refused` with the exact body from the Words, 403, and the file still there. In `drop_mcp/tests.py`: the revoke tool on a held file answers its existing error shape with the sentence, and the file is still there. In `dashboard/tests_members.py`: `test_the_held_count_shows_without_a_name` and `test_no_held_line_when_nothing_is_held`, and `test_it_runs_one_query_per_figure` still holds.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_holds`
Expected: ImportError.

- [ ] **Step 3: Implement**

`hold_files` groups the files by workspace with `uploads_of`. For each workspace, before any lock, it reads the current administrator with a plain query, `Membership.objects.filter(workspace_id=..., role=ADMINISTRATOR, removed_at__isnull=True).select_related("user").first()`, never `administrator_of`, which can write and raise. Then, in its own short transaction, it takes that workspace's lock, calls `mark_held`, and sets inactive the keys of the changed files that have one. After the commit it emails that administrator when the workspace changed any file, an administrator exists, and at least one changed file's uploader is someone else, rendering `file_held_email.txt` with only the count and whether a key stopped. The admin actions use `@admin.action(..., permissions=["change"])`, call the service, `log_change` each file, and report with `message_user`, using `messages.WARNING` for `not_told`. `safety_status` joins `ArtifactAdmin.readonly_fields`. `services.revoke_artifact` refuses a held file. `account.revoke_artifact` answers `file_held` before calling it, as it does for a suspended team. The MCP tool, after `file_to_revoke(` and before the call on `(artifact.workspace`, raises `ValueError("This file is held, so it was not revoked.")`, its existing error path, so its result shape and description stay as they are.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core drop_mcp dashboard.tests_members pages`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend docs/API.md docs/llms.txt
TZ=UTC git commit -m "Staff can hold a file, which stops its key and tells the team's administrator without naming it, and no route revokes a held file"
```

### Task 6: Staff deletion of an account

**Files:**
- Create: `backend/drop_core/account_deletion.py`, `backend/templates/admin/auth/user/delete_account.html`
- Modify: `backend/drop_core/admin.py`, `backend/drop_core/file_access.py`
- Test: `backend/drop_core/tests_account_deletion.py` (new), and the existing `backend/drop_core/tests_fix_storage.py`
- Modify: `backend/pages/tests_site_rules.py` (`READING_TEST_MODULES` gains `drop_core.tests_account_deletion`)

**Interfaces:**
- Produces in `account_deletion.py`: `class DeletionRefused(Exception)` with `code` (`administers_people`, `pays_through_stripe`, `has_held_files`); `why_not_delete(user) -> str | None`; `deletion_summary(user) -> dict` with `teams`, `files` and `keys`; `delete_account(user) -> None`, which asserts that `connection.in_atomic_block` is False.
- Produces in `file_access.py`: `held_files_at_stake(owned_workspace_ids, row_ids) -> bool`, any held file, live or expired and not yet swept, in a workspace the account owns or uploaded by any of its rows; `delete_files_of(row, batch=500) -> int`, deleting rows in batches, one transaction each, so that the `post_delete` receiver removes the bytes after each commit; and the counts `deletion_summary` needs.
- The admin calls `admin.site.unregister(User)` and registers a subclass of `django.contrib.auth.admin.UserAdmin`. It overrides `delete_view` with a view of its own that never opens a transaction and never runs Django's collector: a GET checks `why_not_delete`, and on a refusal shows its sentence with `messages.error` and redirects to the change page, or else draws `delete_account.html` with `deletion_summary`; a POST with `post=yes` checks again, calls `delete_account`, shows "The account is deleted, with its files and keys." and redirects to the change list. It overrides `get_actions` to drop `delete_selected` for users only. In the same commit, `WorkspaceAdmin` and `MembershipAdmin` return False from `has_delete_permission` (decision 8), which no longer blocks deleting an account, because the new delete view never runs Django's collector.

- [ ] **Step 1: Write the failing tests**

`test_a_member_with_keys_and_files_is_deleted_with_them` (today this raises `RestrictedError`); `test_a_member_with_expired_unswept_files_is_deleted`; `test_a_held_file_of_theirs_refuses_and_changes_nothing`; `test_a_held_file_of_a_removed_person_in_their_workspace_refuses`; `test_an_administrator_of_people_is_refused_and_changes_nothing`; `test_a_paying_account_is_refused`; `test_an_administrator_alone_is_deleted_with_the_workspace`; `test_after_a_transfer_the_former_administrator_can_be_deleted_and_the_team_stays`; `test_a_removed_persons_files_left_on_the_team_go_with_their_account` (carried from PR #28); `test_the_invitations_they_sent_are_revoked`; `test_a_safety_event_keeps_its_row_and_loses_the_person`; `test_credentials_stop_before_any_row_is_deleted` (the first step's effects stay if a later step fails); `test_delete_account_refuses_to_run_inside_a_transaction`; and, through the real admin with a superuser and the test client: `test_the_admin_deletes_a_member_with_keys_and_files` (a 302, the user gone, the files' bytes gone, and `delete_account` reached with `in_atomic_block` False), `test_the_admin_deletes_a_lone_administrator`, `test_the_admin_delete_button_refuses_with_the_sentence`, `test_the_confirmation_shows_counts_and_no_file_name`, `test_there_is_no_bulk_delete_of_accounts`, and `test_staff_cannot_delete_a_membership_or_a_workspace` (their delete views answer 403 for a superuser). `tests_fix_storage.test_admin_user_delete_removes_bytes` passes unchanged.

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_account_deletion drop_core.tests_fix_storage`
Expected: ImportError.

- [ ] **Step 3: Implement**

`delete_account` checks `why_not_delete` first. Then, for each team the person has a row on and does not own, in one transaction each, it denies their device logins there, takes that team's workspace lock, closes a current row with `_close_member_row`, and revokes their OAuth grants. Then, outside any transaction, it deletes their files on those teams with `delete_files_of`, their keys there, and the waiting invitations they sent, and finally `user.delete()`, which takes their own workspaces through `Workspace.owner`. A failure after the first step leaves the stopped credentials stopped, which is safe.

- [ ] **Step 4: Run the tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core pages`
Expected: OK, with no test left failing.

- [ ] **Step 5: Commit**

```bash
git add backend
TZ=UTC git commit -m "Staff delete an account through a service that stops it first and refuses what would lose a team or evidence"
```

### Task 7: The browser checks, a look at the pages, and AGENTS.md

**Files:**
- Modify: `site-tests/run_site_checks.py` (`dashboard_members_problems`), `site-tests/make_dashboard_fixtures.py`, `backend/pages/tests_dashboard_fixtures.py`
- Modify: `AGENTS.md` ("People inside an account", and the dashboard recipe's list of team pages)

- [ ] **Step 1: Add the pages to the browser checks.** `dashboard_members_problems` gains tuples for the remove page, the asking page and the cancel page (as the administrator), and the leave page and the accept page (as the member). The fixture gives the member a waiting offer, so that the Overview notice and the accept page have something to show, and `tests_dashboard_fixtures.py` pins it. A GET opens each page, and nothing is posted. The keyboard walk on Members still reaches the invite field within its budget, and its message names the new links.
- [ ] **Step 2: Run the dashboard part locally with the switch on,** as the C2 build did, and fix what it finds.
- [ ] **Step 3: The controller looks at the pages** at 375 and 1280 in Paper and Ink, reads them against DESIGN.md, fixes what the look shows, and keeps the screenshots for the owner.
- [ ] **Step 4: Update AGENTS.md** in its voice: removal, leaving and the transfer and what each stops; that `Workspace.owner` now moves with the role; the hold action and its email; staff deletion, its own confirmation and what it refuses; the `DeviceCode` order; and the read-only admin fields.
- [ ] **Step 5: Run the full backend suite, then commit.**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: OK.

```bash
git add site-tests backend AGENTS.md
TZ=UTC git commit -m "The site checks cover the pages for leaving, removal and the transfer, and AGENTS.md explains them"
```

### Task 8: Rehearse on Postgres, then stop for the owner

Use the rehearsal kit at `~/dev/veilfile-rehearsal-kit` (`prc2/` is the newest) on a throwaway cluster at `127.0.0.1:8779`, with the previous release from the local `origin/main` in a worktree. Never fetch, push or touch port 5432.

- [ ] **Step 1: Seed at the volume of C1 and C2:** 5,146 workspaces, 20,046 keys and 200,207 files, with teams on each plan.
- [ ] **Step 2: Migrate under load** while the previous release reads and writes. **Bar:** 0 probe errors, and the wait of `0020`'s `ALTER TABLE` on the membership table recorded.
- [ ] **Step 3: Compare the switch off with the previous release.** **Bar:** byte-identical pages and JSON once times and tokens are masked, apart from revoking a held file, and every new address is the shell's 404.
- [ ] **Step 4: Race removal and leaving** against the person's uploads, key mints, upload URL PUTs, device polls (including a code with a replaced key) and OAuth requests, with 30 threads. **Bar:** 0 deadlocks, nothing lands on the team after the commit, every old credential refused, and counters equal to what committed.
- [ ] **Step 5: Race the transfer** against uploads, key mints, a billing switch holding the workspace row, a second offer, a removal of the chosen member and a double accept. **Bar:** one administrator at every moment, `Workspace.owner` always that person, no deadlock.
- [ ] **Step 6: Delete accounts with live traffic** through the admin page, a member's, a removed person's and a lone administrator's, and hold files on a busy team. **Bar:** no deadlock that loses work, every refusal leaves the rows as they were, and no email or log line names a file or a person.
- [ ] **Step 7: Attack privacy again** on a team made through the pages: the remove page, the hold count and email, and the pages after a transfer must never show an "only me" name, and every new address must answer someone else's ids as missing.
- [ ] **Step 8: Run the full suite on Postgres.** **Bar:** the same failures as `main`, by test name.
- [ ] **Step 9: Remove the cluster and the worktree,** and confirm port 8779 is closed and port 5432 is untouched.
- [ ] **Step 10: Stop for the owner** with the results, the rulings and the screenshots, and the deploy notes: `0020` adds a column and an index to the membership table; rolling back is the code alone, because the previous release never reads the column.

## Carried to D, E and F

These are not part of this plan.

- **D:** the Stripe customer's email moves to the new administrator at a transfer, and the new administrator's Billing page asks them to check the card on file (section 9, question 3). A checkout started before a transfer can still create a Stripe customer with the former administrator's address, because `checkout` checks the role before the lock.
- **D:** the bar on members of a team that drops to Free must still let them leave, and must not reuse `removed_at` or the inactive keys, because access returns when the team pays again.
- **E:** the REST and MCP sentences a removed person or a member can reach, and agents choosing visibility, as the C2 plan carried.
- **F:** the Terms and the Privacy Policy cover removal, leaving, the transfer, staff deletion of a member's account, and what an administrator is told about a held file, in the owner's words, before the switch turns on. They must not promise more than question 3's answer gives, because an administrator can work out whose key a hold stopped.
- **F:** PRODUCT.md, local and uncommitted, still says a workspace has one member, and is refreshed when the switch turns on.
