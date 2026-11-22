# CohortDesk — Software Requirements Specification

**Document version:** 1.0  
**Status:** Product baseline for MVP planning and implementation  
**Product type:** Multi-tenant SaaS for independent training-center management  
**Initial markets:** Algeria and international markets  
**Primary language targets:** English, French and Arabic (including right-to-left layout)  
**Prepared:** 15 November 2026

---

## 1. Document purpose

This document defines the product scope, users, functional requirements, business rules, data concepts, quality requirements, acceptance criteria and delivery boundaries for CohortDesk.

It is the product reference for design and implementation. Implementation decisions may refine the architecture, but the development agent must not silently change the product scope or omit requirements. Any material change must be recorded in the project decision log and reflected in this document.

This SRS describes the first sellable release (MVP) and the intended product direction beyond it. It deliberately distinguishes **MVP requirements** from **later-release capabilities** so the first release remains achievable.

## 2. Product summary

### 2.1 Product vision

CohortDesk is a practical, multilingual, all-in-one management platform for independently operated training centers and academies. It helps a center manage its administrative and academic operations in one place: prospective learners, student records, courses, cohorts, instructors, schedules, attendance, assessments, fees, payments, certificates and operational reporting.

CohortDesk is an **education/training-center administration system**, not initially a full Learning Management System (LMS). Its first release manages the organization and delivery of instructor-led training. It does not host video courses, provide a full online learning environment or replace a dedicated LMS.

### 2.2 Product positioning

**Working positioning statement:**

> Your entire training center. One organized workspace.

CohortDesk should be easier to adopt and operate than a collection of spreadsheets, messaging conversations and disconnected administrative tools. It should provide a coherent workflow from inquiry and application through enrollment, teaching, payment, assessment and course completion.

The product must not claim to be unique merely because it includes common school-management features. Differentiation should be pursued through usability, quick onboarding, reliable workflows, transparent pricing, multilingual support, data portability and responsive support. These advantages require market validation.

### 2.3 Initial target customers

Primary customer segment:

- Independent language schools and academies.
- IT and computer-training centers.
- Vocational and professional training providers.
- Short-course and certification-training organizations.

Initial ideal-customer hypothesis (to validate, not a market fact):

- Approximately 50–500 active learners.
- Approximately 2–15 instructors.
- Approximately 3–30 active courses.
- A small administrative team.
- Currently using spreadsheets, paper, messaging apps or basic software.

Large universities, public-school systems, government procurement and complex corporate learning departments are not initial target segments.

### 2.4 Product principles

1. **Operational completeness:** Core modules must work together as an end-to-end process.
2. **Simple daily use:** Common tasks must be easy for non-technical administrators and instructors.
3. **Data integrity:** Enrollment, attendance, grades and financial records must be dependable and traceable.
4. **Tenant isolation:** A customer must never access another customer's data.
5. **International readiness:** Support English, French and Arabic, including correct RTL behavior.
6. **Data portability:** Customers must be able to export their important records.
7. **Maintainability:** Prefer a modular monolith and simple deployment over premature distributed architecture.
8. **Incremental delivery:** Deliver and test complete workflows in small milestones.

## 3. Scope

### 3.1 MVP scope — first sellable release

The MVP is intended to let one independent training center run its normal administrative and instructor-led training operations.

The MVP includes:

1. Tenant/organization creation and configuration.
2. User authentication, organization memberships, roles and permissions.
3. Organization profile, locale, timezone and primary currency settings.
4. Student records and student lifecycle management.
5. Basic admissions/application tracking.
6. Programs, courses and course modules.
7. Academic years or configurable academic periods.
8. Cohorts/groups and student enrollment.
9. Instructor and staff records.
10. Rooms and basic room-capacity management.
11. Timetable and scheduled sessions, including recurring schedules and conflict detection.
12. Session-based attendance.
13. Assessments, grade entry and configurable weighted results.
14. Fee agreements, installment schedules, payments, allocations, refunds/adjustments and receipts.
15. Basic operational expense tracking (not full accounting).
16. Certificate generation as PDF with unique verification identifiers.
17. Dashboard and core operational, academic and financial reports.
18. CSV/XLSX import where specified and CSV/XLSX/PDF export where appropriate.
19. In-app notifications or actionable alerts for relevant operational events.
20. Audit trail for sensitive administrative and financial actions.
21. Responsive web interface for desktop, tablet and mobile browsers.
22. English, French and Arabic interface localization, including genuine Arabic RTL layouts.
23. Deployment, monitoring basics, off-server backups and documented restoration.

**Single-branch launch constraint:** The first release is designed for one operating branch/location per organization. The data model should avoid decisions that make multi-branch support prohibitively difficult later, but a complete multi-branch user experience is not required in MVP.

### 3.2 Later releases

The following are valuable but are not MVP blockers unless the product owner explicitly changes scope:

- Public center profile and public course catalog pages.
- Public application/enquiry forms.
- Student portal and student login.
- Parent/guardian portal and linked-family access.
- Automated email delivery and richer communication workflows.
- WhatsApp Business API integration (subject to provider approval, policy and cost).
- QR-based attendance.
- Multiple branches and inter-branch reporting.
- Advanced room/equipment/inventory management.
- Advanced certificate designer and additional templates.
- Subscription self-service and integrated SaaS billing.
- Payment gateway integrations, subject to supported markets and merchant eligibility.
- Public API and third-party integrations.
- Advanced reporting and analytics.
- LMS functionality or integration with an external LMS.

### 3.3 Explicitly out of scope for MVP

- Video hosting or video conferencing.
- SCORM/xAPI content hosting and full LMS authoring.
- Online exam delivery with question banks, proctoring or anti-cheating systems.
- Native Android or iOS applications.
- Payroll, statutory HR or employment-law management.
- Full double-entry accounting, tax filing or statutory bookkeeping.
- Building a payment gateway or holding customer funds.
- Course marketplace or public instructor marketplace.
- AI tutor, AI grading or local/server-side AI inference.
- Complex multi-branch operations.
- Parent and student self-service portals.
- Custom website builder.

## 4. Users, roles and permissions

### 4.1 User concepts

