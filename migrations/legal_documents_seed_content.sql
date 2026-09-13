-- ============================================================================
-- legal_documents_seed_content.sql  —  initial document text, published as v1.0
-- ----------------------------------------------------------------------------
-- The SQL equivalent of `python -m scripts.seed_legal_documents`, for running
-- straight in the Supabase SQL editor. Use one or the other - running both is
-- harmless, since each skips a document that is already published.
--
-- Run AFTER legal_documents_setup.sql, which creates the 14 catalogue rows this fills.
--
-- Publishing goes through publish_legal_document() rather than an INSERT, so this
-- takes exactly the same transactional path as the Publish button in the admin CMS:
-- same version numbering, same audit record, same immutable version row.
--
-- Safe to re-run. A document that already has a published version is SKIPPED - once
-- v1.0 exists the CMS owns the text, and re-running must never silently revert an edit
-- an admin made through the UI. To load revised text over a published document, paste
-- it into the CMS editor and save it as a DRAFT, so publishing stays a human decision.
--
-- The whole thing is ONE DO block, so it is one transaction: if any document fails,
-- none of them are published and you can fix and re-run from a clean state.
-- ============================================================================

DO $seed$
DECLARE
  v_actor UUID;
  v_id    UUID;
  v_done  INT := 0;
  v_skip  INT := 0;
BEGIN
  -- The publisher recorded against v1.0, so the version history reads as a person
  -- rather than an empty cell.
  SELECT id INTO v_actor FROM profiles
   WHERE role = 'admin' AND deleted_at IS NULL
   ORDER BY created_at LIMIT 1;

  IF v_actor IS NULL THEN
    RAISE EXCEPTION 'No admin profile found. Sign up as the admin email and run the '
                    'clear/reset script first, or hard-code an actor id in this file.'
      USING errcode = 'P0001';
  END IF;

  -- ── 01  website-terms-of-use ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '01';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 01 (website-terms-of-use) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Who Operates This Website

This website (immigroov.com) and the Immigroov platform are jointly operated by:

- Immigroov Consulting VOF, a general partnership registered in the Netherlands under KVK number 96600462, VAT number NL867678215B01, registered address: Eindhoven, Netherlands (“Immigroov Netherlands”, “we”, “us”); and

- Immigroov Consulting India LLP, a limited liability partnership registered in India under LLPIN ACO-6062, GST number 33AALFI4626F1ZI, registered address: Tiruchirappalli, Tamil Nadu, India (“Immigroov India”).

Immigroov Netherlands owns and controls the Immigroov brand, the website, the platform technology, and the Groovia AI service. Immigroov India is a separately incorporated entity responsible for contracting with and serving customers located in India, and for related payment and operational functions, under arrangements with Immigroov Netherlands.

Which entity you are contracting with for a paid service depends on your location and is set out in the applicable Customer Terms & Conditions (India or Rest-of-World), not in this document. This document governs your general use of the website only.

## 2. Acceptance of These Terms

By accessing or using the Immigroov website, you agree to be bound by these Website Terms of Use. If you do not agree, do not use the website.

These Website Terms of Use apply to all visitors, including individuals browsing the site, prospective customers, prospective mentors, and any other users, prior to and separate from any account registration or paid service, which are additionally governed by the applicable Customer Terms & Conditions or Mentor Agreement.

## 3. Eligibility

You must be at least 18 years old to create an account, register as a mentor, or purchase any service on this website. By using this website, you confirm that you meet this requirement.

## 4. Nature of the Service

Immigroov operates a platform connecting individuals seeking guidance on immigration and relocation (“Customers”) with independent mentors who have relevant lived or professional experience (“Mentors”), and provides access to Groovia AI, an AI-powered assistant that supports the matching and information process.

Mentors are independent contractors and are not employees, agents, or representatives of Immigroov Netherlands or Immigroov India. Mentors are not licensed immigration lawyers, financial advisors, or other regulated professionals unless independently stated and verified, and mentorship sessions reflect personal experience and guidance, not regulated professional advice.

## 5. Intellectual Property

The Immigroov name, logo, website design, platform software, and Groovia AI are the intellectual property of Immigroov Netherlands and are protected by applicable copyright, trademark, and other intellectual property laws. You may not copy, reproduce, modify, distribute, or create derivative works from any part of the website or platform without prior written permission.

## 6. Account Registration

To access certain features, you must register an account and provide accurate, current, and complete information. You are responsible for maintaining the confidentiality of your account credentials and for all activity under your account. Notify us immediately at support@immigroov.com if you suspect unauthorized use of your account.

## 7. Acceptable Use

You agree not to:

- Use the website for any unlawful purpose or in violation of these terms;

- Attempt to gain unauthorized access to the platform, other users’ accounts, or Immigroov’s systems;

- Circumvent the platform to transact directly with a Mentor or Customer outside the platform in order to avoid applicable fees, where such circumvention breaches the applicable Customer or Mentor terms;

- Upload or transmit any harmful code, or content that is unlawful, defamatory, or infringes the rights of others;

- Scrape, harvest, or otherwise extract data from the website using automated means without prior written consent.

## 8. Groovia AI

Groovia AI is an artificial intelligence system. Your interactions with Groovia AI are subject to the separate Groovia AI Terms of Use and the AI disclosure notice provided within the product, which explain that you are interacting with an AI system and set out its intended use and limitations.

## 9. Third-Party Links and Services

The website may reference or link to third-party services (including payment processors and video-call infrastructure). Immigroov is not responsible for the content, policies, or practices of third-party services. Your use of such services is governed by their own terms.

## 10. Disclaimers

The website and platform are provided on an “as is” and “as available” basis. To the maximum extent permitted by applicable law, Immigroov Netherlands and Immigroov India disclaim all warranties, express or implied, regarding the website’s operation, availability, or fitness for a particular purpose. Nothing in this section limits any liability that cannot be excluded under applicable Dutch or Indian law.

