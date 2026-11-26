"""The business actions of one center's courses, cohorts and enrollments.

Every action re-asks the central authorization layer
(apps.organizations.services.require) before anything is read or written, and
resolves each number through the caller's own organization first, so a number
typed or posted by a browser can never name another center's course, cohort or
student (SEC-004/RULE 6.1).

Contract, the same shape the student actions use: (record_or_none, problems,
notes)
- record: the created/updated record, or None when nothing was saved;
- problems: list of (field, english validator message) rows from full_clean;
- notes: short business codes the page answers with its own words.

Statuses are never free text. COURSE_TRANSITIONS, COHORT_TRANSITIONS and
ENROLLMENT_TRANSITIONS are the only place a status moves (ACAD-005/COH-007/
COH-004, 6.3); docs/DECISIONS.md records the tables for the product owner to
confirm.

Capacity (COH-005) reads the center's own capacity_policy: "prevent" refuses
the enrollment and names the cohort; "warn" saves it and returns
"capacity_warning" as a note, so the page can say what happened. Only
enrollments still in the enrolled state occupy a seat; a completed, withdrawn
or cancelled enrollment no longer does.

Programs (ACAD-004) and academic periods (ACAD-006) are the other half of this
slice: a program is an optional grouping of this center's own courses — a
center that needs none simply has none — and a period is a named, dated year
or span the center defines for its own years. Grouping actions keep the
same-center invariant through the model hooks. A period stays a standalone
record: instead of a foreign key the SRS never asks for, build_report()
computes which records a period contains from their own stored dates (see
reporting.py), and reports the figures for the rows it shows.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.organizations.models import CAPACITY_WARN, Members, SCHEDULE_WARN
from apps.organizations.roles import ROLE_INSTRUCTOR
from apps.organizations.services import require
from apps.students.services import _field_problems, _student_here
from apps.students.statuses import STATUS_ARCHIVED
from apps.staff.models import CohortInstructor

from .models import (
    AcademicPeriod,
    Attendance,
    AuditLog,
    Cohort,
    Course,
    CourseModule,
    Enrollment,
    Program,
    Room,
    SchedulePattern,
    Session,
)
from .reporting import (
    attendance_report_rows,
    attendance_totals,
    cohort_report_rows,
    enrollment_report_rows,
    report_totals,
    session_report_rows,
)
from .scheduling import (
    cohort_date_warning,
    local_wall_time,
    session_conflicts,
)
from .statuses import (
    AUDIT_ATTENDANCE_CORRECTED,
    ATTENDANCE_STATUS_CHOICES,
    COHORT_ACTIVE,
    COHORT_CANCELLED,
    COHORT_COMPLETED,
    COHORT_PLANNED,
    COURSE_ACTIVE,
    COURSE_ARCHIVED,
    COURSE_DRAFT,
    ENROLLMENT_CANCELLED,
    ENROLLMENT_COMPLETED,
    ENROLLMENT_ENROLLED,
    ENROLLMENT_WITHDRAWN,
    ROOM_ARCHIVED,
    SESSION_CANCELLED,
    SESSION_SCHEDULED,
    WEEKDAY_CODES,
    WEEKDAY_INDEX,
)

# ACAD-005: draft -> active -> archived; archived is terminal. A course that
# history references is never hard-deleted, only archived.
COURSE_TRANSITIONS = {
    COURSE_DRAFT: (COURSE_ACTIVE, COURSE_ARCHIVED),
    COURSE_ACTIVE: (COURSE_ARCHIVED,),
    COURSE_ARCHIVED: (),
}
COURSE_ENTRY_STATUSES = (COURSE_DRAFT, COURSE_ACTIVE)

# COH-007: planned -> active -> completed, or cancelled at either open step.
# completed and cancelled are terminal.
COHORT_TRANSITIONS = {
    COHORT_PLANNED: (COHORT_ACTIVE, COHORT_CANCELLED),
    COHORT_ACTIVE: (COHORT_COMPLETED, COHORT_CANCELLED),
    COHORT_COMPLETED: (),
    COHORT_CANCELLED: (),
}
COHORT_ENTRY_STATUSES = (COHORT_PLANNED, COHORT_ACTIVE)

# COH-004/6.3: an enrollment starts enrolled. Completed and cancelled are
# terminal; a withdrawn student may be enrolled again on the same record, and
# that reinstatement is capacity-checked like a new enrollment.
ENROLLMENT_TRANSITIONS = {
    ENROLLMENT_ENROLLED: (
        ENROLLMENT_COMPLETED,
        ENROLLMENT_WITHDRAWN,
        ENROLLMENT_CANCELLED,
    ),
    ENROLLMENT_COMPLETED: (),
    ENROLLMENT_WITHDRAWN: (ENROLLMENT_ENROLLED,),
    ENROLLMENT_CANCELLED: (),
}
ENROLLMENT_ENTRY_STATUSES = (ENROLLMENT_ENROLLED,)

# A cohort accepts enrollments while it is planned or active; a completed or
# cancelled cohort is history (COH-007).
OPEN_COHORT_STATUSES = (COHORT_PLANNED, COHORT_ACTIVE)

# Never editable through the change-one-part action: identifiers, the owning
# relations that decide tenancy, and the status the transition table owns.
EDITABLE_COURSE_FIELDS = (
    "title",
    "description",
    "category",
    "level",
    "duration",
    "learning_objectives",
    "prerequisites",
    "default_fee",
)
EDITABLE_COHORT_FIELDS = ("name", "start_date", "end_date", "capacity", "note")
# A course's program is not changed through the change-one-part action: the
# grouping actions own it, so a number cannot quietly move a course between
# programs while a page is changing something else.
EDITABLE_PROGRAM_FIELDS = ("title", "description")
EDITABLE_PERIOD_FIELDS = ("name", "start_date", "end_date")
# SCH-001: a room's descriptive parts; its status has its own archive action
# and its owning organization never moves through an edit.
EDITABLE_ROOM_FIELDS = ("name", "capacity", "location")
# SCH-008's edit-one-occurrence, without a series behind it: changing one
# session only ever changes that one record, and its cohort reference never
# moves through this action.
EDITABLE_SESSION_FIELDS = ("start_at", "end_at", "note")
# ATT-008/RULE 6.5 eligibility: attendance applies to enrollment in the
# session's cohort. The two states whose students are enrolled there are
# "enrolled" (still holding a seat) and "completed" (participated); a
# withdrawn or cancelled enrollment is not enrollment in that cohort, so no
# record is made for it. The SRS never names these words as the set, so
# docs/OPEN_QUESTIONS.md asks the owner to confirm it.
ATTENDANCE_ELIGIBLE_STATUSES = (ENROLLMENT_ENROLLED, ENROLLMENT_COMPLETED)

_COURSE_STAMP_FIELDS = ("organization", "created_at", "status_changed_at",
                        "archived_at")
_COHORT_STAMP_FIELDS = ("organization", "course", "created_at",
                        "status_changed_at")
_ENROLLMENT_STAMP_FIELDS = ("organization", "cohort", "student", "created_at",
                            "enrolled_at", "status_changed_at")
_PROGRAM_STAMP_FIELDS = ("organization", "created_at")
_PERIOD_STAMP_FIELDS = ("organization", "created_at")
_SESSION_STAMP_FIELDS = ("organization", "cohort", "created_at", "room",
                         "pattern", "status", "cancelled_at")
_ROOM_STAMP_FIELDS = ("organization", "created_at", "status", "archived_at")


def _course_here(organization, course_number):
    """This center's course with that number, or None (RULE 6.1)."""
    return _record_at(Course, organization, course_number)