- **Organization:** A customer training center or academy (tenant).
- **User:** A person who can authenticate to CohortDesk.
- **Membership:** A user's relationship with an organization, including role and permissions.
- **Student record:** An academic/person record. A student does not need a login in MVP.
- **Staff record:** An instructor or employee record. A staff member may or may not have a login.

A user may eventually belong to more than one organization, with separate memberships and permissions. A user's access in one organization must not imply access to another.

### 4.2 MVP roles

| Role | Intended access |
|---|---|
| Owner | Full organization access, settings, membership and role management, all modules and exports. |
| Administrator | Day-to-day management across most modules; cannot transfer ownership or perform owner-only security actions. |
| Receptionist | Admissions, student records, enrollment and basic scheduling; no finance configuration or unrestricted financial reporting. |
| Academic coordinator | Courses, programs, cohorts, instructors, schedules, attendance, assessments and academic reports. |
| Instructor | Assigned cohorts/sessions, class rosters, attendance and permitted grade/assessment entry. No financial access by default. |
| Finance officer | Fees, installments, payments, refunds/adjustments and financial reports. No unrestricted academic or security administration. |

The system should implement permissions in a central, testable authorization layer. Do not scatter role-name checks throughout views or templates.

### 4.3 Later roles

Student and parent/guardian login roles are planned for a later release. The MVP may store guardian/contact details, but must not expose a parent portal or student self-service area.

### 4.4 Permission requirements

- Access must be checked on the server for every protected operation.
- Hiding a navigation item is not authorization.
- Instructors may only access cohorts/sessions to which they are assigned, subject to explicit permissions.
- Finance access must be denied by default to instructors and receptionists.
- Only authorized roles may issue, void or reissue certificates.
- Only authorized roles may record, reverse or adjust financial transactions.
- Only the owner or appropriately authorized administrator may manage users and permissions.
- Export operations must obey the same tenant and role restrictions as on-screen access.

## 5. Functional requirements

Requirement priority uses:

- **MUST:** Required for MVP acceptance.
- **SHOULD:** Strongly preferred; may be deferred only with a documented reason and product-owner approval.
- **LATER:** Post-MVP capability.

### 5.1 Organization, onboarding and settings

**ORG-001 (MUST):** A new customer must be able to create an organization and its initial owner account through a secure onboarding flow, unless launch is deliberately restricted to invitation-based onboarding.

**ORG-002 (MUST):** An organization must have a name, unique internal identifier/slug, contact details and status.

**ORG-003 (MUST):** An organization must configure its primary currency, timezone, preferred locale and date/number formatting.

**ORG-004 (MUST):** The supported interface locales must include English, French and Arabic.

**ORG-005 (MUST):** Arabic mode must use appropriate RTL layout behavior, including navigation, forms, tables, icons where direction matters, alignment and mixed-direction values.

**ORG-006 (MUST):** Organization-specific data and configuration must be isolated from every other organization.

**ORG-007 (MUST):** An organization must be able to update its display name, logo and contact information.

**ORG-008 (MUST):** Organization deactivation and data deletion must be restricted to authorized ownership/admin workflows and must not be triggered by ordinary user actions.

**ORG-009 (SHOULD):** Provide a first-run setup checklist guiding the owner through organization settings, courses, staff, cohorts and first student import.

### 5.2 Authentication and membership

**AUTH-001 (MUST):** Users must authenticate securely using an appropriate credential flow.

**AUTH-002 (MUST):** Passwords must be stored using the framework's approved adaptive password hashing mechanism; plaintext passwords must never be stored or logged.

**AUTH-003 (MUST):** Sessions must use secure cookie settings in production, including HTTPS-only cookies, appropriate SameSite behavior and CSRF protection.

**AUTH-004 (MUST):** Users must be able to sign out; password reset must use a secure, time-limited process if email delivery is enabled for launch.

**AUTH-005 (MUST):** Organization membership and role must be explicit. A user without membership must not access an organization's data.

**AUTH-006 (MUST):** An owner/authorized administrator must be able to invite or create staff users and assign permitted roles.

**AUTH-007 (MUST):** Suspended or deactivated users must not continue to access protected organization data after the relevant session is invalidated or expires.

**AUTH-008 (SHOULD):** Provide login rate limiting and basic suspicious-login protection.

### 5.3 Student management

**STU-001 (MUST):** Authorized users must be able to create, view, edit, search, filter and archive student records.

**STU-002 (MUST):** A student record must support a unique organization-scoped student number or identifier.

**STU-003 (MUST):** Student profiles must support name, relevant contact details, optional date of birth, optional address, optional emergency/guardian contact, status, registration date and administrative notes.

**STU-004 (MUST):** The application must not require a student login to create or maintain a student record.

**STU-005 (MUST):** A student must be able to have multiple enrollment records over time without creating duplicate student identities.

**STU-006 (MUST):** The student profile must present relevant enrollment, cohort, attendance, assessment, payment and certificate history according to the viewer's permissions.

**STU-007 (MUST):** Student status must support at least applicant (where applicable), active, suspended, graduated, withdrawn and archived, with controlled transitions.

**STU-008 (MUST):** Archiving or withdrawing a student must preserve historical enrollment, attendance, grade and financial records.

**STU-009 (MUST):** Duplicate detection should use configurable matching signals (for example name plus phone/email) and must not silently merge records.

**STU-010 (SHOULD):** Support attaching relevant documents to a student record with file type/size restrictions and access controls.

### 5.4 Admissions and applications

**ADM-001 (MUST):** Authorized users must be able to record a prospective learner/application.

**ADM-002 (MUST):** An application must record the desired course/program, applicant details, application date, source (if known), status and internal notes.

**ADM-003 (MUST):** Application statuses must include at least new, under review, accepted, rejected and withdrawn.

**ADM-004 (MUST):** An accepted application must be convertible into a student record without re-entering all information.

**ADM-005 (MUST):** Staff must be able to enroll an existing student directly without an application.

**ADM-006 (SHOULD):** Support a waitlist for a full cohort.

**ADM-007 (LATER):** Public application forms and public course pages.

### 5.5 Academic structure: programs, courses and modules

**ACAD-001 (MUST):** Authorized users must be able to create and manage reusable courses.

**ACAD-002 (MUST):** A course must support title, description, category, level, duration, learning objectives, prerequisites (optional), default fee (optional), status and configurable grading settings.