## 11. Limitation of Liability

To the maximum extent permitted by applicable law, Immigroov Netherlands and Immigroov India shall not be liable for any indirect, incidental, special, or consequential damages arising from your use of the website. This limitation does not apply to liability arising from gross negligence, wilful misconduct, or matters that cannot be excluded under applicable law.

## 12. Changes to These Terms

We may update these Website Terms of Use from time to time. The “Last updated” date at the top of this page reflects the most recent revision. Continued use of the website after changes take effect constitutes acceptance of the revised terms.

## 13. Governing Law and Jurisdiction

- Matters relating to Immigroov Netherlands and this website generally are governed by the laws of the Netherlands, and disputes are subject to the exclusive jurisdiction of the competent courts of Eindhoven, the Netherlands.

- Matters specifically relating to your relationship with Immigroov India as a contracting party are governed by the laws of India, and disputes are subject to the exclusive jurisdiction of the competent courts of Tiruchirappalli, Tamil Nadu, India.

## 14. Contact

General inquiries: support@immigroov.com Legal notices: immigroov@gmail.com Data protection / privacy inquiries: vinothkannan@immigroov.com

This document should be read together with the Privacy Policy, Cookie Policy, and the applicable Customer Terms & Conditions or Mentor Agreement.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 02  cookie-policy ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '02';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 02 (cookie-policy) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Who This Policy Covers

This Cookie Policy applies to your use of the Immigroov website (immigroov.com), operated jointly by Immigroov Consulting VOF (KVK 96600462, Eindhoven, Netherlands) and Immigroov Consulting India LLP (LLPIN ACO-6062, Tiruchirappalli, Tamil Nadu, India). It should be read together with our Privacy Policy.

## 2. What Cookies Are

Cookies are small text files placed on your device when you visit a website. They allow the website to recognize your device, remember information about your visit, and, where applicable, help us and third parties understand how the website is used.

## 3. Categories of Cookies We Use

a) Strictly Necessary Cookies Required for the website and platform to function — for example, keeping you logged in, remembering items in a booking flow, and enabling core security features. These cannot be switched off and do not require consent under applicable law.

b) Analytics Cookies Used to understand how visitors use the website, via web analytics and tag management tools. These help us measure traffic and improve the platform. Set only with your consent.

c) Advertising / Marketing Cookies Used to measure and improve the effectiveness of our marketing, via advertising and social media measurement tools. These may be used to show you relevant Immigroov content on other platforms and to measure campaign performance. Set only with your consent.

d) Functional Cookies Used to remember preferences (such as language or currency display) to improve your experience. Set only with your consent, unless strictly necessary for a feature you have actively requested.

## 4. Third Parties Who May Set Cookies

| Purpose | Category |
|---|---|
| Website/platform hosting and delivery | Frontend hosting/delivery provider |
| Analytics | Web analytics and tag management provider |
| Advertising | Advertising and social media measurement providers |
| CRM / marketing engagement (where applicable to logged-in mentor recruitment flows) | CRM/marketing platform provider |

Each third party’s use of cookies is additionally governed by its own privacy and cookie practices.

## 5. Your Choices

When you first visit the Immigroov website, you will be shown a cookie banner allowing you to accept or reject non-essential cookies (analytics, advertising, and functional categories), and to change your choice at any time via the cookie preference link in the website footer. You can also control cookies through your browser settings; note that blocking strictly necessary cookies may affect the website’s functionality.

## 6. Data Transfers

Some of the third parties listed above may process cookie data outside the European Economic Area. Where this occurs, we rely on appropriate safeguards as described in our Privacy Policy.

## 7. Changes to This Policy

We may update this Cookie Policy from time to time to reflect changes in the cookies and tools we use. The “Last updated” date above reflects the most recent revision.

## 8. Contact

Questions about this Cookie Policy: support@immigroov.com

This document should be read together with the Privacy Policy and Website Terms of Use.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 03  refund-cancellation-policy ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '03';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 03 (refund-cancellation-policy) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Scope

This policy applies to mentoring sessions booked through the Immigroov platform. Your contracting entity (Immigroov Consulting VOF or Immigroov Consulting India LLP) depends on your location, as set out in the applicable Customer Terms & Conditions; this Refund & Cancellation Policy applies regardless of contracting entity unless stated otherwise.

## 2. Cancellation and Rescheduling — Mentor-Set Windows

Each Mentor sets their own cancellation and rescheduling notice period, which is displayed on the Mentor’s profile before you book a session. There is no single platform-wide cancellation deadline — the applicable window is the one shown for the specific Mentor and session you book.

- Requests made outside the Mentor’s stated notice window are processed according to that Mentor’s stated policy (full refund, partial refund, or free reschedule, as applicable).

- Requests made within the Mentor’s stated notice window (i.e., closer to the session than the Mentor allows) are not automatically approved or refunded. They are referred to manual review by the Immigroov team, who will assess the circumstances and make a case-by-case decision.

- Because cancellation terms vary by Mentor, review each Mentor’s stated policy before booking.

## 3. Manual Review

All refund and cancellation disputes are reviewed manually by the Immigroov admin team on a case-by-case basis. There is no automated refund approval. Decisions aim to be fair to both Customer and Mentor and take into account the circumstances of the request, the Mentor’s stated policy, and platform records of the booking.

## 4. Refund Processing

Where a refund is approved:

- Refunds are processed within 5–7 business days of approval.

- Payment gateway fees are non-refundable under any circumstances and will be deducted from the refunded amount.

- Refunds are issued to the original payment method used for the booking, subject to the payment provider’s own processing timelines.

## 5. Completed Sessions

A session is considered completed unless the Mentor or the Customer reports it as a no-show. Completed sessions are not eligible for refund on the basis of session length alone; refund requests relating to service quality are handled under Section 6.

## 6. Mentor No-Shows and Other Disputes

