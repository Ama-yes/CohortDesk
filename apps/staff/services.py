"""The business actions of one center's staff records and assignments.

Every action re-asks the central authorization layer before anything is read
or written, and resolves each staff number, course number and cohort number
through the caller's own organization first, so a browser-supplied value can
never reach another center's record (SEC-004/RULE 6.1).

Contract, the same shape the student and academic actions use:
(record_or_none, problems, notes).
- record: the created/updated record, or None when nothing was saved;
- problems: list of (field, english validator message) rows from full_clean;
- notes: short business codes the page answers with its own words.

Employment status is never free text. STAFF_TRANSITIONS is the only place it
moves. An instructor may be assigned only while active; removing an assignment
is always allowed so a closed cohort or a departing instructor can be cleaned
up without editing history. Assignments are separate records, so the same
instructor may teach several courses and cohorts, and the database refuses a
duplicate of the same pair.
"""
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.academics.services import _cohort_here, _course_here
from apps.organizations.models import Members
from apps.organizations.services import require
from apps.students.services import _field_problems

from .models import CohortInstructor, CourseInstructor, Staff
from .statuses import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_INACTIVE,
    STATUS_ON_LEAVE,
)

# STAFF-001/STAFF-002: active staff may be assigned lessons. On leave and
# inactive staff keep their record and history; archived is terminal.
STAFF_TRANSITIONS = {
    STATUS_ACTIVE: (STATUS_ON_LEAVE, STATUS_INACTIVE, STATUS_ARCHIVED),
    STATUS_ON_LEAVE: (STATUS_ACTIVE, STATUS_INACTIVE, STATUS_ARCHIVED),
    STATUS_INACTIVE: (STATUS_ACTIVE, STATUS_ARCHIVED),
    STATUS_ARCHIVED: (),
}
STAFF_ENTRY_STATUSES = (STATUS_ACTIVE, STATUS_ON_LEAVE)

# The descriptive fields the change-one-part action may edit. The staff number
# is the identifier of a record; status belongs to the transition table; the
# organization and the timestamps are not the page's to change.
EDITABLE_STAFF_FIELDS = (
    "name",
    "email",
    "phone",
    "job_title",
    "specializations",
    "availability",
    "qualifications",
)

_STAFF_STAMP_FIELDS = (
    "organization",
    "created_at",
    "status_changed_at",
    "archived_at",
)


def _staff_here(organization, staff_number):
    """This center's staff record with that number, or None (RULE 6.1)."""
    normalized = str(staff_number).strip().upper()
    if not normalized:
        return None
    return Staff.objects.filter(
        organization=organization, staff_number=normalized
    ).first()


