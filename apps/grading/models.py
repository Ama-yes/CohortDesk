"""Assessment definitions, raw results, and grade-change audit for one center.

Every instance hangs directly from one Organization (RULE 6.1: one center, and
every read and write is scoped to it), and the relations this slice names -
the cohort an assessment belongs to, the enrollment a result is anchored on,
the recorder of a score - are resolved inside the center by the model's own
hooks and by the services, so a number typed by a browser can never name
another center's assessment, enrollment or result (SEC-004).

The score rule (§13 and the grading pilot of §15 item 6): every score is a
DecimalField of two places, never a float, and a missing score is never a
NULL-zero: a student with no result row is missing, a result row with an
empty score is pending, and only a recorded Decimal is a score. No pass
mark, band, scale, aggregate, or final grade is ever stored or derived here
(docs/STATUS.md's deferred list).

The traceability rules (RULE 6.5 and §6.8) keep the history: the relations a
grade change depends on use PROTECT FKs, so deleting an assessment, a result,
an enrollment or an audit row can never erase the history it wrote, and
nothing posted is silently deleted or edited.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.organizations.models import Organization
from apps.students.models import Student
from apps.academics.models import Cohort, CourseModule, Enrollment
from apps.accounts.models import CohortUser

from .statuses import (
    GRADE_AUDIT_ACTION_CHOICES,
    GRADE_CREATED,
    GRADE_RECORDED,
    GRADE_UPDATED,
)

# Assessment scores keep two decimal places: fixed-precision decimals, never
# floats (§13), with room for a cohort's own maximum above 999.99.
SCORE_DIGITS = 6
SCORE_PLACES = 2


class Assessment(models.Model):
    """One assessment of one cohort (GRD-001/GRD-002/GRD-004).

    The SRS's assessment row in §7: a title, a type, a date, and a maximum
    score, attached to one cohort of this center. The type is a bounded,
    required free text (docs/OPEN_QUESTIONS.md: the SRS names no vocabulary
    for it, so none is invented). The optional module names one module of the
    cohort's own course (the same-course module the pilot allows); the
    optional weight is metadata only - AC-007 asks for weighted assessments,
    and this phase records it on the definition and never aggregates it
    (docs/DECISIONS.md and docs/OPEN_QUESTIONS.md).
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="assessments",
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.PROTECT,
        related_name="assessments",
    )
    module = models.ForeignKey(
        CourseModule,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assessments",
    )
    title = models.CharField(max_length=150)
    assessment_type = models.CharField(max_length=40)
    date = models.DateField()
    max_score = models.DecimalField(
        max_digits=SCORE_DIGITS,
        decimal_places=SCORE_PLACES,
    )
    weight = models.DecimalField(
        max_digits=4,
        decimal_places=SCORE_PLACES,
        null=True,
        blank=True,
    )
    instructions = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        CohortUser,
        on_delete=models.PROTECT,
        related_name="grading_assessments",
    )
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """The same-center invariants (RULE 6.1) and the score shape."""
        excluded = set(exclude or ())
        if "cohort" not in excluded and self.cohort_id is not None:
            if self.cohort.organization_id != self.organization_id:
                raise ValidationError(
                    {"cohort": "That cohort belongs to another center."}
                )
        if "module" not in excluded and self.module_id is not None:
            if self.module.course_id != self.cohort.course_id:
                raise ValidationError(
                    {
                        "module":
                            "That module belongs to another course."
                    }
                )
        if "title" not in excluded:
            self.title = str(self.title).strip()
            if not self.title:
                raise ValidationError(
                    {"title": "An assessment must have a title."}
                )
        if "assessment_type" not in excluded:
            self.assessment_type = str(self.assessment_type).strip()
            if not self.assessment_type:
                raise ValidationError(
                    {
                        "assessment_type":
                            "An assessment must say its own type."
                    }
                )
        if "instructions" not in excluded:
            self.instructions = str(self.instructions).strip()
        if "max_score" not in excluded and self.max_score is not None:
            if self.max_score <= 0:
                raise ValidationError(
                    {
                        "max_score":
                            "An assessment maximum must be above zero."
                    }
                )
        if "weight" not in excluded and self.weight is not None:
            if self.weight < 0:
                raise ValidationError(
                    {"weight": "A weight cannot be negative."}
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(max_score__gt=0),
                name="grading_assessment_max_score_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(weight__isnull=True)
                | models.Q(weight__gte=0),
                name="grading_assessment_weight_not_negative",
            ),
        ]