If a Mentor fails to attend a scheduled session without prior notification, or cancels or reschedules later than their own stated notice window, and you do not accept an offered alternative (such as a replacement Mentor), you are entitled to a full refund for that session. In such cases, the Mentor is responsible for the resulting cost, which is not borne by Immigroov.

If you have any other concern or dispute about a session, contact support@immigroov.com. These cases are reviewed manually and may result in a replacement session, partial refund, or full refund, at Immigroov’s discretion, depending on the circumstances.

## 7. How to Request a Cancellation, Reschedule, or Refund

Requests must be submitted through your account on the platform or by emailing support@immigroov.com, stating your booking reference and the reason for the request.

## 8. Changes to This Policy

We may update this policy from time to time. The “Last updated” date above reflects the most recent revision. Changes do not affect bookings already confirmed under the previous version of this policy.

## 9. Contact

support@immigroov.com

This document should be read together with the applicable Customer Terms & Conditions and Payment Terms.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 04  data-subject-rights ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '04';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 04 (data-subject-rights) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Who This Applies To

This page explains how you can exercise your rights over personal data processed by Immigroov, as a Customer, Mentor, or website visitor. It applies alongside our Privacy Policy.

Depending on your location and the nature of your relationship with Immigroov, your data may be controlled by Immigroov Consulting VOF (Netherlands, GDPR) and/or Immigroov Consulting India LLP (India, DPDPA), as set out in the Privacy Policy’s controller/processor allocation.

## 2. Your Rights

If you are located in the European Economic Area / United Kingdom (GDPR):

- Right to access — request a copy of the personal data we hold about you.

- Right to rectification — request correction of inaccurate or incomplete data.

- Right to erasure — request deletion of your personal data, subject to Section 4 below.

- Right to restrict processing — request that we limit how we use your data in certain circumstances.

- Right to data portability — request your data in a structured, commonly used format.

- Right to object — object to processing based on our legitimate interests, including direct marketing.

- Right to withdraw consent — where processing is based on consent, withdraw it at any time without affecting processing carried out before withdrawal.

- Right to lodge a complaint with the Dutch Data Protection Authority (Autoriteit Persoonsgegevens) or your local EU/UK supervisory authority.

If you are located in India (DPDPA):

- Right to access information about your personal data and its processing.

- Right to correction and erasure of your personal data.

- Right to grievance redressal through our designated Grievance Officer (see Section 5).

- Right to nominate another individual to exercise your rights on your behalf in the event of death or incapacity.

- Right to withdraw consent, where processing is based on consent.

## 3. How to Submit a Request

Send your request to vinothkannan@immigroov.com with:

- Your full name and the email address associated with your Immigroov account;

- Whether you are a Customer, Mentor, or other visitor;

- The specific right you wish to exercise;

- Any information that helps us verify your identity, since we must confirm identity before acting on a data request.

We will acknowledge your request and respond within the timeframe required by applicable law (generally one month under GDPR, extendable by two further months for complex requests with notice to you; as required under DPDPA for Indian data principals).

## 4. Limits on Erasure

We may be unable to fully delete certain data where retention is legally required — most significantly, financial and payment records, which must be retained for 7 years under Dutch tax law (bewaarplicht), regardless of an erasure request. Where full deletion is not possible, we will delete or anonymize what we lawfully can and inform you of what is retained and why.

For non-financial personal data, where no other legal retention obligation applies, we aim to delete such data within 90 days of a verified deletion request.

## 5. Grievance Officer (India / DPDPA)

Name: Vinoth Kannan Sundararajan Designation: Director, Immigroov Consulting India LLP Contact: vinothkannan@immigroov.com

## 6. Contact for EU/GDPR Privacy Inquiries

Privacy Contact: Vinoth Kannan Sundararajan Contact: vinothkannan@immigroov.com

This document should be read together with the Privacy Policy.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 05  privacy-policy ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '05';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 05 (privacy-policy) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Introduction

This Privacy Policy explains how Immigroov collects, uses, shares, and protects your personal data when you use the Immigroov website and platform, including Groovia AI.

## 2. Who Controls Your Data

Immigroov operates through two entities:

- Immigroov Consulting VOF (KVK 96600462, VAT NL867678215B01, Eindhoven, Netherlands) — acts as data controller for: the global Immigroov platform and technology, the Mentor relationship and Mentor data, Groovia AI, and Customers located outside India.

- Immigroov Consulting India LLP (LLPIN ACO-6062, GST 33AALFI4626F1ZI, Tiruchirappalli, Tamil Nadu, India) — acts as data controller (under India’s DPDPA, “data fiduciary”) for: the commercial relationship with Customers located in India, and India-specific payment records.

Where both entities are involved in processing the same data (for example, a booking by an Indian Customer with a Mentor whose agreement sits with Immigroov Netherlands), the two entities act under an internal data-sharing arrangement that allocates responsibility between them; this does not reduce either entity’s obligations to you.

## 3. What Personal Data We Collect

- Identity and contact data: name, email address, phone number.

- Account and booking data: account credentials, booking history, session scheduling details.

- Payment data: payment and transaction records processed via our payment providers (see Section 6). We do not store full card numbers; payment providers process and store sensitive payment details directly.

- Session data: video/audio call metadata from mentoring sessions (such as timing, duration, and connection data), processed via our video infrastructure provider.

- Groovia AI interaction data: chat inputs and summaries generated during your interaction with Groovia AI, used to support matching and provide the service.

- Usage and device data: website usage, device, and IP-related data collected via analytics and advertising tools, as described in our Cookie Policy.

We do not currently collect passport, visa, or other government identity document data as part of the standard platform flow.

## 4. How We Use Your Data

We use your personal data to:

- Create and manage your account;

- Match Customers with suitable Mentors, including through Groovia AI;

- Process bookings, payments, and refunds;

- Facilitate mentoring sessions, including video/audio infrastructure;

- Communicate with you about your account, bookings, and (where you have opted in) marketing;