**ACAD-003 (MUST):** A course may contain an ordered list of modules/topics.

**ACAD-004 (SHOULD):** Support programs containing multiple courses. A center that does not need programs must be able to use courses directly without unnecessary setup.

**ACAD-005 (MUST):** Course records referenced by historical cohorts must not be hard-deleted; use archival/deactivation.

**ACAD-006 (MUST):** The application must support configurable academic years or academic periods for organization reporting and grouping.

### 5.6 Cohorts, groups and enrollment

**COH-001 (MUST):** Authorized users must be able to create and manage cohorts/groups based on a course (and optionally a program).

**COH-002 (MUST):** A cohort must support name, course, optional program, start/end dates, capacity, status, default instructor(s), schedule information and optional classroom assignment.

**COH-003 (MUST):** Students must be enrolled into a cohort through an enrollment record.

**COH-004 (MUST):** Enrollment must preserve the student's course/cohort history, enrollment date, enrollment status and agreed fee information.

**COH-005 (MUST):** The system must warn or prevent enrollment beyond configured capacity according to an organization setting.

**COH-006 (MUST):** A student may have multiple concurrent or historical enrollments, subject to configurable conflict warnings.

**COH-007 (MUST):** Cohort status must include planned, active, completed and cancelled (or equivalent clearly defined lifecycle states).

**COH-008 (MUST):** Cohort completion must not automatically issue a certificate unless the completion criteria and authorized action are satisfied.

### 5.7 Staff and instructors

**STAFF-001 (MUST):** Authorized users must be able to create, edit, search and archive instructor/staff records.

**STAFF-002 (MUST):** A staff record must support name, contact details, role/job title, specializations, employment/availability status and optional qualifications/documents.

**STAFF-003 (MUST):** Instructors must be assignable to courses and cohorts.

**STAFF-004 (MUST):** The system must provide a view of an instructor's assigned cohorts and scheduled sessions.

**STAFF-005 (SHOULD):** Support basic instructor availability rules to assist with scheduling conflict detection.

**STAFF-006 (MUST):** Staff records and login accounts must be separate concepts. A staff member may exist without a login, and a login must have explicit organization membership and permissions.

**STAFF-007 (LATER):** Payroll and statutory HR functions.

### 5.8 Rooms and scheduling

**SCH-001 (MUST):** Authorized users must be able to create and manage classrooms/rooms with names and optional capacity/location details.

**SCH-002 (MUST):** A cohort schedule must generate individual session records for scheduled class occurrences.

**SCH-003 (MUST):** Support one-off sessions and recurring schedules on selected weekdays within a date range.

**SCH-004 (MUST):** A session must record cohort, start/end date-time, timezone context, instructor(s), optional room, status and optional lesson notes.

**SCH-005 (MUST):** Detect overlapping room bookings and prevent or explicitly warn about double-booking according to policy.

**SCH-006 (MUST):** Detect instructor schedule overlaps and prevent or explicitly warn about double-booking according to policy.

**SCH-007 (MUST):** Provide day, week and list views for scheduled sessions.

**SCH-008 (MUST):** Authorized users must be able to edit or cancel an individual generated session without unintentionally changing the entire recurring series.

**SCH-009 (MUST):** Store timestamps consistently and display them in the organization's configured timezone.

**SCH-010 (SHOULD):** Warn when session dates fall outside cohort dates or instructor availability.

### 5.9 Attendance

**ATT-001 (MUST):** Each scheduled session must have an attendance register for its enrolled students.

**ATT-002 (MUST):** Attendance status must include present, absent, late and excused.

**ATT-003 (MUST):** There must be at most one current attendance record per student per session.

**ATT-004 (MUST):** Authorized instructors must be able to record attendance for assigned sessions.

**ATT-005 (MUST):** Authorized administrators/coordinators must be able to correct attendance, with an audit record.

**ATT-006 (MUST):** Calculate attendance statistics per student, cohort and relevant reporting period.

**ATT-007 (MUST):** Excused absences and late arrivals must be distinguishable from unexcused absences in reports.

**ATT-008 (MUST):** Attendance must not be recorded for a student who is not enrolled in the relevant cohort/session unless an authorized correction workflow explicitly supports a documented exception.

**ATT-009 (SHOULD):** Provide printable/exportable session attendance registers.

**ATT-010 (LATER):** QR-based check-in and automated messaging on absence.

### 5.10 Assessments and grades

**GRD-001 (MUST):** Authorized academic users must be able to create assessments linked to a course, cohort or relevant module.

**GRD-002 (MUST):** An assessment must support title, assessment type, date, maximum score, weight (where applicable) and instructions/notes.

**GRD-003 (MUST):** Authorized instructors/coordinators must be able to record and update student grades for learners enrolled in the relevant cohort.

**GRD-004 (MUST):** The application must support configurable grading scales and pass thresholds; percentage scoring must not be hardcoded as the only model.

**GRD-005 (MUST):** Weighted averages must be calculated only when the required weights and scores are valid. The system must clearly indicate missing/incomplete grading inputs.

**GRD-006 (MUST):** Grade changes after publication/finalization must be permission-controlled and auditable.

**GRD-007 (MUST):** Provide student and cohort grade reports for authorized users.

**GRD-008 (LATER):** Online exam delivery, question banks, proctoring and automated marking.

### 5.11 Fees, payments and financial records

**FIN-001 (MUST):** Each enrollment must support an agreed fee amount and the organization's configured currency.

**FIN-002 (MUST):** Authorized finance users must be able to define installment schedules with amounts and due dates.

**FIN-003 (MUST):** The system must record payments with amount, date/time, method, reference (optional), recorded-by user and notes.

**FIN-004 (MUST):** Support partial payments and allocation of a payment to one or more outstanding installments where permitted.

**FIN-005 (MUST):** Calculate outstanding balance from fee obligations, valid allocations, discounts/waivers and authorized adjustments; do not rely on a manually editable balance field as the source of truth.

**FIN-006 (MUST):** Support payment methods such as cash, bank transfer and other manually recorded methods. This is transaction recording, not payment processing.

**FIN-007 (MUST):** Generate printable/downloadable payment receipts with organization identity, receipt identifier, student, amount, currency, date, method and relevant enrollment/payment reference.

