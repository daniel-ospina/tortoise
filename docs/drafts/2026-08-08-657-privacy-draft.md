---
title: "Privacy Policy draft — hosted Tortoise (#657)"
type: legal
domain: legal
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-08
---
# Privacy Policy — Hosted Tortoise Service (tortoise.premiselabs.co)

> **STATUS: DRAFT-FOR-G-GATE — not final, not owner-approved.**
> This is Task T2's deliverable for issue daniel-ospina/tortoise#657. It goes to the owner's G-gate review (T3) before any build work. The rendered page (`website/privacy.html`) is produced at T4 from the owner-approved version of this draft.

---

## Draft metadata (for the G-gate reviewer — not part of the published policy)

| Field | Value |
|---|---|
| Issue | daniel-ospina/tortoise#657 |
| Task | **T2** — Privacy Policy content draft |
| Date | 2026-08-08 |
| Status | **draft-for-G-gate** |
| Effective date | `[effective date to be set to the actual deploy date at T8]` (G-gate ① LOCKED) |
| Controller | Daniel Ospina, individual operating as "Premise Labs" (d/b/a) — natural person, NOT a registered entity (D4 LOCKED) |
| Jurisdiction of residence | `[jurisdiction of residence to be confirmed by owner at G-gate]` — lowercase-prose placeholder per the pinned convention; owner confirms at G-gate; reissuable via versioned procedure (§15) if it changes |

### Step 5 — delete-account capability check (build-time, G-gate ⑧)

**RESULT: ABSENT (2026-08-08, re-verified this session).** Evidence:
- `website/welcome.html` — grep for `delete-account|account-settings|deactiv|deleteUser|account-management`: **no matches**; links only to Dashboard (`https://app.premiselabs.co`), signup, self-hosted (no account-settings surface).
- `apps/dashboard/` — **shell only** (single `index.html`; no account-management UI/content).
- `apps/graph-viz/server/main.py` — only content-level deletes: `DELETE /api/points/{point_id}` and `DELETE /api/edges/{edge_id}`; **no user/account deletion endpoint**.
- Full-tree grep for `deleteUser|auth.admin|listUsers|removeUser`: **no matches** anywhere in `website/` or `apps/`.