- Comply with our legal, tax, and regulatory obligations;

- Maintain the security and integrity of the platform.

## 5. Groovia AI and Third-Party AI/ML Processing

Groovia AI is powered by third-party AI/ML model providers, engaged under data processing agreements that include appropriate contractual safeguards. Your interactions with Groovia AI may be processed by these providers solely to deliver and improve the Groovia AI service. See our separate AI Disclosure Notice and Groovia AI Terms of Use for further detail on how Groovia AI works and its limitations.

## 6. Who We Share Your Data With

We share personal data with:

- Mentors, to the extent necessary to deliver a booked session;

- Payment processors, to process payments;

- Infrastructure and hosting providers, including cloud hosting, database, backend, and website infrastructure providers;

- Communications providers, including transactional email and video/audio session infrastructure providers;

- Customer relationship management tools, used to support mentor recruitment;

- Analytics and advertising providers, used to measure website usage and marketing performance;

- Third-party AI/ML model providers, as described in Section 5;

- Regulators, tax authorities, or courts, where required by law;

- A successor entity, in the event of a corporate restructuring, merger, or acquisition, subject to equivalent data protection commitments.

We do not sell your personal data.

## 7. International Data Transfers

Some of the providers listed in Section 6 may process data outside your home jurisdiction, including outside the European Economic Area. Where we transfer personal data internationally, we rely on appropriate safeguards recognized under applicable law, such as Standard Contractual Clauses, or transfers to jurisdictions recognized as providing adequate protection. [This section is pending final confirmation of Standard Contractual Clauses coverage for Groovia AI’s underlying providers and will be updated accordingly before publication.]

## 8. Data Retention

- Financial and payment records are retained for 7 years, as required under Dutch tax law (bewaarplicht), regardless of any deletion request.

- Other (non-financial) personal data is retained for as long as your account is active, and deleted within 90 days of a verified deletion request, except where a further legal retention obligation applies.

## 9. Your Rights

See our separate Data Subject Rights & Deletion Request page for a full description of your rights and how to exercise them.

## 10. Cookies

See our separate Cookie Policy for details of the cookies and tracking technologies used on our website.

## 11. Security

We implement technical and organizational measures designed to protect your personal data against unauthorized access, loss, or misuse. No system is completely secure, and we cannot guarantee absolute security.

## 12. Children’s Privacy

Immigroov is not intended for individuals under 18 years of age. We do not knowingly collect personal data from anyone under 18.

## 13. Changes to This Policy

We may update this Privacy Policy from time to time. The “Last updated” date above reflects the most recent revision. Material changes will be communicated to you where required by applicable law.

## 14. Contact

EU/GDPR Privacy Contact: Vinoth Kannan Sundararajan — vinothkannan@immigroov.com India DPDPA Grievance Officer: Vinoth Kannan Sundararajan, Director — vinothkannan@immigroov.com General inquiries: support@immigroov.com

This document should be read together with the Cookie Policy, Data Subject Rights & Deletion Request page, AI Disclosure Notice, and Groovia AI Terms of Use.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 06  ai-disclosure-notice ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '06';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 06 (ai-disclosure-notice) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## You Are Interacting With an AI System

Groovia is an artificial intelligence-powered assistant. When you interact with Groovia, you are communicating with an AI system, not a human Immigroov team member or a Mentor.

This notice is provided in accordance with applicable AI transparency requirements, including Article 50 of the EU AI Act.

## What Groovia Does

Groovia is designed to:

- Ask you questions to understand your immigration/relocation goals and circumstances;

- Help assess fit with available destination countries and pathways;

- Recommend and help route you to suitable Mentors on the Immigroov platform;

- Provide general informational support based on the information you share.

## What Groovia Is Not

- Groovia is not a licensed immigration lawyer, financial advisor, or other regulated professional, and does not provide regulated legal, financial, tax, or immigration advice.

- Groovia’s output is generated using third-party AI/ML model providers and may not always be accurate, complete, or up to date. Always verify important information, particularly time-sensitive visa, legal, or financial requirements, with a qualified professional or official government source.

- Groovia does not make final decisions on your behalf; recommendations are informational and for your consideration only.

## Your Data and Groovia

Information you share with Groovia, including chat inputs and summaries, is processed to deliver and improve the service, as described in our Privacy Policy and Groovia AI Terms of Use.

## Questions

If you have questions about how Groovia works or how your data is used, contact support@immigroov.com.

This notice should be read together with the Groovia AI Terms of Use and Privacy Policy.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 07  groovia-ai-terms ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '07';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 07 (groovia-ai-terms) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Scope

These terms govern your use of Groovia, the AI-powered assistant provided as part of the Immigroov platform. They supplement, and should be read together with, the Website Terms of Use, Privacy Policy, and AI Disclosure Notice.

## 2. Provider

Groovia AI is owned and operated by Immigroov Consulting VOF (KVK 96600462, Eindhoven, Netherlands). Where you are a Customer of Immigroov Consulting India LLP, that entity may facilitate your access to and payment for services that include Groovia AI, but Immigroov Consulting VOF remains the provider and owner of the underlying Groovia AI service.

## 3. What Groovia Is

Groovia is an AI-powered conversational assistant that helps assess your immigration/relocation goals, provides general informational guidance, and helps match you with a suitable Mentor on the platform. As disclosed in our AI Disclosure Notice, you are interacting with an AI system, not a human.

## 4. No Professional Advice

Groovia does not provide legal, financial, tax, or licensed immigration advice. Its output is generated by third-party AI/ML model providers and is provided for general informational and matching purposes only. You should independently verify any time-sensitive, legal, financial, or visa-related information with a qualified professional or official government source before relying on it.

## 5. Accuracy and Limitations

AI-generated output may contain errors, omissions, or outdated information. Immigroov does not guarantee the accuracy, completeness, or reliability of Groovia’s output. You use Groovia’s output at your own discretion and risk.

## 6. Acceptable Use