**FIN-008 (MUST):** Corrections, refunds and reversals must be recorded as traceable transactions; do not silently delete or overwrite posted financial history.

**FIN-009 (MUST):** Support discounts, waivers or fee adjustments with reason, authorizing user and audit record.

**FIN-010 (MUST):** Provide financial summaries for collected payments, outstanding balances, overdue installments and payment history.

**FIN-011 (MUST):** Support basic operating expense records with category, amount, currency, date, payee/description, optional attachment and recorded-by user.

**FIN-012 (MUST):** Clearly distinguish revenue/collections, receivables and expenses. Do not present basic cash summaries as audited profit or statutory accounts.

**FIN-013 (MUST):** Each organization has one primary currency in MVP. Multi-currency accounting and automatic exchange-rate conversion are out of scope.

**FIN-014 (MUST):** Monetary values must use fixed-precision decimal representations, never binary floating point.

**FIN-015 (MUST):** Financial reports and exports must obey finance permissions and organization isolation.

**FIN-016 (LATER):** Integrated payment gateways, recurring SaaS billing and automatic payment collection.

### 5.12 Certificates and documents

**DOC-001 (MUST):** Authorized users must be able to generate a certificate for an eligible student/enrollment.

**DOC-002 (MUST):** A certificate must support student name, course/program, issue date, issuing organization, unique certificate identifier and configured signatory information.

**DOC-003 (MUST):** Certificates must be generated as downloadable/printable PDFs.

**DOC-004 (MUST):** Certificate identifiers must be unique within the system and verifiable through an appropriate verification endpoint or page.

**DOC-005 (MUST):** Certificate issuance, voiding and reissue must be permission-controlled and auditable.

**DOC-006 (MUST):** Certificate generation must not imply government accreditation or official recognition. The organization is responsible for the validity of its training and certificate claims.

**DOC-007 (SHOULD):** Support a small set of professional templates with organization logo and configured signatory details.

**DOC-008 (SHOULD):** Support controlled attachments for relevant student and staff documents.

### 5.13 Dashboard, reports and exports

**REP-001 (MUST):** The dashboard must show current operational indicators derived from persisted data.

**REP-002 (MUST):** Dashboard information must be filtered by the active organization and the user's permissions.

**REP-003 (MUST):** Provide reports for students, enrollments, cohort capacity, attendance, assessments/results, payments, outstanding balances and expenses.

**REP-004 (MUST):** Reports must support useful date, academic-period, course and cohort filters where relevant.

**REP-005 (MUST):** Authorized users must be able to export appropriate tabular data to CSV or XLSX.

**REP-006 (MUST):** Suitable reports and documents must be exportable/printable as PDF.

**REP-007 (MUST):** Exported data must follow the same authorization and tenant-isolation rules as interactive screens.

**REP-008 (MUST):** Dashboard sample values, if used in a demo environment, must be clearly identified as demo data. Production dashboards must never display hardcoded sample metrics.

### 5.14 Import and migration

**IMP-001 (MUST):** Authorized users must be able to import student data from CSV and XLSX files.

**IMP-002 (MUST):** Before committing an import, show a preview and validate required fields, formats and potential duplicates.

**IMP-003 (MUST):** Report rejected rows with actionable error descriptions and do not silently discard records.

**IMP-004 (MUST):** Import operations must be transactional or provide an explicit, safe partial-import report; users must understand what was and was not imported.

**IMP-005 (SHOULD):** Provide downloadable import templates and field-mapping support.

**IMP-006 (LATER):** Broad migration connectors for named competitor systems.

### 5.15 Notifications and follow-ups

**NOT-001 (MUST):** Provide an in-app notification or action center for important operational situations, such as overdue installments, upcoming due dates and configured attendance concerns.

**NOT-002 (MUST):** Notification thresholds must be configurable where relevant and must avoid duplicate alert generation for the same condition.

**NOT-003 (MUST):** Notifications must link to the relevant record when the user is authorized to view it.

**NOT-004 (SHOULD):** Support basic message templates for staff to copy into email or WhatsApp manually.

**NOT-005 (LATER):** Automated email, SMS and approved WhatsApp Business messaging integrations.

### 5.16 Audit trail

**AUD-001 (MUST):** Record sensitive actions, including user/role changes, student record archival, attendance corrections, grade changes, payment creation/reversal, fee adjustments, certificate issuance/voiding and organization-setting changes.

**AUD-002 (MUST):** Audit entries must identify organization, actor, action, affected record, timestamp and relevant before/after metadata where safe and useful.

**AUD-003 (MUST):** Ordinary users must not be able to edit or delete audit entries.

**AUD-004 (MUST):** Avoid logging passwords, authentication secrets, full payment-card data or unnecessary sensitive personal data.

### 5.17 Public website and portals

**PORT-001 (LATER):** Public center profile and course catalog.

**PORT-002 (LATER):** Public enquiry/application submission.

**PORT-003 (LATER):** Student self-service login and portal.

**PORT-004 (LATER):** Parent/guardian portal with explicitly linked student access and limited permissions.

## 6. Core business rules

The following rules are mandatory unless formally revised.

### 6.1 Tenant isolation

- Every tenant-owned business record must be associated with exactly one organization.
- Every read, write, update, delete, search, report, export, document download and background operation must enforce organization scope.
- Organization context must come from a validated authenticated membership/session, not from an untrusted organization ID supplied by the browser alone.
- Cross-tenant access must be denied even if a user guesses another record's identifier or manipulates a URL/API request.
- Automated tests must explicitly attempt cross-tenant access for representative modules.

### 6.2 Record lifecycle and deletion

- Historical academic and financial records must be preserved.
- Prefer archive/deactivate workflows over hard deletion for students, courses, cohorts, staff and other records referenced by history.
- Hard deletion may be allowed only for unreferenced draft data or controlled privacy/deletion procedures.
- Deletion or anonymization must consider relational integrity, audit requirements and applicable law.

### 6.3 Enrollment and academic history

- Enrollment is a distinct record linking a student to a cohort (and through it to a course/program).
- A student may have multiple historical enrollments.
- Enrollment status changes must be recorded and validated.
- Cohort completion does not automatically prove that every student has satisfied completion criteria.