**CHOSEN BRANCH: SCOPED CLAIM** (matches the plan's expected outcome). The policy below does **not** promise an in-product delete button. Primary erasure path = **"request deletion via GitHub issues"** + Art. 12(3) one-month commitment + 2-month extension-with-notice + Art. 12(6) identity verification + redaction guidance. A product follow-up for the self-service delete feature is filed per scope §6.2 #5 (Supabase Auth admin `deleteUser` + dashboard account UI). If the capability lands before deploy, re-run this check and switch §16 to the in-product path (record evidence in this header).

### LFPDPPP note (non-blocking, scope D4 / §5.6)

The controller is a natural person whose residence jurisdiction is unconfirmed. **If** the operator is Mexico-resident, the Mexican Federal Law on the Protection of Personal Data Held by Private Parties (LFPDPPP) may apply, requiring a separate Aviso de Privacidad and ARCO rights procedures in addition to this policy. This is flagged for owner confirmation at the G-gate; it does not block this release. A conditional line is included in §1.

### Outline coverage (16 areas per plan T2 Step 1 → sections)

① controller identity/contact + Art. 27 → §1 · ② data categories + expectation line → §2 · ③ analytics/cookies → §3 · ④ legal bases + purposes + ePrivacy → §4 · ⑤ CCPA/CPRA + Meta sharing/joint-controller + Shine the Light → §5 · ⑥ retention → §6 · ⑦ security → §7 · ⑧ user rights → §8 · ⑨ DPA → §9 · ⑩ processors → §10 · ⑪ no-AI-training → §11 · ⑫ international transfers → §12 · ⑬ no-intentional-sensitive-PII → §13 · ⑭ PostHog residency (folded into transfers) → §12 · ⑮ version/effective date + revision history → §15 · ⑯ user rights/erasure → §16.

### Canonical-sentence inventory (verbatim, count = 12)

1. no-training — "we do not use your content or usage data to train AI models" (§11)
2. no-sale (statutory headline) — "We do not sell your personal information." (§5)
3. sharing pair — "we share limited data with advertising/analytics providers as disclosed in this policy." (§5) — the **only** present-tense guard-regex match in the policy body by design (pinned expected match for the T7 guard)
4. minimal-PII — "we do not intentionally collect sensitive personal information" (§13)
5. eligibility — "you must be at least 18 years old" (§2)
6. Art. 27 — "We are not currently established in the EU/EEA. If we become subject to GDPR Art. 27, we will designate and disclose an EU representative here." (§1)
7. consent banner — "consent will be obtained via a banner when these tools are activated" (§3)
8. repo-state — "no analytics tools are currently deployed" (§3)
9. purposes (passive) — "your email address and account information are used to deliver the service, respond to requests, and manage billing; usage data is used to improve the product" (§4)
10. expectation — "we expect users not to place sensitive data in the service" (§2)
11. Art. 12(6) — "we may request identity verification before acting on any request" (§16)
12. PostHog residency — "PostHog is a data processor; EU residency (Frankfurt) used when configured" (§12)

### Drafting-rule compliance (classification rule, pinned)

- Processors, data categories, retention, and transfer statements use **passive/impersonal** phrasing ("data is processed by Supabase for authentication", "account data includes your email address", "data is retained only for as long as needed") — they never match the guard regex `we (use|collect|share|sell|process|retain|store|transfer)\b`.
- The **only** present-tense guard match in this document is the pinned canonical pair sentence (inventory #3). All other present-tense claims are from the approved pinned set.
- All analytics disclosures are **conditional/future** ("when you use the hosted service we may use…; activated with your consent") + state current deployment status. No present-tense claims for undeployed instrumentation.
- The policy body never uses the combined "no sale or sharing" negative-phrase construction; the canonical co-occurrence pair (inventory #2 + #3) is used instead.
- **Re-check steps at #528 resolution (process, not body text):** when Meta Pixel activates, re-verify the §5 sharing disclosure + joint-controller wording still match reality; when PostHog config resolves, re-verify the §12 residency sentence (mirror pattern, plan P1-4). No dedicated "Do Not Sell or Share My Personal Information" link ships this release (deferred to #658 tooling — no dead link ships).

---

# Privacy Policy

**Effective date:** [effective date to be set to the actual deploy date at T8]
**Version:** 1.0

## Scope of this policy

This Privacy Policy describes how personal data is processed in connection with the **hosted Tortoise service** operated by Premise Labs at `tortoise.premiselabs.co` (and its API at `api.premiselabs.co`).

This policy does **not** apply to self-hosted deployments of the Tortoise software. In a self-hosted deployment, the operator of that deployment is the data controller for any personal data processed there, and that operator's own policies apply.

This policy is written in plain English. It describes current practices truthfully and, where the service may introduce tools in the future (such as analytics instrumentation), it describes those tools now in conditional terms so this document stays accurate when they are activated ("disclose now, instrument later").

---

## 1. Controller identity and contact (outline ①)

**Data controller:** Daniel Ospina, an individual operating the hosted Tortoise service under the name "Premise Labs" (d/b/a "Premise Labs").

Premise Labs is **not a registered legal entity**. The data controller is the natural person named above, acting in their personal capacity.

**Jurisdiction of residence:** [jurisdiction of residence to be confirmed by owner at G-gate]

**Contact for privacy matters:** open an issue at <https://github.com/daniel-ospina/tortoise/issues>. This is the controller's designated contact channel for all privacy inquiries and requests. Please do not include sensitive personal data in a public issue — see §16 for guidance on how to make a request safely.

**EU/EEA representative (GDPR Art. 27):** "We are not currently established in the EU/EEA. If we become subject to GDPR Art. 27, we will designate and disclose an EU representative here."

**LFPDPPP (Mexico):** If the controller is resident in Mexico, the Mexican Federal Law on the Protection of Personal Data Held by Private Parties (LFPDPPP) may apply, and a separate privacy notice (Aviso de Privacidad) will be published and linked from this policy.

## 2. Data processed by the service (outline ②)

The service processes the following categories of personal data:

- **Account data.** Account data includes your email address, authentication credentials, and account identifiers. Authentication is managed through an identity and database provider (Supabase), and sign-in may be completed through the GitHub or Google OAuth identity providers. This category is required for account creation, authentication, and billing.
- **Usage data.** Usage data includes information about how the service is used — for example, API requests made, features used, and performance signals. Usage data is not tied to your identity beyond your account.
- **Analytics data.** Analytics data includes behavioral and technical signals that would be processed by analytics tools (Meta Pixel, Google Analytics (GA4), and PostHog) **when those tools are activated** — see §3.

**Sensitive data — expectation.** "we expect users not to place sensitive data in the service" — do not place health data, biometric data, government identifiers, or other sensitive personal data in the service. See §13.

**Eligibility.** The service is intended for use by adults: you must be at least 18 years old to create an account or use the service. The service is not directed to children.

## 3. Analytics and cookies (outline ③)

**Current deployment status:** no analytics tools are currently deployed.

**Planned tools, disclosed now.** When you use the hosted service we may use the following analytics tools, and they are activated only with your consent:

- **Meta Pixel** — advertising measurement and conversion tracking (Meta Platforms, Inc.).
- **Google Analytics (GA4)** — audience and product analytics (Google LLC).
- **PostHog** — product analytics (PostHog Inc.).

Cookies and similar technologies are used by these tools when activated. For each tool, consent will be obtained via a banner when these tools are activated. The consent banner is a planned follow-up (issue #658) and will appear only when instrumentation ships.

Until consent is given, none of these tools are loaded and no data is sent to them.

## 4. Purposes of processing and legal bases (outline ④)

**Purposes of processing:** "your email address and account information are used to deliver the service, respond to requests, and manage billing; usage data is used to improve the product"

**Legal bases under the GDPR.** Processing is based on the following grounds, as applicable:

- **Performance of a contract (Art. 6(1)(b)):** account data is processed to deliver the service the user signs up for, including authentication and account administration.
- **Legitimate interests (Art. 6(1)(f)):** usage data is processed to operate, secure, and improve the product, and to prevent abuse.
- **Consent (Art. 6(1)(a)):** analytics tools are activated and process data only after consent is given, as described in §3.
- **Legal obligation (Art. 6(1)(c)):** billing and transactional records may be processed to comply with tax and accounting obligations, where applicable.

Consent, where given, may be withdrawn at any time with effect for the future. Under ePrivacy rules, non-essential cookies and pixels are subject to the same consent: consent will be obtained via a banner when these tools are activated.

## 5. California — CCPA/CPRA disclosures (outline ⑤)

**We do not sell your personal information.** Consistent with the California Consumer Privacy Act (CCPA), as amended by the California Privacy Rights Act (CPRA): we do not sell your personal information, and no personal information has been sold in the preceding 12 months. we share limited data with advertising/analytics providers as disclosed in this policy.

**"Sharing" disclosure for the Meta Pixel (conditional).** If the Meta Pixel is activated, data processed through the Pixel is shared with Meta as an advertising provider, and that may constitute "sharing" for cross-context behavioral advertising under the CPRA. When the Pixel is activated, Meta is a joint controller of personal data processed through the Pixel, and shared data may be used by Meta for its own purposes. Data is shared with Meta only after consent is obtained, as described in §3.

**Opt-out rights.** No dedicated "Do Not Sell or Share My Personal Information" opt-out link is offered at this time, because no sale occurs and no sharing occurs until analytics tools are activated with consent. When consent and opt-out tooling becomes available (planned consent banner, issue #658), a dedicated opt-out control will be added. Until then, opt-out and all other California privacy requests may be submitted through the contact channel in §1.

**Shine the Light (Cal. Civ. Code § 1798.83).** California residents may request, once per calendar year, information about personal information disclosed to third parties for their own direct marketing purposes in the preceding calendar year. Requests may be submitted through the contact channel in §1.

**Non-discrimination.** You will not be denied goods or services, charged different prices, or provided a different quality of service for exercising your California privacy rights.

## 6. Retention (outline ⑥)

Personal data is retained only for as long as needed to deliver the service and to fulfill the purposes described in this policy. When data is no longer needed, it is deleted or de-identified.

Retention carve-outs, stated honestly:

- **Billing and transactional records** are retained as required by applicable law, including tax and accounting obligations (GDPR Art. 17(3) carve-out for legal compliance).
- **Analytics data** is handled in accordance with the analytics section (§3) and the retention terms of each analytics provider; analytics data is not silently claimed to be deleted.
- **Backups** may retain data for a limited additional period after deletion to maintain integrity; data in backups is not used for any other purpose.

## 7. Security (outline ⑦)

Reasonable technical and organizational measures are applied to protect personal data:

- Data in transit is encrypted using TLS.
- Passwords are stored using salted hashing.
- Access to production systems is restricted to authorized personnel and protected by authentication.
- Access to personal data is limited to what is necessary to operate and support the service.

No security measures are absolute, and the security of the internet cannot be guaranteed. You are responsible for keeping your account credentials confidential.

## 8. Your rights (outline ⑧)

**GDPR rights.** If you are located in the EU/EEA, the UK, or Switzerland, you have the right to:

- **Access** a copy of your personal data;
- **Rectify** inaccurate personal data;
- **Erasure** ("right to be forgotten");
- **Restrict** processing in certain circumstances;
- **Data portability** — receive personal data you provided in a structured, machine-readable format;
- **Object** to processing based on legitimate interests;
- **Withdraw consent** at any time, where processing is based on consent;
- **Lodge a complaint** with a supervisory authority in your country of residence.

**CCPA/CPRA rights.** If you are a California resident, you have the right to:

- **Know** the categories and specific pieces of personal data processed about you;
- **Delete** personal data, subject to legal exceptions;
- **Correct** inaccurate personal data;
- **Opt out** of the "sale" or "sharing" of personal data — not applicable at this time because no sale or sharing occurs (§5), and exercisable via the contact channel in §1;
- **Non-discrimination** for exercising these rights.

Where rights under different laws overlap or conflict, the applicable law governs. All requests are subject to identity verification as described in §16.

## 9. Data Processing Agreement (outline ⑨)

A copy of the Data Processing Agreement (DPA) is available at <https://tortoise.premiselabs.co/dpa>.

Under GDPR Art. 28(3), a DPA is required whenever a processor relationship exists — regardless of deal size, including for free-tier users. The DPA applies to the processor relationships described in §10 and takes effect when personal data is submitted to the service.

## 10. Processors and third-party sharing (outline ⑩)

Personal data is shared only with the processors and providers listed below, and only to the extent necessary to operate the service. Each processor is engaged under a data processing agreement that complies with GDPR Art. 28, and processors act only on documented instructions.

- **Supabase** — authentication and account records are processed by Supabase (the processor that holds account personal data: email/login). Used today.
- **Cloudflare** — the website and API are hosted and delivered via Cloudflare. Used today.
- **GitHub** — sign-in may be completed through the GitHub OAuth identity provider; when you choose that option, identity data is processed by GitHub. Used today. GitHub also processes rights-request submissions (see §16).
- **Google** — sign-in may be completed through the Google OAuth identity provider; when you choose that option, identity data is processed by Google. Used today.
- **Meta, Google, PostHog** — analytics data is processed by these providers when the corresponding analytics tools are activated with consent (§3). Not deployed today.
- **Stripe** — when paid plans become available, payment data is processed by Stripe for billing. Not deployed today.

No other sharing of personal data occurs, except as required by law or with your consent.

## 11. AI training (outline ⑪)

**we do not use your content or usage data to train AI models** — at any tier, free or paid, now or in the future under this policy. This is a commitment of the product, not a limitation of a plan.

## 12. International data transfers (outline ⑫, ⑭)

Personal data may be transferred to, and processed in, countries other than the country where you reside, including the United States, where the service's infrastructure and processors are located.

Where personal data is transferred from the EU/EEA, the UK, or Switzerland, appropriate safeguards are provided:

- **Data Privacy Framework (DPF):** transfers to processors that participate in the EU–US DPF, the UK Extension, or the Swiss–US DPF rely on those certifications.
- **Standard Contractual Clauses (SCCs):** where a processor does not rely on the DPF, transfers are protected by SCCs adopted by the European Commission or equivalent safeguards.

**PostHog EU residency (conditional).** PostHog is a data processor; EU residency (Frankfurt) used when configured. If PostHog's EU-resident cloud is configured for EU users, EU-origin analytics data is stored in Frankfurt; this sentence is re-verified when the PostHog configuration is resolved.

## 13. Sensitive data (outline ⑬)

**we do not intentionally collect sensitive personal information.** Sensitive personal data — such as health data, biometric data, government identifiers, racial or ethnic origin, or other special-category data — is not requested, and is not needed for any feature of the service.

As stated in §2, "we expect users not to place sensitive data in the service." If you place sensitive data in the service despite this expectation, that data is processed at your direction in the same way as other content you submit, and you are responsible for ensuring you are entitled to process it.

## 15. Version, effective date, and document history (outline ⑮)

**Current version:** 1.0
**Effective date:** [effective date to be set to the actual deploy date at T8]

This policy is versioned. When this policy changes, the version number and effective date are updated, and a new entry is added to the document history below. Material changes are posted before they take effect.

### Document history / revisions

- **v1.0** — [effective date] — Initial publication of the Privacy Policy for the hosted Tortoise service.

## 16. Exercising your rights — deletion and other requests (outline ⑯)

**Primary mechanism: request via GitHub issues.** To request deletion of your account and associated data, or to make any other privacy request (access, correction, export, restriction, objection), open an issue at <https://github.com/daniel-ospina/tortoise/issues>.

**Please protect your privacy when filing:** the issue tracker is **public**. Do not include personal data in the issue body — redact or omit it — and consider filing from a GitHub account that is not connected to your identity. The submission itself (GitHub username, metadata, and issue content) is processed by GitHub as a data processor (see §10).

**Response commitment.** Requests are answered within **one month** of receipt, as required by GDPR Art. 12(3). If a request is complex or numerous, the response period may be extended by up to two additional months; you will be informed of any extension within one month of receipt, together with the reasons for the delay.

**Identity verification.** we may request identity verification before acting on any request (GDPR Art. 12(6)). Where a request cannot be verified or is manifestly unfounded or excessive, the request may be refused or a reasonable fee may be charged, as permitted by law.

**What this channel covers.** The GitHub-issues channel covers: (a) account data, (b) non-account data, (c) users who cannot log in to their account, and (d) access, export, and objection requests. It is the general-purpose rights channel for this release.

**Deletion scope.** When a deletion request is fulfilled, account and associated data is deleted or de-identified, subject to the retention carve-outs in §6 (billing/transactional records retained as required by law; analytics data handled per the analytics section). Deletion does not extend to content you have already shared publicly or to data that others have lawfully obtained.

**Self-service deletion (future).** An in-product self-service account deletion feature does not exist at the time of this publication, so this policy does not promise one. If a self-service deletion feature is added to the product, this policy will be updated to describe it.

---

*Questions about this policy may be submitted through the contact channel in §1.*
