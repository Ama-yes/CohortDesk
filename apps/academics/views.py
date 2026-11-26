"""The course, cohort and enrollment pages at a tenant address.

Same error model as every other tenant address (apps/organizations/views.py):
- unknown centre / not signed in / no active membership here -> the project
  problem page at 404 (SEC-003/004),
- a member here but the role is not permitted for this page -> this centre's
  denied page at 403,
- a non-read method at a read-only address -> the project problem page at 405
  (built without center=: "problem" exists only in PROJECT_PAGES),
- a number this centre has no record for -> this centre's own not-found page at
  404, which says nothing about any other centre.

Data rule: every row is built here from this centre's own queryset (RULE 6.1).
Every identifier typed on a page is resolved by the service layer inside this
centre, so a tampered course/cohort/student number answers as not found here
and never reaches another centre's record.

Page and form share one definition (the students page pattern): each
*_FORM_FIELDS spec drives both the rows a manage page shows and the form it
collects, so a person is never asked for a value nowhere is shown.
"""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.utils import timezone
from django.utils.timezone import make_aware

from apps.csv_safety import csv_writer
from apps.organizations.permissions import role_has_permission
from apps.organizations.views import READ_METHODS, here
from apps.pages import center_ui, page
from apps.staff.models import CohortInstructor, CourseInstructor

from .models import (
    AcademicPeriod,
    Attendance,
    AuditLog,
    Cohort,
    Course,
    Enrollment,
    Program,
    Room,
    Session,
)
from .reporting import (
    attendance_report_rows,
    attendance_totals,
    capacity_state,
    session_report_rows,
)
from .scheduling import cohort_date_warning, warning_map
from .services import (
    EDITABLE_COHORT_FIELDS,
    EDITABLE_COURSE_FIELDS,
    EDITABLE_PERIOD_FIELDS,
    EDITABLE_PROGRAM_FIELDS,
    EDITABLE_ROOM_FIELDS,
    EDITABLE_SESSION_FIELDS,
    ATTENDANCE_ELIGIBLE_STATUSES,
    _attendance_scope,
    _cohort_here,
    _course_here,
    _period_here,
    _program_here,
    _session_here,
    add_course_module,
    archive_room,
    build_report,
    cancel_session,
    correct_attendance,
    create_room,
    create_schedule_pattern,
    create_session,
    edit_room,
    edit_session,
    record_attendance,
    set_cohort_room,
    set_session_room,
    create_cohort,
    create_course,
    create_period,
    create_program,
    edit_cohort,
    edit_course,
    edit_period,
    edit_program,
    enroll_student,
    set_cohort_program,
    set_cohort_status,
    set_course_status,
    set_program_course,
    clear_program_course,
    set_enrollment_status,
)
from .statuses import (
    ATTENDANCE_LATE,
    ATTENDANCE_PRESENT,
    ATTENDANCE_STATUSES,
    COHORT_PLANNED,
    COHORT_STATUSES,
    COURSE_DRAFT,
    COURSE_STATUSES,
    ENROLLMENT_ENROLLED,
    ENROLLMENT_STATUSES,
    ROOM_STATUSES,
    SESSION_CANCELLED,
    SESSION_SCHEDULED,
    SESSION_STATUSES,
    WEEKDAYS,
)

COURSES_ACTIONS = ("create", "edit", "set_status", "add_module")
COURSES_ACTION_UI = {
    "create": "courses_action_create",
    "edit": "courses_action_edit",
    "set_status": "courses_action_set_status",
    "add_module": "courses_action_add_module",
}
COHORTS_ACTIONS = ("create", "edit", "set_status", "set_program", "set_room")
COHORTS_ACTION_UI = {
    "create": "cohorts_action_create",
    "edit": "cohorts_action_edit",
    "set_status": "cohorts_action_set_status",
    "set_program": "cohorts_action_set_program",
    "set_room": "cohorts_action_set_room",
}
ENROLLMENTS_ACTIONS = ("enroll", "set_status")
ENROLLMENTS_ACTION_UI = {
    "enroll": "enrollments_action_enroll",
    "set_status": "enrollments_action_set_status",
}
PROGRAMS_ACTIONS = ("create", "edit", "set_course", "unassign")
PROGRAMS_ACTION_UI = {
    "create": "programs_action_create",
    "edit": "programs_action_edit",
    "set_course": "programs_action_set_course",
    "unassign": "programs_action_unassign",
}
PERIODS_ACTIONS = ("create", "edit")
PERIODS_ACTION_UI = {
    "create": "periods_action_create",
    "edit": "periods_action_edit",
}

# Which glossary key titles each editable field in the edit row's option list.
COHORT_FIELD_UI = {
    "name": "field_cohort_name",
    "start_date": "field_start_date",
    "end_date": "field_end_date",
    "capacity": "field_capacity",
    "note": "field_note",
}
PROGRAM_FIELD_UI = {
    "title": "field_title",
    "description": "field_description",
}
PERIOD_FIELD_UI = {
    "name": "field_period_name",
    "start_date": "field_start_date",
    "end_date": "field_end_date",
}

# One ordered definition drives both the page rows and the form fields (the
# students manage page pattern). Options are (value, glossary key).
COURSES_FORM_FIELDS = (
    (
        "courses_action",
        "courses_action",
        tuple(
            (action, COURSES_ACTION_UI[action]) for action in COURSES_ACTIONS
        ),
    ),
    ("courses_number", "courses_number", None),
    (
        "courses_field",
        "courses_field",
        tuple(
            (name, "field_" + name) for name in EDITABLE_COURSE_FIELDS
        ),
    ),
    ("courses_value", "courses_value", None),
    ("title", "field_title", None),
    ("description", "field_description", None),
    ("category", "field_category", None),
    ("level", "field_level", None),
    ("duration", "field_duration", None),
    ("learning_objectives", "field_learning_objectives", None),
    ("prerequisites", "field_prerequisites", None),
    ("default_fee", "field_default_fee", None),
    (
        "status",
        "field_status",
        tuple((code, "course_status_" + code) for code in COURSE_STATUSES),
    ),
    ("module_title", "field_module_title", None),
    ("module_description", "field_module_description", None),
)
COHORTS_FORM_FIELDS = (
    (
        "cohorts_action",
        "cohorts_action",
        tuple(
            (action, COHORTS_ACTION_UI[action]) for action in COHORTS_ACTIONS
        ),
    ),
    ("cohorts_number", "cohorts_number", None),
    (
        "cohorts_field",
        "cohorts_field",
        tuple((name, COHORT_FIELD_UI[name]) for name in EDITABLE_COHORT_FIELDS),
    ),
    ("cohorts_value", "cohorts_value", None),
    ("name", "field_cohort_name", None),
    ("course_number", "field_course_number", None),
    ("start_date", "field_start_date", None),
    ("end_date", "field_end_date", None),
    ("capacity", "field_capacity", None),
    ("note", "field_note", None),
    ("program_number", "field_program_number", None),
    ("room_number", "field_room_number", None),
    (
        "status",
        "field_status",
        tuple((code, "cohort_status_" + code) for code in COHORT_STATUSES),
    ),
)
ENROLLMENTS_FORM_FIELDS = (
    (
        "enrollments_action",
        "enrollments_action",
        tuple(
            (action, ENROLLMENTS_ACTION_UI[action])
            for action in ENROLLMENTS_ACTIONS
        ),
    ),
    ("enrollments_number", "enrollments_number", None),
    ("cohort_number", "field_cohort_number", None),
    ("student_number", "field_student_number", None),
    ("agreed_fee", "field_agreed_fee", None),
    (
        "status",
        "field_enrollment_status",
        tuple(
            (code, "enrollment_status_" + code)
            for code in ENROLLMENT_STATUSES
        ),
    ),
    ("note", "field_note", None),
)
PROGRAMS_FORM_FIELDS = (
    (
        "programs_action",
        "programs_action",
        tuple(
            (action, PROGRAMS_ACTION_UI[action]) for action in PROGRAMS_ACTIONS
        ),
    ),
    ("programs_number", "programs_number", None),
    (
        "programs_field",
        "programs_field",
        tuple((name, PROGRAM_FIELD_UI[name]) for name in EDITABLE_PROGRAM_FIELDS),
    ),
    ("programs_value", "programs_value", None),
    ("title", "field_title", None),
    ("description", "field_description", None),
    ("course_number", "field_course_number", None),
)
PERIODS_FORM_FIELDS = (
    (
        "periods_action",
        "periods_action",
        tuple(
            (action, PERIODS_ACTION_UI[action]) for action in PERIODS_ACTIONS
        ),
    ),
    ("periods_number", "periods_number", None),
    (
        "periods_field",
        "periods_field",
        tuple((name, PERIOD_FIELD_UI[name]) for name in EDITABLE_PERIOD_FIELDS),
    ),
    ("periods_value", "periods_value", None),
    ("name", "field_period_name", None),
    ("start_date", "field_start_date", None),
    ("end_date", "field_end_date", None),
)