### 6.4 Scheduling

- All stored event times must be unambiguous and consistently normalized (prefer UTC storage) with organization-timezone display.
- Recurring schedule generation must handle daylight-saving/timezone rules correctly where relevant.
- Room and instructor overlaps must be detected.
- Editing one generated session must not unintentionally rewrite the entire recurring schedule.

### 6.5 Attendance

- At most one attendance state exists per student per session.
- Attendance applies only to eligible enrollment in that cohort/session.
- Corrections after the normal attendance window must be permission-controlled and audited.
- Attendance percentages must document their denominator and treatment of excused sessions/absences.

### 6.6 Grades

- Grade values must be validated against the assessment's maximum score or configured grading scale.
- Weighted results must validate weights and missing scores before calculation.
- Published/final grades require controlled amendments with an audit record.

### 6.7 Financial integrity

- Use decimal monetary types with explicit currency.
- The organization's primary currency applies to MVP transactions.
- Balances are derived from fee obligations, discounts/waivers, allocations, reversals and adjustments.
- A payment must be allocated consistently; prevent accidental double allocation.
- Posted transactions must not be silently overwritten or deleted.
- Refunds and reversals must preserve a traceable history.
- Receipt identifiers must be unique within their defined scope.
- The application is not an accounting ledger or tax-compliance product in MVP.

### 6.8 Certificate integrity

- Each issued certificate has a unique identifier.
- A certificate can be verified without exposing unnecessary student information.
- Voided certificates must show as invalid on verification.
- Only authorized users can issue or void certificates.

## 7. Data model — conceptual entities

The final schema is the development agent's responsibility, but it must preserve the following concepts and relationships. Normalize where appropriate; avoid a single oversized table or a generic key/value model for core business data.

| Entity | Purpose / key relationships |
|---|---|
| Organization | Tenant boundary, configuration, primary currency, timezone and locale. |
| User | Authentication identity. |
| Membership | User-to-organization relationship, role and membership status. |
| Role / Permission | Central authorization model; may use framework groups/permissions with organization-aware checks. |
| Student | Organization-owned learner identity and profile. |
| GuardianContact | Optional contact/guardian association to a student; no portal in MVP. |
| Application | Prospective student/application workflow. |
| Staff | Organization-owned employee/instructor record, independent of login identity. |
| AcademicPeriod | Organization-defined academic year or period. |
| Program | Optional grouping of courses. |
| Course | Reusable educational offering. |
| CourseModule | Ordered topic/module belonging to a course. |
| Cohort | Delivery instance/group of a course/program. |
| Enrollment | Student's participation in a cohort, status and agreed fee terms. |
| Room | Physical learning room and capacity. |
| SchedulePattern | Optional rule used to generate recurring sessions. |
| Session | Individual scheduled class occurrence. |
| Attendance | Student attendance status for a session. |
| Assessment | Assessment/exam definition linked to course/cohort/module. |
| Grade | Student result for an assessment. |
| FeePlan / FeeObligation | Agreed tuition/fee obligation attached to an enrollment. |
| Installment | Scheduled portion of an obligation and due date. |
| Payment | Recorded incoming payment. |
| PaymentAllocation | Allocation of a payment to one or more obligations/installments. |
| FinancialAdjustment | Discount, waiver, correction, refund or reversal with reason and actor. |
| Expense | Basic operating expense record. |
| Receipt | Generated receipt identity/document reference. |
| Certificate | Issued certificate and verification identifier/status. |
| DocumentAttachment | Controlled file metadata and storage reference. |
| Notification | In-app alert/action item. |
| AuditLog | Sensitive action history. |

The implementation may introduce additional entities where justified, but it must document material schema decisions and preserve the business rules above.

## 8. User experience requirements

### 8.1 General interface

- The product must look and behave like a polished commercial SaaS, not an exposed framework administration interface.
- Use a consistent design system for typography, spacing, navigation, colors, buttons, forms, tables, badges, dialogs, alerts and empty/loading/error states.
- Provide persistent, predictable primary navigation.
- Use clear page titles, breadcrumbs where useful and contextual actions.
- Forms must provide validation close to the relevant fields and preserve user input when safe after validation errors.
- Destructive and financially sensitive actions require confirmation and clear consequences.
- Tables must support useful search, sorting, filtering and pagination where appropriate.
- Provide meaningful empty states with next-step guidance.
- Do not use fabricated production metrics or decorative charts that do not reflect real data.

### 8.2 Responsive behavior

- Core workflows must work on desktop and mobile browser widths.
- Attendance entry must be usable on a phone or tablet.
- Navigation must adapt to small screens.
- Wide data tables must have a deliberate mobile presentation (responsive columns, horizontal scrolling or a card/list alternative).
- Essential actions must not depend on hover.

### 8.3 Internationalization and RTL

- All user-visible strings must come from translation resources; do not hardcode text throughout templates.
- Support English, French and Arabic.
- Arabic must use a genuine RTL layout, not only translated strings.
- Use locale-aware date, time and number presentation.
- Student names, addresses and other user-entered content may be in any language and must not be corrupted by UI locale changes.
- Mixed-direction values such as email addresses, phone numbers, identifiers and amounts must remain legible in RTL screens.
- The initial product must use a consistent terminology glossary for each supported language.

### 8.4 Accessibility

- Aim for WCAG 2.2 AA practices where practical for a small SaaS product.
- Provide keyboard-operable controls and visible focus states.
- Associate form labels and errors with their controls.
- Maintain sufficient text/background contrast.
- Do not rely on color alone to communicate status.
- Use semantic headings and accessible names for icon-only controls.

## 9. Non-functional requirements

### 9.1 Security

- Follow current Django security guidance and secure deployment practices.
- Enforce server-side authorization and tenant scoping.
- Use CSRF protection, safe output handling, input validation and parameterized ORM/database access.
- Use secure cookies and HTTPS in production.
- Apply rate limiting to authentication and sensitive endpoints.
- Store secrets only in environment variables or a suitable secret store; never in source control.
- Keep dependencies maintained and review security advisories.
- Limit file uploads by type, size and access; do not serve private uploads as public static files.
- Avoid collecting data that is not needed for the stated workflow.
- Provide a documented security incident and credential-rotation procedure before production launch.