def _cohort_here(organization, cohort_number):
    """This center's cohort with that number, or None (RULE 6.1)."""
    return _record_at(Cohort, organization, cohort_number)


def _enrollment_here(organization, enrollment_number):
    """This center's enrollment with that number, or None (RULE 6.1)."""
    return _record_at(Enrollment, organization, enrollment_number)


def _program_here(organization, program_number):
    """This center's program with that number, or None (RULE 6.1)."""
    return _record_at(Program, organization, program_number)


def _period_here(organization, period_number):
    """This center's academic period with that number, or None (RULE 6.1)."""
    return _record_at(AcademicPeriod, organization, period_number)


def _session_here(organization, session_number):
    """This center's session with that number, or None (RULE 6.1)."""
    return _record_at(Session, organization, session_number)


def _room_here(organization, room_number):
    """This center's room with that number, or None (RULE 6.1)."""
    return _record_at(Room, organization, room_number)


def _pattern_here(organization, pattern_number):
    """This center's schedule pattern with that number, or None."""
    return _record_at(SchedulePattern, organization, pattern_number)


def _record_at(model, organization, number):
    """Resolve a database number inside one organization. A page shows pks."""
    text = str(number).strip()
    if not text.isdigit():
        return None
    return model.objects.filter(organization=organization, pk=int(text)).first()