class Result(models.Model):
    """One raw score of one enrollment on one assessment (GRD-003/GRD-006).

    §7's raw result row: one score per student per assessment, anchored on
    the enrollment (the participation the score belongs to) and naming its
    student, never a derived grade and never a mark-out-of written as free
    text. The score is nullable on purpose: an empty score is a pending
    record, not a zero, and it is never read as one (the missing-versus-
    pending rule above). One result per assessment per enrollment: the DB
    refuses a duplicate as does the service's earlier refusal.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="results",
    )
    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.PROTECT,
        related_name="results",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.PROTECT,
        related_name="results",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="results",
    )
    score = models.DecimalField(
        max_digits=SCORE_DIGITS,
        decimal_places=SCORE_PLACES,
        null=True,
        blank=True,
    )
    recorded_by = models.ForeignKey(
        CohortUser,
        on_delete=models.PROTECT,
        related_name="graded_results",
    )
    recorded_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)

    def clean_fields(self, exclude=None):
        """The same-center invariants (RULE 6.1) and the score bounds."""
        excluded = set(exclude or ())
        if "assessment" not in excluded and self.assessment_id is not None:
            if self.assessment.organization_id != self.organization_id:
                raise ValidationError(
                    {"assessment": "That assessment belongs to another center."}
                )
        if "enrollment" not in excluded and self.enrollment_id is not None:
            if (
                self.enrollment.organization_id != self.organization_id
                or self.enrollment.cohort_id != self.assessment.cohort_id
            ):
                raise ValidationError(
                    {
                        "enrollment":
                            "That enrollment is not in the assessment's cohort."
                    }
                )
        if "student" not in excluded and self.student_id is not None:
            if (
                self.student.organization_id != self.organization_id
                or self.enrollment.student_id != self.student_id
            ):
                raise ValidationError(
                    {
                        "student":
                            "That student is not the enrollment's student."
                    }
                )
        if "score" not in excluded and self.score is not None:
            if self.score < 0:
                raise ValidationError(
                    {"score": "A result score cannot be negative."}
                )
            if (
                self.assessment_id is not None
                and self.score > self.assessment.max_score
            ):
                raise ValidationError(
                    {
                        "score":
                            "That score is above the assessment's maximum."
                    }
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "enrollment"],
                name="grading_result_one_per_assessment_and_enrollment",
            ),
            models.CheckConstraint(
                condition=models.Q(score__isnull=True)
                | models.Q(score__gte=0),
                name="grading_result_score_not_negative",
            ),
        ]


class GradeAuditEvent(models.Model):
    """One audited grade action (GRD-006's history and RULE 6.5).

    What happened, where and who did it: the action, this center's own
    records it happened to, the person who did it, and - for a score
    change - the score before and after, both Decimals or an honest
    absence, never a float. The rows are immutable by convention: no page
    and no service edits or deletes one, and the PROTECT FKs keep the
    history even when a graded record is deleted. The DB itself refuses a
    row whose scores do not fit its action (the CheckConstraint below),
    the way the finance audit rows' kinds are pinned.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="grade_audit_events",
    )
    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.PROTECT,
        related_name="audit_events",
    )
    result = models.ForeignKey(
        Result,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        CohortUser,
        on_delete=models.PROTECT,
        related_name="grade_audit_events",
    )
    action = models.CharField(
        max_length=32,
        choices=GRADE_AUDIT_ACTION_CHOICES,
    )
    at = models.DateTimeField(default=timezone.now)
    previous_score = models.DecimalField(
        max_digits=SCORE_DIGITS,
        decimal_places=SCORE_PLACES,
        null=True,
        blank=True,
    )
    new_score = models.DecimalField(
        max_digits=SCORE_DIGITS,
        decimal_places=SCORE_PLACES,
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=200, blank=True)

    def clean_fields(self, exclude=None):
        """The same-center invariants and the shape of an audited row."""
        excluded = set(exclude or ())
        if "assessment" not in excluded and self.assessment_id is not None:
            if self.assessment.organization_id != self.organization_id:
                raise ValidationError(
                    {
                        "assessment":
                            "That assessment belongs to another center."
                    }
                )
        if "result" not in excluded and self.result_id is not None:
            if (
                self.result.organization_id != self.organization_id
                or self.result.assessment_id != self.assessment_id
            ):
                raise ValidationError(
                    {
                        "result":
                            "That result is not a result of that assessment."
                    }
                )
        if "enrollment" not in excluded and self.enrollment_id is not None:
            if (
                self.enrollment.organization_id
                != self.organization_id
                or self.enrollment.cohort_id
                != self.assessment.cohort_id
            ):
                raise ValidationError(
                    {
                        "enrollment":
                            "That enrollment is not in that assessment's "
                            "cohort."
                    }
                )
            if self.action == GRADE_CREATED:
                raise ValidationError(
                    {
                        "enrollment":
                            "An assessment defined names no enrollment."
                    }
                )
        if "student" not in excluded and self.student_id is not None:
            if self.enrollment_id is not None and (
                self.enrollment.student_id != self.student_id
            ):
                raise ValidationError(
                    {
                        "student":
                            "That student is not the enrollment's student."
                    }
                )
            if self.action == GRADE_CREATED:
                raise ValidationError(
                    {
                        "student":
                            "An assessment defined names no student."
                    }
                )
        if self.action != GRADE_CREATED:
            if (
                "enrollment" not in excluded
                and self.enrollment_id is None
            ):
                raise ValidationError(
                    {
                        "enrollment":
                            "An audited score action must name its "
                            "enrollment."
                    }
                )
        if "reason" not in excluded:
            self.reason = str(self.reason).strip()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    action=GRADE_CREATED,
                    previous_score__isnull=True,
                    new_score__isnull=True,
                )
                | models.Q(
                    action=GRADE_RECORDED,
                    previous_score__isnull=True,
                )
                | models.Q(
                    action=GRADE_UPDATED,
                    new_score__isnull=False,
                ),
                name="grading_audit_event_scores_match_action",
            ),
        ]