# Sessions and attendance (SCH-002..SCH-004, ATT-001..ATT-009): one ordered
# definition drives both the manage page's rows and its form fields, so a
# person is never asked for a value nowhere is shown.
MOMENT_FORMAT = "%Y-%m-%d %H:%M"
SESSION_TIME_FORMAT = "%H:%M"
SESSIONS_ACTIONS = ("create", "edit", "cancel", "set_room", "create_pattern")
SESSIONS_ACTION_UI = {
    "create": "sessions_action_create",
    "edit": "sessions_action_edit",
    "cancel": "sessions_action_cancel",
    "set_room": "sessions_action_set_room",
    "create_pattern": "sessions_action_create_pattern",
}
ROOMS_ACTIONS = ("create", "edit", "archive")
ROOMS_ACTION_UI = {
    "create": "rooms_action_create",
    "edit": "rooms_action_edit",
    "archive": "rooms_action_archive",
}
ROOM_FIELD_UI = {
    "name": "field_room_name",
    "capacity": "field_room_capacity",
    "location": "field_room_location",
}
ROOMS_FORM_FIELDS = (
    (
        "rooms_action",
        "rooms_action",
        tuple((action, ROOMS_ACTION_UI[action]) for action in ROOMS_ACTIONS),
    ),
    ("rooms_number", "rooms_number", None),
    (
        "rooms_field",
        "rooms_field",
        tuple((name, ROOM_FIELD_UI[name]) for name in EDITABLE_ROOM_FIELDS),
    ),
    ("rooms_value", "rooms_value", None),
    ("room_name", "field_room_name", None),
    ("room_capacity", "field_room_capacity", None),
    ("room_location", "field_room_location", None),
)
ATTENDANCE_ACTIONS = ("record", "correct")
ATTENDANCE_ACTION_UI = {
    "record": "attendance_action_record",
    "correct": "attendance_action_correct",
}
SESSIONS_FORM_FIELDS = (
    (
        "sessions_action",
        "sessions_action",
        tuple((action, SESSIONS_ACTION_UI[action]) for action in SESSIONS_ACTIONS),
    ),
    ("sessions_number", "sessions_number", None),
    (
        "sessions_field",
        "sessions_field",
        tuple((name, "field_" + name) for name in EDITABLE_SESSION_FIELDS),
    ),
    ("sessions_value", "sessions_value", None),
    ("cohort_number", "field_cohort_number", None),
    ("start_time", "field_start_time", None),
    ("end_time", "field_end_time", None),
    ("room_number", "field_room_number", None),
    ("note", "field_note", None),
    ("pattern_start_date", "field_pattern_start_date", None),
    ("pattern_end_date", "field_pattern_end_date", None),
    ("pattern_start_time", "field_pattern_start_time", None),
    ("pattern_end_time", "field_pattern_end_time", None),
    (
        "pattern_weekdays",
        "field_pattern_weekdays",
        tuple(
            (code, "weekday_label_" + code) for code, _label in WEEKDAYS
        ),
        True,
    ),
    ("pattern_note", "field_note", None),
)
ATTENDANCE_FORM_FIELDS = (
    (
        "attendance_action",
        "attendance_action",
        tuple((action, ATTENDANCE_ACTION_UI[action]) for action in ATTENDANCE_ACTIONS),
    ),
    ("attendance_number", "attendance_number", None),
    ("attendance_student", "attendance_student", None),
    (
        "attendance_status",
        "attendance_status",
        tuple((status, "attendance_" + status) for status in ATTENDANCE_STATUSES),
    ),
)

# What a service says went wrong, the row the page marks, and the words used.
COURSES_PROBLEM_UI = {
    "not_found": ("courses_number", "course_not_found"),
    "field_not_editable": ("courses_field", "field_not_editable"),
    "transition": ("status", "transition_not_allowed"),
    "entry": ("status", "entry_status_not_allowed"),
}
COHORTS_PROBLEM_UI = {
    "not_found": ("cohorts_number", "cohort_not_found"),
    "course_not_found": ("course_number", "course_not_found"),
    "course_archived": ("course_number", "course_archived"),
    "field_not_editable": ("cohorts_field", "field_not_editable"),
    "transition": ("status", "transition_not_allowed"),
    "entry": ("status", "entry_status_not_allowed"),
    "program_not_found": ("program_number", "program_not_found"),
    "already_linked_program": ("program_number", "already_linked_program"),
    "no_program_linked": ("program_number", "no_program_linked"),
    "room_not_found": ("room_number", "room_not_found"),
    "room_archived": ("room_number", "room_archived"),
}
ENROLLMENTS_PROBLEM_UI = {
    "not_found": ("enrollments_number", "enrollment_not_found"),
    "cohort_not_found": ("cohort_number", "cohort_not_found"),
    "student_not_found": ("student_number", "not_found_record"),
    "student_archived": ("student_number", "student_archived"),
    "already_enrolled": ("student_number", "already_enrolled"),
    "cohort_closed": ("cohort_number", "cohort_closed"),
    "capacity_full": ("cohort_number", "capacity_full"),
    "transition": ("status", "transition_not_allowed"),
}
PROGRAMS_PROBLEM_UI = {
    "not_found": ("programs_number", "program_not_found"),
    "course_not_found": ("course_number", "course_not_found"),
    "already_in_program": ("course_number", "already_in_program"),
    "not_in_program": ("course_number", "not_in_program"),
    "field_not_editable": ("programs_field", "field_not_editable"),
}
PERIODS_PROBLEM_UI = {
    "not_found": ("periods_number", "period_not_found"),
    "field_not_editable": ("periods_field", "field_not_editable"),
}
SESSIONS_PROBLEM_UI = {
    "not_found": ("sessions_number", "session_not_found"),
    "cohort_not_found": ("cohort_number", "cohort_not_found"),
    "field_not_editable": ("sessions_field", "field_not_editable"),
    "room_not_found": ("room_number", "room_not_found"),
    "room_archived": ("room_number", "room_archived"),
    "room_conflict": ("room_number", "room_conflict"),
    "instructor_conflict": ("cohort_number", "instructor_conflict"),
    "session_cancelled": ("sessions_number", "session_cancelled"),
    "already_cancelled": ("sessions_number", "already_cancelled"),
    "pattern_no_occurrences": ("pattern_end_date", "pattern_no_occurrences"),
}
ROOMS_PROBLEM_UI = {
    "not_found": ("rooms_number", "room_not_found"),
    "field_not_editable": ("rooms_field", "field_not_editable"),
    "already_archived": ("rooms_number", "room_already_archived"),
}
ATTENDANCE_PROBLEM_UI = {
    "session_not_found": ("attendance_number", "session_not_found"),
    "not_found": ("attendance_status", "status_invalid"),
    "student_not_found": ("attendance_student", "not_found_record"),
    "student_archived": ("attendance_student", "student_archived"),
    "not_enrolled": ("attendance_student", "not_enrolled"),
    "not_assigned": ("attendance_number", "attendance_not_assigned"),
    "already_recorded": ("attendance_student", "already_recorded"),
    "not_recorded": ("attendance_student", "not_recorded"),
    "no_change": ("attendance_status", "no_change"),
    "session_cancelled": ("attendance_number", "attendance_session_cancelled"),
}

# The parts of a program or an academic period its page shows, and the key
# that titles each.
PROGRAM_PROFILE_UI = (
    ("title", "field_title"),
    ("description", "field_description"),
)
PERIOD_PROFILE_UI = (
    ("name", "field_period_name"),
    ("start_date", "field_start_date"),
    ("end_date", "field_end_date"),
)
COURSE_PROFILE_UI = (
    ("title", "field_title"),
    ("description", "field_description"),
    ("category", "field_category"),
    ("level", "field_level"),
    ("duration", "field_duration"),
    ("learning_objectives", "field_learning_objectives"),
    ("prerequisites", "field_prerequisites"),
    ("default_fee", "field_default_fee"),
    ("status", "field_status"),
)
COHORT_PROFILE_UI = (
    ("name", "field_cohort_name"),
    ("start_date", "field_start_date"),
    ("end_date", "field_end_date"),
    ("capacity", "field_capacity"),
    ("status", "field_status"),
    ("note", "field_note"),
)


def form_fields(ui, sent, spec):
    """Form fields built from a page's field spec (one source for both).

    A spec row is (name, glossary key, options); a fourth truthy value marks a
    multi-value select (the weekday list). Existing single-value forms are
    unchanged.
    """
    fields = []
    for row in spec:
        name, key, options = row[:3]
        multiple = len(row) > 3 and bool(row[3])
        field = {"id": name, "title": ui[key]}
        if options is None:
            field["type"] = "text"
            field["value"] = sent.get(name, "")
        else:
            field["type"] = "select"
            field["current"] = sent.get(name, "") if not multiple else (
                sent.get(name) or []
            )
            field["options"] = [
                {"value": value, "label": ui[label]} for value, label in options
            ]
            if multiple:
                field["multiple"] = True
        fields.append(field)
    return fields


def form_at(action, ui, sent, spec):
    """The manage page's form: the same fields the rows show."""
    return {
        "id": action.rstrip("/"),
        "method": "post",
        "action": action,
        "fields": form_fields(ui, sent, spec),
    }


def money_text(ui, amount, currency):
    """An amount with its explicit currency, or the page's not-recorded words."""
    if amount is None:
        return ui["not_recorded"]
    text = "{:.2f}".format(amount)
    if currency:
        return "{} {}".format(text, currency)
    return text


def date_text(value):
    """A date in the ISO form the form asks for."""
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def moment_text(center, value):
    """A recorded moment, shown in this centre's own timezone (ORG-002)."""
    try:
        zone = ZoneInfo(center.display_timezone)
    except LookupError:
        zone = ZoneInfo("UTC")
    return value.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def parse_date(text):
    """(date or None, accepted) - a page parses before a service is called."""
    text = str(text).strip()
    if not text:
        return None, True
    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), True
    except ValueError:
        return None, False


def parse_int(text):
    """(int or None, accepted) - blank means no value (no capacity limit)."""
    text = str(text).strip()
    if not text:
        return None, True
    try:
        return int(text), True
    except ValueError:
        return None, False


def parse_decimal(text):
    """(Decimal or None, accepted) - blank means no fee recorded."""
    text = str(text).strip()
    if not text:
        return None, True
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, False
    if not value.is_finite():
        return None, False
    return value, True


def course_rows(center, ui):
    """This centre's courses, archived ones included (ACAD-005)."""
    return [
        {
            "number": record.pk,
            "title": record.title,
            "level": record.level or ui["not_recorded"],
            "program": record.program.title if record.program_id else ui["not_recorded"],
            "status": ui["course_status_" + record.status],
        }
        for record in Course.objects.filter(organization=center)
        .select_related("program")
        .order_by("pk")
    ]


def course_profile(center, ui, record):
    """One course's own parts, as the course page shows them."""
    rows = []
    for field, key in COURSE_PROFILE_UI:
        if field == "default_fee":
            shown = money_text(ui, record.default_fee,
                               center.primary_currency)
        elif field == "status":
            shown = ui["course_status_" + record.status]
        else:
            shown = getattr(record, field) or ui["not_recorded"]
        rows.append({"id": field, "title": ui[key], "value": shown})
    rows.insert(
        1,
        {
            "id": "program",
            "title": ui["field_program"],
            "value": record.program.title
            if record.program_id is not None
            else ui["not_recorded"],
        },
    )
    return rows