You agree not to use Groovia to:

- Submit unlawful, abusive, or harmful content;

- Attempt to extract the underlying model, prompts, or training data;

- Use Groovia’s output for any purpose that violates applicable law or the rights of others;

- Rely on Groovia as a substitute for professional legal, financial, or immigration advice.

## 7. Data Processing

Your interactions with Groovia, including chat inputs and generated summaries, are processed as described in our Privacy Policy, including by third-party AI/ML model providers engaged under data processing agreements with appropriate safeguards.

## 8. Availability and Changes

Groovia’s features, availability, and underlying technology may change or be updated from time to time, including as we improve the service or change technology providers. We will maintain the transparency disclosures required by applicable law regardless of the underlying technology used.

## 9. Liability

To the maximum extent permitted by applicable law, Immigroov Consulting VOF is not liable for decisions made in reliance on Groovia’s output. Nothing in these terms limits liability that cannot be excluded under applicable Dutch or other mandatory law.

## 10. Changes to These Terms

We may update these terms from time to time. The “Last updated” date above reflects the most recent revision.

## 11. Contact

support@immigroov.com

This document should be read together with the AI Disclosure Notice, Privacy Policy, and Website Terms of Use.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 08  customer-terms-india ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '08';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 08 (customer-terms-india) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Contracting Entity

If you are located in India, your contract for services booked through the Immigroov platform is with Immigroov Consulting India LLP, LLPIN ACO-6062, GST 33AALFI4626F1ZI, registered address Tiruchirappalli, Tamil Nadu, India (“Immigroov India”, “we”, “us”).

Immigroov India provides the mentoring service to you using the Immigroov brand, platform, and Mentor network made available to it by Immigroov Consulting VOF (Netherlands) under a commercial arrangement between the two entities. Immigroov India is the party responsible to you for the service you book.

## 2. Eligibility

You must be at least 18 years old and located in India to contract under these terms. If you are not located in India, the applicable terms are the Immigroov Customer Terms & Conditions — Rest of World.

## 3. The Service

Immigroov India offers access to independent Mentors who provide mentoring services based on personal or professional experience relevant to immigration and relocation, along with access to Groovia AI as described in the Groovia AI Terms of Use.

Mentors are independent contractors engaged under agreement with Immigroov Consulting VOF and are not employees of Immigroov India. Mentors are not licensed immigration lawyers, financial advisors, or other regulated professionals unless separately and expressly stated.

## 4. Booking and Account

Guest bookings are permitted. If you book as a guest, certain features (such as booking history and submitting a review) require a registered account. You are responsible for the accuracy of the information you provide during booking.

## 5. Pricing and Fees

- The price shown to you at checkout includes the Mentor’s session price plus Immigroov’s platform/operations fee.

- The total price shown includes Immigroov’s platform/operations fee and all applicable taxes.

- All applicable taxes, including GST, are included in or added to the price as shown at checkout.

- Full pricing mechanics are set out in the separate Payment Terms.

## 6. Payment

Payments are collected via Razorpay. By making a payment, you agree to Razorpay’s applicable terms in addition to these Terms. See the Payment Terms for further detail.

## 7. Cancellation, Rescheduling, and Refunds

Cancellation, rescheduling, and refund terms are set out in the Refund & Cancellation Policy. Cancellation and rescheduling windows are set individually by each Mentor and shown on the Mentor’s profile prior to booking.

## 8. Your Responsibilities

You agree to attend booked sessions in good faith, provide accurate information to your Mentor and to Groovia AI, and use the platform lawfully. Mentoring guidance reflects personal experience and is not a substitute for regulated legal, financial, or immigration advice; decisions based on that guidance remain your own responsibility.

Immigroov and its Mentors do not guarantee, promise, or imply any outcome over which they have no control, including visa approval, job offers, employment placement, or university/college admission. Mentors may share experience and strategy, but outcomes with government authorities, employers, and educational institutions are decided solely by those third parties.

## 9. Reviews

Reviews may only be submitted following a verified completed booking.

## 10. Data Protection

Your personal data is processed as described in our Privacy Policy.

## 11. Limitation of Liability

To the maximum extent permitted under Indian law, Immigroov India’s liability arising from your use of the platform is limited to the amount paid by you for the relevant booking. This limitation does not apply to liability that cannot be excluded under applicable law.

## 12. Termination

We may suspend or terminate your account for breach of these terms, fraudulent activity, or misuse of the platform.

## 13. Governing Law and Dispute Resolution

These terms are governed by the laws of India. Any disputes arising under these terms are subject to the exclusive jurisdiction of the competent courts of Tiruchirappalli, Tamil Nadu, India.

## 14. Changes to These Terms

We may update these terms from time to time. The “Last updated” date above reflects the most recent revision. Continued use of the platform after changes take effect constitutes acceptance.

## 15. Contact

support@immigroov.com | Legal notices: immigroov@gmail.com

This document should be read together with the Website Terms of Use, Privacy Policy, Payment Terms, Refund & Cancellation Policy, and Groovia AI Terms of Use.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 09  customer-terms-row ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '09';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 09 (customer-terms-row) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Contracting Entity

If you are located outside India, your contract for services booked through the Immigroov platform is with Immigroov Consulting VOF, KVK 96600462, VAT NL867678215B01, registered address Eindhoven, Netherlands (“Immigroov”, “we”, “us”).

If you are located in India, the applicable terms are the Immigroov Customer Terms & Conditions — India, and your contract is with Immigroov Consulting India LLP instead.

## 2. Eligibility

You must be at least 18 years old to contract under these terms.

## 3. The Service

Immigroov offers access to independent Mentors who provide mentoring services based on personal or professional experience relevant to immigration and relocation, along with access to Groovia AI as described in the Groovia AI Terms of Use.

Mentors are independent contractors engaged under agreement with Immigroov and are not our employees. Mentors are not licensed immigration lawyers, financial advisors, or other regulated professionals unless separately and expressly stated.