### 9.2 Privacy and data governance

- Treat student and staff records as confidential.
- Provide organization-level export capability.
- Define retention, deletion and anonymization behavior before public launch.
- Limit access to sensitive fields by role.
- Avoid unnecessary personal information, especially for minors.
- Do not claim GDPR, Algerian legal or other regulatory compliance without a documented legal/technical assessment.
- The customer organization is expected to control the lawful collection and use of its learners' data; CohortDesk's contractual role and processing obligations must be established before commercial launch.

### 9.3 Reliability and recovery

- Production data must be backed up automatically to storage independent of the production VPS.
- Backups must be encrypted in transit and at rest where supported.
- Backup success must be monitored and failures surfaced.
- Restoration must be tested, documented and repeatable.
- Database migrations must be version-controlled and tested against a copy of representative data.
- Deployments must have a rollback/recovery procedure.
- A backup stored only on the same VPS does not satisfy the off-server backup requirement.

### 9.4 Performance and resource constraints

Initial deployment target is an Oracle Cloud Always Free VM believed to be an E2.1.Micro-class instance. The actual instance shape, CPU architecture, available RAM, storage and other running services must be inspected before deployment; do not assume the remembered shape is accurate.

The known E2.1.Micro profile is highly constrained (approximately 1 GB RAM and 1/8 OCPU). Therefore:

- Use a lightweight modular monolith.
- Avoid microservices and unnecessary always-on workers.
- Avoid requiring Redis/Celery for MVP unless a measured requirement justifies them.
- Avoid a heavy client-side application bundle if server-rendered pages satisfy the workflows.
- Tune Gunicorn worker count, PostgreSQL memory and connection usage conservatively.
- Do not host AI inference, video processing or other heavy workloads on the production VM.
- Measure actual memory use and response times under representative load.
- If the VM cannot run the app and database reliably, propose a supported external database or a suitable VM upgrade rather than silently weakening data integrity.

No unverified claim about maximum concurrent users or guaranteed response time may be made. Define measurable performance targets after profiling the deployed environment and expected initial customer load.

### 9.5 Maintainability

- Use a modular Django monolith with clear application boundaries.
- Keep business rules out of templates and avoid oversized view functions.
- Use migrations for schema changes.
- Add type hints to non-trivial Python code where useful.
- Document environment variables and operational commands.
- Avoid unnecessary abstractions and speculative extension points.
- Add tests for business-critical calculations and permissions.

### 9.6 Observability

- Provide structured application logs without leaking secrets or unnecessary personal data.
- Log application errors with enough context to diagnose them.
- Provide a health endpoint that does not expose sensitive configuration.
- Monitor disk space, memory, database availability and backup outcomes.
- Rotate logs and define retention to prevent disk exhaustion.

## 10. Recommended implementation architecture

This is the preferred baseline, subject to an initial compatibility and resource check.

| Layer | Recommended technology | Notes |
|---|---|---|
| Backend | Python + Django | Core business logic, ORM, authentication, permissions and server-side rendering. |
| Frontend | Django templates + HTMX | Server-rendered pages with targeted dynamic interactions. |
| Styling | Tailwind CSS | Consistent responsive design system. |
| Database | PostgreSQL | Relational integrity and transactional financial records. |
| WSGI server | Gunicorn | Conservative worker and connection settings for low RAM. |
| Reverse proxy/TLS | Caddy | HTTPS and reverse proxy, assuming compatible host setup. |
| Process supervision | systemd or a similarly simple, documented service manager | Avoid unnecessary orchestration. |
| Source control | Private GitHub repository | Versioned source, issues and release history. |
| Testing | Django test framework and appropriate browser/integration tests | Unit, integration, authorization and workflow coverage. |

### 10.1 Architecture principles

- Start as a modular monolith, not microservices.
- Use PostgreSQL as the authoritative persistent data store.
- Keep the app stateless where practical; user-uploaded files need persistent, backed-up storage.
- Use database transactions for financial allocations, enrollment transitions and other multi-step business operations.
- Use an organization-aware service/query layer for tenant-owned data.
- Keep background work synchronous or scheduled simply for MVP; add a queue only when an actual workload requires it.
- Do not introduce Docker/Kubernetes solely for fashion. Containerization may be used only if it is measured to fit the host and simplifies reproducible deployment.

### 10.2 Deployment environment

The application must support separate development and production configuration.

Production requirements:

- Linux VPS with a supported Python and PostgreSQL version.
- Domain name and HTTPS.
- Firewall exposing only required ports.
- SSH key-based administration; disable password SSH where operationally safe.
- Non-root application service account.
- Environment-based secrets and production settings.
- Persistent database and upload storage.
- Off-server backup destination.
- Documented deployment, migration, restart, rollback and restore commands.

The deployment must not overwrite or destroy unrelated existing services or files on the VPS. The agent must inspect the server before changing it and obtain explicit approval before destructive operations.

## 11. GitHub and engineering workflow requirements

### 11.1 Repository setup

- Create a dedicated private GitHub repository for CohortDesk, subject to the account's available permissions.
- Use a clear project structure and a complete README.
- Store this SRS in the repository (for example, `docs/SRS.md` or repository-root `SRS.md`).
- Include `.env.example` with placeholder names only, never real secrets.
- Include setup, test, migration, backup and deployment documentation.
- Include a changelog or release notes.

### 11.2 Development workflow

- Before coding, inspect and follow `S:\work\code\agents.md` and all relevant project instructions.
- Inspect the existing workspace before creating, moving or overwriting files.
- Create small, coherent milestones and implement them incrementally.
- Run relevant automated tests before committing.
- Commit completed, tested milestones using meaningful commit messages.
- Push completed milestones regularly; do not push secrets, local model files, virtual environments, datasets, user data, database dumps or build artifacts.
- Keep the default branch deployable.
- Prefer feature branches or pull requests for risky changes; document the chosen workflow.
- Never treat a successful `git push` as proof that the application works.

### 11.3 Continuous integration

At minimum, CI should run:

- Dependency/install checks.
- Formatting/lint checks selected by the project.
- Django system checks.
- Automated tests.
- Migration consistency checks.
- Static analysis or dependency security checks where practical.

A failed required check must block release/deployment.

