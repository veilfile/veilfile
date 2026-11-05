# Team seats: people on Team, Team Plus and Scale

- **Date:** 2026-11-05
- **Status:** approved by the owner on 2026-11-05. The decisions in section 3 are the owner's, from the conversation on 2026-11-05. Section 4 lists the calls I made, which the owner saw and did not overrule.
- **Mockup:** a wireframe the owner approved for its content, not its look, at `.superpowers/brainstorm/47040-1793858362/content/team-first-look.html` (local, git-ignored).

## 1. Intent

Today a Veilfile account is one person. The plan names Team and Team Plus suggest several people, and a review of the Plans page found that a buyer would read them that way. The owner chose to make the names true rather than explain them away.

The first customer the owner pictures is a software development team that works with open-source coding agents such as pi coding agent or oh-my-pi. Those agents run outside the Claude apps, so the team has no access to Claude's private artifacts. Their agents need to hand a screenshot to a person, a user they are helping or a teammate, or to another agent, as a private link that expires. Veilfile already does that. What the team lacks is a way to work as a team: each developer signs in as themselves, each person's agents have their own keys, the team shares one set of limits and one bill, and someone can manage who is on it.

**Success looks like this.** A team lead subscribes to Team, invites four colleagues by email, and each of them signs in with their own work address and connects their agents. The lead can see each person's usage, cap it, and remove a person who leaves, without anyone sharing a login. Nothing changes for an existing customer who never invites anyone.

## 2. Words

The dashboard and the site call it a **team**. An **administrator** manages it, and everyone on it is a **member**; the administrator is a member too. A **seat** is a place for one member. This document uses "team" for what the code calls a workspace.

## 3. Decisions (the owner's)

### People and accounts

1. **Seats are included in the plan's price.** Free has 1 (no team features), Team 5, Team Plus 10 and Scale unlimited, the same numbers as each plan's API keys. An extra person costs nothing; a team that needs more seats than its plan has moves up a plan. There is no charge per person.
2. **One account is one email address, and it belongs to exactly one place.** An account is either on its own or a member of one team. A person who wants a personal account and a work account uses two addresses (for example grace@gmail.com and grace@rivetlabs.dev) and signs out of one to sign in to the other. There is no switcher between places in the dashboard.
3. **Members join by an invitation sent to their email address.** An invitation can go to an address that already has a Veilfile account. If that account accepts, its own plan ends, and the files it already has keep working until they expire.
4. **A team has one administrator: the person who pays.** The administrator can hand the role to another member from the dashboard, for when someone leaves, is promoted or moves to another team.

### Limits

5. **The plan's limits are a hard ceiling for the whole team,** whatever any member's allowance says.
6. **The administrator can set an allowance for each member** for uploads a month, storage and keys, the way Claude Code lets a team cap usage per person. An allowance is the most that member can use, not a share set aside for them. A member with no allowance set can use the whole pool. Example: if one member has no allowance and uses the team's whole storage, a member with a 5 GB allowance gets no more, even though they are under their own allowance.
7. **The plan's key limit is one number shared by the whole team.** Team's 5 keys are 5 keys in all, not 5 per person.

### Files

8. **Each file is visible either to its uploader only ("only me") or to the team.** Visibility decides only whether the file appears on a teammate's Files page. The link itself works for anyone it is sent to, until it expires, as it does today.
9. **The administrator chooses one of three settings:** each member decides, files are always visible to the team, or files are always visible only to their uploader. When members decide, new files start as "only me".
10. **"Only me" means only that member.** The administrator cannot see the names or contents of a member's "only me" files, in the dashboard or anywhere else. The administrator does see each member's usage counts, and "only me" files count toward the member's allowance and the team's limits, so the meters stay honest.
11. **Members can revoke their own files. The administrator can revoke any file shared with the team,** since those are the only files the administrator can see.

### Leaving, and failed payment

12. **When the administrator removes a member, that member's keys stop working at once.** The administrator chooses what happens to the member's files: revoke all of them now, including the "only me" files (without seeing their names), or let them keep working until they expire. Until they expire, they go on counting toward the team's storage.
13. **If the team's payment fails and the team drops to Free, members are shut out** until the team is back on a paid plan: they cannot sign in to the team, and their keys are refused. Links already shared keep working until they expire, as the Terms promise after a downgrade. The administrator keeps access, as Free's one person, to fix the billing. When the team is back on a paid plan, everyone's access returns.