## 4. Booking and Account

Guest bookings are permitted. If you book as a guest, certain features (such as booking history and submitting a review) require a registered account. You are responsible for the accuracy of the information you provide during booking.

## 5. Pricing and Fees

- The price shown to you at checkout includes the Mentor’s session price plus Immigroov’s platform/operations fee.

- The total price shown includes Immigroov’s platform/operations fee and all applicable taxes.

- All applicable taxes are included in or added to the price as shown at checkout.

- Full pricing mechanics are set out in the separate Payment Terms.

## 6. Payment

Payments may be collected via our payment provider(s) as made available to you at checkout. Payment collection for certain transactions may be facilitated via Immigroov Consulting India LLP acting on our behalf under a payment collection arrangement; this does not change Immigroov Consulting VOF’s role as your contracting party. See the Payment Terms for further detail.

## 7. Cancellation, Rescheduling, and Refunds

Cancellation, rescheduling, and refund terms are set out in the Refund & Cancellation Policy. Cancellation and rescheduling windows are set individually by each Mentor and shown on the Mentor’s profile prior to booking.

## 8. Your Responsibilities

You agree to attend booked sessions in good faith, provide accurate information to your Mentor and to Groovia AI, and use the platform lawfully. Mentoring guidance reflects personal experience and is not a substitute for regulated legal, financial, or immigration advice; decisions based on that guidance remain your own responsibility.

Immigroov and its Mentors do not guarantee, promise, or imply any outcome over which they have no control, including visa approval, job offers, employment placement, or university/college admission. Mentors may share experience and strategy, but outcomes with government authorities, employers, and educational institutions are decided solely by those third parties.

## 9. Reviews

Reviews may only be submitted following a verified completed booking.

## 10. Data Protection

Your personal data is processed as described in our Privacy Policy.

## 11. Limitation of Liability

To the maximum extent permitted under applicable law, Immigroov’s liability arising from your use of the platform is limited to the amount paid by you for the relevant booking. This limitation does not apply to liability that cannot be excluded under applicable law.

## 12. Termination

We may suspend or terminate your account for breach of these terms, fraudulent activity, or misuse of the platform.

## 13. Governing Law and Dispute Resolution

These terms are governed by the laws of the Netherlands. Any disputes arising under these terms are subject to the exclusive jurisdiction of the competent courts of Eindhoven, the Netherlands, without prejudice to any mandatory consumer-protection rights you may have under the law of your country of residence.

## 14. Changes to These Terms

We may update these terms from time to time. The “Last updated” date above reflects the most recent revision. Continued use of the platform after changes take effect constitutes acceptance.

## 15. Contact

support@immigroov.com | Legal notices: immigroov@gmail.com

This document should be read together with the Website Terms of Use, Privacy Policy, Payment Terms, Refund & Cancellation Policy, and Groovia AI Terms of Use.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 10  payment-terms ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '10';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 10 (payment-terms) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Scope

These Payment Terms apply to all payments made on the Immigroov platform, and should be read together with the applicable Customer Terms & Conditions (India or Rest of World) and the Refund & Cancellation Policy.

## 2. Pricing Structure

The total price shown to you at checkout is made up of:

- The Mentor’s session price; and

- Immigroov’s platform/operations fee, added to the Mentor’s session price.

Prices may be adjusted based on the Mentor selected and the pathway or service booked. Applicable taxes (including GST, where applicable) are shown at checkout and included in or added to your total.

## 3. Payment Processing

- If you are located in India, your payment is collected by Immigroov Consulting India LLP via Razorpay, and your contract for the underlying service is with Immigroov Consulting India LLP as described in the Customer Terms & Conditions — India.

- If you are located outside India, your contract for the underlying service is with Immigroov Consulting VOF. Payment collection for your transaction may be processed via Immigroov Consulting India LLP acting on Immigroov Consulting VOF’s behalf under an internal payment-collection arrangement, using the payment provider(s) made available to you at checkout.

- Your payment details are processed directly by our payment provider(s); Immigroov does not store your full card details.

## 4. Currency

Prices may be displayed and charged in your local currency where supported. Currency conversion, where applicable, is handled by the payment provider, and any conversion fees charged by your bank or card issuer are not controlled by Immigroov.

## 5. Failed or Disputed Payments

If a payment fails, your booking will not be confirmed. If you dispute a charge (including via a chargeback), contact support@immigroov.com first so we can investigate before any dispute is raised with your bank or card provider.

## 6. Refunds

Refunds are governed by the Refund & Cancellation Policy. Where a refund is approved, it is processed within 5–7 business days; payment gateway fees are non-refundable under any circumstances.

## 7. Taxes

You are responsible for any taxes applicable to your purchase, which are calculated and shown at checkout based on your location and applicable law. Immigroov is responsible for remitting applicable taxes it is legally required to collect.

## 8. Changes to These Terms

We may update these Payment Terms from time to time. The “Last updated” date above reflects the most recent revision.

## 9. Contact

support@immigroov.com

This document should be read together with the applicable Customer Terms & Conditions and the Refund & Cancellation Policy.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 11  mentor-agreement ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '11';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 11 (mentor-agreement) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$## 1. Parties

This Agreement is between Immigroov Consulting VOF, KVK 96600462, VAT NL867678215B01, registered address Eindhoven, Netherlands (“Immigroov”, “we”, “us”), and you, the individual registering as a Mentor on the Immigroov platform (“Mentor”, “you”).

Where you are located in India and payment logistics are handled locally, Immigroov Consulting India LLP may act on Immigroov’s behalf for India-based payment processing, as described in Section 7, but your contractual relationship as a Mentor is with Immigroov Consulting VOF.

## 2. Eligibility

You must be at least 18 years old to register as a Mentor. Immigroov determines Mentor eligibility, which may include verification of your relevant lived or professional experience relating to the destination country/pathway you offer mentoring for.

