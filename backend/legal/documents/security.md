# Security

## How are API keys stored?

We never store your raw API key. The key is shown to you once, at creation, and the only thing we keep is its SHA-256 hash. API keys created by signing in from a terminal (`veilfile login`) count toward your plan's key limit, and approving a new sign-in at the limit replaces a key you choose. Keys can be revoked instantly, and revocation is scoped to future use: existing artifact links keep their expiry and are not touched.

## How do you detect abuse and illegal material?

Every upload is checked twice: once against the declared MIME type, and once against the actual file bytes (magic-byte verification: files that lie about what they are do not get stored). We accept images and documents only; video is rejected and the cap is 25 MiB per artifact. Every upload's SHA-256 is checked against a blocked-hash table, uploads are scanned for patterns that look like credentials (advisory by default: flagged on the artifact, not blocked), and per-key rate limits apply. The Terms of Service prohibit CSAM, malware, and unlawful material. Reports go to abuse@veilfile.com, and you do not need an account to report.

What we do not claim: stronger abuse-detection systems are still under review and are not part of this page. We make no public claim about perceptual hash detection until an approved system is running.

## Is data encrypted in transit and at rest?

Yes. All traffic to veilfile.com runs over HTTPS, so API keys and uploads are encrypted in transit. Artifact bytes are stored on Cloudflare R2, which encrypts all stored objects at rest by default with AES-256 and keys Cloudflare manages. That is R2's published default, not a control we configure ourselves.

## Where is data stored?

Our app servers run on Render in Oregon, USA, and artifact bytes are stored on Cloudflare R2. We make no storage-jurisdiction promise beyond that today. If your compliance requires a specific region, write to support@veilfile.com.

## What happens if there is a breach?

We will notify affected users within 72 hours of confirming a breach that affects their data. The 72-hour window starts at confirmation, not at first suspicion. If you are asking because your procurement requires a commitment, write to support@veilfile.com.

## Do you sign DPAs, and how do I reach security?

You can reach us at abuse@veilfile.com (abuse reports, no account required) and support@veilfile.com. We do not offer a data processing addendum today. If your procurement needs one, write to support@veilfile.com; the ask shapes what we build next.

## What logs do you keep, and for how long?

From the Privacy Policy (last updated 2026-11-04): device sign-in records, including IP and user agent, are purged 1 hour after the code expires, or 1 day after a key is issued. Email send-log IP addresses are blanked after 90 days. Anonymous funnel counts are kept for 365 days. OAuth refresh tokens last about 14 days without use.

## What about backups and business continuity?

We have not published a backup or business-continuity posture. The page stays silent on this until it is verified.

## Are you SOC 2 certified? Have you been audited?

No. We have no SOC 2 certification, no third-party security audit, and no penetration test to cite. This page says what we do, and we do not claim certifications or audits we have not done.