def create_staff(user, organization, staff_number, name, email="", phone="",
                 job_title="", specializations="", employment_status=STATUS_ACTIVE,
                 availability="", qualifications=""):
    """STAFF-001/STAFF-002: make one staff record at this center."""
    require(user, organization, "staff.manage")
    if _staff_here(organization, staff_number) is not None:
        return None, [], ["number_used"]
    record = Staff(
        organization=organization,
        staff_number=str(staff_number),
        name=str(name),
        email=str(email),
        phone=str(phone),
        job_title=str(job_title),
        specializations=str(specializations),
        employment_status=str(employment_status),
        availability=str(availability),
        qualifications=str(qualifications),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    if record.employment_status not in STAFF_ENTRY_STATUSES:
        return None, [], ["entry"]
    record.status_changed_at = timezone.now()
    record.save()
    return record, [], []


def edit_staff(user, organization, staff_number, field, value):
    """STAFF-001/STAFF-002: change one descriptive field of one staff record."""
    require(user, organization, "staff.manage")
    record = _staff_here(organization, staff_number)
    if record is None:
        return None, [], ["not_found"]
    if field not in EDITABLE_STAFF_FIELDS:
        return None, [], ["field_not_editable"]
    original = getattr(record, field)
    setattr(record, field, value)
    exclude = list(_STAFF_STAMP_FIELDS) + [
        name for name in EDITABLE_STAFF_FIELDS if name != field
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


def set_staff_status(user, organization, staff_number, new_status):
    """STAFF-001: move a staff record through this center's transition table."""
    require(user, organization, "staff.manage")
    record = _staff_here(organization, staff_number)
    if record is None:
        return None, [], ["not_found"]
    target = str(new_status).strip().lower()
    if target not in STAFF_TRANSITIONS.get(record.employment_status, ()):
        return None, [], ["transition"]
    record.employment_status = target
    record.status_changed_at = timezone.now()
    if target == STATUS_ARCHIVED and record.archived_at is None:
        record.archived_at = record.status_changed_at
    record.full_clean()
    record.save(update_fields=["employment_status", "status_changed_at",
                               "archived_at"])
    return record, [], []


def _assign(user, organization, staff_number, target, assignment_model, field,
            missing_code):
    """Create one assignment, scoped to this center (STAFF-003)."""
    require(user, organization, "staff.assign")
    staff = _staff_here(organization, staff_number)
    if staff is None:
        return None, [], ["staff_not_found"]
    if target is None:
        return None, [], [missing_code]
    if staff.employment_status != STATUS_ACTIVE:
        return None, [], ["staff_not_active"]
    if assignment_model.objects.filter(
        staff=staff, **{field: target}
    ).exists():
        return None, [], ["already_assigned"]
    record = assignment_model(
        organization=organization,
        staff=staff,
        **{field: target},
    )
    try:
        record.full_clean()
    except ValidationError as error:
        return None, _field_problems(error), []
    record.save()
    return record, [], []


def _unassign(user, organization, staff_number, target, assignment_model, field,
              missing_code):
    """Remove one assignment, scoped to this center (STAFF-003)."""
    require(user, organization, "staff.assign")
    staff = _staff_here(organization, staff_number)
    if staff is None:
        return None, [], ["staff_not_found"]
    if target is None:
        return None, [], [missing_code]
    record = assignment_model.objects.filter(
        staff=staff, **{field: target}
    ).first()
    if record is None:
        return None, [], ["not_assigned"]
    record.delete()
    return record, [], []


def assign_staff_to_course(user, organization, staff_number, course_number):
    """STAFF-003: assign one active instructor to one course of this center."""
    return _assign(
        user,
        organization,
        staff_number,
        _course_here(organization, course_number),
        CourseInstructor,
        "course",
        "course_not_found",
    )


def remove_staff_from_course(user, organization, staff_number, course_number):
    """STAFF-003: remove one instructor from one course of this center."""
    return _unassign(
        user,
        organization,
        staff_number,
        _course_here(organization, course_number),
        CourseInstructor,
        "course",
        "course_not_found",
    )


def assign_staff_to_cohort(user, organization, staff_number, cohort_number):
    """STAFF-003: assign one active instructor to one cohort of this center."""
    return _assign(
        user,
        organization,
        staff_number,
        _cohort_here(organization, cohort_number),
        CohortInstructor,
        "cohort",
        "cohort_not_found",
    )


# -- the explicit Staff-record <-> account link (STAFF-006) ----------------
# One explicit action per link, scoped to this center. Linking grants no
# permission and changes no membership role: authorization for a linked
# person is always that member's own Members.role. The account must already
# be an active member of THIS center, and each account may be linked to at
# most one staff record per center; the same account may be linked again at
# another center (each center owns its own records). Archived staff can be
# unlinked but never newly linked, and the link never changes employment
# status, assignments, or any other staff data (docs/DECISIONS.md).

_LINK_STATUSES = (STATUS_ACTIVE, STATUS_ON_LEAVE)


def link_staff_account(user, organization, staff_number, account):
    """STAFF-006: link this center's staff record to one account here."""
    require(user, organization, "staff.link")
    staff = _staff_here(organization, staff_number)
    if staff is None:
        return None, [], ["staff_not_found"]
    if account is None or not Members.objects.filter(
        organization=organization,
        user=account,
        status="active",
    ).exists():
        return None, [], ["account_not_found"]
    if staff.user_id is not None and staff.user_id == account.pk:
        return None, [], ["already_linked"]
    if Staff.objects.filter(organization=organization, user=account).exists():
        return None, [], ["account_taken"]
    if staff.employment_status not in _LINK_STATUSES:
        return None, [], ["staff_not_linkable"]
    staff.user = account
    try:
        staff.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    staff.save()
    return staff, [], [staff.employment_status]


def unlink_staff_account(user, organization, staff_number):
    """STAFF-006: remove one staff record's account link, any status."""
    require(user, organization, "staff.link")
    staff = _staff_here(organization, staff_number)
    if staff is None:
        return None, [], ["staff_not_found"]
    if staff.user_id is None:
        return None, [], ["not_linked"]
    account = staff.user
    if not Members.objects.filter(
        organization=organization,
        user=account,
        status="active",
    ).exists():
        # The account left this center; the link stays until cleaned up.
        return None, [], ["account_not_found"]
    staff.user = None
    staff.save()
    return staff, [], ["unlinked"]


def remove_staff_from_cohort(user, organization, staff_number, cohort_number):
    """STAFF-003: remove one instructor from one cohort of this center."""
    return _unassign(
        user,
        organization,
        staff_number,
        _cohort_here(organization, cohort_number),
        CohortInstructor,
        "cohort",
        "cohort_not_found",
    )