### 11.4 Production release policy

- Production deployment must use a tested release/commit.
- The agent must not automatically deploy every experimental commit.
- Before risky migrations, create and verify a current off-server backup.
- Deployment must include a documented rollback plan.
- Do not run destructive schema or data changes against production without explicit approval.
- Preserve previous known-good application releases where feasible.
- Verify health, login and key workflows after deployment.

## 12. Testing strategy and MVP acceptance criteria

### 12.1 Required test categories

1. Unit tests for fee calculations, payment allocation, attendance statistics, grade weighting and certificate identifiers.
2. Integration tests for enrollment, scheduling, payment, assessment and certificate workflows.
3. Authorization tests for every role and protected module.
4. Cross-tenant isolation tests across students, cohorts, finance, documents, reports and exports.
5. Validation tests for imports and invalid data.
6. Localization tests for English, French and Arabic/RTL.
7. Responsive/browser tests for high-value workflows.
8. Migration and backup/restore tests.
9. Production configuration and security checks.

### 12.2 MVP end-to-end acceptance scenarios

The MVP is not accepted merely because pages render. The following scenarios must work against a persistent test database.

**AC-001 — Organization isolation**

- Create Organization A and Organization B.
- Create users and records in both.
- Attempt to access Organization B records while authenticated only in Organization A.
- All direct URL, query, export and document-access attempts must be denied without data leakage.

**AC-002 — Initial setup**

- Create an organization and owner.
- Configure name, primary currency, timezone and locale.
- Add a staff user with a restricted role.
- Confirm permissions are enforced server-side.

**AC-003 — Admissions to enrollment**

- Create an applicant.
- Accept the application.
- Convert it to a student without duplicate identity creation.
- Enroll the student into a cohort.
- Confirm the enrollment and fee terms are visible in the correct authorized views.

**AC-004 — Course and cohort setup**

- Create a course with modules.
- Create a cohort with dates, capacity and instructor.
- Enroll students.
- Confirm capacity validation works.

**AC-005 — Scheduling and conflict detection**

- Create rooms and instructor records.
- Create a recurring cohort schedule.
- Verify individual sessions are generated.
- Attempt to double-book a room and an instructor.
- Verify the configured block/warning behavior.
- Edit one occurrence and confirm the rest of the recurring series remains correct.

**AC-006 — Attendance**

- Open a session register.
- Record present, absent, late and excused statuses.
- Confirm one attendance record per student/session.
- Correct a saved status as an authorized user.
- Confirm the audit trail and attendance statistics update correctly.

**AC-007 — Assessment and grading**

- Create weighted assessments.
- Enter valid and invalid scores.
- Confirm validation and weighted calculation.
- Change a finalized grade as an authorized user and confirm the audit trail.

**AC-008 — Financial workflow**

- Create an enrollment fee obligation and installments.
- Record a partial payment.
- Allocate the payment.
- Confirm remaining balance and installment status.
- Record another payment.
- Issue a receipt.
- Record a refund/reversal or adjustment.
- Confirm history is preserved and balances remain correct.

**AC-009 — Expenses and reports**

- Record categorized expenses.
- Confirm financial summaries distinguish collections, receivables and expenses.
- Export reports and verify permissions and organization scope.

**AC-010 — Certificates**

- Issue an authorized certificate for an eligible enrollment.
- Download/print the PDF.
- Verify the unique identifier.
- Void the certificate and confirm verification reports it as invalid.

**AC-011 — Import/export**

- Import valid and invalid CSV/XLSX student data.
- Preview the rows and duplicate warnings.
- Commit the import.
- Confirm rejected rows are reported and no silent data loss occurs.
- Export the organization's permitted data.

**AC-012 — Localization and RTL**

- Switch between English, French and Arabic.
- Confirm strings are translated and persisted data is unchanged.
- Confirm Arabic navigation, forms, tables and mixed-direction values remain usable.

**AC-013 — Backup and recovery**

- Create an off-server backup.
- Restore it into an isolated test environment.
- Verify representative organization, student, academic and financial records.
- Document the procedure and outcome.

### 12.3 Definition of Done for each feature

A feature is complete only when:

- Its agreed requirements are implemented.
- Server-side validation and authorization are implemented.
- Tenant isolation is tested.
- Relevant automated tests pass.
- Error, empty and loading states are handled.
- The interface works at desktop and mobile sizes where relevant.
- English, French and Arabic strings are included.
- Sensitive actions are audited where required.
- Documentation is updated.
- The change is committed and pushed without secrets or unrelated artifacts.

## 13. Delivery milestones

The local development agent must estimate effort only after repository and environment inspection. It must not promise an arbitrary calendar delivery date.

### Milestone 0 — Discovery and foundation

- Read project instructions in `agents.md`.
- Inspect local workspace and available tools.
- Inspect VPS environment without changing it.
- Confirm GitHub authentication and create private repository.
- Record architecture decisions and open questions.
- Create project skeleton, CI, configuration example and documentation.
- Establish development database and test baseline.

**Exit criteria:** Reproducible local setup, repository established, CI passing and project architecture documented.

### Milestone 1 — Tenant and access foundation

- Organization and membership model.
- Authentication and owner onboarding.
- Roles and permissions.
- Organization settings and localization foundation.
- Tenant-isolation test suite.

**Exit criteria:** Two tenants can use the application with proven data isolation.

### Milestone 2 — People and academic structure

- Student records.
- Admissions.
- Staff/instructors.
- Academic periods.
- Programs/courses/modules.
- Cohorts and enrollment.

**Exit criteria:** A prospective learner can become an enrolled student in a cohort.

### Milestone 3 — Scheduling and attendance

- Rooms.
- Recurring schedules and sessions.
- Conflict detection.
- Instructor views.
- Attendance registers and statistics.

**Exit criteria:** A center can schedule and record a complete class session.

### Milestone 4 — Finance and assessment

- Fee obligations and installment plans.
- Payments and allocations.
- Refunds/adjustments and receipts.
- Basic expenses.
- Assessments and grades.

**Exit criteria:** Student academic and financial records are connected and calculations are tested.

### Milestone 5 — Documents, reporting and imports

- Certificates and verification.
- Dashboard and reports.
- CSV/XLSX import/export.
- Audit trail completion.

