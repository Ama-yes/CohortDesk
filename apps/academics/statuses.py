"""The vocabularies a course, a cohort, an enrollment and an attendance may have.

Like apps/students/statuses.py, one module owns these so the models (field
choices) and the services (transition rules) read the same source:

- ACAD-005: a course that history may reference is archived, never deleted;
- COH-007: cohort status is planned, active, completed or cancelled;
- COH-004/6.3: an enrollment has a status, and it changes only through the
  transition table in services.py.

The transitions themselves are business rules and live in services.py; the
tables there are the only place a status is ever changed.
"""

COURSE_DRAFT = "draft"
COURSE_ACTIVE = "active"
COURSE_ARCHIVED = "archived"

COURSE_STATUSES = (COURSE_DRAFT, COURSE_ACTIVE, COURSE_ARCHIVED)

COHORT_PLANNED = "planned"
COHORT_ACTIVE = "active"
COHORT_COMPLETED = "completed"
COHORT_CANCELLED = "cancelled"

COHORT_STATUSES = (
    COHORT_PLANNED,
    COHORT_ACTIVE,
    COHORT_COMPLETED,
    COHORT_CANCELLED,
)

ENROLLMENT_ENROLLED = "enrolled"
ENROLLMENT_COMPLETED = "completed"
ENROLLMENT_WITHDRAWN = "withdrawn"
ENROLLMENT_CANCELLED = "cancelled"

ENROLLMENT_STATUSES = (
    ENROLLMENT_ENROLLED,
    ENROLLMENT_COMPLETED,
    ENROLLMENT_WITHDRAWN,
    ENROLLMENT_CANCELLED,
)

COURSE_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in COURSE_STATUSES
]
COHORT_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in COHORT_STATUSES
]
ENROLLMENT_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in ENROLLMENT_STATUSES
]

# ATT-002: the only attendance states the product names. No other value is a
# legal status, and the set is fixed here so the model choices and the
# services read one source (RULE 6.5: one state per student per session).
ATTENDANCE_PRESENT = "present"
ATTENDANCE_ABSENT = "absent"
ATTENDANCE_LATE = "late"
ATTENDANCE_EXCUSED = "excused"

ATTENDANCE_STATUSES = (
    ATTENDANCE_PRESENT,
    ATTENDANCE_ABSENT,
    ATTENDANCE_LATE,
    ATTENDANCE_EXCUSED,
)

ATTENDANCE_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in ATTENDANCE_STATUSES
]

# The audited actions of this slice. RULE 6.5: "corrections after the normal
# attendance window must be permission-controlled and audited", so a changed
# attendance status writes one audit row naming the change; the action name is
# fixed here so the AuditLog choices and the services agree.
AUDIT_ATTENDANCE_CORRECTED = "attendance_corrected"

AUDIT_ACTIONS = (AUDIT_ATTENDANCE_CORRECTED,)

AUDIT_ACTION_CHOICES = [
    (value, value.replace("_", " ").title()) for value in AUDIT_ACTIONS
]

# SCH-001: a room is archived instead of deleted, exactly as a course is
# (ACAD-005). An archived room keeps every session that already names it but
# cannot be picked for a new session, pattern or cohort default.
ROOM_ACTIVE = "active"
ROOM_ARCHIVED = "archived"

ROOM_STATUSES = (ROOM_ACTIVE, ROOM_ARCHIVED)

ROOM_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in ROOM_STATUSES
]

# SCH-008 names cancelling one occurrence. The SRS never names a wider
# lifecycle, so exactly two session states exist: scheduled and cancelled.
# Cancellation is terminal.
SESSION_SCHEDULED = "scheduled"
SESSION_CANCELLED = "cancelled"

SESSION_STATUSES = (SESSION_SCHEDULED, SESSION_CANCELLED)

SESSION_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in SESSION_STATUSES
]

# SCH-003: the weekdays a recurring pattern may name, in the canonical order
# the forms and the stored value use (Monday first, Sunday last).
WEEKDAYS = (
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
    ("sat", "Saturday"),
    ("sun", "Sunday"),
)

WEEKDAY_CODES = tuple(code for code, _label in WEEKDAYS)

# Python's date.weekday(): Monday is 0 and Sunday is 6, the same order.
WEEKDAY_INDEX = {code: index for index, (code, _label) in enumerate(WEEKDAYS)}
