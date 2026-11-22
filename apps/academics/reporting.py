"""The reporting calculations: how academic periods contain records.

ACAD-006 asks for configurable academic years or periods "for organization
reporting and grouping", and REP-004 asks reports to support academic-period
filters. The SRS data model gives AcademicPeriod no foreign keys, and nothing
in the SRS says a cohort, enrollment or course is "attached" to one year: a
cohort is a delivery instance with its own dates (COH-002), and an enrollment
is linked to a student and a cohort (COH-003, RULE 6.3). So a period contains
records by their own stored dates, computed here at report time:

- a cohort is in a period when its start date falls inside the period, both
  ends included;
- an enrollment is in the period of its cohort (RULE 6.3: the enrollment is
  linked to the cohort, and through it to the course and program);
- a record with no start date is in no bounded period;
- a period with no start or end date has no boundary on that side, so a period
  with neither date contains every record.

No foreign key is added: duplicating the year onto every cohort or enrollment
would let two answers disagree, and the SRS never asks for such a link. Every
query is scoped to one organization (RULE 6.1), and the services resolve a
period/course/cohort number inside the caller's own center before these
functions see it.

Capacity (COH-005/REP-003) counts the enrollments still in the enrolled state,
the same seats the capacity policy counts, so a completed, withdrawn or
cancelled enrollment does not fill a place.

Attendance (ATT-006/ATT-007) counts each session's attendance records by their
own stored status, and a session belongs to a reporting period through its
cohort's start date, the same containment rule as above applied to a session.
The four states are counted apart from each other so an excused absence and a
late arrival are visible separately from the rest (ATT-007), and no percentage
is invented: the SRS names no denominator for one, so the counts are shown and
docs/OPEN_QUESTIONS.md asks the owner what the rate should be.

The figures are recomputed from the stored records on every read: this slice
does not snapshot report output. The dates and statuses it reads are the ones
COH-004/6.2 already preserve, and the report shows the cohort start date it
grouped by, so a changed date is visible rather than silently rewritten.
docs/DECISIONS.md records this choice and its limit.
"""

from django.db.models import Count, Q

from .models import Attendance, Cohort, Enrollment, Session
from .statuses import (
    ATTENDANCE_ABSENT,
    ATTENDANCE_EXCUSED,
    ATTENDANCE_LATE,
    ATTENDANCE_PRESENT,
    ATTENDANCE_STATUSES,
    ENROLLMENT_ENROLLED,
)

CAPACITY_UNLIMITED = "unlimited"
CAPACITY_OVER = "over"
CAPACITY_FULL = "full"
CAPACITY_OPEN = "open"


def period_span(period):
    """(start, end) of a period, either of which may be None (open side)."""
    return period.start_date, period.end_date


def _within_period(queryset, date_field, period):
    """Apply a period's own dates to a date field, either side optional."""
    start, end = period_span(period)
    if start is not None:
        queryset = queryset.filter(**{date_field + "__gte": start})
    if end is not None:
        queryset = queryset.filter(**{date_field + "__lte": end})
    return queryset


def cohort_report_rows(organization, period=None, course=None, cohort=None):
    """This center's cohorts, grouped by period and narrowed by the filters.

    Each row carries `seats_taken`, counted in one query (no per-row count).
    """
    rows = Cohort.objects.filter(organization=organization).select_related(
        "course"
    )
    if period is not None:
        rows = _within_period(rows, "start_date", period)
    if course is not None:
        rows = rows.filter(course=course)
    if cohort is not None:
        rows = rows.filter(pk=cohort.pk)
    rows = rows.annotate(
        seats_taken=Count(
            "enrollments",
            filter=Q(enrollments__status=ENROLLMENT_ENROLLED),
        )
    )
    return list(rows.order_by("start_date", "pk"))


def enrollment_report_rows(organization, period=None, course=None, cohort=None):
    """This center's enrollments, in the period of their own cohort.

    RULE 6.3: an enrollment belongs to a cohort, so the period dates are
    applied to the cohort's start date, never to a copied date of its own.
    """
    rows = Enrollment.objects.filter(organization=organization).select_related(
        "student", "cohort", "cohort__course"
    )
    if period is not None:
        rows = _within_period(rows, "cohort__start_date", period)
    if course is not None:
        rows = rows.filter(cohort__course=course)
    if cohort is not None:
        rows = rows.filter(cohort=cohort)
    return list(rows.order_by("cohort__start_date", "enrolled_at", "pk"))