## 3. Independent Contractor Status

You provide mentoring services as an independent contractor, not as an employee, partner, agent, or representative of Immigroov. Nothing in this Agreement creates an employment, partnership, joint venture, or agency relationship. You are responsible for your own tax and social security obligations arising from income earned through the platform.

## 4. Nature of Mentoring Services

You agree to provide mentoring services based on your genuine personal or professional lived experience. You are not, by virtue of this Agreement, presented as a licensed immigration lawyer, financial advisor, or other regulated professional, and must not represent yourself as such on the platform unless you hold and disclose the relevant professional qualification.

## 5. Service Standards and Quality

Immigroov sets Mentor quality standards, which may include profile accuracy, responsiveness, session conduct, and adherence to the Mentor Code of Conduct. Immigroov may review Mentor performance, including through verified Customer reviews, and may suspend or remove a Mentor from the platform for failure to meet these standards.

## 6. Cancellation and Rescheduling

You set your own cancellation and rescheduling notice window, which is displayed on your Mentor profile and shown to Customers before booking. You are responsible for keeping this setting accurate and for honoring the terms you have set, as described in the Refund & Cancellation Policy.

## 7. Commission and Payment

- Your compensation is calculated as a share of the session price, with Immigroov retaining a commission. The applicable commission range and how it is determined are set out in the separate Mentor Commission & Payout Terms, which forms part of this Agreement.

- Your payout is processed to the bank account or payment method you submit and designate, in your preferred supported currency, regardless of your location. You are responsible for keeping your submitted payout details accurate and up to date.

- Where a payout involves an international/cross-border transfer, any related transfer fees or charges are borne solely by you and will be deducted from your payout.

- Payment is contingent on session completion, as defined in the Mentor Commission & Payout Terms.

- If you fail to deliver a booked session without prior notification, or cancel or reschedule later than your own stated notice window, and the Customer does not accept an alternative (such as a replacement Mentor) and is instead granted a refund, you are responsible for the resulting penalty and associated costs, which may be deducted from your current or future payouts.

## 8. Mentor Replacement

If you are unable to continue providing services to a Customer (including due to unavailability, breach of standards, or removal from the platform), Immigroov may offer the Customer a replacement Mentor. This does not entitle you to compensation for sessions not delivered.

## 9. Confidentiality

You agree to keep confidential any non-public information you receive about Immigroov’s business, platform, or Customers, both during and after your engagement, except as required by law.

## 10. Non-Circumvention

You agree not to solicit Customers you meet through the platform to transact directly outside the platform in order to avoid applicable Immigroov fees, for the duration of this Agreement and for 12 months after it ends. Breach of this clause is grounds for termination of this Agreement under Section 12; upon such a breach, you will not be assigned any new bookings, and this Agreement will terminate once any sessions already booked at the time of the breach are completed.

## 11. Data Protection

Personal data relating to your engagement, and any Customer data you access in the course of providing mentoring services, is processed as described in the separate Mentor Data Processing Addendum, which forms part of this Agreement.

## 12. Term and Termination

This Agreement remains in effect while you are an active Mentor on the platform. Either party may terminate this Agreement at any time, with or without cause, subject to completion of any sessions already booked at the time of termination.

## 13. Limitation of Liability

To the maximum extent permitted under applicable law, Immigroov’s liability to you under this Agreement is limited to unpaid compensation properly owed to you for completed sessions. This limitation does not apply to liability that cannot be excluded under applicable law.

## 14. Governing Law and Dispute Resolution

This Agreement is governed by the laws of the Netherlands. Any disputes are subject to the exclusive jurisdiction of the competent courts of Eindhoven, the Netherlands.

## 15. Changes to This Agreement

We may update this Agreement from time to time. Continued provision of mentoring services after changes take effect constitutes acceptance of the revised terms.

## 16. Contact

support@immigroov.com | Legal notices: immigroov@gmail.com

This Agreement should be read together with the Mentor Commission & Payout Terms, Mentor Code of Conduct, Mentor Data Processing Addendum, and Privacy Policy.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 12  mentor-commission-payout ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '12';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 12 (mentor-commission-payout) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$This document forms part of, and should be read together with, the Mentor Agreement.

## 1. Commission Structure

Immigroov retains a commission on each completed session, and you receive the remaining share as your Mentor compensation. The commission percentage is dynamic and determined by Immigroov, and may vary between individual Mentors and sessions based on factors including, but not limited to: pathway/corridor demand, Mentor experience and quality rating, session type, and platform pricing considerations at the time of booking.

Immigroov’s commission is fully dynamic and is determined as agreed between you and Immigroov, shown to you before you accept each booking, as described in Section 2.

## 2. Where to Find Your Applicable Rate

Your specific commission rate for a given session type is shown to you in your Mentor dashboard prior to acceptance of a booking. You are not committed to a rate that has not been disclosed to you before the relevant session is confirmed.

## 3. Session Completion Requirement

Payment is triggered on session completion, defined as a session that is not reported by you or the Customer as a no-show, consistent with the Refund & Cancellation Policy. Sessions reported as a no-show are not eligible for payout, and are instead handled under the Refund & Cancellation Policy.

## 4. Changes to Commission Rates

Immigroov may adjust commission rates and ranges from time to time. Changes will be reflected in your Mentor dashboard and will apply to new bookings from the date of change; rates already disclosed and confirmed for existing bookings are not retroactively changed.

## 5. Payout Schedule

- Payouts are processed in batches, generally on a set schedule communicated to you via your Mentor dashboard.

- Payouts are subject to a minimum post-completion holding period before release, to allow for dispute or refund review.

- Your payout is made to the bank account or payment method you submit and designate, in your preferred supported currency, regardless of your location. You are responsible for keeping your submitted payout details accurate and up to date.

- Where a payout involves an international/cross-border transfer, any related transfer fees or charges are borne solely by you and will be deducted from your payout.

## 6. Deductions

