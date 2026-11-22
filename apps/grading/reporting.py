"""Computed grading reports for one center (REP-003/REP-005/REP-006/REP-007).

Only raw figures are computed: the recorded scores, the pending records and
the missing results each assessment has, read from this center's own records
and from nothing else (SEC-002/RULE 6.1). No aggregate, weighted figure,
pass rate or final grade is computed anywhere in this phase (docs/STATUS.md's
deferred list): the report shows the raw results as they are, with an honest
mark for what is missing and pending, and the population it counts is the
open enrollments of the assessment's own cohort - the same eligibility rule
the services record under, so a page never claims a score nothing could make.
"""

from apps.grading.models import Assessment, Result
from apps.academics.models import Enrollment
from apps.grading.services import GRADE_ELIGIBLE_STATUSES
from apps.students.statuses import STATUS_ARCHIVED


def result_counts(assessment):
    """{"recorded": n, "pending": n, "missing": n} for one assessment.

    A student with no result row is missing; a result row with an empty score
    is pending and never counted as a zero (the missing-versus-pending rule).
    Results on enrollments that are no longer open (withdrawn or cancelled)
    are history the report does not count: the report counts what could be
    graded now.
    """
    open_enrollments = set(
        Enrollment.objects.filter(
            cohort=assessment.cohort,
            status__in=GRADE_ELIGIBLE_STATUSES,
        )
        .exclude(student__status=STATUS_ARCHIVED)
        .values_list("pk", flat=True)
    )
    recorded = 0
    pending = 0
    counted = set()
    for enrollment_id, score in Result.objects.filter(
        assessment=assessment
    ).values_list("enrollment_id", "score"):
        if enrollment_id not in open_enrollments:
            continue
        counted.add(enrollment_id)
        if score is None:
            pending += 1
        else:
            recorded += 1
    return {
        "recorded": recorded,
        "pending": pending,
        "missing": len(open_enrollments) - len(counted),
    }


def grading_rows(center, scope=None):
    """This center's assessments, each with its counts, in one's own order.

    scope (cohort ids) narrows the rows to the cohorts a caller may act on:
    the report answers the same rows the pages may show, never more (SEC-001:
    a hidden link is not a scope).
    """
    records = Assessment.objects.filter(organization=center)
    if scope is not None:
        records = records.filter(cohort_id__in=scope)
    return [
        {
            "assessment": assessment,
            "counts": result_counts(assessment),
        }
        for assessment in records.order_by("cohort", "pk")
    ]


def open_enrollments(cohort):
    """The enrollments a cohort's assessment may be graded on (the rule).

    The report's and the page's population is this center's own open
    enrollment of the cohort: enrolled or completed, never withdrawn or
    cancelled, and the student behind it never archived (the services refuse
    those the same way, 6.3: what a page can claim, a service can make).
    """
    return [
        enrollment
        for enrollment in Enrollment.objects.filter(
            cohort=cohort, status__in=GRADE_ELIGIBLE_STATUSES
        ).select_related("student")
        if enrollment.student.status != STATUS_ARCHIVED
    ]
