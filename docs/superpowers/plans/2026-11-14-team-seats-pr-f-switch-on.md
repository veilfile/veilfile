# Team seats F: Turning the Switch On, Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement F2 task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Team seats go live on veilfile.com: the owner's Terms and Privacy words are in place, the last code a launch needs is merged, one rehearsal on Postgres passes, and the owner sets `VEILFILE_TEAM_SEATS=on` in Render.

**Architecture:** F is five slices, each of which leaves something the owner can read, test or run. F1 is words outside the repository. F2 is one small code pull request whose changes all take effect only when the switch is on, apart from one read-only command and one billing fix. F3 is a local rehearsal. F4 is the owner's checklist and the switch itself. F5 removes the switch once it has stayed on.

**Tech Stack:** Django 6.1.1, stripe-python 11 (tests patch it), Postgres 16 in the rehearsal, Render.

**Spec:** `docs/superpowers/specs/2026-11-05-team-seats-design.md`, section 4, calls 4 and 5: "The Plans page states seats only once this is live" and "Nothing here ships before those words are approved." The carry lists are in the C2, C3, D, D2, E1, E2 and E3 plans.

## Questions for the owner

Each has a recommendation, and the plan proceeds on it.

1. **The slices.** F1 to F5 as below, in that order (recommended).
2. **Mail.** The C2 plan carried a move to a dedicated sending service before the switch turns on. Recommended now: launch on today's mail with the caps as they are, 30 invitations a day per team and 100 a day for the site, and move to a sending service before either cap is raised. The caps bound how many strangers can be mailed, and a service costs money each month. The other choice is a service first, which you pick and pay for.
3. **Stripe checks before the switch.** Recommended: only the checks whose answers change what Veilfile promises or charges. They are the `again` result, who bears the tax on a credit under Managed Payments, and that the live restricted key has Write on Subscriptions and Invoices. F2's void rule makes the already-void question moot. Every other check in D2's question 6 ends at worst in `plan_not_ended`, which refuses the join and leaves the person as they were. They are run after launch only if support sees that refusal.
4. **Placing the legal words.** Agents do not edit `backend/legal/documents/`. Recommended: once you approve F1's drafts, you paste them into the two files on a branch I prepare. I compare the result with the approved drafts, run the legal pages' tests, and commit and open the pull request through Muse on your go-ahead.
5. **The switch.** Recommended: F2 declares `VEILFILE_TEAM_SEATS` in `render.yaml` with `sync: false`, as the other dashboard settings are declared, so merging never turns it on. You set it to `on` in Render's dashboard at F4, and a docs pull request that states seats merges right after.
6. **A sentence about a waiting credit** on Leave, Overview or Billing (D2's carry). Recommended: after launch, because it needs a Stripe read or a new field, and the Terms already say where the credit goes.
7. **The words** below.

## The slices

- **F1, the words.** I draft the Terms and Privacy wording into `~/dev/veilfile-legal-drafts/`, one file each, in the form of `terms-joining-a-team.md`: where each sentence goes, and the code it describes. The topics are seats; what an administrator can see and do; invitations, and how long an invited address is kept; removal, leaving and the transfer, with the Stripe customer's email at a transfer; staff deletion of a member's account; the hold notice, which must not promise more than an administrator can work out; the bar on members when a team drops to Free; and joining from a paid plan, with that file's three open sentences. I also draft support's steps for refunding a checkout paid after a join, for `veilfile/ops`. You decide every sentence.
- **F2, the code** (tasks below, branch `seats/ready-for-switch`).
- **F2b, keep and forget** (`2026-11-14-team-seats-pr-f2b-keep-and-forget.md`). The sweep deletes invitations 30 days after they end, and it keeps held files until staff release them.
- **F3, the rehearsal.** On Postgres, never port 5432, from a copy migrated from the previous release, with the kit in `~/dev/veilfile-rehearsal-kit/prd2` and D2's Task 7 steps. With the switch on, it checks the following:
  - Every existing account is a team of one, with its dashboard, keys, files, limits and billing unchanged.
  - The Plans page states seats.
  - Both list commands print their zero lines.
  - An invitation, a join from Free, a join from a paid plan against the fake Stripe, leaving, removal and the transfer all work.
  - A drop to Free bars the team's members.
  - The webhook ends a plan that nobody administers.

  Then it turns the switch off again and records what people who have joined a team see. That is the way back if F4 goes wrong, so you read it before F4.
- **F4, your checklist.** Each step is yours. I prepare the commands, and nothing outward happens without your go-ahead.
  1. F1's pull request is merged and live.
  2. F2 is merged, and the Render log shows the deploy.
  3. F3 passed.
  4. Question 3's answers are in, and the `again` result is settled: either the offer's third sentence ships, or the spec's promise changes.
  5. In a Render shell, `python manage.py sync_stripe_billing --list-unadministered` prints only `0 workspace(s) with a paid plan or a subscription id and no current administrator`, and `--list-shared-customers` prints only its zero line.
  6. You set `VEILFILE_TEAM_SEATS=on`, and then `healthz` answers and the Plans page states seats.
  7. You run a short card on production with two addresses of your own.
  8. Then the docs pull request that states seats merges, you request the releases `npm-v0.5.0` and `pypi-v0.5.0`, and I refresh PRODUCT.md, which stays uncommitted.
- **F5, later, once the switch has stayed on.** Remove the switch, `SwitchOffTests`, `dashboard/golden/` and the docs' "with team seats on" qualifiers.

## Words

Voice: complete sentences, semicolons only between independent clauses, no em dashes, no exclamation marks, American spelling.

- **Each plan card, first in its list, with the switch on:** "1 person" (Free), "Up to 5 people" (Team), "Up to 10 people" (Team Plus), "Unlimited people" (Scale). The figures come from `PLANS[...]["seats"]`.
- **On the Plans page, after the sentence on yearly billing, with the switch on:** "On Team, Team Plus and Scale, the administrator invites people by email. Everyone on a team shares its uploads, storage and keys, and the administrator can set how much each person may use."
- **The refund sentence with the switch on,** if you approve F1's sentence 1, the 14-day refund on a join: "If you cancel within 14 days of your first paid subscription, or join someone else's team in that time, the first payment is refunded in full, whether it was monthly or yearly. To ask, write to support@veilfile.com." If instead the credit replaces the refund, the switched-on sentence keeps today's words and adds: "If you join someone else's team, your plan ends at once, and the unused part is credited instead."
- **The new command's zero line:** "0 Stripe customer(s) held by more than one workspace". Each row prints the customer id and the workspace ids, and nothing else.

## Global Constraints

- With the switch off, every page, answer and log line of F2 is what `main` gives today. The void rule runs only at a join, which needs the switch, and the new command is read-only.
- Every plan figure comes from `backend/drop_core/plans.py`, and `drop_core.tests_fix_docs` still passes.
- Agents never contact Stripe, production or GitHub, apart from Muse's push and pull request on the owner's go-ahead. Tests patch the library. Nobody opens a `.env` file or prints a secret value.
- Agents do not edit `backend/legal/documents/`. Commits are made as `Veilfile <support@veilfile.com>` with `TZ=UTC`, with no Co-Authored-By, session link or model name. PRODUCT.md and DESIGN.md stay uncommitted.
- Tests run from `backend/` with `DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test <module>`, one module at a time, and the whole suite once at the end, in the background.

## Review Focus

1. The Plans page, its catalog entry and its structured data are unchanged with the switch off.
2. A `past_due` or `unpaid` join whose latest invoice is already void sends the cancel without a second void, and a failed read of the invoice sends no cancel and ends in `plan_not_ended`, as a failed void does.
3. `--list-shared-customers` writes nothing, refuses to run with any other option as `--list-unadministered` does, and ignores workspaces with no customer id.
4. The refund sentence's 14 days still come from `REFUND_DAYS`.

---

### Task 1: The Plans page states seats with the switch on

**Files:** Modify `backend/pages/plan_cards.py`, `backend/pages/views.py` (the Plans context), `backend/templates/chapters/plans.html` (inside its words block). Test: new `backend/pages/tests_plan_seats.py`.

- [ ] **Step 1: Write the failing tests.**
  - `test_each_card_states_its_seats_first`: under `override_settings(TEAM_SEATS=True)`, `plan_cards()`'s `limits[0]` for free, team, team_plus and scale is the Words line.
  - `test_the_switch_off_cards_are_todays`: with the switch off, no card's limits mention a person.
  - `test_the_page_says_who_can_be_invited`: GET `/plans/` with the switch on contains the Words sentence. With the switch off, it does not.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** `plan_cards()` puts the seat phrase first when `settings.TEAM_SEATS`. The view passes `team_seats`, and the template wraps the sentence in `{% if team_seats %}`.
- [ ] **Step 4: Run `pages` and `drop_core.tests_fix_docs`.** Expected: OK.
- [ ] **Step 5: Commit** "The Plans page states seats when team seats are on".

### Task 2: The refund sentence with the switch on

**Files:** `backend/templates/chapters/plans.html`. Test: `backend/pages/tests_plan_seats.py`. Its words follow your answer to sentence 1 of `terms-joining-a-team.md`.

- [ ] **Step 1: Failing test** `test_the_refund_sentence_names_a_join`: with the switch on, the page has the switched-on refund sentence with `REFUND_DAYS` in it. With the switch off, it has today's sentence. `pages.tests_chapters` 197-205 stay as they are.
- [ ] **Step 2: Run and see it fail. Step 3: Implement. Step 4: Run `pages`.** Expected: OK.
- [ ] **Step 5: Commit** "With team seats on, the refund sentence covers a join".

### Task 3: The void rule (D2 decision 15)

**Files:** `backend/drop_billing/account.py` (the join's void-then-cancel loop, about line 1016). Test: `backend/drop_billing/tests_paid_join.py`, new class `AlreadyVoidTests`.

- [ ] **Step 1: Failing tests,** with the module's fake Stripe given an `Invoice.retrieve`:
  - `test_an_invoice_already_void_is_not_voided_again`: a `past_due` subscription whose latest invoice has status `void` gets no `void_invoice` call and one cancel, and the join completes.
  - `test_a_failed_read_sends_no_cancel`: `Invoice.retrieve` raises a `StripeError`, so no void and no cancel are sent, the join answers `plan_not_ended`, and the WARNING names the ids and the exception's class only.
  - `test_an_open_invoice_is_voided_as_today`: status `open` gets one void, then the cancel.
- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement.** Before a void, retrieve the invoice with `max_network_retries=0`. Skip the void when its status is `void`, and treat a failed read as a failed void.
- [ ] **Step 4: Run `drop_billing`.** Expected: OK.
- [ ] **Step 5: Commit** "A join does not void an invoice that is already void".

### Task 4: `--list-shared-customers` and the switch in `render.yaml`

**Files:** `backend/drop_billing/management/commands/sync_stripe_billing.py`, `render.yaml` (the comment at line 79 becomes an `envVars` entry with `sync: false`). Test: `backend/drop_billing/tests_paid_join.py`, new class `ListSharedCustomersTests`, beside `ListUnadministeredTests`.

- [ ] **Step 1: Failing tests:** two workspaces with one customer id print one row and the count line "1 Stripe customer(s) held by more than one workspace". No sharing prints only the zero line. Workspaces with an empty id are ignored. The option refuses any other option. No Stripe call is made, and no row changes.
- [ ] **Step 2: Run and see them fail. Step 3: Implement,** with the query from D2's carry list (`exclude(stripe_customer_id="")`, `annotate(n=Count("pk"))`, `filter(n__gt=1)`).
- [ ] **Step 4: Run `drop_billing`.** Expected: OK.
- [ ] **Step 5: Commit** "A read-only list of Stripe customers that more than one workspace holds, and the switch declared in render.yaml".

### Task 5: The test card

- [ ] Write `~/dev/veilfile-test-cards/f2-local.md`: the Plans page with the switch off and on, both list commands against the E1 card's database, and the void rule covered by its tests, because it needs Stripe. Each step ends with a **Pass** line.

## Changed during the build

- The refund sentence uses the recommended wording, because the owner had not yet answered sentence 1 of `terms-joining-a-team.md`. It uses the curly apostrophe the page already uses. The other wording is a one-line change and one test string.
- The invoice read keeps the library's retries, because a read is safe to repeat. The cancel keeps `max_network_retries=0`.
- The tests' fake Stripe answers the invoice read in `PaidJoinBase` and adds nothing to the recorded calls, so every existing call-sequence test stands unchanged. The rehearsal kit's fake Stripe must answer `Invoice.retrieve` in F3.
- Workspace ids are UUIDs, so the list command orders each customer's workspaces by when they were made.
- The test card's tool gained `customer` and `uncustomer`, and the card was dry-run on a throwaway database.

## Later, from F2's review

- The component catalog shows seats when the switch is on, as the Plans page does. `plans.html` has two adjacent `{% if team_seats %}` blocks that could be one. An invoice that is neither void nor open, such as a draft, still goes to the void, and a refused void ends in `plan_not_ended`, as D2 allowed.

## Owner decisions raised by F1 (answered 2026-11-14)

- **Invitations:** each one is deleted 30 days after it is accepted, declined, withdrawn or expires. F2b does it (`2026-11-14-team-seats-pr-f2b-keep-and-forget.md`).
- **A team on Free:** its members other than the administrator can still sign in, but only to see and revoke their own files or leave, as the code does. The spec's item 10 settles it over decision 13's "cannot sign in". The Terms say so, and no code changes.
- **Held files:** each one is kept past its expiry until staff release it, and F2b does it. A lawyer approves the policy sentence, since a report to NCMEC may oblige Veilfile to keep a file for a year.
- **The three joining sentences in `terms-joining-a-team.md`:** all go in. Someone who joins within 14 days can ask for the first payment back in place of the credit. A payment that completes after a join is credited, or refunded on request. Unused credit is refunded before staff delete an account.