**Exit criteria:** Core end-to-end acceptance scenarios pass.

### Milestone 6 — Product polish and localization

- Complete English, French and Arabic translations.
- Arabic RTL visual validation.
- Responsive UX refinement.
- Accessibility review.
- Error handling and empty states.
- Sample/demo data separation.

**Exit criteria:** A non-technical pilot customer can complete key workflows without developer assistance.

### Milestone 7 — Production readiness and pilot

- Production VPS inspection and capacity test.
- Domain/HTTPS and secure configuration.
- Off-server backups and restore rehearsal.
- Logs, health check and disk/memory monitoring.
- Security review.
- Controlled deployment and post-deployment verification.
- Pilot onboarding and feedback process.

**Exit criteria:** A pilot center can use the production service with documented recovery and support procedures.

## 14. Commercial and operational assumptions

The following are hypotheses, not confirmed business facts:

- Subscription pricing may be per organization rather than per student.
- An entry-level plan and a higher-capacity plan may be sufficient initially.
- Manual subscription administration may be used during early pilots.
- Algeria may be the first customer-discovery market, while the product remains international-ready.
- A free trial or limited pilot may reduce adoption friction.

Do not hardcode prices, plan limits, tax rules or payment providers into business logic. Subscription entitlements should be configurable when SaaS billing is implemented.

## 15. Open decisions requiring validation

These items should be tracked in `docs/OPEN_QUESTIONS.md` or an equivalent decision register. The agent must not invent legal or commercial answers.

1. **Brand/domain:** Confirm CohortDesk name availability, domain availability and trademark considerations before public launch.
2. **Customer discovery:** Interview training-center owners in Algeria and at least one international target segment to validate priorities and willingness to pay.
3. **Legal documents:** Determine applicable terms of service, privacy notice, data processing terms and retention obligations for intended markets.
4. **Minors:** Determine whether the first customer segment commonly enrolls minors and what consent/guardian requirements apply.
5. **Student fields:** Confirm which personal details are genuinely needed; avoid collecting sensitive or unnecessary attributes.
6. **Grading models:** Validate common grading formats with pilot centers and keep the implementation configurable.
7. **Fee practices:** Validate discounts, installments, refunds, receipt expectations and local currency conventions.
8. **Certificates:** Validate what centers need to display and avoid implying official recognition.
9. **Hosting:** Confirm actual Oracle VM shape, region, ARM/x86 architecture, memory, storage and tenancy eligibility for alternative free shapes.
10. **Database hosting:** Decide whether PostgreSQL can run reliably on the selected VPS or should be external.
11. **File storage:** Select persistent, secure storage for student documents and certificate assets; ensure it is backed up.
12. **Email provider:** Select a provider only when email delivery enters the release scope.
13. **Subscription collection:** Validate an appropriate payment method/provider for each target market; do not assume Stripe or any other provider is available to an Algerian business.
14. **Support and onboarding:** Decide how pilot customers will receive support and how data migration will be handled.

## 16. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Scope grows into a full ERP/LMS | MVP delayed and difficult to maintain | Enforce MVP/later-release boundary and change control. |
| Existing competitors cover the same features | Weak differentiation | Validate customer pain, onboarding, pricing and workflows before scaling. |
| Weak tenant isolation | Critical data breach | Central tenant-aware services, authorization tests and cross-tenant penetration scenarios. |
| Incorrect financial calculations | Loss of trust and disputes | Decimal arithmetic, transaction model, immutable history and extensive tests. |
| Limited VPS memory/CPU | Outages or poor performance | Profile early, tune conservatively, externalize DB or upgrade when necessary. |
| Backups not recoverable | Permanent data loss | Off-server backups and scheduled restoration tests. |
| Multilingual/RTL defects | Poor usability in key markets | Include localization in design system and test real Arabic layouts. |
| Customer data import is difficult | Adoption friction | Preview/validation workflow, templates and pilot migration testing. |
| Unvalidated legal/payment assumptions | Compliance or commercial blockage | Keep integrations out of MVP and obtain market-specific advice. |

## 17. Change control

Any proposed change to the MVP must include:

1. The user/customer problem it solves.
2. The requirement(s) affected.
3. Whether it is a defect, clarification or new feature.
4. Technical and data-model impact.
5. Security, tenant-isolation and privacy impact.
6. VPS resource impact.
7. Test and documentation impact.
8. Whether it delays or displaces another MVP requirement.

The development agent may propose changes but must not silently expand scope or remove MUST requirements.

## 18. Source-of-truth project documents

The repository should maintain these documents as the project evolves:

- `SRS.md` — this requirements baseline.
- `README.md` — project overview and local setup.
- `docs/ARCHITECTURE.md` — system architecture and module boundaries.
- `docs/DATA_MODEL.md` — entities, relationships and key constraints.
- `docs/SECURITY.md` — threat considerations and security controls.
- `docs/DEPLOYMENT.md` — VPS setup, release, rollback and recovery.
- `docs/BACKUP_RESTORE.md` — backup schedule and tested restoration process.
- `docs/TESTING.md` — test strategy and execution commands.
- `docs/DECISIONS.md` — architecture/product decision log.
- `docs/OPEN_QUESTIONS.md` — unresolved product, legal and commercial questions.
- `CHANGELOG.md` — user-facing changes by release.
- `.env.example` — placeholder environment variable names only.

## 19. Final MVP release gate

CohortDesk MVP may be described as ready for pilot only when all of the following are true:

- The core student, admissions, academic, scheduling, attendance, finance, assessment and certificate workflows work end to end.
- Role permissions and cross-tenant isolation tests pass.
- Financial calculations and transaction history tests pass.
- English, French and Arabic RTL interface checks pass for core workflows.
- Import/export behavior is documented and tested.
- Production deployment is reproducible and does not rely on secrets committed to GitHub.
- HTTPS, backups, logs and health checks are operational.
- An off-server backup has been successfully restored in an isolated environment.
- The deployment has been tested against the actual VPS resource limits or an approved alternative host.
- No known critical/high-severity security issue remains unresolved.
- Documentation is sufficient for another developer/operator to install, deploy, back up and restore the system.
- At least one pilot center can complete its core daily workflow with realistic data.

**End of specification.**