Payment gateway and payout processing fees, where applicable, may be deducted from your payout amount. Applicable taxes on your earnings are your own responsibility to report and remit, consistent with your independent contractor status under the Mentor Agreement.

## 7. Disputes Over Payout

If you believe a payout amount or timing is incorrect, contact support@immigroov.com with your booking reference. Disputes are reviewed manually on a case-by-case basis.

## 8. Contact

support@immigroov.com

This document should be read together with the Mentor Agreement.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 13  mentor-code-of-conduct ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '13';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 13 (mentor-code-of-conduct) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$This Code of Conduct forms part of, and should be read together with, the Mentor Agreement.

## 1. Purpose

This Code sets the standard of behavior expected of all Mentors on the Immigroov platform, to protect Customers and maintain trust in the platform.

## 2. Honesty and Accuracy

- Represent your experience, qualifications, and destination-country knowledge honestly and accurately on your profile.

- Do not claim to be a licensed immigration lawyer, financial advisor, or other regulated professional unless you hold and disclose the relevant qualification.

- Do not guarantee immigration outcomes (such as visa approval); mentoring reflects personal experience and guidance, not a guaranteed result.

- Do not guarantee, promise, or imply commitments over which you have no control, including job offers, employment placement, or university/college admission; mentoring may share experience and strategy, but outcomes with employers and educational institutions are decided solely by those third parties.

## 3. Professional Conduct

- Attend booked sessions on time and prepared.

- Communicate respectfully with Customers at all times.

- Do not discriminate against Customers on the basis of protected characteristics under applicable law.

- Do not provide guidance while impaired, or in a manner that could reasonably mislead or harm a Customer.

## 4. Confidentiality

- Keep any personal or sensitive information shared by a Customer during a session confidential, and do not use it for any purpose beyond delivering the mentoring service.

## 5. Platform Integrity

- Do not solicit Customers to transact outside the platform to avoid Immigroov’s fees.

- Do not submit or solicit fake reviews.

- Do not misuse Groovia AI or other platform tools.

## 6. Reporting Concerns

If you witness or experience conduct that violates this Code, report it to support@immigroov.com.

## 7. Consequences of Violation

Violations of this Code may result in warnings, suspension, or permanent removal from the platform, at Immigroov’s discretion, in addition to any other rights available under the Mentor Agreement.

## 8. Contact

support@immigroov.com

This document should be read together with the Mentor Agreement.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  -- ── 14  mentor-data-processing ──
  SELECT id INTO v_id FROM legal_documents WHERE code = '14';
  IF v_id IS NULL THEN
    RAISE WARNING 'no catalogue row for 14 (mentor-data-processing) - run legal_documents_setup.sql first';
  ELSIF (SELECT current_version_id FROM legal_documents WHERE id = v_id) IS NOT NULL THEN
    v_skip := v_skip + 1;
  ELSE
    PERFORM legal_save_draft(v_id, v_actor, $doc$This Addendum forms part of, and should be read together with, the Mentor Agreement.

## 1. Purpose

This Addendum explains how your personal data is processed by Immigroov, and your own data protection obligations as a Mentor when you access Customer personal data in the course of providing mentoring services.

## 2. Immigroov as Controller of Your Data

Immigroov Consulting VOF is the data controller for personal data relating to your Mentor profile, engagement, performance, and payout (name, contact details, profile content, session records, and payment records), and processes this data as described in our Privacy Policy. Where you are based in India, Immigroov Consulting India LLP may process related payout information on Immigroov Consulting VOF’s behalf, as described in the Mentor Commission & Payout Terms.

## 3. Your Role Regarding Customer Data

In the course of a mentoring session, you will receive certain personal data about the Customer (such as their name, contact details, and information they choose to share about their circumstances) directly from the Customer or via the platform. In relation to this Customer data:

- You must use it solely to deliver the mentoring service to that Customer, and for no other purpose.

- You must not retain, copy, or use Customer data after the engagement for marketing, solicitation, or any purpose outside the platform.

- You must keep Customer data confidential and take reasonable steps to protect it from unauthorized access, loss, or disclosure.

- You must not share Customer data with any third party, except as strictly necessary to deliver the session (e.g., a scheduling tool you use, provided it offers an equivalent level of protection).

- If applicable law in your jurisdiction (such as GDPR, where you are based in the EU/EEA) treats you as an independent controller for this processing, you are responsible for your own compliance with that law in respect of your handling of Customer data, in addition to the obligations in this Addendum.

## 4. Data Breach Notification

If you become aware of any unauthorized access to, loss of, or disclosure of Customer or platform data, you must notify Immigroov immediately at support@immigroov.com.

## 5. International Data Transfers

If you are based outside the European Economic Area and access personal data of an EU/EEA-based Customer (or vice versa), you agree to handle that data consistently with the safeguards described in this Addendum, and to cooperate with Immigroov in implementing any additional contractual safeguards (such as Standard Contractual Clauses) that may be required by applicable law.

## 6. Data on Termination

On termination of your Mentor Agreement, you must cease using and delete any Customer data in your possession that is not otherwise required to be retained under applicable law, except data retained within the Immigroov platform itself.

## 7. Contact

support@immigroov.com | Privacy inquiries: vinothkannan@immigroov.com

This document should be read together with the Mentor Agreement and Privacy Policy.$doc$);
    PERFORM publish_legal_document(v_id, v_actor, 'Initial import');
    v_done := v_done + 1;
  END IF;

  RAISE NOTICE 'legal seed: published % document(s), skipped % already published', v_done, v_skip;
END $seed$;

-- Confirm: all 14 should show a version, a date and a publisher.
SELECT d.code, d.title, v.version, v.published_at,
       COALESCE(v.published_by_name, v.published_by_email) AS published_by,
       LENGTH(v.content) AS chars
  FROM legal_documents d
  LEFT JOIN legal_document_versions v ON v.id = d.current_version_id
 ORDER BY d.sort_order;