### The dashboard

14. **A member's first view** shows the team they are on, how many members it has, their own allowance with what they have used, and the team's totals beside it.
15. **The administrator has a Members page:** each member with their allowance and this month's usage, an invitation by email, the visibility setting, removal (with the choice in decision 12), and the transfer of the administrator role.
16. **The pages match the rest of the site.** They use the dashboard's existing pieces (the usage meters, notices and forms) in the typeset style that DESIGN.md describes. The mockup shows content, not appearance.

## 4. Calls I made (shown to the owner, not overruled)

1. **Agents choose visibility too.** The upload API, the MCP upload tools and both CLIs take an optional visibility of "only me" or "team" for each file. It is honored only when the administrator lets members choose; otherwise the team's setting applies, and the response says which visibility the file got.
2. **Distinct errors.** An upload refused by a member's own allowance gets a different error code from one refused by the team's limit, so an agent or a person can tell which limit stopped it and whom to ask.
3. **Today's customers carry over unchanged.** Every existing account becomes a team of one, with its owner as administrator. Nothing about their dashboard, keys, files, limits or billing changes until they invite someone.
4. **The Plans page states seats only once this is live:** up to 5 people on Team, up to 10 on Team Plus, and unlimited on Scale.
5. **The Terms of Service and the Privacy Policy need new wording** about seats, what an administrator can see and do, removal, and failed payment. I draft it; the owner decides it. Nothing here ships before those words are approved.

These calls settle questions the builder check raised (section 9 has the ones only the owner can answer):

6. **Keys record their member.** Members create and revoke their own keys. The administrator sees every key on the team and whose it is, and can revoke any of them, because a key is a credential on the team's plan, not content.
7. **Every credential is checked against its person's membership on every request:** keys, upload URLs, OAuth grants used with the team, and dashboard sessions. Removal ends them. Barring after a drop to Free refuses them without deleting them, so that access can return.
8. **Every file records its uploader,** including files that arrive through an upload URL or an MCP call: the member whose key or connection made the request. Today some of these checks use the workspace owner (`APIKey.verify`, the upload URL checks in `backend/drop_core/views.py`, `resolve_oauth_credential` in `backend/drop_core/oauth.py`); in a team that would be the administrator, which would break decision 10.
9. **Allowances are applied at upload time with the team's limits,** in the same transaction and for the same calendar month in UTC. An allowance is stored as the administrator set it; after a plan change, the Members page shows any allowance above the new plan's limits as capped by the team's limit.
10. **A removed member's account stays, as its own Free account.** The files left to run out stay theirs to see and revoke until they expire, and count toward the team's storage until then. A barred member can sign in only to see and revoke their own files. Without this, nobody could revoke a removed or barred member's "only me" files.
11. **Each person's account state governs only their own credentials.** Suspending one member, or the administrator, does not bar the rest of the team; acting on the whole team is a separate decision by Veilfile staff.
12. **A pending invitation holds a seat** until it is accepted, declined or expires after 7 days. Invitations are rate-limited on every plan, including Scale. The owner considered a team opened to strangers (for example, a public thread inviting anyone to one Scale account) and chose on 2026-11-05 to keep Scale's seats unlimited, with these rate limits as the guard for now. The pooled limits in decision 5 bound what such a team can use, and abuse handling (section 9, question 5) applies to every file. Because the rate limits carry this weight, the plan states their numbers for the owner to see.
13. **A member can change their email address as today,** with verification. Deleting a member's account works like removal with "revoke all of them now".
14. **Usage counts can reveal a single file.** A member with one upload this month shows a count of 1 and that file's size; decision 10 accepts this.
15. **A member can leave a team on their own,** from the dashboard. Their files keep working until they expire, as with the second choice in decision 12, and their keys stop at once.

## 5. Not in this version

- More than one administrator, or roles other than administrator and member.
- Single sign-on, or joining a team by email domain without an invitation.
- Charging per person, or buying single extra seats.
- An activity log of who did what.
- An account belonging to more than one team, or moving files between accounts or teams.

## 6. What would make this a failure, even if it works

