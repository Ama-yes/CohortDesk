"""The business actions of one center's assessments, results and audit.

Every action re-asks the central authorization layer (apps.organizations.services.require)
before anything is read or written, and resolves every number through the caller's
own organization first, so a number typed or posted by a browser can never name
another center's assessment, enrollment or result (SEC-004/RULE 6.1).

Contract, the same shape the other services use: (record_or_none, problems, notes)
- record: the created/updated record, or None when nothing was saved;
- problems: list of (field, english validator message) rows from full_clean;
- notes: short business codes the page answers with its own words.

Instructor scope (GRD-004's mirror of ATT-004/STAFF-004): an instructor may act
only on assessments of the cohorts assigned to their own staff record, computed
server-side from apps.staff.models' CohortInstructor, never from a hidden link.
Every other permitted role is unrestricted inside its own center.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.organizations.services import require
from apps.organizations.models import Members
from apps.organizations.roles import ROLE_INSTRUCTOR

from apps.staff.models import CohortInstructor

from apps.academics.models import CourseModule
from apps.academics.services import _cohort_here, _enrollment_here
from apps.academics.statuses import (
    ENROLLMENT_COMPLETED,
    ENROLLMENT_ENROLLED,
    COHORT_CANCELLED,
)
from apps.students.statuses import STATUS_ARCHIVED

from apps.grading.models import Assessment, GradeAuditEvent, Result
from apps.grading.statuses import GRADE_CREATED, GRADE_RECORDED, GRADE_UPDATED

# The enrollment statuses a raw score may be recorded and updated on: the same
# enrolled and completed states the attendance register allows. A withdrawn or
# cancelled enrollment is closed; an archived student is closed too. Nothing
# already recorded is ever deleted when a status changes (docs/DECISIONS.md).
GRADE_ELIGIBLE_STATUSES = (ENROLLMENT_ENROLLED, ENROLLMENT_COMPLETED)

# The assessment fields a person may change after the definition exists.
EDITABLE_ASSESSMENT_FIELDS = (
    "title",
    "assessment_type",
    "date",
    "max_score",
    "instructions",
    "weight",
)


def _pk_of(number):
    """A typed number as a pk, or None when it does not look like one."""
    text = str(number).strip()
    return int(text) if text.isdigit() else None


def _assessment_here(organization, number):
    """This center's assessment with that number, or None (RULE 6.1)."""
    pk = _pk_of(number)
    if pk is None:
        return None
    return Assessment.objects.filter(organization=organization, pk=pk).first()


def _result_here(organization, number):
    """This center's result with that number, or None (RULE 6.1)."""
    pk = _pk_of(number)
    if pk is None:
        return None
    return Result.objects.filter(organization=organization, pk=pk).first()


def _grade_scope(user, organization):
    """Cohort ids the caller may act on for grading, or None (all).

    Mirrors the attendance scope exactly: only an instructor is narrowed, to
    the cohorts their own staff record is assigned to; every other role is
    unrestricted inside its own center.
    """
    member = Members.objects.filter(user=user, organization=organization).first()
    if member is None or member.role != ROLE_INSTRUCTOR:
        return None
    return set(
        CohortInstructor.objects.filter(
            organization=organization, staff__user=user
        ).values_list("cohort_id", flat=True)
    )


def _audit_row(organization, action, actor, **records):
    """One audit row of one audited grade action (RULE 6.5 and §15 item 6).

    What happened, where and who did it: the action, this centre's own records
    it happened to, the person who did it, and for a score change the score
    before and after. Saving it with the action inside the same atomic block is
    what keeps the trail traceable.
    """
    row = GradeAuditEvent(
        organization=organization,
        action=action,
        actor=actor,
        assessment=records.get("assessment"),
        result=records.get("result"),
        enrollment=records.get("enrollment"),
        student=records.get("student"),
        previous_score=records.get("previous_score"),
        new_score=records.get("new_score"),
        reason=records.get("reason", ""),
    )
    row.full_clean(exclude=["organization"])
    row.save()
    return row