def capacity_state(capacity, seats_taken):
    """(state code, places left or None) for one cohort (COH-005)."""
    if capacity is None:
        return CAPACITY_UNLIMITED, None
    places_left = capacity - seats_taken
    if places_left < 0:
        return CAPACITY_OVER, places_left
    if places_left == 0:
        return CAPACITY_FULL, 0
    return CAPACITY_OPEN, places_left


def report_totals(enrollments, cohorts):
    """The report's own counted figures, over exactly the rows it shows."""
    seats_taken = sum(row.seats_taken for row in cohorts)
    seats_offered = sum(
        row.capacity for row in cohorts if row.capacity is not None
    )
    return {
        "enrollments": len(enrollments),
        "cohorts": len(cohorts),
        "seats_taken": seats_taken,
        "seats_offered": seats_offered,
        "cohorts_without_limit": sum(
            1 for row in cohorts if row.capacity is None
        ),
        "cohorts_over_capacity": sum(
            1
            for row in cohorts
            if row.capacity is not None and row.seats_taken > row.capacity
        ),
    }


def attendance_totals(attendance_rows, sessions):
    """The report's own attendance figures, over exactly the rows it shows."""
    counts = {value: 0 for value in ATTENDANCE_STATUSES}
    for row in attendance_rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return {
        "sessions": len(sessions),
        "records": len(attendance_rows),
        "present": counts[ATTENDANCE_PRESENT],
        "absent": counts[ATTENDANCE_ABSENT],
        "late": counts[ATTENDANCE_LATE],
        "excused": counts[ATTENDANCE_EXCUSED],
    }


def session_report_rows(organization, period=None, course=None, cohort=None,
                        cohorts=None):
    """This center's sessions, narrowed by the filters, with their own counts.

    ATT-006/ATT-007: one session's attendance is counted by its own four
    states, kept apart so an excused absence and a late arrival are visible
    separately from the rest. `cohorts` is the instructor's server-side scope
    (ATT-004/STAFF-004): a set of cohort ids, and only a session of one of
    them is a row. The counts are annotated here, over the session's own
    attendance records, so a page or a report adds session rows without a
    per-row query.
    """
    rows = Session.objects.filter(organization=organization).select_related(
        "cohort", "room"
    )
    if period is not None:
        rows = _within_period(rows, "cohort__start_date", period)
    if course is not None:
        rows = rows.filter(cohort__course=course)
    if cohort is not None:
        rows = rows.filter(cohort=cohort)
    if cohorts is not None:
        rows = rows.filter(cohort__id__in=cohorts)
    rows = rows.annotate(
        present=Count(
            "attendances", filter=Q(attendances__status=ATTENDANCE_PRESENT)
        ),
        absent=Count(
            "attendances", filter=Q(attendances__status=ATTENDANCE_ABSENT)
        ),
        late=Count(
            "attendances", filter=Q(attendances__status=ATTENDANCE_LATE)
        ),
        excused=Count(
            "attendances", filter=Q(attendances__status=ATTENDANCE_EXCUSED)
        ),
        attendance_records=Count("attendances"),
    )
    return list(rows.order_by("start_at", "pk"))


def attendance_report_rows(organization, period=None, course=None,
                           cohort=None, cohorts=None):
    """This center's attendance records, in the period of their own cohort.

    RULE 6.5: the record is one student's current state of one session, so
    the period dates are applied through the session's cohort, never to a
    date copied onto the record. `cohorts` scopes the rows to the cohorts
    of an assigned instructor, the same rule as above.
    """
    rows = Attendance.objects.filter(organization=organization).select_related(
        "session", "session__cohort", "student"
    )
    if period is not None:
        rows = _within_period(rows, "session__cohort__start_date", period)
    if course is not None:
        rows = rows.filter(session__cohort__course=course)
    if cohort is not None:
        rows = rows.filter(session__cohort=cohort)
    if cohorts is not None:
        rows = rows.filter(session__cohort__id__in=cohorts)
    return list(rows.order_by("session__start_at", "student__student_number", "pk"))