- An administrator, or anyone but its uploader, can list, read or download the names or contents of a member's "only me" files, through any page, API, export or support tool built for this.
- A removed member, or a member of a team that has dropped to Free, can still upload on the team's plan.
- Per-member allowances let a team go past its plan's limits, or a race lets two members together exceed the team's limit.
- Anything changes for an existing single-person customer before they invite someone.
- The new pages look like a different product from the rest of the dashboard.

## 7. Constraints

- **The data today has no people inside an account.** `Workspace` has a single `owner` (`backend/drop_core/models.py`; "One user owns one workspace in MVP"), and `get_or_create_workspace` returns the user's first workspace. An `APIKey` belongs to a workspace and records no person. An `Artifact` points to its workspace and to the key that sent it. Membership, per-member allowances and per-file visibility are all new.
- **Billing belongs to the team.** The Stripe customer and subscription are on the workspace (`Workspace.stripe_customer_id`, `drop_billing.models.Subscription`), so the subscription stays with the team when the administrator role moves.
- **Plan state changes only from Stripe-verified state** (AGENTS.md, invariant 3), including the drop to Free in decision 13.
- **Limits are enforced atomically today.** `upload_artifact` reserves the month's upload with a conditional update and checks storage under a row lock (`backend/drop_core/services.py`). Member allowances must hold under the same concurrency.
- **Every plan figure comes from `backend/drop_core/plans.py`.** A test pins every figure on the Plans page and in the plan copy to it (AGENTS.md, "Pricing changes"), so seat counts belong there.
- **Agents connect with keys.** OAuth sign-in serves only the Claude apps today; open-source agents use keys, and `veilfile login` mints a key that counts toward the key limit.
- **Repository rules hold:** the repository is public and carries no personal names; the legal documents change only with the owner; every word follows AGENTS.md's voice rules; the shell script budget (10,240 B gzipped per file) applies if the dashboard needs script.

## 8. Left to the builder

The data model and its migration, including how today's single owner becomes a team of one; how invitations are made, sent, expire and are accepted; the layout of the Members page within the design system; API, MCP and CLI field names and error codes; how bulk revocation at removal works; how a barred member's sign-in is refused; and the tests, including the site checks for the new pages.

## 9. Open questions for the owner

From the builder check on 2026-11-05 (four readers: plain, against the code, the privacy promise, and billing). The owner agreed to recommendations 1, 2, 3 and 5 on 2026-11-05, and to the revised answer to question 4, so all five are decisions now.

1. **Does changing the visibility setting relabel existing files?** Decided: no. The setting governs files uploaded after the change, and every file keeps the visibility it had. Files from before an account joins or becomes a team are "only me".
2. **What happens when a team has more members than its plan's seats?** Decided: a switch to a plan with fewer seats is refused until the administrator has removed enough members. The lockout in decision 13 applies whenever the team ends up on Free, whether from failed payment or a cancellation reaching the end of its period. While Stripe is still retrying a payment, everyone keeps access, as the Terms say for the paid plan itself.
3. **How does the administrator transfer work?** Decided: the chosen member must accept, because the role comes with the bill. On acceptance, the billing emails and the billing portal move to the new administrator at once, and the Billing page asks them to check the card on file, which stays until they change it. The former administrator becomes an ordinary member, and their "only me" files stay private. An administrator cannot leave the team or delete their account while it has other members; they transfer the role first.
4. **What if an invited account has its own paid plan?** Decided (the owner asked that an account move from individual to team member, and back): it can accept. Its own subscription ends on acceptance, and the unused part is credited to that account's Stripe customer balance, as the Terms already do for a downgrade, rather than refunded; the credit applies if the account later subscribes again as an individual. Its old files keep working until they expire, stay visible only to that person, and do not count toward the team's limits. Its old keys stop working, and its agents connect again with `veilfile login` as the team member, so that a team never inherits keys it did not set up. The invitation page says all of this before the person accepts. An administrator of a team with other members cannot accept another team's invitation until they transfer their role. When the person leaves or is removed, the account is an individual Free account again.
5. **How are abuse reports handled for "only me" files?** Decided: as today. In the owner's words, Veilfile must be able to detect and act on abuse regardless of user settings. Veilfile staff act on a report by link or file ID under the Terms, which can include looking at the reported file; "only me" is a promise about teammates and the administrator, not about Veilfile's handling of abuse. A hold lands on the file and on the key that sent it, as it does now. The administrator is told that a member's file was held, without its name. The Terms and the Privacy Policy say this.
