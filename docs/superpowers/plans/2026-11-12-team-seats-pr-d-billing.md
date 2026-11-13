# Team seats, PR D: Billing and the Bar on Free Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bar the members of a team that ends up on Free until it pays again, refuse a plan with fewer seats than the team has people, and make billing follow the administrator at a transfer, all behind the switch that is off in production.

**Architecture:** The bar is derived, never stored: a person whose current row is a member row of a workspace on Free is barred. Every credential and write checks it, with the plan read from the row it already loads or from the workspace row it locks. Billing gains a seat check, and a re-read of the administrator under the workspace lock that `switch` and `_ensure_customer` already take. It also gains one Stripe call, `Customer.modify`, made after a transfer commits. One additive migration records that a transfer handed a paid plan to someone new.

**Tech Stack:** Django 6.1.1, Django REST framework, django-oauth-toolkit, stripe-python 11.0.0 (API version 2025-03-31.basil), SQLite in tests, Postgres 16 in the rehearsal, Postgres in production. Playwright for the site checks.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md` (decision 13; calls 7, 10, 11 and 12; section 6; section 7's billing constraints; section 9, questions 2, 3 and 4). PR A's plan holds the table of pull requests, and this plan covers its row D apart from question 1 below. The carry lists are the last sections of the C2 plan ("Carried to C3 and D") and the C3 plan ("Changed during the build", "Carried to D, E and F"). Code maps of the merged code at `97f4bd6`, and the review of this plan, are in the session scratchpad: `seats-d-map/` and `d-plan-review/`.

## Questions for the owner

These change what gets built. The owner answered all four on 2026-11-12 with the recommendation, so the plan builds each as recommended. The Stripe settings in question 2 are the owner's to make in the Stripe dashboard, before F turns the switch on.

1. **When joining from a paid individual plan is built.** The design decides that it is allowed (section 9, question 4); the question is the order. Recommended: a pull request of its own, D2, which must merge before F turns the switch on. Until D2 merges, this plan keeps refusing a paid individual with today's `has_paid_plan` sentence. Nothing in the code ends a subscription or credits a balance today. The likely call, `Subscription.cancel(prorate=True, invoice_now=True)`, cannot be checked without Stripe, and agents may not contact it. Several things need one test-mode check by the owner first:
   - whether that call puts the credit on the customer balance under Managed Payments
   - what it does to a subscription that is past due, unpaid, paused, trialing or waiting on a pending switch
   - how an immediate ending meets the Terms' 14-day refund and "your plan stays active until the end of the current billing period"

   The other choice builds it in D, with the same check before F.
2. **Stripe settings this plan relies on and cannot see.** The owner checks these in the Stripe dashboard, and in test mode where the item says so.
   - **The portal and plan changes.** The portal's settings apply to every customer, so turning plan changes off there would change the portal for every one-person customer, which section 6 counts as a failure. Recommended: the owner makes a second portal configuration with plan changes off. `portal` passes it, through a new setting, only for a team of more than one person with the switch on. Then every plan change for a team goes through Veilfile's switch, where the seat check is, and one-person customers keep today's portal. Until it exists, decision 7 covers what a portal downgrade does.
   - **After the last retry of a failed payment.** Recommended: Stripe marks the subscription unpaid or cancels it. If it leaves the subscription past due, the team keeps its paid plan and nobody is ever barred.
   - **Stripe's own emails** about failed payments and receipts are on, since decision 14 relies on them.
   - **In test mode:** `Customer.modify` with a new email works under Managed Payments.
3. **A paid team with more people than its plan's seats.** A portal downgrade with the default configuration, or a switch that waited for payment and that Stripe applies later, can leave a paid team over its seats. Invariant 3 means the webhook writes the plan Stripe reports, so the code cannot refuse it there. Recommended: tolerate it. Nobody new can be invited or join, which today's checks already ensure, nobody is removed and nobody is barred, and Members and Billing say so. The other choice bars the people who joined last, beyond the seats.
4. **Whether Veilfile emails anyone when a team drops to Free or pays again.** Nothing does today. Recommended: no email in D. Stripe's own emails reach the administrator, whose address decision 9 keeps on the customer, and members see the bar on Overview when they sign in. F can add mail with its sending service. The other choice emails the members at the drop, through the site's mail account, which C2's limits already strain.

## Decisions this plan takes

The owner reviews these with the plan. Each says what it costs if wrong.

1. **The bar is derived, never stored.** A person is barred when their current row has `role == "member"` and `removed_at` empty, and its workspace's `plan == "free"`. Nothing is written when a team drops to Free or pays again: no `removed_at`, no inactive key, no revoked grant and no used upload URL. So access returns as soon as a sync derives a paid plan again. The one exception is a device login approved before the drop and not yet collected: the poll that meets the bar denies that code, so the person runs `veilfile login` again, as for any code older than ten minutes. The administrator's row is never barred. A one-person account has only an administrator row, so it is never barred, on any plan, with the switch on or off. The bar reads no setting, because only a join, which the switch guards, makes a member row; if the switch were ever turned off with teams in place, the bar would still hold. If wrong: a column and a migration.
2. **Every credential and write refuses a barred member.**
   - `APIKey.verify` and `resolve_oauth_credential` return `None`, so REST, MCP and OAuth refuse with today's 401.
   - The upload URL PUT answers 403 `team_on_free` once `_minting_credential_active` has passed, so a dead credential still answers 404 and has its URL ended. A barred member's URL is left unused, so it works again if the team pays before it expires.
   - `upload_artifact` refuses `team_on_free` twice. First, from the member row it is given, before the caps, so a barred member is told about the bar and not about a full plan. Second, under the workspace lock, after `workspace_suspended` and `member_removed`, with `plan` read from the locked row. An upload URL's claim is released after either refusal.
   - `create_key_within_cap` refuses `team_on_free` under its lock, with the role read under it.
   - The device page refuses to approve right after its email check, before anything is written, and a poll is refused through `create_key_within_cap`.

   The checks under the lock decide the race with a webhook, and the others are early refusals. An upload that took the workspace lock before the drop committed still lands, as one that began before a removal does. If wrong: a barred member's upload in flight lands once.
3. **What a barred member keeps.** This is wider than call 10's "see and revoke their own files", because revoking a key or an app is revoking, and leaving is C3's way out.
   - They can sign in.
   - Overview gives them a notice and the Leave link, without the Get connected panel.
   - Files shows only their own files, including those shared with the team, and Revoke works; there is no visibility control.
   - Keys lists and revokes their own keys, with no form to create one and no Get connected section.
   - Apps lists and disconnects their apps, and Billing gives them its member sentence.

   They cannot upload, make keys, approve device logins or change a file's visibility. The OAuth consent page still lets them connect an app. The grant it makes is refused like every other credential until the team pays, and then it works, so it is not stopped there (E words that page for a member). Removal, leaving and revoking read no plan (C3's constraint), and public links read no plan, so links already shared keep working until they expire (decision 13).
4. **The administrator of a Free team with other people keeps Free's one person's access.** They reach Members to remove people and Billing to choose a plan. They can upload and make keys within Free's limits, which count the members' live files and the month's uploads (call 10), so a team that stored more than Free allows cannot upload until files expire or it pays. While the team is on Free, barred members' active keys do not count toward the key cap, so the administrator can still make Free's one key. They count again when the team pays, and a team over its key cap then keeps its keys, as after any downgrade. If wrong: the administrator of a lapsed team cannot make a key.
5. **Nobody is barred while Stripe retries.** `past_due` is already an entitled status, so the plan stays paid. Signed-webhook tests pin it: a failed payment leaves the member working, an unpaid or deleted subscription bars them, and a paid invoice restores them. How long Stripe retries, and what follows, are Stripe settings (question 2).
6. **A switch or a checkout to a plan with fewer seats than the team has people is refused** with 409 `not_enough_seats`. The body is `{"error": ..., "code": "not_enough_seats", "plan": slug, "people": N, "seats": M}`.
   - Only current people count, not waiting invitations, as in `why_not_join`. A waiting invitation beyond the new plan's seats can then never be accepted: it answers `no_free_seat` until the administrator cancels it or it expires, and Members shows the seats in use with it counted, as today.
   - The switch re-reads the administrator and then checks the seats, under the workspace lock it already holds, before any Stripe call that changes something. Checkout does both first under `_ensure_customer`'s lock.
   - A switch to the plan the team is already on, to change the interval, is never refused.
   - With the switch off, the check returns before any query.
   - On Billing, a plan with too few seats shows a sentence instead of its button, because the dashboard has no disabled controls.

   If wrong: one check.
7. **A paid team over its seats is tolerated** (question 3).
8. **Billing re-reads the administrator under the lock** (the C3 carry).
   - **Checkout:** `_ensure_customer` gains the plan being bought and checks, first under its lock and before its early return for a workspace that already has a customer, that the caller's row is still the open administrator row and that the team fits the plan. It raises a private exception that `checkout` turns into the answer at each of its three calls.
   - **Switch:** `switch` does the same re-read directly after its `select_for_update()`, before `_switch`.
   - **Reading the administrator:** under the lock, it is a plain `Membership` filter, never `administrator_of` or `current_membership`, which can write and must not run under that lock. A caller who is no longer the administrator gets today's `_administrator_only()` answer, 403 with no `code`.
   - **A new customer's email:** a new Stripe customer gets the email of the locked team's administrator, from their row, as `email or None`, so a one-person account sends exactly what it sends today.
   - **The portal** takes no lock and keeps its check before the call (C3's Ruling 12).

   If wrong: a former administrator's request in flight acts once more.
9. **At a transfer, the Stripe customer's email moves to the new administrator, after the commit.**
   - **The call:** `move_billing_email(workspace)` in `drop_billing/account.py` calls `Customer.modify(customer_id, email=...)`, with the address from the team's open administrator row, when the workspace has a customer. The accept view calls it after `accept_administrator_offer` returns.
   - **Failure:** a failure, including Stripe not being configured, is logged with the exception's class and never undoes the transfer.
   - **Repair:** while the transfer's marker (decision 10) is set, `portal` repeats the move before it opens a session, so the first time the new administrator opens Manage billing repairs a failed move.
   - **The key:** the live key already has write access to Customers. AGENTS.md says to tell the owner before deploying a new Stripe call.

   If wrong: Stripe's emails go to the former administrator until someone opens Manage billing.
10. **The new administrator's Billing page asks them to check the card until they open Manage billing.**
   - **The field:** migration `0021` adds a nullable `Workspace.billing_handed_over_at`. `_accept_once` sets it under the lock, in the same `update()` that moves `owner_id`, only when the workspace has a subscription, because only then is there a card the former administrator added.
   - **Clearing:** `portal` clears it with a compare-and-set on the value it read, so a second transfer in the meantime keeps its own marker, and only once the email has moved and the session is made. `_forget_missing_customer` clears it with the customer.
   - **The notice:** it shows only while the field and the customer are both set. The staff admin shows the field read-only.

   If wrong: a flash instead, and the column stays unused.
11. **A transfer is allowed while the team is on Free.** The role comes with the bill, so a member who accepts can pay for a team whose administrator could not, and that is how a lapsed team gets one who can. The accept page says the team is on Free. The former administrator is then a barred member. If wrong: one refusal code.
12. **A checkout that completes after its payer has joined another team is applied and reported, not undone** (the C2 carry). `_sync_workspace` applies the plan Stripe reports, as invariant 3 says. When the workspace has no open administrator row and that plan is paid, it logs an ERROR telling staff to reconcile it by hand, with the workspace and subscription ids, and `sync_stripe_billing` logs the same. D2 ends such a subscription with a credit. If wrong: the person pays for a workspace nobody uses until staff act, and the ERROR reaches the owner.
13. **Joining from a paid individual plan stays refused until D2** (question 1). `has_paid_plan` and its words stay as they are.
14. **No email at a drop or a recovery** (question 4).
15. **Staff can still edit a workspace's plan in the admin, and `sync_stripe_billing --reset-unbilled` and a missing customer can still set Free.** Each bars a team's members as a drop does, which is consistent. The deploy notes say so. If wrong: read-only fields.
16. **A barred member's agents get today's 401 on a key or a token.** `verify` and `resolve_oauth_credential` return `None` for every reason. A sentence that names the bar is E's, which carries the REST and MCP sentences a member can reach. Session uploads, upload URLs and key mints, which have a code, say that the team is on Free.
17. **The team portal configuration** (question 2) is a new setting, `STRIPE_TEAM_PORTAL_CONFIGURATION`, read from the environment, empty by default and documented in the README. While it is empty, `portal` passes no configuration. If wrong: one setting.

## Words

These are for the owner's review with this plan. Only a person on a team of more than one can see them, and only with the switch on. `{{ admin }}` is the team's administrator's address, which members already see on Overview.

| Where | Words |
|---|---|
| A barred member's Overview, the first notice after the billing notices | "Your team is on the Free plan, so you cannot upload or make keys until {{ admin }} chooses a paid plan. You can still see and revoke your own files, and you can leave the team." |
| A barred member's Keys page, in place of the form and the Get connected section | "Your team is on the Free plan, so you cannot make keys for now. You can still revoke the ones you have." |
| Key refused (`team_on_free`: the Keys page, REST, and a device poll) | "Your team is on the Free plan, so no key was made. Ask your administrator to choose a paid plan under Billing." |
| Upload refused (`team_on_free`: REST, MCP and an upload URL) | "Your team is on the Free plan, so nothing was stored. Ask your administrator to choose a paid plan under Billing." |
| A barred member's device page, in place of Approve | "Your team is on the Free plan, so this login cannot make a key. Ask your administrator to choose a paid plan under Billing." |
| The administrator's Billing page, on Free with other people | "Your team is on the Free plan, so the {{ n }} other {{ person or people }} on it cannot upload or make keys. Choose a plan with enough seats for everyone, or remove people under Members." |
| The administrator's Members page, on Free with other people | "The team is on the Free plan, so nobody but you can upload or make keys. Choose a paid plan under Billing, or remove people here." |
| The administrator's Billing and Members pages, over a paid plan's seats | "Your team has {{ people }} people, and {{ plan }} has {{ seats }} seats, so nobody new can join until people leave or you change plans." |
| A plan with too few seats, on Billing in place of its button | "{{ plan }} has {{ seats }} seats, and your team has {{ people }} people." |
| Switch or checkout refused (`not_enough_seats`, on Billing) | "Your team has {{ people }} people, and {{ plan }} has {{ seats }} seats. Remove {{ excess }} {{ person or people }} under Members before you change to {{ plan }}." |
| Switch or checkout refused (`not_enough_seats`, the JSON `error`) | "This team has more people than the plan has seats. Remove people first." |
| The new administrator's Billing page, until they open Manage billing | "You are now this team's administrator, so its plan is yours to pay. The card on file is the one the former administrator added. Check it, and change it if you need to, under Manage billing." |
| The accept page (`dashboard/team_administrator.html`), on a Free team with other people, after its first paragraph | "The team is on the Free plan, so only its administrator can upload or make keys. If you accept, you can choose a paid plan under Billing." |

## Global Constraints

- **With the switch off, nothing changes.**
  - `SwitchOffTests` compares pages with bytes captured from `main`.
  - `drop_core/tests_json_answers.py` and `drop_billing/tests_json_answers.py` pass unchanged. The test runner forces the switch off, so a one-person account's answers with the switch on are pinned by `override_settings(TEAM_SEATS=True)` tests that compare them with the switch-off answers.
  - Every new helper that reads seats returns its empty value before any query when the switch is off, and each task pins that with `assertNumQueries(0)`.
  - The bar needs no switch check, because it needs a member row (decision 1). `test_a_free_one_person_account_sees_no_change_with_the_switch_on` keeps passing.
- **The new JSON answers:**
  - 409 `not_enough_seats`, from checkout and switch
  - 403 `team_on_free`, from session uploads, upload URLs and key creation
  - the MCP upload error with the same sentence, which `_service_failure` reads from `ERROR_MESSAGES`

  Only a person on a team of more than one can reach them. Each code has its rows in `docs/API.md` (the error table and the list of upload codes) and `backend/docs/pages/api.md`, in the same task as the code, because `ErrorCodeDocsTests` checks every code in `ERROR_STATUS`.
- **Plan state changes only from Stripe** (AGENTS.md, invariant 3). Nothing here writes `Workspace.plan`; the bar reads it.
- **Locks:**
  - A check that decides a write reads the plan and the role from the workspace row it locked with `select_for_update()`, and from a plain read under that lock.
  - Under a workspace lock, the administrator is read with `Membership.objects.filter(workspace=..., role=Membership.ADMINISTRATOR, removed_at__isnull=True).select_related("user")`, never with `administrator_of` or `current_membership`.
  - Nothing here locks a `Membership` row.
  - No new Stripe call runs under a lock: `move_billing_email` runs after the commit, and the re-reads inside `switch` and `_ensure_customer` read the database only.
- **New Stripe calls go through `_stripe_library()`,** so that `patch_stripe` in `drop_billing/tests_fix_billing.py` sees them. Its map gains `Customer.modify` under the key `customer_modify`, because `modify` is already `Subscription.modify`. New billing test modules subclass that file's `FixBillingBase` and sign their webhooks with its `post_event`.
- **Removal, leaving, revoking and public links never read the plan.**
- **Nothing reads `workspace.owner`.** The administrator's address comes from their `Membership` row, and `OwnerReadsTests` keeps its `ALLOWED` table empty.
- **Only `drop_core/file_access.py` lists, counts, expires, holds or deletes files** for this pull request's code. `OnlyFileAccessReadsFilesTests.REMAINING` stays as it is.
- **Writers use `update()` with the fields they change.** `billing_handed_over_at` moves in the same `update()` as `owner_id`, and is cleared with a filter on the value read.
- **Existing tests whose teams sit on Free** and use a member's key, token, upload or key mint get their fixture team put on Team first. Only the fixture changes, never an assertion or a golden file. Task 1 names them.
- **Templates add no whitespace with the switch off.** The newline goes inside the block, with `{% endif %}` opening the next line, as in `overview.html`.
- **Tests:**
  - Every new test class sets `longMessage = False`, and every new test module joins `READING_TEST_MODULES` in `backend/pages/tests_site_rules.py`.
  - Every new form has one test with `Client(enforce_csrf_checks=True)` and a scraped token.
  - A test that waits for a missing name imports it inside the test, so the module loads and the test fails for its stated reason.
- **Pages follow DESIGN.md and AGENTS.md's dashboard recipe:** no disabled controls, no colour for state, no new script, and tokens only. No new address is added.
- **Migrations are additive and safe on a live Postgres table,** and the release on main keeps serving while they run. Rolling back is the code alone.
- **Never name a plan and then a figure on one line** in a file `drop_core/tests_fix_docs.py` scans, which includes `dashboard/billing_page.py`, every template and the API docs. Seat figures reach pages from `PLANS`, through the views.
- AGENTS.md invariants hold, notably 3, 4 (published clients keep working), 8 (no edits to `backend/legal/documents/*`) and 9 (no package versions). No new runtime dependencies.
- Words are the ones in this plan's Words section, verbatim, and any others follow AGENTS.md's Voice section: complete sentences, semicolons only between independent clauses, no em dashes, no exclamation marks, and American spelling.
- **This Mac is shared and has run out of memory before.** Run heavy checks one at a time, and check `memory_pressure | tail -1` first. Never signal a process you did not start. Never contact Stripe, production or GitHub from a task.
- Commits: `Veilfile <support@veilfile.com>`, `TZ=UTC git commit`, plain-sentence subjects, no attribution lines of any kind, no personal names. Branch: `seats/billing`, from the latest `origin/main`. Pushing, opening the pull request and merging each need the owner's go-ahead, through Muse's account.

## Review Focus

1. **A webhook drops a team to Free while its members upload, make keys, poll device logins and use OAuth.** No upload or key mint that takes the workspace lock after the drop commits succeeds, and nothing deadlocks. When `invoice.paid` restores the plan, every key, token and grant works again with nothing made anew, apart from the device codes of decision 1. Tests: `test_a_signed_webhook_bars_and_a_paid_invoice_restores` (Task 1), and Task 8 on Postgres by lock and commit order.
2. **A one-person account on any plan, with the switch on, answers exactly as before,** with the same number of queries on the key, upload URL, upload and MCP list paths. Test: `test_a_one_person_account_is_never_barred_and_answers_as_before` (Task 1).
3. **A switch to a smaller plan racing a join:** after both commit, never more people than the plan's seats. Tests: `test_the_count_is_read_after_the_lock` (Task 3), and Task 8 with real threads.
4. **After a transfer, Stripe's email and the card notice follow the new administrator,** and a Stripe failure never undoes the transfer. Tests: `test_a_failed_email_move_keeps_the_transfer_and_the_portal_repairs_it` (Task 4).
5. **A barred member can still leave, revoke their own files, "only me" included, and disconnect apps, and the administrator still sees their team-shared files.** Test: `test_a_barred_member_keeps_the_ways_out` (Task 2).

---

### Task 1: The bar on agent credentials and writes

**Files:**
- Modify: `backend/drop_core/models.py` (`is_barred`, `BARRED`, `APIKey.verify`), `backend/drop_core/oauth.py` (`resolve_oauth_credential`), `backend/drop_core/views.py` (`PendingUploadView.put`, `ERROR_STATUS`, `ERROR_MESSAGES`), `backend/drop_core/services.py` (`upload_artifact`), `backend/drop_core/key_caps.py` (`create_key_within_cap`, `active_keys`), `backend/drop_core/account.py` (`KEY_NOT_ALLOWED_WORDS`), `backend/drop_core/device.py` (`device_approve`, `_render_consent`, `_redeem`), `backend/templates/device/device_consent.html`, `docs/API.md`, `backend/docs/pages/api.md`
- Modify (fixtures only): `backend/drop_core/tests_members.py` (`PersonReadsTests`, `CredentialPersonTests`, `UploaderTests`), `backend/drop_core/tests_allowances.py` (`KeyAllowanceTests`), `backend/drop_core/tests_one_place.py` (`DeviceLookupTests`, `LookupTests`), `backend/drop_core/tests_file_access.py` (`PrivacyMatrixTests`, `FileAccessTests`), and any other test the full run turns red because its team sits on Free
- Test: `backend/drop_core/tests_bar.py` (new)
- Modify: `backend/pages/tests_site_rules.py` (`READING_TEST_MODULES` gains `drop_core.tests_bar`)

**Interfaces:**
- Produces in `models.py`:
  - `is_barred(member, workspace=None) -> bool`. True only when `member` is not None, `member.role == Membership.MEMBER`, `member.removed_at is None`, and the workspace's `plan == Workspace.PLAN_FREE`. The workspace is `workspace` when given, else `member.workspace`. It tests the role first, so an administrator row costs no read of the workspace.
  - `BARRED = Q(member__role=Membership.MEMBER, workspace__plan=Workspace.PLAN_FREE)`, for `APIKey` querysets.
- Produces the code `team_on_free`, raised as `ValueError("team_on_free")` by `upload_artifact` and as `KeyNotAllowed("team_on_free")` by `create_key_within_cap`. It answers 403, with the upload sentence on uploads and upload URLs, and the key sentence on keys and device polls.
- Produces in `key_caps.py`: `active_keys(workspace)` leaves out the keys of member rows while `workspace.plan == "free"`.

- [ ] **Step 1: Record the query counts on `main`** for a one-person account on each plan: `APIKey.verify`, an upload URL PUT, `upload_artifact` through REST, and the MCP `list_artifacts` call. Write them into the test below as literals.
- [ ] **Step 2: Write the failing tests in `drop_core/tests_bar.py`.**

  Use `team_of` from `drop_core.tests_teams` and `second_member` from `drop_core.tests_members`, with `override_settings(TEAM_SEATS=True)`. Put the team on Team, and drop it with `Workspace.objects.filter(pk=...).update(plan="free")` where a test does not need a webhook.

  - `test_a_barred_members_key_and_token_are_refused_and_untouched`: `APIKey.verify` of Grace's key and `resolve_oauth_credential` of her token return None. The key's `is_active`, her row's `removed_at` and her OAuth tokens are unchanged.
  - `test_access_returns_with_the_plan_and_nothing_is_remade`: after `update(plan="team")`, the same key verifies and the same token resolves.
  - `test_a_signed_webhook_bars_and_a_paid_invoice_restores`: with `FixBillingBase`'s `post_event` and `patch_stripe`:
    - `invoice.payment_failed` with Stripe reporting `past_due` keeps Team, and Grace's key verifies.
    - `customer.subscription.updated` to `unpaid` makes the team Free, and her key is refused.
    - `invoice.paid` with `active` restores Team, and the same key verifies.
    - `customer.subscription.deleted` bars her again.
  - `test_the_administrator_of_a_free_team_keeps_access`: with the team on Free, Ada has no key and Grace has two active keys. Ada makes Free's one key, `active_keys(team)` counts only Ada's, and Ada's upload stores a file.
  - `test_a_one_person_account_is_never_barred_and_answers_as_before`: for an account alone on each plan, `is_barred` is False, and each path in Step 1 runs in the recorded number of queries.
  - `test_a_barred_member_is_told_about_the_bar_before_the_caps`: with Free's monthly uploads used, Grace's session upload answers 403 `team_on_free`, not `monthly_cap_reached`.
  - `test_an_upload_waiting_on_the_lock_sees_the_drop`: patch `drop_core.services.Workspace.objects.select_for_update` so the row it returns is on Free while the member row passed in says Team. `upload_artifact` raises `team_on_free`, and both counters are back where they were.
  - `test_a_session_upload_answers_team_on_free`: a REST POST as Grace's session answers 403 with `"code": "team_on_free"` and the sentence. The MCP `upload_artifact` call answers the same sentence.
  - `test_an_upload_url_is_refused_and_left_unused`: the PUT answers 403 `team_on_free`, and the URL's `used` stays False. After `update(plan="team")`, the same URL stores the file.
  - `test_a_dead_credentials_url_still_answers_404`: on a Free team, a URL whose key was revoked answers 404 and is marked used.
  - `test_a_suspended_free_team_answers_workspace_suspended`: suspension comes first.
  - `test_a_barred_member_cannot_make_a_key`: `create_key_within_cap` raises `team_on_free`. The dashboard Keys POST shows the key sentence, and the REST key POST answers 403 with the code.
  - `test_a_role_read_under_the_lock_decides`: Grace's row is promoted to administrator by an `update()` after her `Membership` object was loaded. She can make a key on a Free team.
  - `test_a_device_login_is_refused_before_anything_is_written`: Grace's device page shows the device sentence and no Approve button, and her approve POST shows the sentence and leaves the code pending. A poll of a code she approved before the drop answers `access_denied` with the key sentence.
  - `test_removal_leaving_revoking_and_links_ignore_the_plan`: on a Free team, Ada removes a member, Grace leaves, a barred member revokes her own file, and her public link still serves.
- [ ] **Step 3: Run them and see them fail.**

  Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_bar`
  Expected: FAIL. Tests that import `is_barred` fail with ImportError inside the test, and the rest fail because the key still verifies.
- [ ] **Step 4: Add `is_barred` and `BARRED`, and exclude `BARRED` in `APIKey.verify`** inside its existing query, keeping its query count.
- [ ] **Step 5: Refuse in `resolve_oauth_credential`,** after its suspension test, from the row that `current_membership(..., reopen=False)` already returns with its workspace.
- [ ] **Step 6: Refuse in `PendingUploadView.put`,** right after `_minting_credential_active` passes, with `is_barred(pending.member, pending.workspace)`. Answer 403 `team_on_free` without marking the URL used.
- [ ] **Step 7: Refuse in `upload_artifact`.**
  - **Early:** add an early refusal from the member row passed in, before the caps.
  - **Under the lock:** add `"plan"` to the locked read's `.only(...)`, and replace the membership `.exists()` with a read of the row's `role`. The read keeps the `member_removed` refusal, and refuses `team_on_free` when the locked plan is Free and the role is member.
  - **The release list:** the upload URL's release list stays `("content_not_allowed", "member_removed")`, so a `team_on_free` refusal releases the claim.
  - **Codes and docs:** add `team_on_free` to `ERROR_STATUS` (403) and `ERROR_MESSAGES`, and its rows to `docs/API.md` (the error table and the upload codes line) and `backend/docs/pages/api.md`.
- [ ] **Step 8: Refuse in `create_key_within_cap`** under its lock, reading the role instead of the `.exists()`. Add the code to `KeyNotAllowed`'s docstring, to `KEY_NOT_ALLOWED_WORDS`, and to the REST key rows in both API docs, and filter `active_keys`.
- [ ] **Step 9: Refuse a barred member's device login.**
  - In `device_approve`, test `is_barred` right after the email gate, before any replacement branch, and answer with the device sentence.
  - Give `_render_consent` a `barred` flag that replaces the fieldset and Approve in `device_consent.html` with the same sentence.
  - Map `team_on_free` in `_redeem`'s `KeyNotAllowed` branch to the key sentence.
- [ ] **Step 10: Fix the fixtures** in the existing tests named above: put their team on Team before they use a member's credential.
- [ ] **Step 11: Run the tests and see them pass.** Then run, one at a time: `drop_core.tests_json_answers`, `drop_core.tests_teams`, `drop_core.tests_removal`, `drop_core.tests_members`, `drop_core.tests_allowances`, `drop_core.tests_one_place`, `drop_core.tests_file_access`, `drop_core.tests_device_pages`, `drop_core.tests_fix_docs`, `drop_mcp`. Last, run the full backend suite once, serially, because other fixtures may sit on Free, and fix any such fixture as in Step 10.
- [ ] **Step 12: Commit** "A member of a team on Free is refused on every credential and write, and nothing is stopped, so access returns with the plan".

### Task 2: A barred member's pages, and the administrator's

**Files:**
- Modify: `backend/drop_core/file_access.py` (`files_visible_to`, `file_details`, `set_visibility`), `backend/drop_core/teams.py` (`team_summary`, `too_few_seats`), `backend/dashboard/overview_page.py`, `backend/dashboard/keys.py`, `backend/dashboard/members.py`, `backend/dashboard/team.py`, `backend/dashboard/views.py` (`billing`), `backend/dashboard/billing_page.py` (`page_context`)
- Modify (templates): `backend/templates/dashboard/overview.html`, `keys.html`, `members.html`, `team_administrator.html` and `billing.html`
- Test: `backend/dashboard/tests_bar_pages.py` (new), added to `READING_TEST_MODULES`

**Interfaces:**
- Consumes: `is_barred` (Task 1).
- Produces in `file_access.py`:
  - `files_visible_to(member)` drops its team-shared branch when `is_barred(member)`. A barred member then lists their own files here and those left under closed rows.
  - `file_details` gives `can_change_visibility` False for a barred member.
  - `set_visibility` puts the bar in its `UPDATE`'s filter, as a condition on the member's role and the workspace's plan, so a stale request changes nothing.
- Produces in `teams.py`: `too_few_seats(workspace, plan=None) -> tuple[int, int] | None`. It gives `(people, seats)` when the team's current people outnumber the seats of `plan`, which is the workspace's own plan when None. It gives None when that plan's seats are unlimited, when the team fits, and when the switch is off, which it checks before any query. Task 3 uses it too.
- Produces in `teams.py`: `team_summary(member)` gains the following, each empty with the switch off:
  - `barred: bool`, for a member
  - `others_barred: int`, the other current people, for an administrator of a Free team
  - `over_seats: {"people", "seats", "plan"} | None`, for an administrator of a paid team, from `too_few_seats`

- [ ] **Step 1: Write the failing tests.**
  - `test_a_barred_member_sees_only_their_own_files`: Files lists Grace's "only me" and team-shared files, and not Lin's team-shared file. The visibility control is absent, and a forged visibility POST changes nothing. Revoke works on both of hers.
  - `test_the_administrator_still_sees_a_barred_members_team_files`, and can revoke them.
  - `test_a_barred_member_keeps_the_ways_out`:
    - Leave works.
    - Apps lists and disconnects.
    - Keys lists and revokes her key, and shows the Keys sentence instead of the form and of the Get connected section.
  - `test_a_barred_members_overview_says_so`: the notice is the Words sentence with Ada's address, there is no Get connected section, and the Leave link is there.
  - `test_the_administrator_of_a_free_team_is_told_on_billing_and_members`: Billing names the count of other people, and Members carries its sentence.
  - `test_an_over_seats_paid_team_is_told_on_billing_and_members`: six people on Team, so both pages carry the over-seats sentence.
  - `test_the_accept_page_says_the_team_is_on_free`: `team_administrator.html` carries the sentence after its first paragraph.
  - `test_the_new_helpers_read_nothing_with_the_switch_off`: `too_few_seats` and the new `team_summary` fields under `assertNumQueries(0)`.
  - `test_a_free_one_person_account_sees_no_change_with_the_switch_on` and `SwitchOffTests` stay green.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Change `files_visible_to`, `file_details` and `set_visibility`.**
- [ ] **Step 4: Add `too_few_seats` and the summary fields.** Then add the notices and hidden controls in the views and templates, with the Words section's sentences, and no whitespace with the switch off.
- [ ] **Step 5: Run the tests,** then, one at a time: `dashboard`, `drop_core.tests_file_access`, `drop_core.tests_teams`, `drop_core.tests_fix_docs`.
- [ ] **Step 6: Commit** "A member of a team on Free sees and revokes only their own files and can leave, and the administrator is told what the team needs".

### Task 3: The seat check, and billing re-reads the administrator under the lock

**Files:**
- Modify: `backend/drop_billing/account.py` (`checkout`, `_ensure_customer`, `switch`, `_switch`), `backend/dashboard/billing_page.py` (`refusal`, `not_enough_seats_sentence`, the plan options), `backend/dashboard/views.py` (`billing_switch`, `billing_checkout`), `backend/templates/dashboard/billing.html`, `backend/templates/dashboard/billing_switch.html`, `docs/API.md`, `backend/docs/pages/api.md`
- Test: `backend/drop_billing/tests_seats.py` (new, subclassing `FixBillingBase`), added to `READING_TEST_MODULES`

**Interfaces:**
- Consumes: `teams.too_few_seats(workspace, plan)` (Task 2).
- Produces in `account.py`:
  - 409 `not_enough_seats` with `plan`, `people` and `seats`.
  - Today's `_administrator_only()` when the caller's row is no longer the open administrator row under the lock.
  - `_ensure_customer(stripe_mod, workspace, user, plan, replace="")`: under its lock, before its early return, it re-reads the administrator and checks the seats. It raises a private `_Refused(answer)` that `checkout` catches around each of its three calls. It reads the new customer's email from the locked team's administrator row.
  - `switch` re-reads the administrator directly after `select_for_update()`. `_switch` checks the seats only when `plan != workspace.plan`, before `Subscription.modify` and after the branches that refuse past due and multiple subscriptions.
- Produces in `billing_page.py`:
  - `not_enough_seats_sentence(body) -> str`, from the body's `plan`, `people` and `seats`, with `excess = people - seats`.
  - `refusal()` answers `(409, "not_enough_seats")` with it, logs at WARNING, and shows no Reference.
  - The plan options carry, for a plan with too few seats, the sentence in place of the button.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_switch_below_the_people_is_refused_before_stripe`: six people on Team Plus. A switch to Team answers 409 `not_enough_seats` with `plan` "team", `people` 6 and `seats` 5, and neither `Subscription.modify` nor `Invoice.void_invoice` is called.
  - `test_a_switch_to_enough_seats_and_an_interval_change_go_through`, including six people on Team switching from monthly to yearly.
  - `test_waiting_invitations_do_not_count`.
  - `test_a_checkout_below_the_people_is_refused`: seven people on Free, with and without an existing customer. Checkout of Team answers 409, and `checkout.Session.create` is not called.
  - `test_the_count_is_read_after_the_lock`: with `CaptureQueriesContext`, the people count in `switch` runs after the workspace SELECT that opens its transaction.
  - `test_a_former_administrator_cannot_create_the_customer_or_switch`: Ada's row is demoted by an `update()` after `current_membership` returned it. Checkout and switch answer `_administrator_only()`'s body, and neither `Customer.create` nor `Subscription.list` is called inside the lock.
  - `test_the_new_customer_gets_the_current_administrators_address`, and a one-person account's `Customer.create` gets exactly `email=user.email or None`.
  - `test_the_billing_page_shows_the_seat_sentence_instead_of_the_button`, and the refusal sentence, with no Reference, after a refused POST.
  - `test_one_person_billing_answers_do_not_change_with_the_switch_on`: for an account alone, `status`, `checkout` and `switch` give the same bodies with `override_settings(TEAM_SEATS=True)` as with it off.
  - `test_too_few_seats_reads_nothing_with_the_switch_off`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Add the re-reads and the seat checks,** as the Interfaces say.
- [ ] **Step 4: Add the Billing page's sentences, the refusal branch and the API rows.**
- [ ] **Step 5: Run the tests,** then, one at a time: `drop_billing`, `dashboard.tests_billing`, `dashboard.tests_annual_billing`, `drop_core.tests_fix_docs`.
- [ ] **Step 6: Commit** "A plan with fewer seats than the team has people is refused, and billing re-reads the administrator under the lock".

### Task 4: Billing at a transfer, and the team portal

**Files:**
- Create: `backend/drop_core/migrations/0021_workspace_billing_handed_over_at.py`
- Modify: `backend/drop_core/models.py` (`Workspace.billing_handed_over_at`), `backend/drop_core/admin.py` (read-only), `backend/drop_core/teams.py` (`_accept_once`), `backend/drop_billing/account.py` (`move_billing_email`, `portal`, `_forget_missing_customer`), `backend/config/settings.py` (`STRIPE_TEAM_PORTAL_CONFIGURATION`), `README.md` (its row), `backend/dashboard/team.py` (the accept view), `backend/dashboard/billing_page.py` and `backend/templates/dashboard/billing.html` (the notice), `backend/drop_billing/tests_fix_billing.py` (`patch_stripe` gains `customer_modify`)
- Test: `backend/drop_billing/tests_handover.py` (new, subclassing `FixBillingBase`), added to `READING_TEST_MODULES`

**Interfaces:**
- Produces: `Workspace.billing_handed_over_at = models.DateTimeField(null=True, blank=True)`. `_accept_once`'s `update()` sets it when `stripe_subscription_id` is not empty.
- Produces in `account.py`: `move_billing_email(workspace) -> bool`. It is True when Stripe accepted the address of the team's open administrator row, or when the workspace has no customer. It is False, logged with the exception's class only, when Stripe fails or is not configured. It makes no call when it is not configured.
- `portal(user)`:
  - While `billing_handed_over_at` is set, it calls `move_billing_email` first. It clears the field with `filter(pk=..., billing_handed_over_at=<value read>).update(billing_handed_over_at=None)`, only when that call and `billing_portal.Session.create` both succeed.
  - It passes `configuration=settings.STRIPE_TEAM_PORTAL_CONFIGURATION` only when the setting is set, the switch is on, and the team has other people.
- `_forget_missing_customer` clears `billing_handed_over_at` in its save.

- [ ] **Step 1: Write the failing tests.**
  - `test_the_transfer_moves_stripes_email_after_the_commit`: `Customer.modify` is called once with the new administrator's address, outside any transaction, by the `connection.savepoint_ids` pattern in `drop_billing/tests_account.py`.
  - `test_a_failed_email_move_keeps_the_transfer_and_the_portal_repairs_it`:
    - The transfer stands, and the log line names the class only.
    - The next `portal` calls `Customer.modify`, then clears the field.
    - When Stripe is not configured, no call is made and the field stays.
  - `test_a_second_transfer_keeps_its_own_marker`: a newer value written between the portal's read and its clear survives.
  - `test_billing_follows_the_new_administrator`: the new administrator's `portal` and `switch` work, and the former administrator's answer `_administrator_only()`. `stripe_customer_id` and the `Subscription` row are unchanged.
  - `test_a_team_with_no_subscription_moves_nothing_and_shows_no_notice`.
  - `test_the_new_administrator_is_asked_to_check_the_card_until_manage_billing`. The former administrator gets the member sentence, and the notice goes when `_forget_missing_customer` runs.
  - `test_the_team_portal_configuration_is_passed_only_for_a_team`: set, with other people and the switch on, it is passed. A one-person portal makes the same single call as on `main`.
  - `test_the_migration_adds_a_nullable_column_only`: `sqlmigrate` shows one `ADD COLUMN` with no default.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Add the field, the migration and the admin's read-only field.** Then set the field in `_accept_once`.
- [ ] **Step 4: Add `move_billing_email`, its call in the accept view after the commit, the repair and the configuration in `portal`, and the clear in `_forget_missing_customer`.** Then add the setting and its README row.
- [ ] **Step 5: Add the notice.**
- [ ] **Step 6: Run the tests,** then, one at a time: `drop_billing`, `drop_core.tests_transfer`, `dashboard`, `drop_core.tests_fix_docs`.
- [ ] **Step 7: Commit** "At a transfer, Stripe's email follows the new administrator after the commit, and Billing asks them to check the card".

### Task 5: A payment for a workspace nobody administers is reported

**Files:**
- Modify: `backend/drop_billing/views.py` (`_sync_workspace`), `backend/drop_billing/management/commands/sync_stripe_billing.py`
- Test: `backend/drop_billing/tests_fix_billing.py` (a new class on `FixBillingBase`)

**Interfaces:**
- Produces an ERROR log line, "billing: workspace %s has a paid subscription %s and no current administrator; reconcile it by hand". It is written under the lock after `_apply_subscriptions`, when the plan applied is paid and no open administrator row exists, with a plain `Membership` `.exists()`. It names ids only.

- [ ] **Step 1: Write the failing tests.** `test_a_checkout_paid_after_its_payer_joined_a_team_is_applied_and_reported`: a signed `checkout.session.completed` for a workspace whose administrator row a real join closed. Then the same for the command.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Add the check.**
- [ ] **Step 4: Run** `drop_billing`.
- [ ] **Step 5: Commit** "A paid subscription on a workspace nobody administers is applied and reported for staff".

### Task 6: The docs and the site checks

**Files:**
- Modify: `AGENTS.md`:
  - "People inside an account": billing follows the administrator, the bar and what it keeps, and the seat check. This includes the lines that say billing is unchanged and that PR D owns the rest of billing.
  - The admin bullet, for the new read-only field.
  - The Site checks paragraph, for the new fixtures and checks.
- Modify: `site-tests/make_dashboard_fixtures.py`:
  - a Free workspace `site-dash-lapsed`, administered by the fixture user `site-dash-lapsed-admin`, with the barred member `site-dash-barred`
  - a Team workspace just handed over, with fake Stripe ids written to the rows and `billing_handed_over_at` set, administered by `site-dash-heir`
- Modify: `site-tests/run_site_checks.py`: a new `dashboard_bar_problems` in `run_dashboard`'s list of checks, run at 375 and 1280, in Paper and Ink, with motion on and off, with axe, no sideways scroll and no console error. It opens:
  - the barred member's Overview, Files and Keys, and checks their sentences, that there is no key form, no Get connected section and no visibility control, and that the Leave link is there
  - the lapsed administrator's Billing and Members, and checks their sentences
  - the heir's Billing, and checks the card sentence

  No Stripe variable is set, the fixtures are made afresh before each run, and the checks wait on the page, never with `wait_for_timeout`.
- Test: `pages.tests_site_parts`, `drop_core.tests_fix_docs`, `pages`

- [ ] **Step 1: Write the AGENTS.md changes.**
- [ ] **Step 2: Add the fixtures and the checks.**
- [ ] **Step 3: Run,** one at a time: `pages`, `drop_core.tests_fix_docs`.
- [ ] **Step 4: Commit** "The docs and the site checks cover the bar on Free and billing at a transfer".

### Task 7: The browser part and the look (controller)

- [ ] **Step 1: The full backend suite once,** serially.
- [ ] **Step 2: The site checks' dashboard part with the switch on,** on fresh fixtures, on ports 8777 to 8779.
- [ ] **Step 3: Screenshots,** at 375 and 1280, in both themes, and look at each one:
  - a barred member's Overview, Files and Keys
  - the lapsed administrator's Billing and Members
  - the over-seats Members page
  - a refused switch
  - the heir's Billing

### Task 8: Rehearse on Postgres

Like C3's, with scripts in `~/dev/veilfile-rehearsal-kit/prd`, starting from `prc3-recheck`'s. The session scratchpad's `seats-d-map/rehearsal-billing.md` describes the fake Stripe and the shim.

- **The cluster:** a throwaway cluster on 127.0.0.1:8779, with `log_lock_waits=on`, `deadlock_timeout=200ms` and `track_commit_timestamp=on`.
- **The servers:** the previous release on 8777 and the branch on 8778, both in production mode. Commit timestamps are the oracle for "after the drop".
- **The fake Stripe:** a stdlib HTTP server. It answers what the branch calls (subscription lists, `Subscription.modify`, `Customer.create`, `Customer.modify`, checkout and portal sessions with https URLs, invoice void), with knobs for a delay and a failure. It records every call in a journal.
  - It listens on 127.0.0.1:8777 only in Steps 4 to 6, when the previous release is not running.
  - A settings shim, chosen through the kit's `XENV` as `prc3/rehearsal_prod.py` does, points `stripe.api_base` at it, sets `max_network_retries` to 0, and stops the server if `api_base` is not on 127.0.0.1.
- **Stripe settings:** the servers run under `env -i`, with `STRIPE_SECRET_KEY=sk_test_rehearsal`, `STRIPE_WEBHOOK_SECRET=whsec_rehearsal`, fake price ids and `HTTPS_PROXY` set to a closed local port. Nothing contacts Stripe.

- [ ] **Step 1: Seed at C3's volume.**
- [ ] **Step 2: Migrate under load.** Record `0021`'s wait behind a workspace row held for 5 s, as a webhook holds it across a Stripe call, and how long readers queue. Confirm that the previous release serves on the migrated schema.
- [ ] **Step 3: The switch off,** byte for byte against the previous release.
- [ ] **Step 4: Drop a team and restore it under load.**
  - **The drop:** a signed webhook drops a team to Free while its members upload, make keys, poll device logins and use OAuth, and its administrator uploads.
  - **The restore:** `invoice.paid` restores it.
  - **Checks:** by lock and commit order, no member's upload or key that took the workspace lock after the drop's commit succeeds. Everything works after the restore, apart from the device codes of decision 1, and nothing deadlocks.
  - **The second drop path:** also run a missing customer.
- [ ] **Step 5: Race the seats and the address.**
  - A switch to a smaller plan races joins in threads: never more people than seats after both commit.
  - A checkout races a transfer: never a customer with the former administrator's address, as the journal shows.
- [ ] **Step 6: Fail and repair a transfer.** A transfer runs with the fake failing `Customer.modify`, then the portal repairs it. A checkout is paid after its payer joined a team, and the ERROR is logged.
- [ ] **Step 7: The full suite on Postgres, on `main` and on the branch.** Bar: the same failures, by test name.
- [ ] **Step 8: An attack on the results, then cleanup.**
- [ ] **Step 9: Stop for the owner** with the results, the rulings, the screenshots and the deploy notes:
  - `0021` adds a nullable column to the workspace table and can wait behind a webhook's Stripe call, as `0018` did, so deploy at a quiet time.
  - Rolling back is the code alone, because the previous release never reads the column.
  - The new `Customer.modify` call needs the owner's word before the deploy.
  - A staff edit of a plan, or a missing customer, bars a team's members as a drop does.

## Changed during the build

- **The seat check reads the plan Stripe's subscription is on,** not the workspace's own plan, which can lag a webhook. So a change of interval on the plan the subscription is already on is never refused, and a stale record cannot skip a real downgrade. An automated security review and the whole-branch review found this.
- **A transfer marks the handover whenever the team has a Stripe customer,** not only with a subscription, and the card notice shows only while there is also a subscription. Checkout repairs the email as the portal does, so a lapsed team's new administrator is never left with the former administrator's address after one failed move. Checkout repairs only after it has passed the live-subscription check, so a refused checkout leaves the notice in place.
- **A portal or checkout request re-reads the administrator after Stripe returns the session,** and gives no link to someone the role has just left. The attack showed a former administrator's request in flight at a transfer getting a portal link, which can change the card or cancel.
- **Moving Stripe's email re-reads the administrator after each call,** and moves it again for the current one, at most three moves in all, so a slow first transfer's call cannot overwrite a second's.
- **With the switch off, Billing and the switch confirmation make the queries `main` made,** because the views reach the member only with the switch on.
- **A status refresh that applies a paid plan to a workspace nobody administers reports it,** as the webhook and the sync command do.
- **On Billing, a plan with too few seats is named once, below the monthly buttons,** and its buttons are left out of both rows; a sentence in the row of buttons read as a button.
- **A barred member's Overview shows their own use and not the team's,** whose key meter left out their key, and their Files caption is the one-person caption.
- **The switch off is not byte-identical in one way that only a team can meet:** a member of a team on Free is barred whatever the switch says (decision 1), and nothing makes such a member while the switch is off. Otherwise 823 responses and 37 Stripe calls matched the previous release, and one-person billing over HTTP matched it with the switch on and off.
- **`0021` waits for every transaction that holds a lock on any workspace row,** including a webhook's Stripe call, and requests queue behind it: 3.5 s behind a 5 s hold, with no errors. Deploy at a quiet time.
- **Left as they are:**
  - Staff can still edit a plan in the admin, and a hand-set Free bars a team's members (decision 15).
  - `sync_stripe_billing` reports a workspace nobody administers only with `--apply`.
  - A refused checkout after its session was made leaves an unused Stripe session, which expires on its own.

## Carried to D2, E and F

These are not part of this plan.

- **D2 must merge before F turns the switch on** (question 1). D2 does three things:
  - When a paid individual joins, it ends their plan with the unused part credited, after the owner's test-mode check.
  - It ends a subscription paid for a workspace nobody administers (decision 12).
  - It settles how the Terms' refund and cancellation sentences meet an immediate ending.
  The D2 plan, `docs/superpowers/plans/2026-11-12-team-seats-pr-d2-paid-join.md`, does the first two, and it leaves the third to the owner in F, through its question 4.
- **The C2 and C3 carry lists for E and F stay open as written there.** That includes agents choosing visibility, the upgrade sentences a member reads, the Plans page's seats, the sending service and `SITE_INVITES_PER_DAY`, the relay timeout, removing `dashboard/golden/` and `SwitchOffTests`, PRODUCT.md, and the Terms and Privacy Policy on invitations, removal, leaving, the transfer, staff deletion and the hold notice.
- **E:** the REST and MCP sentences a barred member's agent reaches, including the 401 of decision 16, and the OAuth consent page for a barred member (decision 3).
- **F:**
  - The Terms and the Privacy Policy cover the bar on Free, and say what the Stripe customer's email does at a transfer, in the owner's words, before the switch turns on.
  - If the owner wants mail at a drop (question 4), it goes through the sending service.
- **Later:**
  - A Stripe customer's email does not follow an administrator who changes their own address, as on `main` for one-person accounts.
  - A cancellation at the end of the period is not shown before it takes effect, so an administrator learns that the team's members are barred only when it happens.