def module_rows(record, ui):
    """A course's modules in their stored order (ACAD-003)."""
    return [
        {
            "position": module.position,
            "title": module.title,
            "description": module.description or ui["not_recorded"],
        }
        for module in record.modules.all()
    ]


def cohort_rows(center, ui):
    """This centre's cohorts, every status (COH-007)."""
    return [
        {
            "number": record.pk,
            "name": record.name,
            "course": record.course.title,
            "start_date": date_text(record.start_date) or ui["not_recorded"],
            "end_date": date_text(record.end_date) or ui["not_recorded"],
            "capacity": (
                str(record.capacity)
                if record.capacity is not None
                else ui["capacity_unlimited"]
            ),
            "status": ui["cohort_status_" + record.status],
        }
        for record in Cohort.objects.filter(
            organization=center
        ).select_related("course").order_by("pk")
    ]


def cohort_profile(center, ui, record):
    """One cohort's own parts, as the cohort page shows them."""
    rows = []
    for field, key in COHORT_PROFILE_UI:
        if field == "capacity":
            shown = (
                str(record.capacity)
                if record.capacity is not None
                else ui["capacity_unlimited"]
            )
        elif field in ("start_date", "end_date"):
            shown = date_text(getattr(record, field)) or ui["not_recorded"]
        elif field == "status":
            shown = ui["cohort_status_" + record.status]
        else:
            shown = getattr(record, field) or ui["not_recorded"]
        rows.append({"id": field, "title": ui[key], "value": shown})
    rows.insert(
        1,
        {"id": "course", "title": ui["field_course"], "value": record.course.title},
    )
    rows.insert(
        2,
        {
            "id": "program",
            "title": ui["field_program"],
            "value": record.program.title
            if record.program_id is not None
            else ui["not_recorded"],
        },
    )
    rows.insert(
        3,
        {
            "id": "default_room",
            "title": ui["field_default_room"],
            "value": record.default_room.name
            if record.default_room_id is not None
            else ui["not_recorded"],
        },
    )
    return rows


def program_rows(center, ui):
    """This centre's programs, newest first (ACAD-004)."""
    return [
        {
            "number": str(record.pk),
            "title": record.title,
            "description": record.description or ui["not_recorded"],
        }
        for record in Program.objects.filter(organization=center).order_by("-created_at", "-pk")
    ]


def program_profile(center, ui, record):
    """One program's own parts, as its page shows them."""
    rows = []
    for field, key in PROGRAM_PROFILE_UI:
        shown = getattr(record, field) or ui["not_recorded"]
        rows.append({"id": field, "title": ui[key], "value": shown})
    return rows


def program_course_rows(center, ui, program):
    """The courses that belong to one program of this centre (ACAD-004)."""
    return [
        {
            "number": str(record.pk),
            "title": record.title,
        }
        for record in Course.objects.filter(program=program).order_by("pk")
    ]


def period_rows(center, ui):
    """This centre's academic periods, newest first (ACAD-006)."""
    return [
        {
            "number": str(record.pk),
            "name": record.name,
            "start_date": date_text(record.start_date) or ui["not_recorded"],
            "end_date": date_text(record.end_date) or ui["not_recorded"],
        }
        for record in AcademicPeriod.objects.filter(
            organization=center
        ).order_by("-created_at", "-pk")
    ]


def period_profile(center, ui, record):
    """One academic period's own parts, as its page shows them."""
    rows = []
    for field, key in PERIOD_PROFILE_UI:
        if field in ("start_date", "end_date"):
            shown = date_text(getattr(record, field)) or ui["not_recorded"]
        else:
            shown = getattr(record, field) or ui["not_recorded"]
        rows.append({"id": field, "title": ui[key], "value": shown})
    return rows


def enrollment_rows(center, ui, cohort=None):
    """Enrollments of this centre, optionally just one cohort's."""
    queryset = Enrollment.objects.filter(organization=center)
    if cohort is not None:
        queryset = queryset.filter(cohort=cohort)
    queryset = queryset.select_related("cohort", "student").order_by("pk")
    return [
        {
            "number": record.pk,
            "student_number": record.student.student_number,
            "student_name": record.student.name,
            "cohort": record.cohort.name,
            "status": ui["enrollment_status_" + record.status],
            "fee": money_text(ui, record.agreed_fee, record.fee_currency),
            "enrolled_at": moment_text(center, record.enrolled_at),
        }
        for record in queryset
    ]


def courses_page(request, slug):
    """This centre's reusable courses (ACAD-001)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.course.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request, "courses", {"courses": course_rows(center, ui)}, center=center
    )


def course_page(request, slug, number):
    """One course of this centre, with its ordered modules (ACAD-003)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.course.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = _course_here(center, number)
    if record is None:
        return not_found(request, center, ui, "course")
    return page(
        request,
        "course",
        {
            "number": record.pk,
            "profile": course_profile(center, ui, record),
            "modules": module_rows(record, ui),
            "instructors": course_instructors(center, record),
        },
        center=center,
    )


def course_instructors(center, record):
    """The instructors assigned to one course of this centre (STAFF-003)."""
    return [
        {"number": assignment.staff.staff_number, "name": assignment.staff.name}
        for assignment in CourseInstructor.objects.filter(
            organization=center, course=record
        )
        .select_related("staff")
        .order_by("staff__name")
    ]


def cohort_instructors(center, record):
    """The instructors assigned to one cohort of this centre (STAFF-003)."""
    return [
        {"number": assignment.staff.staff_number, "name": assignment.staff.name}
        for assignment in CohortInstructor.objects.filter(
            organization=center, cohort=record
        )
        .select_related("staff")
        .order_by("staff__name")
    ]


def not_found(request, center, ui, kind):
    """This centre's own not-found page, saying nothing about any other."""
    return page(
        request,
        "not_found",
        {
            "message": ui[kind + "_not_found"],
            "back": {
                "href": f"/o/{center.slug}/center/{kind}s/",
                "kind": kind + "s",
                "title": ui[kind + "s_heading"],
            },
        },
        center=center,
        status=404,
    )


def room_rows(center, ui):
    """This centre's rooms, archived ones included (SCH-001)."""
    return [
        {
            "number": str(record.pk),
            "name": record.name,
            "capacity": (
                str(record.capacity)
                if record.capacity is not None
                else ui["rooms_capacity_none"]
            ),
            "location": record.location or ui["not_recorded"],
            "status": ui["room_status_" + record.status],
        }
        for record in Room.objects.filter(organization=center).order_by(
            "name", "pk"
        )
    ]


