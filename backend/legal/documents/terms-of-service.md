# Terms of Service

Last updated: 2026-11-19

## 1. What Veilfile is

Veilfile is a private file-hosting service for AI agents. You upload files through our API and receive unguessable, expiring links to share or retrieve them. Files are stored with a time-to-live (TTL) and deleted when the TTL expires. We don't make uploaded content public or searchable. Links are the only way in.

We may change, pause, or discontinue features at any time. This document governs your use of Veilfile at https://veilfile.com.

## 2. Your account and API keys

You are responsible for everything done under your account and API keys. We only issue API keys to accounts with a verified email address. You sign up with an address you control and confirm it through the link we send you. If we cannot reach you, you cannot use the service. API keys created by signing in from a terminal (`veilfile login`) count toward your plan's key limit. At the limit, approving a new sign-in replaces an existing key you choose. Keep keys secret; treat a leaked key like a compromised password and revoke it immediately. If we detect abuse, we may revoke keys or suspend accounts without notice.

## 3. Acceptable use

You agree not to:

- Upload content that is illegal under applicable law (including CSAM, terrorist content, or material you have no right to share).
- Upload or distribute malware, ransomware, or anything designed to harm systems or people.
- Abuse the service: scraping, credential stuffing, DDoS, circumventing limits, or probing others' files.
- Violate anyone's intellectual property or privacy rights.

We scan uploads for secrets. We may remove content or restrict access when we believe it violates these terms, with or without notice.

Uploads are capped at 25 MiB each. We accept images (png, jpg, jpeg, webp, gif), text files (txt, md, log), json and har files, and pdfs. We do not accept video, executables, or archives. We check a file's contents, not just its name: images and PDFs must carry the correct file signature, and text files must be valid UTF-8. Anything outside these limits is refused before it is stored.

## 4. Data retention and deletion

Files are deleted when their TTL expires. New uploads are deleted when their plan's TTL runs out: Free 7 days, Team 90 days, Team Plus 90 days, Scale 365 days. Files uploaded before a downgrade keep their original expiry (see section 6). Deletion runs on a daily sweep, so a file may linger up to a day past its expiry, and up to two days after a service restart. We also delete account data and keys when you close your account. A file we have held because it may break these terms is not deleted with your account, and is not deleted when its TTL expires. We will not delete an account while a held file would go with it. A held file stays until we release it; once released, it is deleted on the next daily sweep. Deletion of expired files is automatic and irreversible.

## 5. Service availability

Veilfile is provided **as-is, without warranty** of any kind. We aim for high availability but make no SLA commitment. We are a tiny team, and downtime, data loss, or bugs are possible. You should keep your own copies of anything that matters.

## 6. Billing

Paid plans are monthly or yearly subscriptions: Team **$4/month or $40/year** (2,000 uploads, 90-day TTL, 25 GB storage, 5 API keys, 5 seats), Team Plus **$6/month or $60/year** (4,000 uploads, 90-day TTL, 40 GB storage, 10 API keys, 10 seats), Scale **$12/month or $120/year** (10,000 uploads, 365-day TTL, 100 GB storage, unlimited API keys, unlimited seats). Free is $0 (100 uploads/month, 7-day TTL, 1 GB storage, 1 API key, 1 seat). A yearly plan costs the price of ten months, so two months are free. Plan limits are the same whether you pay monthly or yearly.

- **Merchant of record:** payments are processed through Stripe, and Stripe (via Link) is the merchant of record, the legal seller of your subscription. The price you see is the price you pay: all applicable taxes are included in the displayed price, everywhere.
- **Plan changes:** you can switch between Team, Team Plus and Scale at any time. An upgrade charges the prorated difference immediately and takes effect once that payment succeeds. A downgrade credits the unused difference to your Stripe customer balance, applied to future invoices rather than refunded.
- **Team seats:** your plan includes a number of seats: Free 1, Team 5, Team Plus 10, Scale unlimited.
  A seat is one member of your workspace, including you. There are two roles: administrator, who owns the workspace, and member. You may add members up to your plan's seat count; each member signs in with their own Veilfile account and verified email address.
  Only the administrator can invite members, by email address. An invitation is a link sent to that address; it expires after 7 days. A pending invitation holds a seat until it expires, is declined, is revoked, or is accepted. When every seat is in use, a new invitation is refused, and a person who tries to accept an invitation into a full team is refused the same way. If you change to a plan with fewer seats than your workspace has members, the change is refused until you have removed enough members. To join, the invitee must hold a verified Veilfile account under the same email address the invitation was sent to.
  Members get their own API keys, and those keys count toward the plan's key limit and toward the member's own key allowance. A file a member uploads is private to that member unless the member marks it visible to the team, in which case every member of the workspace can see it. Team-visible files stay inside the workspace: they are never public, and unguessable links remain the only way in for anyone outside the workspace.
  You are responsible for what your members do in your workspace, as you are for your own account under section 2. Removing a member ends their access to the workspace at once. Files they uploaded stay in the workspace until they expire. A removed member can still see and delete their own files from their own workspace, but no longer sees the team's files. Only the workspace administrator manages billing: checkout, plan changes, and the billing portal are administrator-only.
- **Billing interval changes:** you can switch between monthly and yearly billing at any time. An interval switch starts a new billing period on the day of the switch and is invoiced at once, less credit for the unused part of your current period. Switching to yearly billing charges the yearly price less that credit; switching to monthly billing credits the unused part of your yearly period to your Stripe customer balance, applied to future invoices rather than refunded.
- **Failed payments:** if a renewal payment fails, your paid plan stays active while Stripe retries the charge. It ends, and your workspace moves to Free, once the subscription becomes unpaid, paused or canceled.
- **After a downgrade or cancellation:** existing API keys, files and links keep working with their original expiry. While you are over the new plan's limits, new API keys and new uploads that would exceed the storage cap are refused. New uploads get the new plan's maximum TTL.
- **Cancellation:** cancel anytime; your plan stays active until the end of the current billing period. For a monthly plan that is up to a month of continued access; for a yearly plan, up to a year.
- **Refunds:** if you cancel within 14 days of your first paid subscription, we'll refund that first payment in full, whether that payment was monthly or yearly. Just ask. After that, subscription fees are non-refundable, but you can cancel anytime and keep your plan until the end of the billing period. Refunds are executed by Stripe as the merchant of record.
- We may change prices with 30 days' notice; continuing to use the service after the notice period means you accept the new price.

## 7. Limitation of liability

To the maximum extent permitted by law, our total liability for any claim is limited to the amount you paid us in the 12 months before the claim (or $100 if you paid nothing). We're not liable for indirect, incidental, or consequential damages: lost data, lost profits, or downtime, even if we knew they were possible.

## 8. Termination

We may suspend or terminate your account for violating these terms, abusing the service, or non-payment. You may stop using Veilfile and delete your account at any time. On termination, your files and keys are deleted as described in section 4. A file we have held stays until we release it, as described in section 4.

## 9. Changes to these terms

We'll update this page when terms change, with the date noted above. Continued use after a change means you accept it.

## 10. Governing law

These terms are governed by the laws of the Commonwealth of Pennsylvania, USA.

## 11. Contact

Questions about these terms: support@veilfile.com.

To report content that violates these terms, email abuse@veilfile.com or use the
abuse reporting page at veilfile.com/report-abuse/. Include the link to the file and a
short description of what you found. Reports are reviewed within one business day.
Reports of child sexual abuse material are prioritized above all else.
