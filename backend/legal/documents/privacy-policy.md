# Privacy Policy

Last updated: 2026-11-19

## 1. What we collect

- **Account email:** when you sign up, to identify your account and contact you about the service.
- **Team members:** when you add someone to your workspace, we record the link between their account and your workspace: their role in the workspace and when they joined. Members use their own accounts, so we collect nothing from a member that we do not already collect from any account holder, as listed above.
- **File visibility:** each file is either private to the member who uploaded it or visible to everyone in the workspace. The uploader chooses. Team-visible files can be seen by every member of that workspace and by no one else.
- **Invitations:** when an administrator invites someone to a workspace, we record the email address the invitation was sent to, who sent it, and when it was sent, accepted, declined, or revoked. We store only a hash of the invitation link, never the link itself. An invitation expires after 7 days. When an invitation ends, for any reason, we keep the record for 30 days and then delete it on the next daily sweep. If the invitee declines, the same address cannot be invited again during those 30 days. The invitation email carries the inviter's verified address, the link, and when it expires.
- **Uploaded files:** the file content you upload, stored until its TTL expires.
- **Metadata:** filenames, file sizes, upload times, and expiry timestamps.
- **API usage and logs:** server request logs (method, path, status, response size, user agent, and the IP address of our hosting provider's proxy). Download links and other secret tokens in paths, query strings and referrers are redacted before they are written. We use logs for billing, abuse prevention and debugging.
- **IP address and user agent:** we record these for terminal sign-in requests (`veilfile login`), and show them to you on the approval page; those records are deleted 1 hour after the 10-minute code expires if no key was issued, and 1 day after if one was. We record the IP address of requests that send a signup or verification email, to limit abuse, and delete it after 90 days.
- **Cookies:** `sessionid` and `csrftoken` keep you signed in and protect forms; the session lasts up to 14 days, and both are Secure over HTTPS. `vf_funnel` is a first-party analytics cookie on the landing and signup pages. It holds a random ID, is HttpOnly, lasts only for the browser session, is never shared, and contains no IP address or user agent. We use it only to count landing visits and signup starts once per browser, and keep those counts for 365 days. We don't count bots, link previews or scripted clients, and we don't use advertising cookies.

We don't run ads and don't profile you. Files are private to your workspace. A file is visible only to the member who uploaded it unless that member marks it visible to the team, in which case every member of the workspace can see it. Unguessable links are the only access path for anyone outside the workspace, and we never publish or index them.

## 2. Why we collect it

To operate the service (storage and retrieval), bill you (paid plans), prevent abuse, and improve reliability. That's it.

## 3. How long we keep it

Deletion is TTL-based. New uploads are deleted 7 days after upload on Free, 90 days on Team, 90 days on Team Plus, and 365 days on Scale. A file uploaded under a longer plan keeps its original expiry after a downgrade, so a file uploaded on Scale is deleted when its Scale TTL runs out even if the account has since moved to Free. When a file's TTL expires, it is deleted automatically and irreversibly on the next daily sweep, normally within 24 hours of expiry and no later than 48. This does not apply to a file we have held because it may break our Terms of Service; a held file is deleted on the next daily sweep after we release it. Account data and API keys are deleted when you close your account. A file we have held because it may break our Terms of Service is not deleted when its TTL expires. It stays until we release it, and is then deleted on the next daily sweep, normally within 24 hours of release and no later than 48. If our storage provider is unavailable when a file is revoked or an account is closed, the link stops working immediately and the stored bytes are deleted on the next daily cleanup run, normally within 24 hours.

## 4. Who we share data with (subprocessors)

We don't sell your data. We share it only as needed to run the service:

- **Stripe:** payment processing and subscription billing (billing data only, never file contents).
- **Cloudflare R2:** object storage for uploaded files.
- **Render:** application hosting, including the Postgres database that holds account and metadata records.
- **Google (Gmail SMTP):** delivers transactional email (account verification and password reset). Receives your email address and the message content.

We may also disclose data if required by law.

## 5. Your rights

You can at any time:

- **Access:** ask us for a copy of the data we hold about your account.
- **Deletion:** ask us to delete your account and its data: uploads, keys, and metadata.
- **Revocation:** revoke an API key yourself, instantly, in the dashboard or with `veilfile logout`. This stops that key from uploading or listing files. It does not disable download links the key already created: to kill a link, revoke the file itself (the Revoke button in the dashboard, `DELETE /api/v1/artifacts/<id>`, or the `revoke_artifact` tool). Apps connected through OAuth, such as MCP clients, can be disconnected under Connected apps in the dashboard.

Apps you authorize, such as MCP clients, keep access until you disconnect them. Access lapses after about 14 days without use, and a password reset disconnects all apps.

To exercise a right, contact us at support@veilfile.com. We'll respond within 30 days.

## 6. Security

Files are stored in Cloudflare R2 and protected by unguessable URLs and key-based access. Uploads are scanned for embedded secrets: flagged by default, and refused instead of stored when the uploader asks (`on_secret=reject`). Sign-in attempts are rate limited. No system is perfect. Please don't upload anything you can't afford to lose.

## 7. Changes

We'll update this page when the policy changes, with the date noted above. Continued use after a change means you accept it.