def rooms_page(request, slug):
    """The centre's bookable rooms (SCH-001)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "rooms.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request,
        "rooms",
        {"rooms": room_rows(center, ui)},
        center=center,
    )


def rooms_manage(request, slug):
    """The page a room is made, changed or archived on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "rooms.manage")
    action_at = f"/o/{center.slug}/center/rooms/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "rooms_manage",
            {
                "fields": manage_rows(ui, sent, {}, ROOMS_FORM_FIELDS),
                "form": form_at(action_at, ui, sent, ROOMS_FORM_FIELDS),
                "notice": ui["rooms_manage_intro"],
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {
        row[0]: request.POST.get(row[0], "")
        for row in ROOMS_FORM_FIELDS
    }
    problems = {}
    action = sent["rooms_action"]
    if action == "":
        problems["rooms_action"] = ui["field_required"]
        note, kind = ui["rooms_rejected"], "rejected"
    elif action not in ROOMS_ACTIONS:
        problems["rooms_action"] = ui["field_invalid"].format(action)
        note, kind = ui["rooms_rejected"], "rejected"
    else:
        try:
            saved, field_problems, codes = run_room_action(
                request, center, action, sent, problems, ui
            )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            target = room_problem_field(action, name)
            problems[target] = ui["field_invalid"].format(message)
        note, kind = rooms_outcome(ui, saved, problems, codes)
    return page(
        request,
        "rooms_manage",
        {
            "fields": manage_rows(ui, sent, problems, ROOMS_FORM_FIELDS),
            "form": form_at(action_at, ui, sent, ROOMS_FORM_FIELDS),
            "notice": ui["rooms_manage_intro"],
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def run_room_action(request, center, action, sent, problems, ui):
    """Call the room service the action names, after parsing its values."""
    if action == "create":
        capacity, ok = parse_int(sent["room_capacity"])
        if not ok:
            problems["room_capacity"] = ui["field_invalid"].format(
                sent["room_capacity"]
            )
            return None, [], []
        return create_room(
            request.user,
            center,
            sent["room_name"],
            capacity,
            sent["room_location"],
        )
    if action == "edit":
        field = sent["rooms_field"]
        if field not in EDITABLE_ROOM_FIELDS:
            problems["rooms_field"] = ui["field_not_editable"]
            return None, [], []
        value, ok = room_value(field, sent["rooms_value"])
        if not ok:
            problems["rooms_value"] = ui["field_invalid"].format(
                sent["rooms_value"]
            )
            return None, [], []
        return edit_room(
            request.user, center, sent["rooms_number"], field, value
        )
    return archive_room(request.user, center, sent["rooms_number"])


def room_value(field, text):
    """The form's text as the room field expects it (edit-one-part row)."""
    if field == "capacity":
        return parse_int(text)
    return text, True


def room_problem_field(action, name):
    if action == "edit" and name in EDITABLE_ROOM_FIELDS:
        return "rooms_value"
    return {"name": "room_name", "capacity": "room_capacity",
            "location": "room_location"}.get(name, name)


def rooms_outcome(ui, saved, problems, codes):
    for code in codes:
        field_id, key = ROOMS_PROBLEM_UI[code]
        problems[field_id] = ui[key]
    if saved:
        return ui["rooms_saved"], "saved"
    if problems:
        return ui["rooms_rejected"], "rejected"
    return ui["nothing_changed"], "unchanged"


def courses_manage(request, slug):
    """The page courses are made, changed, statused, or given modules on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "academics.course.manage")
    action_at = f"/o/{center.slug}/center/courses/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "courses_manage",
            {
                "fields": manage_rows(ui, sent, {}, COURSES_FORM_FIELDS),
                "form": form_at(action_at, ui, sent, COURSES_FORM_FIELDS),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {
        name: request.POST.get(name, "")
        for name, _key, _options in COURSES_FORM_FIELDS
    }
    problems = {}
    action = sent["courses_action"]
    if action == "":
        problems["courses_action"] = ui["field_required"]
        note, kind = ui["courses_rejected"], "rejected"
    elif action not in COURSES_ACTIONS:
        problems["courses_action"] = ui["field_invalid"].format(action)
        note, kind = ui["courses_rejected"], "rejected"
    else:
        try:
            saved, field_problems, codes = run_course_action(
                request, center, action, sent, problems, ui
            )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            target = course_problem_field(action, name)
            problems[target] = ui["field_invalid"].format(message)
        note, kind = courses_outcome(ui, saved, problems, codes)
    return page(
        request,
        "courses_manage",
        {
            "fields": manage_rows(ui, sent, problems, COURSES_FORM_FIELDS),
            "form": form_at(action_at, ui, sent, COURSES_FORM_FIELDS),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def run_course_action(request, center, action, sent, problems, ui):
    """Call the service the action names, after the page parses its values."""
    if action == "create":
        fee, ok = parse_decimal(sent["default_fee"])
        if not ok:
            problems["default_fee"] = ui["field_invalid"].format(
                sent["default_fee"]
            )
            return None, [], []
        return create_course(
            request.user,
            center,
            sent["title"],
            sent["description"],
            sent["category"],
            sent["level"],
            sent["duration"],
            sent["learning_objectives"],
            sent["prerequisites"],
            fee,
            sent["status"] or COURSE_DRAFT,
        )
    if action == "edit":
        field = sent["courses_field"]
        value, ok = course_value(field, sent["courses_value"])
        if not ok:
            problems["courses_value"] = ui["field_invalid"].format(
                sent["courses_value"]
            )
            return None, [], []
        return edit_course(
            request.user, center, sent["courses_number"], field, value
        )
    if action == "add_module":
        return add_course_module(
            request.user,
            center,
            sent["courses_number"],
            sent["module_title"],
            sent["module_description"],
        )
    return set_course_status(
        request.user, center, sent["courses_number"], sent["status"]
    )


def course_value(field, text):
    """The form's text as the model field expects it (edit-one-part row)."""
    if field == "default_fee":
        return parse_decimal(text)
    return text, True


def course_problem_field(action, name):
    """Where the page marks a validator's rejection: the row that was typed."""
    if action == "edit" and name in EDITABLE_COURSE_FIELDS:
        return "courses_value"
    if action == "add_module" and name == "title":
        return "module_title"
    if action == "add_module" and name == "description":
        return "module_description"
    if action == "add_module" and name == "position":
        return "module_title"
    return name


def courses_outcome(ui, saved, problems, codes):
    for code in codes:
        field_id, key = COURSES_PROBLEM_UI[code]
        problems[field_id] = ui[key]
    if saved:
        return ui["courses_saved"], "saved"
    if problems:
        return ui["courses_rejected"], "rejected"
    return ui["nothing_changed"], "unchanged"


def cohorts_page(request, slug):
    """This centre's cohorts (COH-001)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.cohort.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request, "cohorts", {"cohorts": cohort_rows(center, ui)}, center=center
    )


def cohort_page(request, slug, number):
    """One cohort of this centre, with the students enrolled in it."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.cohort.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = _cohort_here(center, number)
    if record is None:
        return not_found(request, center, ui, "cohort")
    return page(
        request,
        "cohort",
        {
            "number": record.pk,
            "profile": cohort_profile(center, ui, record),
            "enrollments": enrollment_rows(center, ui, cohort=record),
            "instructors": cohort_instructors(center, record),
        },
        center=center,
    )


def programs_page(request, slug):
    """This centre's programs (ACAD-004). A centre that needs none has none."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.program.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request, "programs", {"programs": program_rows(center, ui)}, center=center
    )


def program_page(request, slug, number):
    """One program of this centre, with the courses that belong to it."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.program.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = _program_here(center, number)
    if record is None:
        return not_found(request, center, ui, "program")
    return page(
        request,
        "program",
        {
            "number": record.pk,
            "profile": program_profile(center, ui, record),
            "courses": program_course_rows(center, ui, record),
        },
        center=center,
    )


def periods_page(request, slug):
    """This centre's academic years and periods (ACAD-006)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.period.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request, "periods", {"periods": period_rows(center, ui)}, center=center
    )


def period_page(request, slug, number):
    """One academic period of this centre, in full."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.period.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = _period_here(center, number)
    if record is None:
        return not_found(request, center, ui, "period")
    return page(
        request,
        "period",
        {
            "number": record.pk,
            "profile": period_profile(center, ui, record),
        },
        center=center,
    )


def cohorts_manage(request, slug):
    """The page cohorts are made, changed, started or closed on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "academics.cohort.manage")
    action_at = f"/o/{center.slug}/center/cohorts/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "cohorts_manage",
            {
                "fields": manage_rows(ui, sent, {}, COHORTS_FORM_FIELDS),
                "form": form_at(action_at, ui, sent, COHORTS_FORM_FIELDS),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {
        name: request.POST.get(name, "")
        for name, _key, _options in COHORTS_FORM_FIELDS
    }
    problems = {}
    action = sent["cohorts_action"]
    if action == "":
        problems["cohorts_action"] = ui["field_required"]
        note, kind = ui["cohorts_rejected"], "rejected"
    elif action not in COHORTS_ACTIONS:
        problems["cohorts_action"] = ui["field_invalid"].format(action)
        note, kind = ui["cohorts_rejected"], "rejected"
    else:
        try:
            saved, field_problems, codes = run_cohort_action(
                request, center, action, sent, problems, ui
            )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            target = cohort_problem_field(action, name)
            problems[target] = ui["field_invalid"].format(message)
        note, kind = cohort_outcome(ui, saved, problems, codes)
    return page(
        request,
        "cohorts_manage",
        {
            "fields": manage_rows(ui, sent, problems, COHORTS_FORM_FIELDS),
            "form": form_at(action_at, ui, sent, COHORTS_FORM_FIELDS),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def manage_rows(ui, sent, problems, spec):
    """The page rows for a spec (sent values and rejections)."""
    return [
        {
            "id": row[0],
            "title": ui[row[1]],
            "value": sent.get(row[0], ""),
            "problem": problems.get(row[0], ""),
        }
        for row in spec
    ]


def run_cohort_action(request, center, action, sent, problems, ui):
    """Call the service the action names, after the page parses its values."""
    if action == "create":
        start, ok_start = parse_date(sent["start_date"])
        end, ok_end = parse_date(sent["end_date"])
        capacity, ok_capacity = parse_int(sent["capacity"])
        if not ok_start:
            problems["start_date"] = ui["field_invalid"].format(
                sent["start_date"]
            )
        if not ok_end:
            problems["end_date"] = ui["field_invalid"].format(sent["end_date"])
        if not ok_capacity:
            problems["capacity"] = ui["field_invalid"].format(sent["capacity"])
        if problems:
            return None, [], []
        return create_cohort(
            request.user,
            center,
            sent["name"],
            sent["course_number"],
            start,
            end,
            capacity,
            sent["status"] or COHORT_PLANNED,
            sent["note"],
            sent["program_number"],
        )
    if action == "edit":
        field = sent["cohorts_field"]
        value, ok = cohort_value(field, sent["cohorts_value"])
        if not ok:
            problems["cohorts_value"] = ui["field_invalid"].format(
                sent["cohorts_value"]
            )
            return None, [], []
        return edit_cohort(
            request.user, center, sent["cohorts_number"], field, value
        )
    if action == "set_program":
        return set_cohort_program(
            request.user,
            center,
            sent["cohorts_number"],
            sent["program_number"],
        )
    if action == "set_room":
        return set_cohort_room(
            request.user,
            center,
            sent["cohorts_number"],
            sent["room_number"],
        )
    return set_cohort_status(
        request.user, center, sent["cohorts_number"], sent["status"]
    )


def cohort_value(field, text):
    """The form's text as the cohort field expects it (edit-one-part row)."""
    if field in ("start_date", "end_date"):
        return parse_date(text)
    if field == "capacity":
        return parse_int(text)
    return text, True


def cohort_problem_field(action, name):
    if action == "edit" and name in EDITABLE_COHORT_FIELDS:
        return "cohorts_value"
    return name


def cohort_outcome(ui, saved, problems, codes):
    for code in codes:
        field_id, key = COHORTS_PROBLEM_UI[code]
        problems[field_id] = ui[key]
    if saved:
        return ui["cohorts_saved"], "saved"
    if problems:
        return ui["cohorts_rejected"], "rejected"
    return ui["nothing_changed"], "unchanged"


def programs_manage(request, slug):
    """The page programs are made, changed, and given their courses on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "academics.program.manage")
    action_at = f"/o/{center.slug}/center/programs/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "programs_manage",
            {
                "fields": manage_rows(ui, sent, {}, PROGRAMS_FORM_FIELDS),
                "form": form_at(action_at, ui, sent, PROGRAMS_FORM_FIELDS),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {
        name: request.POST.get(name, "")
        for name, _key, _options in PROGRAMS_FORM_FIELDS
    }
    problems = {}
    action = sent["programs_action"]
    if action == "":
        problems["programs_action"] = ui["field_required"]
        note, kind = ui["programs_rejected"], "rejected"
    elif action not in PROGRAMS_ACTIONS:
        problems["programs_action"] = ui["field_invalid"].format(action)
        note, kind = ui["programs_rejected"], "rejected"
    else:
        try:
            saved, field_problems, codes = run_program_action(
                request, center, action, sent, problems, ui
            )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            target = program_problem_field(action, name)
            problems[target] = ui["field_invalid"].format(message)
        note, kind = program_outcome(ui, saved, problems, codes)
    return page(
        request,
        "programs_manage",
        {
            "fields": manage_rows(ui, sent, problems, PROGRAMS_FORM_FIELDS),
            "form": form_at(action_at, ui, sent, PROGRAMS_FORM_FIELDS),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def run_program_action(request, center, action, sent, problems, ui):
    """Call the service the action names, after the page parses its values."""
    if action == "create":
        return create_program(
            request.user, center, sent["title"], sent["description"]
        )
    if action == "edit":
        field = sent["programs_field"]
        return edit_program(
            request.user, center, sent["programs_number"], field,
            sent["programs_value"],
        )
    if action == "set_course":
        return set_program_course(
            request.user,
            center,
            sent["programs_number"],
            sent["course_number"],
        )
    return clear_program_course(request.user, center, sent["course_number"])


def program_problem_field(action, name):
    """Where the page marks a validator's rejection: the row that was typed."""
    if action == "edit" and name in EDITABLE_PROGRAM_FIELDS:
        return "programs_value"
    return name


def program_outcome(ui, saved, problems, codes):
    for code in codes:
        field_id, key = PROGRAMS_PROBLEM_UI[code]
        problems[field_id] = ui[key]
    if saved:
        return ui["programs_saved"], "saved"
    if problems:
        return ui["programs_rejected"], "rejected"
    return ui["nothing_changed"], "unchanged"


def periods_manage(request, slug):
    """The page academic years and periods are made and changed on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "academics.period.manage")
    action_at = f"/o/{center.slug}/center/periods/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "periods_manage",
            {
                "fields": manage_rows(ui, sent, {}, PERIODS_FORM_FIELDS),
                "form": form_at(action_at, ui, sent, PERIODS_FORM_FIELDS),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {
        name: request.POST.get(name, "")
        for name, _key, _options in PERIODS_FORM_FIELDS
    }
    problems = {}
    action = sent["periods_action"]
    if action == "":
        problems["periods_action"] = ui["field_required"]
        note, kind = ui["periods_rejected"], "rejected"
    elif action not in PERIODS_ACTIONS:
        problems["periods_action"] = ui["field_invalid"].format(action)
        note, kind = ui["periods_rejected"], "rejected"
    else:
        try:
            saved, field_problems, codes = run_period_action(
                request, center, action, sent, problems, ui
            )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            target = period_problem_field(action, name)
            problems[target] = ui["field_invalid"].format(message)
        note, kind = period_outcome(ui, saved, problems, codes)
    return page(
        request,
        "periods_manage",
        {
            "fields": manage_rows(ui, sent, problems, PERIODS_FORM_FIELDS),
            "form": form_at(action_at, ui, sent, PERIODS_FORM_FIELDS),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def run_period_action(request, center, action, sent, problems, ui):
    """Call the service the action names, after the page parses its values."""
    if action == "create":
        start, ok_start = parse_date(sent["start_date"])
        end, ok_end = parse_date(sent["end_date"])
        if not ok_start:
            problems["start_date"] = ui["field_invalid"].format(sent["start_date"])
        if not ok_end:
            problems["end_date"] = ui["field_invalid"].format(sent["end_date"])
        if problems:
            return None, [], []
        return create_period(request.user, center, sent["name"], start, end)
    field = sent["periods_field"]
    value, ok = period_value(field, sent["periods_value"])
    if not ok:
        problems["periods_value"] = ui["field_invalid"].format(
            sent["periods_value"]
        )
        return None, [], []
    return edit_period(
        request.user, center, sent["periods_number"], field, value
    )


def period_value(field, text):
    """The form's text as the academic period field expects it."""
    if field in ("start_date", "end_date"):
        return parse_date(text)
    return text, True


def period_problem_field(action, name):
    """Where the page marks a validator's rejection: the row that was typed."""
    if action == "edit" and name in EDITABLE_PERIOD_FIELDS:
        return "periods_value"
    return name


def period_outcome(ui, saved, problems, codes):
    for code in codes:
        field_id, key = PERIODS_PROBLEM_UI[code]
        problems[field_id] = ui[key]
    if saved:
        return ui["periods_saved"], "saved"
    if problems:
        return ui["periods_rejected"], "rejected"
    return ui["nothing_changed"], "unchanged"


def enrollments_page(request, slug):
    """This centre's enrollment records (COH-003/COH-004)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.enrollment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request,
        "enrollments",
        {"enrollments": enrollment_rows(center, ui)},
        center=center,
    )


def enrollments_manage(request, slug):
    """The page a student is enrolled on and an enrollment is moved on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(
        member.role, "academics.enrollment.manage"
    )
    action_at = f"/o/{center.slug}/center/enrollments/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "enrollments_manage",
            {
                "fields": manage_rows(ui, sent, {}, ENROLLMENTS_FORM_FIELDS),
                "form": form_at(action_at, ui, sent, ENROLLMENTS_FORM_FIELDS),
                "notice": ui["fee_defaults_from_course"],
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {
        name: request.POST.get(name, "")
        for name, _key, _options in ENROLLMENTS_FORM_FIELDS
    }
    problems = {}
    warning = ""
    action = sent["enrollments_action"]
    if action == "":
        problems["enrollments_action"] = ui["field_required"]
        note, kind = ui["enrollments_rejected"], "rejected"
    elif action not in ENROLLMENTS_ACTIONS:
        problems["enrollments_action"] = ui["field_invalid"].format(action)
        note, kind = ui["enrollments_rejected"], "rejected"
    else:
        try:
            saved, field_problems, codes = run_enrollment_action(
                request, center, action, sent, problems, ui
            )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            problems[name] = ui["field_invalid"].format(message)
        for code in codes:
            if code == "capacity_warning":
                warning = ui["capacity_warning"]
            else:
                field_id, key = ENROLLMENTS_PROBLEM_UI[code]
                problems[field_id] = ui[key]
        if saved:
            note, kind = ui["enrollments_saved"], "saved"
        elif problems:
            note, kind = ui["enrollments_rejected"], "rejected"
        else:
            note, kind = ui["nothing_changed"], "unchanged"
        if warning and saved:
            note = warning
            kind = "warning"
    return page(
        request,
        "enrollments_manage",
        {
            "fields": manage_rows(ui, sent, problems, ENROLLMENTS_FORM_FIELDS),
            "form": form_at(action_at, ui, sent, ENROLLMENTS_FORM_FIELDS),
            "notice": ui["fee_defaults_from_course"],
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def run_enrollment_action(request, center, action, sent, problems, ui):
    """Call the service the action names, after the page parses its values."""
    if action == "enroll":
        fee, ok = parse_decimal(sent["agreed_fee"])
        if not ok:
            problems["agreed_fee"] = ui["field_invalid"].format(
                sent["agreed_fee"]
            )
            return None, [], []
        return enroll_student(
            request.user,
            center,
            sent["cohort_number"],
            sent["student_number"],
            fee,
            note=sent["note"],
        )
    return set_enrollment_status(
        request.user, center, sent["enrollments_number"], sent["status"]
    )


# A report filter naming a record this center does not have is answered like
# every other guessed number: this center's own not-found page (RULE 6.1).
REPORT_FILTER_PROBLEM_UI = {
    "period_not_found": "period",
    "course_not_found": "course",
    "cohort_not_found": "cohort",
}


def report_selection(request):
    """The three filter numbers the report form sent, as typed text."""
    return (
        str(request.GET.get("period", "")).strip(),
        str(request.GET.get("course", "")).strip(),
        str(request.GET.get("cohort", "")).strip(),
    )


def report_filter_form(center, ui, report):
    """The GET filter form: this center's own periods, courses and cohorts.

    Options come from the center's own querysets only (RULE 6.1), and the
    current value is the record the service already resolved inside it.
    """
    selected = {
        "period": report["period"],
        "course": report["course"],
        "cohort": report["cohort"],
    }
    options = {
        "period": [
            (str(record.pk), record.name)
            for record in AcademicPeriod.objects.filter(
                organization=center
            ).order_by("-created_at", "-pk")
        ],
        "course": [
            (str(record.pk), record.title)
            for record in Course.objects.filter(organization=center).order_by(
                "pk"
            )
        ],
        "cohort": [
            (str(record.pk), record.name)
            for record in Cohort.objects.filter(organization=center).order_by(
                "pk"
            )
        ],
    }
    titles = {
        "period": ui["reports_period"],
        "course": ui["reports_course"],
        "cohort": ui["reports_cohort"],
    }
    fields = []
    for name in ("period", "course", "cohort"):
        record = selected[name]
        fields.append(
            {
                "id": name,
                "title": titles[name],
                "current": str(record.pk) if record is not None else "",
                "options": [{"value": "", "label": ui["reports_all"]}]
                + [
                    {"value": value, "label": label}
                    for value, label in options[name]
                ],
            }
        )
    return {
        "id": "reports_filters",
        "method": "get",
        "action": "/o/{}/center/reports/".format(center.slug),
        "submit": ui["reports_show"],
        "fields": fields,
    }


def report_total_rows(ui, totals):
    """The report's counted figures, in the page's own words."""
    return [
        {
            "id": "report_total_enrollments",
            "title": ui["reports_total_enrollments"],
            "value": str(totals["enrollments"]),
        },
        {
            "id": "report_total_cohorts",
            "title": ui["reports_total_cohorts"],
            "value": str(totals["cohorts"]),
        },
        {
            "id": "report_seats_taken",
            "title": ui["reports_seats_taken"],
            "value": str(totals["seats_taken"]),
        },
        {
            "id": "report_seats_offered",
            "title": ui["reports_seats_offered"],
            "value": str(totals["seats_offered"]),
        },
        {
            "id": "report_cohorts_without_limit",
            "title": ui["reports_cohorts_without_limit"],
            "value": str(totals["cohorts_without_limit"]),
        },
        {
            "id": "report_cohorts_over_capacity",
            "title": ui["reports_cohorts_over_capacity"],
            "value": str(totals["cohorts_over_capacity"]),
        },
    ]


def attendance_total_rows(ui, totals):
    """The report's attendance figures, in the page's own words."""
    return [
        {
            "id": "report_total_sessions",
            "title": ui["reports_total_sessions"],
            "value": str(totals["sessions"]),
        },
        {
            "id": "report_total_attendance_records",
            "title": ui["reports_total_attendance_records"],
            "value": str(totals["records"]),
        },
        {
            "id": "report_attendance_present",
            "title": ui["reports_attendance_present"],
            "value": str(totals["present"]),
        },
        {
            "id": "report_attendance_absent",
            "title": ui["reports_attendance_absent"],
            "value": str(totals["absent"]),
        },
        {
            "id": "report_attendance_late",
            "title": ui["reports_attendance_late"],
            "value": str(totals["late"]),
        },
        {
            "id": "report_attendance_excused",
            "title": ui["reports_attendance_excused"],
            "value": str(totals["excused"]),
        },
    ]


def report_enrollment_rows(center, ui, enrollments):
    """The enrollment report's rows, each with the date that placed it."""
    return [
        {
            "number": record.pk,
            "student_number": record.student.student_number,
            "student_name": record.student.name,
            "cohort": record.cohort.name,
            "cohort_number": record.cohort.pk,
            "course": record.cohort.course.title,
            "start_date": date_text(record.cohort.start_date)
            or ui["not_recorded"],
            "enrolled_at": moment_text(center, record.enrolled_at),
            "status": ui["enrollment_status_" + record.status],
            "fee": money_text(ui, record.agreed_fee, record.fee_currency),
        }
        for record in enrollments
    ]


def report_capacity_rows(center, ui, cohorts):
    """The cohort capacity report's rows (COH-005/REP-003)."""
    rows = []
    for record in cohorts:
        state, places_left = capacity_state(record.capacity, record.seats_taken)
        rows.append(
            {
                "number": record.pk,
                "name": record.name,
                "course": record.course.title,
                "start_date": date_text(record.start_date)
                or ui["not_recorded"],
                "status": ui["cohort_status_" + record.status],
                "capacity": (
                    str(record.capacity)
                    if record.capacity is not None
                    else ui["capacity_unlimited"]
                ),
                "seats_taken": str(record.seats_taken),
                "places_left": (
                    str(places_left)
                    if places_left is not None
                    else ui["capacity_unlimited"]
                ),
                "state": ui["capacity_state_" + state],
            }
        )
    return rows


def report_csv_href(center, report, export="enrollments"):
    """The CSV link, carrying the same filters the page is showing."""
    values = {}
    for name in ("period", "course", "cohort"):
        record = report[name]
        if record is not None:
            values[name] = str(record.pk)
    query = urlencode(values)
    href = "/o/{}/center/reports/{}.csv".format(center.slug, export)
    return href + ("?" + query if query else "")


def report_context(center, ui, report, can_attendance=False):
    """Everything the report page and its CSV link show.

    The sessions and the attendance figures answer only to those who may see
    the attendance at this centre: a receptionist reads the enrollment and
    capacity report and none of the attendance part (ATT-001's permission,
    not the report's own). The attendance CSV link is offered only when the
    attendance itself is shown.
    """
    rows = {}
    if can_attendance:
        rows = {
            "sessions": session_rows(center, ui, report["sessions"]),
            "attendance_totals": attendance_total_rows(
                ui, report["attendance_totals"]
            ),
            "attendance": attendance_rows(
                center, ui, records=report["attendance"]
            ),
            "attendance_csv_href": report_csv_href(
                center, report, export="attendance"
            ),
        }
    return {
        "form": report_filter_form(center, ui, report),
        "totals": report_total_rows(ui, report["totals"]),
        "enrollments": report_enrollment_rows(
            center, ui, report["enrollments"]
        ),
        "cohorts": report_capacity_rows(center, ui, report["cohorts"]),
        "csv_href": report_csv_href(center, report),
        **rows,
    }


def reports_page(request, slug):
    """The center's academic reports: enrollments and cohort capacity.

    ACAD-006/REP-003/REP-004: periods group the records by their own dates
    (see reporting.py), and the period, course and cohort filters are all
    resolved inside this center. The address is read-only: a non-read method
    is answered 405, so no request here can change an academic record.
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.report.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    try:
        report, codes = build_report(
            request.user, center, *report_selection(request)
        )
    except PermissionDenied:
        return page(request, "denied", {}, center=center, status=403)
    if codes:
        return not_found(
            request, center, ui, REPORT_FILTER_PROBLEM_UI[codes[0]]
        )
    can_attendance = role_has_permission(member.role, "attendance.view")
    return page(
        request,
        "reports",
        report_context(center, ui, report, can_attendance=can_attendance),
        center=center,
    )


def reports_csv(request, slug):
    """REP-005/REP-007: the enrollment report as CSV, under the same rules.

    The export asks the same permission, resolves the same filters inside the
    same center, and writes exactly the rows the page would show.
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "academics.report.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    try:
        report, codes = build_report(
            request.user, center, *report_selection(request)
        )
    except PermissionDenied:
        return page(request, "denied", {}, center=center, status=403)
    if codes:
        return not_found(
            request, center, ui, REPORT_FILTER_PROBLEM_UI[codes[0]]
        )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="enrollments-{}.csv"'.format(center.slug)
    )
    write = csv_writer(response)
    write(
        [
            ui["enrollments_number"],
            ui["field_student_number"],
            ui["field_name"],
            ui["field_cohort_name"],
            ui["field_course"],
            ui["field_start_date"],
            ui["field_enrolled_at"],
            ui["field_enrollment_status"],
            ui["field_agreed_fee"],
        ]
    )
    for record in report["enrollments"]:
        write(
            [
                record.pk,
                record.student.student_number,
                record.student.name,
                record.cohort.name,
                record.cohort.course.title,
                date_text(record.cohort.start_date),
                moment_text(center, record.enrolled_at),
                record.status,
                money_text(ui, record.agreed_fee, record.fee_currency),
            ]
        )
    return response


def parse_moment(center, text):
    """(moment or None, "ok" | "invalid" | "nonexistent") for a session time.

    SCH-009: a session's times are shown through moment_text, in the centre's
    configured timezone, so the page asks for a time in that same language: a
    person types what the page shows them. The value is stored as a Django
    moment (kept consistently on the database) and read back through the same
    words, so the time typed answers as the time shown. A time the page's own
    format cannot name is refused here, before the service is asked about it.

    D74/D77: a wall clock time a fall-back change repeats is read with the
    first fold, the convention make_aware answers with by default. A
    spring-forward wall clock time that does not exist is refused instead of
    being silently shifted, by comparing the round trip back to local time
    with what was asked - the same rule generation follows.
    """
    text = str(text).strip()
    if not text:
        return None, "invalid"
    try:
        raw = datetime.strptime(text, MOMENT_FORMAT)
    except ValueError:
        return None, "invalid"
    try:
        zone = ZoneInfo(center.display_timezone or "UTC")
    except LookupError:
        zone = ZoneInfo("UTC")
    aware = make_aware(raw, zone)
    # Through UTC: astimezone() with the very same tzinfo is a no-op in Python,
    # so a direct round trip would never notice a shifted wall time.
    round_trip = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != raw:
        return None, "nonexistent"
    return aware, "ok"


def session_rows(center, ui, sessions=None):
    """Sessions with their own attendance counts (ATT-004/ATT-006).

    The counts come from this centre's own attendance records, kept apart by
    their four states, so one session's attendance is visible without opening
    the register. A session nothing was recorded for answers zero.

    SCH-004/SCH-005/SCH-006: each row also names its room and its status, and
    every scheduled row carries the overlaps derived from the current
    timetable (warning_map). Nothing about a warning is stored, so it
    disappears as soon as the underlying overlap is gone. A cancelled
    occurrence stays visible but is never a conflict (SCH-008).
    """
    if sessions is None:
        sessions = session_report_rows(center)
    warnings = warning_map(center, sessions)
    return [
        {
            "number": str(session.pk),
            "cohort": session.cohort.name,
            "cohort_number": session.cohort.pk,
            "course": session.cohort.course.title,
            "start": moment_text(center, session.start_at),
            "end": moment_text(center, session.end_at),
            "note": session.note or ui["sessions_note_none"],
            "room": session.room.name
            if session.room_id is not None
            else ui["not_recorded"],
            "status": ui["session_status_" + session.status],
            "cancelled": session.status == SESSION_CANCELLED,
            "warnings": [
                {
                    "kind": item["kind"],
                    "text": ui[
                        "room_conflict_warning"
                        if item["kind"] == "room"
                        else "instructor_conflict_warning"
                    ].format(item["other"].pk),
                }
                for item in warnings.get(session.pk, [])
            ],
            "present": str(session.present),
            "absent": str(session.absent),
            "late": str(session.late),
            "excused": str(session.excused),
            "records": str(session.attendance_records),
        }
        for session in sessions
    ]


def sessions_page(request, slug):
    """The centre's class occurrences (SCH-002/ATT-004)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "sessions.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request,
        "sessions",
        {"sessions": session_rows(center, ui)},
        center=center,
    )


def sessions_manage(request, slug):
    """The page a session is scheduled on and changed one part on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "sessions.manage")
    action_at = f"/o/{center.slug}/center/sessions/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "sessions_manage",
            {
                "fields": manage_rows(ui, sent, {}, SESSIONS_FORM_FIELDS),
                "form": form_at(action_at, ui, sent, SESSIONS_FORM_FIELDS),
                "notice": ui["sessions_manage_intro"],
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {}
    for row in SESSIONS_FORM_FIELDS:
        name = row[0]
        if len(row) > 3 and row[3]:
            sent[name] = request.POST.getlist(name)
        else:
            sent[name] = request.POST.get(name, "")
    problems = {}
    action = sent["sessions_action"]
    if action == "":
        problems["sessions_action"] = ui["field_required"]
        note, kind = ui["sessions_rejected"], "rejected"
    elif action not in SESSIONS_ACTIONS:
        problems["sessions_action"] = ui["field_invalid"].format(action)
        note, kind = ui["sessions_rejected"], "rejected"
    else:
        try:
            saved, field_problems, codes = run_session_action(
                request, center, action, sent, problems, ui
            )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            target = session_problem_field(action, name)
            problems[target] = ui["field_invalid"].format(message)
        note, kind = sessions_outcome(ui, saved, problems, codes)
    return page(
        request,
        "sessions_manage",
        {
            "fields": manage_rows(ui, sent, problems, SESSIONS_FORM_FIELDS),
            "form": form_at(action_at, ui, sent, SESSIONS_FORM_FIELDS),
            "notice": ui["sessions_manage_intro"],
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def moment_problem(ui, state, text):
    """The words a refused session moment answers with (D74/D77)."""
    if state == "nonexistent":
        return ui["session_time_nonexistent"].format(text)
    return ui["field_invalid"].format(text)


def run_session_action(request, center, action, sent, problems, ui):
    """Call the service the action names, after the page parsed its values."""
    if action == "create":
        start_at, start_state = parse_moment(center, sent["start_time"])
        end_at, end_state = parse_moment(center, sent["end_time"])
        if not sent["start_time"].strip():
            problems["start_time"] = ui["field_required"]
        elif start_at is None:
            problems["start_time"] = moment_problem(
                ui, start_state, sent["start_time"]
            )
        if not sent["end_time"].strip():
            problems["end_time"] = ui["field_required"]
        elif end_at is None:
            problems["end_time"] = moment_problem(
                ui, end_state, sent["end_time"]
            )
        if problems:
            return None, [], []
        return create_session(
            request.user,
            center,
            sent["cohort_number"],
            start_at,
            end_at,
            sent["note"],
            sent["room_number"],
        )
    if action == "cancel":
        return cancel_session(
            request.user, center, sent["sessions_number"]
        )
    if action == "set_room":
        return set_session_room(
            request.user,
            center,
            sent["sessions_number"],
            sent["room_number"],
        )
    if action == "create_pattern":
        start_date, ok_start_date = parse_date(sent["pattern_start_date"])
        end_date, ok_end_date = parse_date(sent["pattern_end_date"])
        start_time, ok_start_time = parse_time(sent["pattern_start_time"])
        end_time, ok_end_time = parse_time(sent["pattern_end_time"])
        weekdays = sent["pattern_weekdays"]
        if not ok_start_date:
            problems["pattern_start_date"] = ui["field_invalid"].format(
                sent["pattern_start_date"]
            )
        if not ok_end_date:
            problems["pattern_end_date"] = ui["field_invalid"].format(
                sent["pattern_end_date"]
            )
        if not ok_start_time:
            problems["pattern_start_time"] = ui["field_invalid"].format(
                sent["pattern_start_time"]
            )
        if not ok_end_time:
            problems["pattern_end_time"] = ui["field_invalid"].format(
                sent["pattern_end_time"]
            )
        if not weekdays:
            problems["pattern_weekdays"] = ui["field_required"]
        if problems:
            return None, [], []
        return create_schedule_pattern(
            request.user,
            center,
            sent["cohort_number"],
            weekdays,
            start_date,
            end_date,
            start_time,
            end_time,
            sent["room_number"],
            sent["pattern_note"],
        )
    field = sent["sessions_field"]
    if field not in EDITABLE_SESSION_FIELDS:
        problems["sessions_field"] = ui["field_not_editable"]
        return None, [], []
    value = sent["sessions_value"]
    if field in ("start_at", "end_at"):
        moment, state = parse_moment(center, value)
        if moment is None:
            problems["sessions_value"] = moment_problem(ui, state, value)
            return None, [], []
        value = moment
    return edit_session(
        request.user, center, sent["sessions_number"], field, value
    )


def parse_time(text):
    """(time or None, accepted) - a pattern's local wall-clock time."""
    text = str(text).strip()
    if not text:
        return None, False
    try:
        return datetime.strptime(text, SESSION_TIME_FORMAT).time(), True
    except ValueError:
        return None, False


def session_problem_field(action, name):
    """The row a rejected value belongs to: the value row when editing one."""
    if action == "edit" and name in EDITABLE_SESSION_FIELDS:
        return "sessions_value"
    if action == "create_pattern":
        return {
            "weekdays": "pattern_weekdays",
            "start_time": "pattern_start_time",
            "end_time": "pattern_end_time",
            "start_date": "pattern_start_date",
            "end_date": "pattern_end_date",
            "room": "room_number",
            "cohort": "cohort_number",
            "note": "pattern_note",
        }.get(name, name)
    return {
        "start_at": "start_time",
        "end_at": "end_time",
        "room": "room_number",
        "cohort": "cohort_number",
        "note": "note",
    }.get(name, name)


def sessions_outcome(ui, saved, problems, codes):
    """The words a session attempt answers with, and what it did.

    SCH-005/SCH-006: a warning never marks the form as rejected - the record
    was saved and the person is told what overlaps. A cancellation is a save;
    cancelling an already cancelled occurrence changes nothing (SCH-008).
    """
    messages = []
    unchanged = False
    warned = False
    for code in codes:
        if code == "cancelled":
            messages.append(ui["session_cancelled_saved"])
        elif code == "already_cancelled":
            unchanged = True
            messages.append(ui["already_cancelled"])
        elif code == "room_warning":
            warned = True
            messages.append(ui["room_conflict_warning_saved"])
        elif code == "instructor_warning":
            warned = True
            messages.append(ui["instructor_conflict_warning_saved"])
        elif code == "cohort_dates_warning":
            warned = True
            messages.append(ui["cohort_dates_warning"])
        else:
            field_id, key = SESSIONS_PROBLEM_UI[code]
            # A service that named the conflicting session has already keyed a
            # precise sentence to this row; the generic copy only fills a gap.
            problems.setdefault(field_id, ui[key])
    if saved:
        if problems:
            return ui["sessions_rejected"], "rejected"
        if unchanged:
            return " ".join(messages), "unchanged"
        if warned:
            return " ".join(messages), "warning"
        if messages:
            return " ".join(messages), "saved"
        return ui["sessions_saved"], "saved"
    if problems:
        return ui["sessions_rejected"], "rejected"
    return ui["nothing_changed"], "unchanged"


def local_midnight(center, day):
    """The moment this centre's own day begins (SCH-007/SCH-009)."""
    try:
        zone = ZoneInfo(center.display_timezone or "UTC")
    except LookupError:
        zone = ZoneInfo("UTC")
    return make_aware(datetime.combine(day, time.min), zone)


def schedule_window(center, start_day, days):
    """(start moment, end moment, sessions) for a local day or week.

    The window is built from the centre's own local midnights, so the rows are
    exactly the occurrences a person sees under those dates even across a DST
    change (SCH-009).
    """
    start_at = local_midnight(center, start_day)
    end_at = local_midnight(center, start_day + timedelta(days=days))
    sessions = [
        session
        for session in session_report_rows(center)
        if start_at <= session.start_at < end_at
    ]
    return start_at, end_at, sessions


def schedule_day(request, slug):
    """One local day of this centre's timetable (SCH-007)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "sessions.view"):
        return page(request, "denied", {}, center=center, status=403)
    text = request.GET.get("date", "").strip()
    if text:
        day, accepted = parse_date(text)
        if not accepted:
            return page(request, "problem", {}, status=404)
    else:
        try:
            zone = ZoneInfo(center.display_timezone or "UTC")
        except LookupError:
            zone = ZoneInfo("UTC")
        day = timezone.now().astimezone(zone).date()
    ui = center_ui(center)
    _start, _end, sessions = schedule_window(center, day, 1)
    return page(
        request,
        "schedule_day",
        {
            "day": date_text(day),
            "previous": date_text(day - timedelta(days=1)),
            "next": date_text(day + timedelta(days=1)),
            "week_start": date_text(day - timedelta(days=day.weekday())),
            "sessions": session_rows(center, ui, sessions),
        },
        center=center,
    )


def schedule_week(request, slug):
    """One Monday-to-Sunday week of this centre's timetable (SCH-007)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "sessions.view"):
        return page(request, "denied", {}, center=center, status=403)
    text = request.GET.get("date", "").strip()
    if text:
        day, accepted = parse_date(text)
        if not accepted:
            return page(request, "problem", {}, status=404)
    else:
        try:
            zone = ZoneInfo(center.display_timezone or "UTC")
        except LookupError:
            zone = ZoneInfo("UTC")
        day = timezone.now().astimezone(zone).date()
    monday = day - timedelta(days=day.weekday())
    ui = center_ui(center)
    _start, _end, sessions = schedule_window(center, monday, 7)
    return page(
        request,
        "schedule_week",
        {
            "start": date_text(monday),
            "end": date_text(monday + timedelta(days=6)),
            "previous": date_text(monday - timedelta(days=7)),
            "next": date_text(monday + timedelta(days=7)),
            "sessions": session_rows(center, ui, sessions),
        },
        center=center,
    )


def attendance_rows(center, ui, student=None, cohorts=None, records=None,
                    session=None):
    """This centre's attendance records, by their session's own start.

    Given a student, only that student's records here (ATT-005): a student of
    another centre has none here, so the answer says nothing about them.
    `cohorts` is the server-side scope of an instructor (ATT-004). The words
    for a status come from the centre's own glossary, so a record is named in
    the same language everywhere it is shown.
    """
    if records is None:
        rows = Attendance.objects.filter(organization=center)
        if session is not None:
            rows = rows.filter(session=session)
        if student is not None:
            rows = rows.filter(student=student)
        if cohorts is not None:
            rows = rows.filter(session__cohort__id__in=cohorts)
        records = rows.order_by(
            "session__start_at", "student__student_number", "pk"
        ).select_related("session", "session__cohort", "student")
    return [
        {
            "session_number": str(record.session.pk),
            "session_start": moment_text(center, record.session.start_at),
            "cohort": record.session.cohort.name,
            "course": record.session.cohort.course.title,
            "student_number": str(record.student.student_number),
            "name": record.student.name,
            "status": ui["attendance_" + record.status],
            "recorded": moment_text(center, record.recorded_at),
            "changed": moment_text(center, record.status_changed_at)
            if record.status_changed_at
            else ui["attendance_not_recorded"],
            "note": record.note or ui["attendance_history_note_none"],
        }
        for record in records
    ]


def audit_rows(center, ui, student=None, cohorts=None, session=None):
    """Attendance corrections here, newest first (ATT-009's audit trail).

    Given a student, only the corrections naming them (STU-006, ATT-009). The
    words name both sides of one correction: what it was and what it became,
    and who corrected it when.
    """
    rows = AuditLog.objects.filter(organization=center)
    if session is not None:
        rows = rows.filter(session=session)
    if student is not None:
        rows = rows.filter(student=student)
    if cohorts is not None:
        rows = rows.filter(session__cohort__id__in=cohorts)
    return [
        {
            "session_number": str(record.session.pk),
            "cohort": record.session.cohort.name,
            "student_number": str(record.student.student_number),
            "name": record.student.name,
            "previous": ui["attendance_" + record.previous_status],
            "new": ui["attendance_" + record.new_status],
            "actor": record.actor.display_name or record.actor.username,
            "at": moment_text(center, record.at),
        }
        for record in rows.order_by("-at", "-pk").select_related(
            "session", "session__cohort", "student", "actor"
        )
    ]


def attendance_page(request, slug):
    """The centre's attendance register (ATT-001/ATT-002/ATT-004).

    An instructor sees the records of the sessions of the cohorts they teach,
    and nothing else: the scope is decided here from the centre's own
    assignment, never from the address typed (SEC-001). The figures are the
    centre's own, over exactly the rows shown (ATT-006/ATT-007: the four
    states stay apart so an excused absence is visible separately).
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "attendance.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    scope = _attendance_scope(request.user, center)
    sessions = session_report_rows(center, cohorts=scope)
    attendances = Attendance.objects.filter(organization=center)
    if scope is not None:
        attendances = attendances.filter(session__cohort__id__in=scope)
    attendances = list(attendances)
    return page(
        request,
        "attendance",
        {
            "sessions": session_rows(center, ui, sessions),
            "attendance": attendance_rows(center, ui, cohorts=scope),
            "attendance_totals": attendance_total_rows(
                ui, attendance_totals(attendances, sessions)
            ),
            "audit": audit_rows(center, ui, cohorts=scope),
        },
        center=center,
    )


def attendance_session_page(request, slug, number):
    """One session's attendance register (ATT-001/ATT-004/ATT-006/ATT-008).

    ATT-001: the register of one session names who may be marked, and that
    list is this centre's own enrollment of the session's cohort with nothing
    recorded yet - the same eligibility rule the service answers from
    (ATT-008), so the page cannot claim a mark nothing could make. An
    instructor is answered this centre's own not-found for a session outside
    the cohorts they teach, exactly as if it were not theirs.
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "attendance.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    session = _session_here(center, number)
    if session is None:
        return not_found(request, center, ui, "session")
    scope = _attendance_scope(request.user, center)
    if scope is not None and session.cohort_id not in scope:
        return not_found(request, center, ui, "session")
    recorded = list(
        Attendance.objects.filter(session=session).values_list(
            "student_id", flat=True
        )
    )
    counts = {status: 0 for status in ATTENDANCE_STATUSES}
    for status in Attendance.objects.filter(session=session).values_list(
        "status", flat=True
    ):
        counts[status] = counts.get(status, 0) + 1
    marked = [
        {
            "student_number": str(enrollment.student.student_number),
            "name": enrollment.student.name,
        }
        for enrollment in Enrollment.objects.filter(
            cohort=session.cohort,
            status__in=ATTENDANCE_ELIGIBLE_STATUSES,
        )
        .select_related("student")
        .order_by("student__student_number")
        if enrollment.student_id not in recorded
    ]
    return page(
        request,
        "attendance_session",
        {
            "number": str(session.pk),
            "heading": ui["attendance_session_heading"].format(session.pk),
            "cohort": session.cohort.name,
            "cohort_number": session.cohort.pk,
            "course": session.cohort.course.title,
            "start": moment_text(center, session.start_at),
            "end": moment_text(center, session.end_at),
            "note": session.note or ui["sessions_note_none"],
            "counts": [
                {
                    "id": "attendance_session_" + status,
                    "title": ui["attendance_session_" + status],
                    "value": str(counts[status]),
                }
                for status in ATTENDANCE_STATUSES
            ]
            + [
                {
                    "id": "attendance_session_records",
                    "title": ui["attendance_session_records"],
                    "value": str(len(recorded)),
                }
            ],
            "records": attendance_rows(center, ui, session=session),
            "students": marked,
            "audit": audit_rows(center, ui, session=session),
        },
        center=center,
    )


def attendance_manage(request, slug):
    """The page attendance is recorded on and corrected on (ATT-003/ATT-005)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_record = role_has_permission(member.role, "attendance.record")
    can_correct = role_has_permission(member.role, "attendance.correct")
    action_at = f"/o/{center.slug}/center/attendance/manage/"
    if request.method in READ_METHODS:
        if not (can_record or can_correct):
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "attendance_manage",
            {
                "fields": manage_rows(ui, sent, {}, ATTENDANCE_FORM_FIELDS),
                "form": form_at(action_at, ui, sent, ATTENDANCE_FORM_FIELDS),
                "notice": ui["attendance_manage_intro"],
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not (can_record or can_correct):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {
        name: request.POST.get(name, "")
        for name, _key, _options in ATTENDANCE_FORM_FIELDS
    }
    problems = {}
    action = sent["attendance_action"]
    if action == "":
        problems["attendance_action"] = ui["field_required"]
        note, kind = ui["attendance_rejected"], "rejected"
    elif action not in ATTENDANCE_ACTIONS:
        problems["attendance_action"] = ui["field_invalid"].format(action)
        note, kind = ui["attendance_rejected"], "rejected"
    elif action == "record" and not can_record:
        return page(request, "denied", {}, center=center, status=403)
    elif action == "correct" and not can_correct:
        return page(request, "denied", {}, center=center, status=403)
    else:
        if sent["attendance_status"] not in ATTENDANCE_STATUSES:
            problems["attendance_status"] = ui["status_invalid"]
            note, kind = ui["attendance_rejected"], "rejected"
        else:
            try:
                saved, field_problems, codes = run_attendance_action(
                    request, center, action, sent, problems, ui
                )
            except PermissionDenied:
                return page(request, "denied", {}, center=center, status=403)
            for name, message in field_problems:
                problems[name] = ui["field_invalid"].format(message)
            note, kind = attendance_outcome(ui, saved, problems, codes)
    return page(
        request,
        "attendance_manage",
        {
            "fields": manage_rows(ui, sent, problems, ATTENDANCE_FORM_FIELDS),
            "form": form_at(action_at, ui, sent, ATTENDANCE_FORM_FIELDS),
            "notice": ui["attendance_manage_intro"],
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def run_attendance_action(request, center, action, sent, problems, ui):
    """Call the attendance service the action names."""
    if action == "record":
        return record_attendance(
            request.user,
            center,
            sent["attendance_number"],
            sent["attendance_student"],
            sent["attendance_status"],
        )
    return correct_attendance(
        request.user,
        center,
        sent["attendance_number"],
        sent["attendance_student"],
        sent["attendance_status"],
    )


def attendance_outcome(ui, saved, problems, codes):
    """The words an attendance attempt answers with, and what it did."""
    for code in codes:
        field_id, key = ATTENDANCE_PROBLEM_UI[code]
        problems[field_id] = ui[key]
    if saved:
        return ui["attendance_saved"], "saved"
    if problems:
        return ui["attendance_rejected"], "rejected"
    return ui["nothing_changed"], "unchanged"


def attendance_register_csv(request, slug, number):
    """ATT-009: one session's attendance register as a file.

    The same permission and the same server-side instructor scope as the
    register page, and exactly the rows that page would show.
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "attendance.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    session = _session_here(center, number)
    if session is None:
        return not_found(request, center, ui, "session")
    scope = _attendance_scope(request.user, center)
    if scope is not None and session.cohort_id not in scope:
        return not_found(request, center, ui, "session")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="attendance-{}-{}.csv"'.format(session.pk, center.slug)
    )
    write = csv_writer(response)
    write(
        [
            ui["sessions_number"],
            ui["attendance_student"],
            ui["field_name"],
            ui["attendance_status"],
            ui["attendance_recorded_at"],
            ui["attendance_status_changed"],
            ui["attendance_history_note"],
        ]
    )
    for record in Attendance.objects.filter(session=session).order_by(
        "student__student_number", "pk"
    ).select_related("student"):
        write(
            [
                str(session.pk),
                record.student.student_number,
                record.student.name,
                record.status,
                moment_text(center, record.recorded_at),
                moment_text(center, record.status_changed_at)
                if record.status_changed_at
                else "",
                record.note,
            ]
        )
    return response


def attendance_report_csv(request, slug):
    """ATT-009/REP-007: the attendance report as CSV, under the report rules.

    The same permission the reports page asks and the same filters resolved
    in the same centre, and exactly the attendance rows the report covers -
    no export of anyone's own personal data.
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    # The combined rule: this is the report's CSV, and it carries attendance
    # rows, so it asks for both the report permission and the attendance one
    # (ATT-004, SEC-001). A receptionist can see the report but not the
    # attendance, so the attendance CSV stays closed to them at the URL layer,
    # the same 403 the attendance pages answer with.
    if not role_has_permission(
        member.role, "academics.report.view"
    ) or not role_has_permission(member.role, "attendance.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    try:
        report, codes = build_report(
            request.user, center, *report_selection(request)
        )
    except PermissionDenied:
        return page(request, "denied", {}, center=center, status=403)
    if codes:
        return not_found(
            request, center, ui, REPORT_FILTER_PROBLEM_UI[codes[0]]
        )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="attendance-{}.csv"'.format(center.slug)
    )
    write = csv_writer(response)
    write(
        [
            ui["sessions_number"],
            ui["field_start_time"],
            ui["field_cohort_name"],
            ui["attendance_student"],
            ui["field_name"],
            ui["attendance_status"],
            ui["attendance_recorded_at"],
            ui["attendance_status_changed"],
            ui["attendance_history_note"],
        ]
    )
    for record in report["attendance"]:
        write(
            [
                record.session.pk,
                moment_text(center, record.session.start_at),
                record.session.cohort.name,
                record.student.student_number,
                record.student.name,
                record.status,
                moment_text(center, record.recorded_at),
                moment_text(center, record.status_changed_at)
                if record.status_changed_at
                else "",
                record.note,
            ]
        )
    return response