def create_assessment(user, organization, cohort_number, title, assessment_type,
                      date, max_score, module_number="", weight=None,
                      instructions=""):
    """GRD-001/GRD-002: define one assessment of one cohort of this center."""
    require(user, organization, "grading.assessment.manage")
    cohort = _cohort_here(organization, cohort_number)
    if cohort is None:
        return None, [], ["cohort_not_found"]
    if cohort.status == COHORT_CANCELLED:
        return None, [], ["cohort_cancelled"]
    module = None
    if str(module_number).strip():
        module = CourseModule.objects.filter(
            course=cohort.course, pk=_pk_of(module_number)
        ).first() if _pk_of(module_number) is not None else None
        if module is None:
            return None, [], ["module_not_found"]
    record = Assessment(
        organization=organization,
        cohort=cohort,
        module=module,
        title=title,
        assessment_type=assessment_type,
        date=date,
        max_score=max_score,
        weight=weight,
        instructions=instructions,
        created_by=user,
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as problems:
        return None, list(problems.error_dict.items()), []
    with transaction.atomic():
        record.save()
        _audit_row(
            organization,
            GRADE_CREATED,
            actor=user,
            assessment=record,
        )
    return record, [], []


def edit_assessment(user, organization, assessment_number, field, value):
    """GRD-002: change one editable field of this center's assessment.

    The stamps, the cohort, the module and the creator stay as they were.
    Lowering the maximum below an already recorded score is refused (6.3:
    nothing half-taken, no recorded score is ever orphaned). Editing a
    definition is not a grade change: the three audited actions are the only
    grade actions, so no audit row is written for an edit (docs/DECISIONS.md).
    """
    require(user, organization, "grading.assessment.manage")
    record = _assessment_here(organization, assessment_number)
    if record is None:
        return None, [], ["assessment_not_found"]
    scope = _grade_scope(user, organization)
    if scope is not None and record.cohort_id not in scope:
        return None, [], ["not_assigned"]
    if field not in EDITABLE_ASSESSMENT_FIELDS:
        return None, [], ["field_not_editable"]
    original = getattr(record, field)
    if field == "max_score" and value is not None:
        above = Result.objects.filter(
            assessment=record, score__gt=value
        ).exists()
        if above:
            return None, [], ["max_below_results"]
    setattr(record, field, value)
    try:
        record.full_clean(exclude=["organization", "created_at", "created_by"])
    except ValidationError as problems:
        return None, list(problems.error_dict.items()), []
    if value == original:
        return record, [], ["unchanged"]
    record.save(update_fields=[field])
    return record, [], []


def record_result(user, organization, assessment_number, enrollment_number,
                  score=None, reason=""):
    """GRD-003/GRD-005: record one raw score for one enrollment.

    Anchored on the enrollment (the participation the score belongs to): the
    assessment, the enrollment and the student must all be this center's own
    and fit together, and the enrollment must still be open. An empty score is
    allowed and stays pending - never a zero.
    """
    require(user, organization, "grading.result.record")
    assessment = _assessment_here(organization, assessment_number)
    if assessment is None:
        return None, [], ["assessment_not_found"]
    scope = _grade_scope(user, organization)
    if scope is not None and assessment.cohort_id not in scope:
        return None, [], ["not_assigned"]
    enrollment = _enrollment_here(organization, enrollment_number)
    if enrollment is None:
        return None, [], ["enrollment_not_found"]
    if enrollment.cohort_id != assessment.cohort_id:
        return None, [], ["cohort_mismatch"]
    if enrollment.student.status == STATUS_ARCHIVED:
        return None, [], ["student_archived"]
    if enrollment.status not in GRADE_ELIGIBLE_STATUSES:
        return None, [], ["enrollment_closed"]
    if score is not None and score > assessment.max_score:
        return None, [], ["score_above_max"]
    if Result.objects.filter(assessment=assessment, enrollment=enrollment).exists():
        return None, [], ["already_recorded"]
    record = Result(
        organization=organization,
        assessment=assessment,
        enrollment=enrollment,
        student=enrollment.student,
        score=score,
        recorded_by=user,
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as problems:
        return None, list(problems.error_dict.items()), []
    with transaction.atomic():
        record.save()
        _audit_row(
            organization,
            GRADE_RECORDED,
            actor=user,
            assessment=assessment,
            result=record,
            enrollment=enrollment,
            student=enrollment.student,
            new_score=score,
            reason=reason,
        )
    return record, [], []


def update_result(user, organization, result_number, score, reason=""):
    """GRD-006: change a recorded score, and audit the previous one."""
    require(user, organization, "grading.result.record")
    record = _result_here(organization, result_number)
    if record is None:
        return None, [], ["result_not_found"]
    scope = _grade_scope(user, organization)
    if scope is not None and record.assessment.cohort_id not in scope:
        return None, [], ["not_assigned"]
    if score is None:
        return None, [], ["score_required"]
    if score == record.score:
        return record, [], ["unchanged"]
    if score > record.assessment.max_score:
        return None, [], ["score_above_max"]
    original = record.score
    previous_score = record.score
    record.score = score
    record.updated_at = timezone.now()
    try:
        record.full_clean(
            exclude=["organization", "assessment", "enrollment", "student",
                     "recorded_by", "recorded_at"]
        )
    except ValidationError as problems:
        return None, list(problems.error_dict.items()), []
    with transaction.atomic():
        record.save(update_fields=["score", "updated_at"])
        _audit_row(
            organization,
            GRADE_UPDATED,
            actor=user,
            assessment=record.assessment,
            result=record,
            enrollment=record.enrollment,
            student=record.student,
            previous_score=original,
            new_score=score,
            reason=reason,
        )
    return record, [], []
