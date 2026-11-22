"""Room and instructor overlap rules and the display of their warnings.

SCH-005/SCH-006: two class occurrences in one centre overlap when their
half-open intervals intersect - ``new_start < existing_end`` and
``new_end > existing_start`` - so a session that ends exactly when another
starts is not a conflict. Only this centre's scheduled sessions take part:
cancelled occurrences are history (SCH-008) and are excluded. An occurrence's
instructors are every current ``CohortInstructor`` of its cohort (STAFF-004);
the SRS never assigns instructors per session.

The functions here are pure reads over the stored rows. The services use them
to decide whether an operation is refused (policy "prevent") or saved with a
warning (policy "warn"); the day, week and list pages use the same rules to
show a warning derived from the current timetable, so no warning is stored and
one disappears as soon as the underlying overlap is gone.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.utils.timezone import make_aware

from .models import Session
from .statuses import SESSION_SCHEDULED

# apps.staff is the one app that owns cohort-instructor assignments. Importing
# its model here is the same cross-app read the academics services already use
# for the attendance scope (D73); nothing is written outside this app.
from apps.staff.models import CohortInstructor


def local_wall_time(zone, day, clock):
    """(aware moment, accepted) for a local wall-clock time.

    SCH-009/D74: a moment is read in the centre's own IANA timezone. A
    fall-back ambiguity keeps Python's first fold (the D74 convention). A
    spring-forward wall time that does not exist must never be silently
    shifted, so the round trip back to local time is compared with what was
    asked: a mismatch is refused.
    """
    naive = datetime.combine(day, clock)
    aware = make_aware(naive, zone)
    # Compare through UTC: astimezone() with the very same tzinfo is a no-op in
    # Python, so a direct round trip would never notice a shifted wall time.
    round_trip = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != naive:
        return None, False
    return aware, True


def overlapping_sessions(organization, start_at, end_at, exclude_pk=None):
    """This centre's scheduled sessions that half-open overlap the window."""
    rows = Session.objects.filter(
        organization=organization,
        status=SESSION_SCHEDULED,
        start_at__lt=end_at,
        end_at__gt=start_at,
    )
    if exclude_pk is not None:
        rows = rows.exclude(pk=exclude_pk)
    return rows


def room_conflicts(organization, room, start_at, end_at, exclude_pk=None):
    """Sessions already booked in this room over the same window."""
    if room is None:
        return Session.objects.none()
    return overlapping_sessions(
        organization, start_at, end_at, exclude_pk=exclude_pk
    ).filter(room=room)


def cohort_instructor_ids(organization, cohort):
    """Every staff id currently assigned to one cohort of this centre."""
    return list(
        CohortInstructor.objects.filter(
            organization=organization, cohort=cohort
        ).values_list("staff_id", flat=True)
    )


def instructor_conflicts(organization, cohort, start_at, end_at,
                         exclude_pk=None):
    """Sessions whose cohort shares an instructor with this session's cohort."""
    staff_ids = cohort_instructor_ids(organization, cohort)
    if not staff_ids:
        return Session.objects.none()
    return overlapping_sessions(
        organization, start_at, end_at, exclude_pk=exclude_pk
    ).filter(
        cohort__instructor_assignments__staff_id__in=staff_ids
    ).distinct()


def session_conflicts(organization, cohort, start_at, end_at, room=None,
                      exclude_pk=None):
    """[(kind, session)] for every room and instructor overlap, room first."""
    conflicts = []
    for other in room_conflicts(
        organization, room, start_at, end_at, exclude_pk=exclude_pk
    ).order_by("start_at", "pk"):
        conflicts.append(("room", other))
    for other in instructor_conflicts(
        organization, cohort, start_at, end_at, exclude_pk=exclude_pk
    ).order_by("start_at", "pk"):
        conflicts.append(("instructor", other))
    return conflicts


def cohort_date_warning(center, session):
    """SCH-010 (partial): does this session fall outside its cohort's dates?

    A cohort with no dates has no boundary, so nothing is outside it. The
    comparison uses the session's start in the centre's own timezone, the same
    terms the page shows.
    """
    cohort = session.cohort
    start = session.start_at
    try:
        zone = ZoneInfo(center.display_timezone or "UTC")
    except LookupError:
        zone = ZoneInfo("UTC")
    local_day = start.astimezone(zone).date()
    if cohort.start_date is not None and local_day < cohort.start_date:
        return True
    if cohort.end_date is not None and local_day > cohort.end_date:
        return True
    return False


def warning_map(organization, sessions):
    """{session pk: [{"kind", "other"}]} over the current scheduled timetable.

    `sessions` are the rows a page shows; the map is derived by comparing each
    of them with every scheduled session of the centre, so an overlap with a
    session outside the shown window is still named. Cancelled sessions are
    visible elsewhere but never conflict (SCH-008).
    """
    shown = [row for row in sessions if row.status == SESSION_SCHEDULED]
    if not shown:
        return {}
    all_rows = list(
        Session.objects.filter(
            organization=organization, status=SESSION_SCHEDULED
        ).only("pk", "cohort_id", "room_id", "start_at", "end_at")
    )
    staff_by_cohort = {}
    for cohort_id, staff_id in CohortInstructor.objects.filter(
        organization=organization
    ).values_list("cohort_id", "staff_id"):
        staff_by_cohort.setdefault(cohort_id, set()).add(staff_id)
    warnings = {}
    for row in shown:
        found = []
        instructors = staff_by_cohort.get(row.cohort_id, set())
        for other in all_rows:
            if other.pk == row.pk:
                continue
            if not (row.start_at < other.end_at and row.end_at > other.start_at):
                continue
            if row.room_id is not None and row.room_id == other.room_id:
                found.append({"kind": "room", "other": other})
            if instructors & staff_by_cohort.get(other.cohort_id, set()):
                found.append({"kind": "instructor", "other": other})
        if found:
            warnings[row.pk] = found
    return warnings