def create_course(user, organization, title, description="", category="",
                  level="", duration="", learning_objectives="",
                  prerequisites="", default_fee=None,
                  status=COURSE_DRAFT):
    """ACAD-001/ACAD-002: make one reusable course at this center."""
    require(user, organization, "academics.course.manage")
    record = Course(
        organization=organization,
        title=str(title),
        description=str(description),
        category=str(category),
        level=str(level),
        duration=str(duration),
        learning_objectives=str(learning_objectives),
        prerequisites=str(prerequisites),
        default_fee=default_fee,
        status=str(status),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    if record.status not in COURSE_ENTRY_STATUSES:
        return None, [], ["entry"]
    record.save()
    return record, [], []


def edit_course(user, organization, course_number, field, value):
    """ACAD-001/ACAD-002: change one descriptive field of one course."""
    require(user, organization, "academics.course.manage")
    record = _course_here(organization, course_number)
    if record is None:
        return None, [], ["not_found"]
    if field not in EDITABLE_COURSE_FIELDS:
        return None, [], ["field_not_editable"]
    original = getattr(record, field)
    setattr(record, field, value)
    exclude = list(_COURSE_STAMP_FIELDS) + [
        name for name in EDITABLE_COURSE_FIELDS if name != field
    ]
    problems = []
    try:
        record.full_clean(exclude=exclude)
    except ValidationError as error:
        problems = _field_problems(error)
    if problems:
        setattr(record, field, original)
        return None, problems, []
    record.full_clean()
    record.save(update_fields=[field])
    return record, [], []


def set_course_status(user, organization, course_number, new_status):
    """ACAD-005: move a course through this center's transition table."""
    require(user, organization, "academics.course.manage")
    record = _course_here(organization, course_number)
    if record is None:
        return None, [], ["not_found"]
    target = str(new_status).strip().lower()
    if target not in COURSE_TRANSITIONS.get(record.status, ()):
        return None, [], ["transition"]
    record.status = target
    record.status_changed_at = timezone.now()
    if target == COURSE_ARCHIVED and record.archived_at is None:
        record.archived_at = record.status_changed_at
    record.full_clean()
    record.save(update_fields=["status", "status_changed_at", "archived_at"])
    return record, [], []


def add_course_module(user, organization, course_number, title,
                      description=""):
    """ACAD-003: append one module to a course's ordered list."""
    require(user, organization, "academics.course.manage")
    course = _course_here(organization, course_number)
    if course is None:
        return None, [], ["not_found"]
    last = course.modules.order_by("-position").first()
    position = 1 if last is None else last.position + 1
    module = CourseModule(
        course=course,
        title=str(title),
        description=str(description),
        position=position,
    )
    try:
        module.full_clean()
    except ValidationError as error:
        return None, _field_problems(error), []
    module.save()
    return module, [], []


def create_cohort(user, organization, name, course_number, start_date=None,
                  end_date=None, capacity=None, status=COHORT_PLANNED,
                  note="", program_number=None):
    """COH-001/COH-002: make one cohort of this center's own course.

    COH-001 lets a cohort be based on a course and optionally a program: when
    a program is named at all it must be one of this center's own.
    """
    require(user, organization, "academics.cohort.manage")
    course = _course_here(organization, course_number)
    if course is None:
        return None, [], ["course_not_found"]
    if course.status == COURSE_ARCHIVED:
        return None, [], ["course_archived"]
    program = None
    if program_number is not None and str(program_number).strip():
        program = _program_here(organization, program_number)
        if program is None:
            return None, [], ["program_not_found"]
    record = Cohort(
        organization=organization,
        course=course,
        program=program,
        name=str(name),
        start_date=start_date,
        end_date=end_date,
        capacity=capacity,
        status=str(status),
        note=str(note),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    if record.status not in COHORT_ENTRY_STATUSES:
        return None, [], ["entry"]
    record.save()
    return record, [], []


def edit_cohort(user, organization, cohort_number, field, value):
    """COH-002: change one descriptive field of one cohort.

    The course is not editable: a cohort is the delivery of one course, and
    moving it after enrollments would rewrite history (COH-004/6.3). The
    program is not either: the linking action owns it, so a page changing a
    date or a name can never quietly move a cohort between programs.
    """
    require(user, organization, "academics.cohort.manage")
    record = _cohort_here(organization, cohort_number)
    if record is None:
        return None, [], ["not_found"]
    if field not in EDITABLE_COHORT_FIELDS:
        return None, [], ["field_not_editable"]
    original = getattr(record, field)
    setattr(record, field, value)
    exclude = list(_COHORT_STAMP_FIELDS) + [
        name for name in EDITABLE_COHORT_FIELDS if name != field
    ]
    problems = []
    try:
        record.full_clean(exclude=exclude)
    except ValidationError as error:
        problems = _field_problems(error)
    if problems:
        setattr(record, field, original)
        return None, problems, []
    record.full_clean()
    record.save(update_fields=[field])
    return record, [], []


def set_cohort_status(user, organization, cohort_number, new_status):
    """COH-007: move a cohort through this center's transition table.

    COH-008: reaching completed does not issue anything by itself; no
    certificate or completion action exists in this slice.
    """
    require(user, organization, "academics.cohort.manage")
    record = _cohort_here(organization, cohort_number)
    if record is None:
        return None, [], ["not_found"]
    target = str(new_status).strip().lower()
    if target not in COHORT_TRANSITIONS.get(record.status, ()):
        return None, [], ["transition"]
    record.status = target
    record.status_changed_at = timezone.now()
    record.full_clean()
    record.save(update_fields=["status", "status_changed_at"])
    return record, [], []


def _seats_taken(cohort):
    """How many enrollments currently occupy a seat (COH-005)."""
    return Enrollment.objects.filter(
        cohort=cohort, status=ENROLLMENT_ENROLLED
    ).count()


def _capacity_blocks(organization, cohort):
    """COH-005: is this enrollment over capacity, and does the center prevent it?"""
    if cohort.capacity is None:
        return False, False
    over = _seats_taken(cohort) >= cohort.capacity
    return over, over and organization.capacity_policy != CAPACITY_WARN


def enroll_student(user, organization, cohort_number, student_number,
                   agreed_fee=None, fee_currency="", note=""):
    """COH-003/COH-004/COH-005: enroll one existing student in one cohort.

    The student must already exist at this center (STU-005: enrollment links
    the one student identity; no second student model is introduced). If no
    agreed fee is supplied, the course's default fee is used (ACAD-002); the
    currency is the center's primary currency at enrollment time (6.7).
    """
    require(user, organization, "academics.enrollment.manage")
    cohort = _cohort_here(organization, cohort_number)
    if cohort is None:
        return None, [], ["cohort_not_found"]
    student = _student_here(organization, student_number)
    if student is None:
        return None, [], ["student_not_found"]
    if student.status == STATUS_ARCHIVED:
        return None, [], ["student_archived"]
    if cohort.status not in OPEN_COHORT_STATUSES:
        return None, [], ["cohort_closed"]
    if Enrollment.objects.filter(cohort=cohort, student=student).exists():
        return None, [], ["already_enrolled"]
    fee = agreed_fee
    if fee is None:
        fee = cohort.course.default_fee
    currency = ""
    if fee is not None:
        currency = str(fee_currency).strip().upper() or organization.primary_currency
    over, blocked = _capacity_blocks(organization, cohort)
    if blocked:
        return None, [], ["capacity_full"]
    record = Enrollment(
        organization=organization,
        cohort=cohort,
        student=student,
        status=ENROLLMENT_ENROLLED,
        agreed_fee=fee,
        fee_currency=currency,
        note=str(note),
        status_changed_at=timezone.now(),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    record.save()
    if over:
        return record, [], ["capacity_warning"]
    return record, [], []


def set_enrollment_status(user, organization, enrollment_number, new_status):
    """COH-004/6.3: move an enrollment through this center's table.

    Re-enrolling a withdrawn student occupies a seat again, so the capacity
    rule is re-asked exactly as it is for a new enrollment (COH-005).
    """
    require(user, organization, "academics.enrollment.manage")
    record = _enrollment_here(organization, enrollment_number)
    if record is None:
        return None, [], ["not_found"]
    target = str(new_status).strip().lower()
    if target not in ENROLLMENT_TRANSITIONS.get(record.status, ()):
        return None, [], ["transition"]
    over = False
    if target == ENROLLMENT_ENROLLED:
        over, blocked = _capacity_blocks(organization, record.cohort)
        if blocked:
            return None, [], ["capacity_full"]
    record.status = target
    record.status_changed_at = timezone.now()
    record.full_clean()
    record.save(update_fields=["status", "status_changed_at"])
    if over:
        return record, [], ["capacity_warning"]
    return record, [], []


def create_program(user, organization, title, description=""):
    """ACAD-004: make one program of this center (an optional grouping)."""
    require(user, organization, "academics.program.manage")
    record = Program(
        organization=organization,
        title=str(title),
        description=str(description),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    record.save()
    return record, [], []


def edit_program(user, organization, program_number, field, value):
    """ACAD-004: change one part of one program of this center."""
    require(user, organization, "academics.program.manage")
    record = _program_here(organization, program_number)
    if record is None:
        return None, [], ["not_found"]
    if field not in EDITABLE_PROGRAM_FIELDS:
        return None, [], ["field_not_editable"]
    original = getattr(record, field)
    setattr(record, field, value)
    exclude = list(_PROGRAM_STAMP_FIELDS) + [
        name for name in EDITABLE_PROGRAM_FIELDS if name != field
    ]
    problems = []
    try:
        record.full_clean(exclude=exclude)
    except ValidationError as error:
        problems = _field_problems(error)
    if problems:
        setattr(record, field, original)
        return None, problems, []
    record.full_clean()
    record.save(update_fields=[field])
    return record, [], []


def set_program_course(user, organization, program_number, course_number):
    """ACAD-004: put one of this center's own courses into one program.

    A course can also be moved from one program to another. Both numbers are
    resolved inside this center, so no action here can ever point a course at
    another center's program (RULE 6.1).
    """
    require(user, organization, "academics.program.manage")
    program = _program_here(organization, program_number)
    if program is None:
        return None, [], ["not_found"]
    course = _course_here(organization, course_number)
    if course is None:
        return None, [], ["course_not_found"]
    if course.program_id == program.pk:
        return None, [], ["already_in_program"]
    original = course.program
    course.program = program
    exclude = list(_COURSE_STAMP_FIELDS) + [
        name for name in EDITABLE_COURSE_FIELDS
    ]
    problems = []
    try:
        course.full_clean(exclude=exclude)
    except ValidationError as error:
        problems = _field_problems(error)
    if problems:
        course.program = original
        return None, problems, []
    course.full_clean(exclude=list(_COURSE_STAMP_FIELDS))
    course.save(update_fields=["program"])
    return course, [], []


def clear_program_course(user, organization, course_number):
    """ACAD-004: take one of this center's courses out of its program.

    ACAD-004's other half: a center can use its courses directly, so a
    grouping a course no longer needs can be removed.
    """
    require(user, organization, "academics.program.manage")
    course = _course_here(organization, course_number)
    if course is None:
        return None, [], ["not_found"]
    if course.program_id is None:
        return None, [], ["not_in_program"]
    course.program = None
    course.full_clean(exclude=list(_COURSE_STAMP_FIELDS))
    course.save(update_fields=["program"])
    return course, [], []


def set_cohort_program(user, organization, cohort_number, program_number=None):
    """COH-001/COH-002: link one cohort to one program, or unlink it.

    A blank program number unlinks: COH-002 names the program as optional.
    The cohort record itself is what this action changes, so it keeps the
    cohort permission; the program named must be one of this center's own.
    """
    require(user, organization, "academics.cohort.manage")
    record = _cohort_here(organization, cohort_number)
    if record is None:
        return None, [], ["not_found"]
    if program_number is None or not str(program_number).strip():
        if record.program_id is None:
            return None, [], ["no_program_linked"]
        record.program = None
        record.full_clean(exclude=list(_COHORT_STAMP_FIELDS))
        record.save(update_fields=["program"])
        return record, [], []
    program = _program_here(organization, program_number)
    if program is None:
        return None, [], ["program_not_found"]
    if record.program_id == program.pk:
        return None, [], ["already_linked_program"]
    original = record.program
    record.program = program
    exclude = list(_COHORT_STAMP_FIELDS) + [
        name for name in EDITABLE_COHORT_FIELDS
    ]
    problems = []
    try:
        record.full_clean(exclude=exclude)
    except ValidationError as error:
        problems = _field_problems(error)
    if problems:
        record.program = original
        return None, problems, []
    record.full_clean(exclude=list(_COHORT_STAMP_FIELDS))
    record.save(update_fields=["program"])
    return record, [], []


def create_period(user, organization, name, start_date=None, end_date=None):
    """ACAD-006: make one academic year or period of this center."""
    require(user, organization, "academics.period.manage")
    record = AcademicPeriod(
        organization=organization,
        name=str(name),
        start_date=start_date,
        end_date=end_date,
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    record.save()
    return record, [], []


def edit_period(user, organization, period_number, field, value):
    """ACAD-006: change one part of one academic period of this center."""
    require(user, organization, "academics.period.manage")
    record = _period_here(organization, period_number)
    if record is None:
        return None, [], ["not_found"]
    if field not in EDITABLE_PERIOD_FIELDS:
        return None, [], ["field_not_editable"]
    original = getattr(record, field)
    setattr(record, field, value)
    exclude = list(_PERIOD_STAMP_FIELDS) + [
        name for name in EDITABLE_PERIOD_FIELDS if name != field
    ]
    problems = []
    try:
        record.full_clean(exclude=exclude)
        # A dates rule can only fire once both dates are checked, which the
        # field-only pass above skips, so clean the whole record here too.
        record.full_clean()
    except ValidationError as error:
        problems = _field_problems(error)
    if problems:
        setattr(record, field, original)
        return None, problems, []
    record.save(update_fields=[field])
    return record, [], []


def build_report(user, organization, period_number=None, course_number=None,
                 cohort_number=None):
    """ACAD-006/REP-003/REP-004: one center's academic report.

    Returns (report_or_none, codes). Every number is resolved inside the
    caller's own center before it filters anything, so a period, course or
    cohort number typed into the report form can never reach another center
    (RULE 6.1). The figures themselves come from reporting.py, where they are
    pure functions of the rows the report shows.
    """
    require(user, organization, "academics.report.view")
    period = None
    if period_number is not None and str(period_number).strip():
        period = _period_here(organization, period_number)
        if period is None:
            return None, ["period_not_found"]
    course = None
    if course_number is not None and str(course_number).strip():
        course = _course_here(organization, course_number)
        if course is None:
            return None, ["course_not_found"]
    cohort = None
    if cohort_number is not None and str(cohort_number).strip():
        cohort = _cohort_here(organization, cohort_number)
        if cohort is None:
            return None, ["cohort_not_found"]
    cohorts = cohort_report_rows(organization, period, course, cohort)
    enrollments = enrollment_report_rows(organization, period, course, cohort)
    # ATT-006..ATT-009: the sessions and the attendance recorded on them, over
    # exactly the filters the report asks with. Pages show these only to those
    # with access to the attendance, and the report CSV writes these rows.
    sessions = session_report_rows(organization, period, course, cohort)
    attendance = attendance_report_rows(organization, period, course, cohort)
    return {
        "period": period,
        "course": course,
        "cohort": cohort,
        "enrollments": enrollments,
        "cohorts": cohorts,
        "totals": report_totals(enrollments, cohorts),
        "sessions": sessions,
        "attendance": attendance,
        "attendance_totals": attendance_totals(attendance, sessions),
    }, []


def _conflict_problem_rows(conflicts):
    """Field-keyed messages naming the conflict type and the other session."""
    rows = []
    seen = set()
    for kind, other in conflicts:
        if kind == "room":
            row = (
                "room",
                "Room is already booked by session {} at this time.".format(
                    other.pk
                ),
            )
        else:
            row = (
                "cohort",
                "An instructor of this cohort already teaches session {} "
                "at this time.".format(other.pk),
            )
        if row not in seen:
            seen.add(row)
            rows.append(row)
    return rows


def _conflict_codes(conflicts):
    """The short business codes a page answers a prevented conflict with."""
    kinds = {kind for kind, _other in conflicts}
    codes = []
    if "room" in kinds:
        codes.append("room_conflict")
    if "instructor" in kinds:
        codes.append("instructor_conflict")
    return codes


def _room_for_booking(organization, cohort, room_number):
    """(room or None, problem code or None) for a new session or pattern.

    An explicitly selected active room wins. A blank selection falls back to
    the cohort's own default room; an archived default is never silently
    dropped, so the answer is `room_archived` rather than a roomless record
    (SCH-001/SCH-004).
    """
    text = str(room_number if room_number is not None else "").strip()
    if text:
        room = _room_here(organization, text)
        if room is None:
            return None, "room_not_found"
        if room.status == ROOM_ARCHIVED:
            return None, "room_archived"
        return room, None
    room = cohort.default_room
    if room is not None and room.status == ROOM_ARCHIVED:
        return None, "room_archived"
    return room, None


def _attach_schedule_warnings(record, conflicts, center):
    """Attach derived warnings to a saved record and return their codes."""
    record.conflict_warnings = [
        {"kind": kind, "other": other} for kind, other in conflicts
    ]
    record.cohort_dates_warning = cohort_date_warning(center, record)
    codes = []
    if any(kind == "room" for kind, _other in conflicts):
        codes.append("room_warning")
    if any(kind == "instructor" for kind, _other in conflicts):
        codes.append("instructor_warning")
    if record.cohort_dates_warning:
        codes.append("cohort_dates_warning")
    return codes


def _room_name_taken(organization, name, exclude_pk=None):
    """Is this centre's room name already used by another room? (SCH-001)"""
    rooms = Room.objects.filter(organization=organization, name=name)
    if exclude_pk is not None:
        rooms = rooms.exclude(pk=exclude_pk)
    return rooms.exists()


def create_room(user, organization, name, capacity=None, location=""):
    """SCH-001: make one bookable room of this center."""
    require(user, organization, "rooms.manage")
    record = Room(
        organization=organization,
        name=str(name).strip(),
        capacity=capacity,
        location=str(location),
    )
    if _room_name_taken(organization, record.name):
        return None, [(
            "name",
            "A room with this name already exists in this centre.",
        )], []
    try:
        record.full_clean()
    except ValidationError as error:
        return None, _field_problems(error), []
    record.save()
    return record, [], []


def edit_room(user, organization, room_number, field, value):
    """SCH-001: change one descriptive part of one room."""
    require(user, organization, "rooms.manage")
    record = _room_here(organization, room_number)
    if record is None:
        return None, [], ["not_found"]
    if field not in EDITABLE_ROOM_FIELDS:
        return None, [], ["field_not_editable"]
    if field == "name" and _room_name_taken(
        organization, str(value).strip(), exclude_pk=record.pk
    ):
        return None, [(
            "name",
            "A room with this name already exists in this centre.",
        )], []
    original = getattr(record, field)
    setattr(record, field, value)
    exclude = list(_ROOM_STAMP_FIELDS) + [
        name for name in EDITABLE_ROOM_FIELDS if name != field
    ]
    try:
        record.full_clean(exclude=exclude)
    except ValidationError as error:
        setattr(record, field, original)
        return None, _field_problems(error), []
    record.save(update_fields=[field])
    return record, [], []


def archive_room(user, organization, room_number):
    """SCH-001: archive a room; history keeps its reference (ACAD-005 rule).

    Archiving never rewrites a session, a pattern or a cohort default. A new
    booking that relies on this room is refused by the booking services.
    """
    require(user, organization, "rooms.manage")
    record = _room_here(organization, room_number)
    if record is None:
        return None, [], ["not_found"]
    if record.status == ROOM_ARCHIVED:
        return record, [], ["already_archived"]
    record.status = ROOM_ARCHIVED
    record.archived_at = timezone.now()
    record.save(update_fields=["status", "archived_at"])
    return record, [], []


def set_cohort_room(user, organization, cohort_number, room_number):
    """COH-002: set or clear one cohort's optional default room."""
    require(user, organization, "academics.cohort.manage")
    record = _cohort_here(organization, cohort_number)
    if record is None:
        return None, [], ["not_found"]
    text = str(room_number if room_number is not None else "").strip()
    if not text:
        record.default_room = None
        record.save(update_fields=["default_room"])
        return record, [], []
    room = _room_here(organization, text)
    if room is None:
        return None, [], ["room_not_found"]
    if room.status == ROOM_ARCHIVED:
        return None, [], ["room_archived"]
    record.default_room = room
    try:
        record.full_clean(exclude=list(_COHORT_STAMP_FIELDS))
    except ValidationError as error:
        return None, _field_problems(error), []
    record.save(update_fields=["default_room"])
    return record, [], []


def create_session(user, organization, cohort_number, start_at, end_at,
                   note="", room_number=None):
    """SCH-003/SCH-004: schedule one one-off class occurrence of a cohort.

    Timestamps arrive already in the caller's terms and are stored
    consistently; the page shows them in the center's own timezone (SCH-009,
    ORG-002). A room is optional: an explicit room wins, otherwise the
    cohort's default room applies. Room and instructor overlap follow the
    center's schedule_conflict_policy (SCH-005/SCH-006).
    """
    require(user, organization, "sessions.manage")
    cohort = _cohort_here(organization, cohort_number)
    if cohort is None:
        return None, [], ["cohort_not_found"]
    room, room_code = _room_for_booking(organization, cohort, room_number)
    if room_code:
        return None, [], [room_code]
    record = Session(
        organization=organization,
        cohort=cohort,
        room=room,
        start_at=start_at,
        end_at=end_at,
        note=str(note),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    conflicts = session_conflicts(
        organization,
        cohort,
        record.start_at,
        record.end_at,
        room=record.room,
    )
    if conflicts and organization.schedule_conflict_policy != SCHEDULE_WARN:
        return None, _conflict_problem_rows(conflicts), _conflict_codes(conflicts)
    record.save()
    codes = _attach_schedule_warnings(record, conflicts, organization)
    return record, [], codes


def set_session_room(user, organization, session_number, room_number):
    """SCH-004/SCH-008: change one occurrence's room, never its series."""
    require(user, organization, "sessions.manage")
    record = _session_here(organization, session_number)
    if record is None:
        return None, [], ["not_found"]
    if record.status == SESSION_CANCELLED:
        return None, [], ["session_cancelled"]
    text = str(room_number if room_number is not None else "").strip()
    if text:
        room = _room_here(organization, text)
        if room is None:
            return None, [], ["room_not_found"]
        if room.status == ROOM_ARCHIVED:
            return None, [], ["room_archived"]
    else:
        room = None
    original = record.room
    record.room = room
    conflicts = session_conflicts(
        organization,
        record.cohort,
        record.start_at,
        record.end_at,
        room=room,
        exclude_pk=record.pk,
    )
    if conflicts and organization.schedule_conflict_policy != SCHEDULE_WARN:
        record.room = original
        return None, _conflict_problem_rows(conflicts), _conflict_codes(conflicts)
    record.save(update_fields=["room"])
    codes = _attach_schedule_warnings(record, conflicts, organization)
    return record, [], codes


def cancel_session(user, organization, session_number):
    """SCH-008: cancel one occurrence; cancellation is terminal.

    Attendance already recorded stays exactly as it is and its audit history
    is untouched (D71); a second cancellation changes nothing.
    """
    require(user, organization, "sessions.manage")
    record = _session_here(organization, session_number)
    if record is None:
        return None, [], ["not_found"]
    if record.status == SESSION_CANCELLED:
        return record, [], ["already_cancelled"]
    record.status = SESSION_CANCELLED
    record.cancelled_at = timezone.now()
    record.save(update_fields=["status", "cancelled_at"])
    return record, [], ["cancelled"]


def _weekday_codes(weekdays):
    """(canonical codes, accepted) for the weekdays a pattern names."""
    if isinstance(weekdays, str):
        raw = [part.strip() for part in weekdays.split(",")]
    else:
        raw = [str(part).strip() for part in weekdays]
    raw = [code for code in raw if code]
    invalid = set(raw) - set(WEEKDAY_CODES)
    canonical = [code for code in WEEKDAY_CODES if code in set(raw)]
    if not raw or invalid or len(canonical) != len(set(raw)):
        return [], False
    return canonical, True


def create_schedule_pattern(user, organization, cohort_number, weekdays,
                            start_date, end_date, start_time, end_time,
                            room_number=None, note=""):
    """SCH-002/SCH-003: generate one recurring timetable, all or nothing.

    Every matching weekday in the inclusive date range becomes one Session.
    The local start and end times are read in the centre's own timezone
    (SCH-009/D74): an ambiguous fall-back time keeps the first fold, while a
    spring-forward wall time that does not exist is refused instead of being
    silently shifted. The whole generation is transactional; a
    schedule_conflict_policy of "prevent" writes neither the pattern nor any
    session when one occurrence overlaps (SCH-005/SCH-006).
    """
    require(user, organization, "sessions.manage")
    cohort = _cohort_here(organization, cohort_number)
    if cohort is None:
        return None, [], ["cohort_not_found"]
    codes, accepted = _weekday_codes(weekdays)
    if not accepted:
        return None, [("weekdays", "Choose at least one valid weekday.")], []
    room, room_code = _room_for_booking(organization, cohort, room_number)
    if room_code:
        return None, [], [room_code]
    pattern = SchedulePattern(
        organization=organization,
        cohort=cohort,
        weekdays=",".join(codes),
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        room=room,
        note=str(note),
    )
    try:
        pattern.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    try:
        zone = ZoneInfo(organization.display_timezone or "UTC")
    except LookupError:
        zone = ZoneInfo("UTC")
    selected = {WEEKDAY_INDEX[code] for code in codes}
    moments = []
    day = pattern.start_date
    while day <= pattern.end_date:
        if day.weekday() in selected:
            starts, ok_start = local_wall_time(zone, day, pattern.start_time)
            if not ok_start:
                return None, [(
                    "start_time",
                    "That start time does not exist on {} in this centre's "
                    "timezone.".format(day.isoformat()),
                )], []
            ends, ok_end = local_wall_time(zone, day, pattern.end_time)
            if not ok_end:
                return None, [(
                    "end_time",
                    "That end time does not exist on {} in this centre's "
                    "timezone.".format(day.isoformat()),
                )], []
            if ends <= starts:
                return None, [(
                    "end_time", "A session must end after it starts."
                )], []
            moments.append((starts, ends))
        day += timedelta(days=1)
    if not moments:
        return None, [], ["pattern_no_occurrences"]
    conflicts = []
    for starts, ends in moments:
        candidate = Session(
            organization=organization,
            cohort=cohort,
            room=room,
            start_at=starts,
            end_at=ends,
            note=str(note),
        )
        try:
            candidate.full_clean(exclude=["organization", "pattern"])
        except ValidationError as error:
            return None, _field_problems(error), []
        conflicts.extend(
            session_conflicts(
                organization, cohort, starts, ends, room=room
            )
        )
    if conflicts and organization.schedule_conflict_policy != SCHEDULE_WARN:
        return None, _conflict_problem_rows(conflicts), _conflict_codes(conflicts)
    with transaction.atomic():
        pattern.save()
        saved = []
        for starts, ends in moments:
            session = Session(
                organization=organization,
                cohort=cohort,
                room=room,
                pattern=pattern,
                start_at=starts,
                end_at=ends,
                note=str(note),
            )
            session.save()
            saved.append(session)
    pattern.conflict_warnings = [
        {"kind": kind, "other": other} for kind, other in conflicts
    ]
    outside = [
        session for session in saved
        if cohort_date_warning(organization, session)
    ]
    pattern.cohort_dates_warning = bool(outside)
    codes = []
    if any(kind == "room" for kind, _other in conflicts):
        codes.append("room_warning")
    if any(kind == "instructor" for kind, _other in conflicts):
        codes.append("instructor_warning")
    if outside:
        codes.append("cohort_dates_warning")
    return pattern, [], codes


def edit_session(user, organization, session_number, field, value):
    """SCH-008: change one part of one session, never a whole series.

    A cancelled occurrence keeps its note editable but refuses time and room
    changes. A time change is re-checked against the centre's conflict policy
    (SCH-005/SCH-006); the edited session is excluded from its own check.
    """
    require(user, organization, "sessions.manage")
    record = _session_here(organization, session_number)
    if record is None:
        return None, [], ["not_found"]
    if field not in EDITABLE_SESSION_FIELDS:
        return None, [], ["field_not_editable"]
    if record.status == SESSION_CANCELLED and field in ("start_at", "end_at"):
        return None, [], ["session_cancelled"]
    original = getattr(record, field)
    setattr(record, field, value)
    exclude = list(_SESSION_STAMP_FIELDS) + [
        name for name in EDITABLE_SESSION_FIELDS if name != field
    ]
    problems = []
    try:
        record.full_clean(exclude=exclude)
        # A dates rule can only fire once both dates are checked, which the
        # field-only pass above skips, so clean the whole record here too.
        record.full_clean()
    except ValidationError as error:
        problems = _field_problems(error)
    if problems:
        setattr(record, field, original)
        return None, problems, []
    conflicts = []
    if field in ("start_at", "end_at"):
        conflicts = session_conflicts(
            organization,
            record.cohort,
            record.start_at,
            record.end_at,
            room=record.room,
            exclude_pk=record.pk,
        )
        if conflicts and organization.schedule_conflict_policy != SCHEDULE_WARN:
            setattr(record, field, original)
            return (
                None,
                _conflict_problem_rows(conflicts),
                _conflict_codes(conflicts),
            )
    record.save(update_fields=[field])
    if field in ("start_at", "end_at"):
        codes = _attach_schedule_warnings(record, conflicts, organization)
        return record, [], codes
    return record, [], []


def _attendance_scope(user, organization):
    """Cohort ids the caller may take attendance actions on, or None (all).

    ATT-004/STAFF-004 scope an instructor to assigned sessions: the scope is
    computed server-side from the instructor's staff record and its cohort
    assignment, never from a hidden link (SEC-001). Every other role is
    unrestricted inside its own center.
    """
    member = Members.objects.filter(
        user=user, organization=organization
    ).first()
    if member is None or member.role != ROLE_INSTRUCTOR:
        return None
    return set(
        CohortInstructor.objects.filter(
            organization=organization, staff__user=user
        ).values_list("cohort_id", flat=True)
    )


def record_attendance(user, organization, session_number, student_number,
                      status):
    """ATT-001/ATT-002/ATT-003/ATT-004/ATT-008 plus RULE 6.5.

    One current state per student per session: a student already recorded
    here is refused (their change is a correction, owned by
    correct_attendance). ATT-008: only a student enrolled in the session's
    cohort takes a record here; the enrollment table is the one answer, and
    the exception it names - a documented correction workflow - is
    correct_attendance below, which works on an existing record only.
    """
    require(user, organization, "attendance.record")
    session = _session_here(organization, session_number)
    if session is None:
        return None, [], ["session_not_found"]
    if session.status == SESSION_CANCELLED:
        return None, [], ["session_cancelled"]
    scope = _attendance_scope(user, organization)
    if scope is not None and session.cohort_id not in scope:
        return None, [], ["not_assigned"]
    student = _student_here(organization, student_number)
    if student is None:
        return None, [], ["student_not_found"]
    if student.status == STATUS_ARCHIVED:
        return None, [], ["student_archived"]
    if not Enrollment.objects.filter(
        cohort=session.cohort,
        student=student,
        status__in=ATTENDANCE_ELIGIBLE_STATUSES,
    ).exists():
        return None, [], ["not_enrolled"]
    if Attendance.objects.filter(session=session, student=student).exists():
        return None, [], ["already_recorded"]
    record = Attendance(
        organization=organization,
        session=session,
        student=student,
        status=str(status),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    record.save()
    return record, [], []


def correct_attendance(user, organization, session_number, student_number,
                       new_status):
    """ATT-005/RULE 6.5: a permission-controlled, audited status change.

    Only a saved record can be corrected, and only by a role holding
    attendance.correct (owner, administrator, coordinator). The change
    updates the one current record in place - at most one state per student
    per session stays true - and writes one AuditLog row naming what moved,
    where and who moved it. An archived enrollment or a student outside the
    session's cohort can still be corrected: RULE 6.5's audit trail and the
    SRS "documented exception" clause in ATT-008 are answered by this
    audited workflow, so eligibility is not rechecked here.
    """
    require(user, organization, "attendance.correct")
    session = _session_here(organization, session_number)
    if session is None:
        return None, [], ["session_not_found"]
    scope = _attendance_scope(user, organization)
    if scope is not None and session.cohort_id not in scope:
        return None, [], ["not_assigned"]
    student = _student_here(organization, student_number)
    if student is None:
        return None, [], ["student_not_found"]
    existing = Attendance.objects.filter(
        session=session, student=student
    ).first()
    if existing is None:
        return None, [], ["not_recorded"]
    target = str(new_status).strip().lower()
    if target == existing.status:
        return None, [], ["no_change"]
    # The new status is cleaned on a copy before the saved row moves, so a
    # rejected value cannot half-change the attendance row. The pair is not
    # re-asked of the database here: the copy always shares the saved row's
    # own session and student, so a pair check would only ever answer with
    # the row itself. The model's constraint and the fresh lookup above keep
    # the pair exactly one row; what is checked here is the new status.
    candidate = Attendance(
        organization=organization,
        session=session,
        student=student,
        status=target,
    )
    try:
        candidate.full_clean(
            exclude=["organization", "session", "student"],
            validate_unique=False,
            validate_constraints=False,
        )
    except ValidationError as error:
        return None, _field_problems(error), []
    previous = existing.status
    with transaction.atomic():
        # RULE 6.5: the corrected row and its audit row commit together, so a
        # failed audit save can never leave a changed status without history.
        existing.status = target
        existing.status_changed_at = timezone.now()
        existing.save(update_fields=["status", "status_changed_at"])
        log = AuditLog(
            organization=organization,
            session=session,
            student=student,
            actor=user,
            action=AUDIT_ATTENDANCE_CORRECTED,
            previous_status=previous,
            new_status=target,
        )
        log.full_clean(exclude=["organization"])
        log.save()
    return existing, [], []
