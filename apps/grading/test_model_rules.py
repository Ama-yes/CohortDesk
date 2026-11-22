"""Assessment, result and audit rules as this slice's services answer them.

The rules under test are GRD-001..GRD-007 and RULE 6.5 as this product's
services implement them: assessments attached to one cohort of this center
and its own optional module (GRD-001/GRD-002), one raw score per student
per assessment anchored on the enrollment (GRD-003), the honest missing
versus pending rule with a score that is a Decimal and never a float (§13),
the enrollment and student eligibility lines the services refuse on, a
maximum never lowered below a recorded score, corrections that audit the
score they replace (GRD-006/RULE 6.5), instructor scope computed
server-side from the staff record (GRD-004's mirror of ATT-004), PROTECT
FKs that keep a grade change traceable, and the three audited actions being
the only grade actions (edits not audited, docs/DECISIONS.md). Cross-tenant
proof through the URL layer lives in apps/grading/test_access.py.

Every action here is called the way a page calls it (the service function,
not the model API), because the service layer is the one place the rules
live.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from apps.academics.models import Cohort, CourseModule, Enrollment
from apps.academics.services import (
    add_course_module,
    create_cohort,
    enroll_student,
    set_cohort_status,
    set_enrollment_status,
)
from apps.academics.statuses import (
    COHORT_CANCELLED,
    ENROLLMENT_COMPLETED,
    ENROLLMENT_ENROLLED,
    ENROLLMENT_WITHDRAWN,
)
from apps.organizations.models import Members
from apps.organizations.permissions import role_has_permission
from apps.staff.models import CohortInstructor, Staff
from apps.students.models import Student
from apps.students.statuses import STATUS_ARCHIVED
from apps.academics.tests import CenterFixture

from .models import Assessment, GradeAuditEvent, Result
from .reporting import open_enrollments, result_counts
from .services import (
    EDITABLE_ASSESSMENT_FIELDS,
    GRADE_ELIGIBLE_STATUSES,
    create_assessment,
    edit_assessment,
    record_result,
    update_result,
)
from .statuses import GRADE_AUDIT_ACTIONS, GRADE_CREATED, GRADE_RECORDED, GRADE_UPDATED


class GradingFixture(CenterFixture):
    """Two centers, an enrolled learner, and one assessment with a score."""

    def make_assessment(self, user=None, center=None, cohort=None, **kwargs):
        record, problems, notes = create_assessment(
            user or self.owner,
            center or self.alpha,
            (cohort or self.cohort).pk,
            kwargs.get("title", "Autumn practical"),
            kwargs.get("assessment_type", "practical"),
            kwargs.get("date", date(2026, 10, 5)),
            kwargs.get("max_score", 20),
            module_number=kwargs.get("module_number", ""),
            weight=kwargs.get("weight", None),
            instructions=kwargs.get("instructions", ""),
        )
        return record

    def make_enrollment(self, student, user=None, center=None, cohort=None):
        record, problems, notes = enroll_student(
            user or self.owner,
            center or self.alpha,
            (cohort or self.cohort).pk,
            student.student_number,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def make_learner_instructor(self, staff_number="T-1", assign_to=None):
        """A staff record under the teacher, assigned to a cohort or not."""
        staff = Staff.objects.create(
            organization=self.alpha,
            staff_number=staff_number,
            name="Alpha Teacher",
            user=self.instructor,
        )
        for cohort in assign_to or ():
            CohortInstructor.objects.create(
                organization=self.alpha, staff=staff, cohort=cohort
            )
        return staff

    def setUp(self):
        super().setUp()
        self.cohort = self.make_cohort()
        self.enrollment = self.make_enrollment(self.student)
        self.assessment = self.make_assessment()


class VocabularyTests(TestCase):
    """GRD-001..GRD-007's audit vocabulary is fixed, not free text."""

    def test_grade_audit_vocabulary(self):
        self.assertEqual(
            GRADE_AUDIT_ACTIONS, (GRADE_CREATED, GRADE_RECORDED, GRADE_UPDATED)
        )

    def test_enrollment_eligibility_vocabulary(self):
        self.assertEqual(
            GRADE_ELIGIBLE_STATUSES, (ENROLLMENT_ENROLLED, ENROLLMENT_COMPLETED)
        )

    def test_editable_assessment_fields(self):
        self.assertEqual(
            EDITABLE_ASSESSMENT_FIELDS,
            ("title", "assessment_type", "date", "max_score", "instructions",
             "weight"),
        )


class AssessmentDefinitionRules(GradingFixture):
    """GRD-001/GRD-002: one assessment of one cohort of this center."""

    def test_a_defined_assessment_is_audited_once_created(self):
        record = self.make_assessment(title="Another practical")
        self.assertIsNotNone(record)
        events = GradeAuditEvent.objects.filter(
            organization=self.alpha, assessment=record
        )
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertEqual(event.action, GRADE_CREATED)
        self.assertEqual(event.actor, self.owner)
        self.assertIsNone(event.previous_score)
        self.assertIsNone(event.new_score)
        self.assertIsNone(event.enrollment_id)
        self.assertIsNone(event.student_id)

    def test_a_foreign_cohort_number_is_cohort_not_found(self):
        # The beta center's cohort typed with its number gets not-found
        # silence at alpha, never the other center's cohort
        # (SEC-004/RULE 6.1).
        beta_cohort, problems, notes = create_cohort(
            self.beta_owner, self.beta, "Beta A", self.beta_course.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            beta_cohort.pk,
            "Borrowed",
            "practical",
            date(2026, 10, 5),
            10,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["cohort_not_found"])
        self.assertEqual(
            Assessment.objects.filter(organization=self.alpha).count(), 1
        )

    def test_a_cancelled_cohort_refuses_an_assessment(self):
        cohort = self.make_cohort(name="Spring A")
        record, problems, notes = set_cohort_status(
            self.owner, self.alpha, cohort.pk, COHORT_CANCELLED
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            cohort.pk,
            "Frozen",
            "practical",
            date(2026, 10, 5),
            10,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["cohort_cancelled"])

    def test_a_module_must_belong_to_the_cohorts_own_course(self):
        module, problems, notes = add_course_module(
            self.owner, self.alpha, self.alpha_course.pk, "Verbs"
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            self.cohort.pk,
            "With module",
            "practical",
            date(2026, 10, 5),
            10,
            module_number=module.pk,
        )
        self.assertIsNotNone(saved)
        self.assertEqual(saved.module, module)

    def test_a_module_of_another_courses_is_module_not_found(self):
        other_course = self.make_course(self.owner, self.alpha, "Other course")
        module, problems, notes = add_course_module(
            self.owner, self.alpha, other_course.pk, "Foreign module"
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            self.cohort.pk,
            "Wrong module",
            "practical",
            date(2026, 10, 5),
            10,
            module_number=module.pk,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["module_not_found"])

    def test_a_zero_maximum_never_saves(self):
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            self.cohort.pk,
            "No scale",
            "practical",
            date(2026, 10, 5),
            0,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, [])
        self.assertEqual([name for name, _ in problems], ["max_score"])
        self.assertEqual(
            Assessment.objects.filter(organization=self.alpha).count(), 1
        )

    def test_a_negative_weight_never_saves(self):
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            self.cohort.pk,
            "Bad weight",
            "practical",
            date(2026, 10, 5),
            10,
            weight=-1,
        )
        self.assertIsNone(saved)
        self.assertIn("weight", [name for name, _ in problems])

    def test_a_blank_title_never_saves(self):
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            self.cohort.pk,
            "   ",
            "practical",
            date(2026, 10, 5),
            10,
        )
        self.assertIsNone(saved)
        self.assertIn("title", [name for name, _ in problems])

    def test_the_assessment_type_is_required_free_text(self):
        # The SRS names no type vocabulary, so it is a bounded required
        # text field: free words are kept as given (docs/OPEN_QUESTIONS.md),
        # but a blank type and an over-long one are refused by the model.
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            self.cohort.pk,
            "No type",
            " ",
            date(2026, 10, 5),
            10,
        )
        self.assertIsNone(saved)
        self.assertIn("assessment_type", [name for name, _ in problems])
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            self.cohort.pk,
            "Too long a type",
            "p" * 41,
            date(2026, 10, 5),
            10,
        )
        self.assertIsNone(saved)
        self.assertIn("assessment_type", [name for name, _ in problems])
        saved, problems, notes = create_assessment(
            self.owner,
            self.alpha,
            self.cohort.pk,
            "Typed",
            "own words",
            date(2026, 10, 5),
            10,
        )
        self.assertIsNotNone(saved)
        self.assertEqual(saved.assessment_type, "own words")


class ResultRecordingRules(GradingFixture):
    """GRD-003/GRD-005: one raw score, anchored on the enrollment."""

    def setUp(self):
        super().setUp()
        self.cohort_two = self.make_cohort(name="Winter A")
        self.assessment_two = self.make_assessment(cohort=self.cohort_two)
        self.learner_two = self.make_student("AC-2")
        self.enrollment_two = self.make_enrollment(
            self.learner_two, cohort=self.cohort_two
        )

    def test_a_recorded_score_is_a_decimal_and_audited(self):
        saved, problems, notes = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=15,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        # The service's in-memory record keeps the Decimal as given: the
        # value is equal, and the saved row reads it to fixed places.
        self.assertEqual(saved.score, Decimal("15.00"))
        event = GradeAuditEvent.objects.filter(
            organization=self.alpha, action=GRADE_RECORDED
        ).first()
        self.assertEqual(event.result, saved)
        self.assertEqual(event.enrollment, self.enrollment)
        self.assertEqual(event.student, self.student)
        self.assertIsNone(event.previous_score)

    def test_an_empty_score_stays_pending_and_is_never_a_zero(self):
        saved, problems, notes = record_result(
            self.owner, self.alpha, self.assessment.pk, self.enrollment.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(saved)
        self.assertIsNone(saved.score)

    def test_a_score_above_the_maximum_is_refused(self):
        saved, problems, notes = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=21,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["score_above_max"])
        self.assertEqual(
            Result.objects.filter(organization=self.alpha).count(), 0
        )

    def test_a_negative_score_never_saves(self):
        saved, problems, notes = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=-1,
        )
        self.assertIsNone(saved)
        self.assertIn("score", [name for name, _ in problems])

    def test_the_second_result_for_one_enrollment_is_refused(self):
        saved, _, _ = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=10,
        )
        self.assertIsNotNone(saved)
        again, problems, notes = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=12,
        )
        self.assertIsNone(again)
        self.assertEqual(notes, ["already_recorded"])
        # The model constraint holds the same line without the service:
        # the pair the DB itself refuses.
        with self.assertRaises(IntegrityError):
            Result.objects.create(
                organization=self.alpha,
                assessment=self.assessment,
                enrollment=self.enrollment,
                student=self.student,
                recorded_by=self.owner,
            )

    def test_an_assessment_of_the_wrong_cohort_is_refused(self):
        saved, problems, notes = record_result(
            self.owner,
            self.alpha,
            self.assessment_two.pk,
            self.enrollment.pk,
            score=5,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["cohort_mismatch"])

    def test_a_withdrawn_enrollment_is_closed_for_grading(self):
        record, problems, notes = set_enrollment_status(
            self.owner, self.alpha, self.enrollment.pk, ENROLLMENT_WITHDRAWN
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        saved, problems, notes = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=5,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["enrollment_closed"])

    def test_an_archived_student_is_refused(self):
        archived = self.make_student("AC-9", name="Archived Learner")
        enrollment = self.make_enrollment(archived, cohort=self.cohort_two)
        archived.status = STATUS_ARCHIVED
        archived.save()
        saved, problems, notes = record_result(
            self.owner,
            self.alpha,
            self.assessment_two.pk,
            enrollment.pk,
            score=5,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["student_archived"])

    def test_a_completed_enrollment_keeps_grading_open(self):
        record, problems, notes = set_enrollment_status(
            self.owner, self.alpha, self.enrollment.pk, ENROLLMENT_COMPLETED
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        saved, problems, notes = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=5,
        )
        self.assertIsNotNone(saved)

    def test_nothing_recorded_is_deleted_when_a_status_changes(self):
        saved, _, _ = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=7,
        )
        self.assertIsNotNone(saved)
        _, problems, notes = set_enrollment_status(
            self.owner, self.alpha, self.enrollment.pk, ENROLLMENT_WITHDRAWN
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(
            Result.objects.filter(
                organization=self.alpha, assessment=self.assessment
            ).count(),
            1,
        )

    def test_missing_pending_and_recorded_are_three_different_things(self):
        # Three learners of one cohort: one row recorded, one row pending,
        # one learner with no row at all. The counts keep them apart and a
        # pending row is never counted as a zero (§13's rule). The second
        # learner of the sheet is the setUp's second learner, enrolled here in
        # this cohort too - one student may be in two cohorts of one centre.
        learner_three = self.make_student("AC-3")
        enrollment_two = self.make_enrollment(self.learner_two)
        self.make_enrollment(learner_three)
        first, _, _ = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=Decimal("12.50"),
        )
        self.assertIsNotNone(first)
        second, _, _ = record_result(
            self.owner, self.alpha, self.assessment.pk, enrollment_two.pk
        )
        self.assertIsNotNone(second)
        self.assertEqual(str(first.score), "12.50")
        self.assertIsNone(second.score)
        counts = result_counts(self.assessment)
        self.assertEqual(counts, {"recorded": 1, "pending": 1, "missing": 1})
        self.assertEqual(len(open_enrollments(self.cohort)), 3)


class ScoreCorrections(GradingFixture):
    """GRD-002's edits and GRD-006's corrections, audited the right way."""

    def setUp(self):
        super().setUp()
        self.result, _, _ = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=12,
            reason="first pass",
        )
        self.assertIsNotNone(self.result)

    def test_a_correction_audits_the_score_it_replaces(self):
        saved, problems, notes = update_result(
            self.owner, self.alpha, self.result.pk, 15, reason="rechecked"
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(saved.score, Decimal("15.00"))
        self.assertIsNotNone(saved.updated_at)
        events = GradeAuditEvent.objects.filter(
            organization=self.alpha, assessment=self.assessment
        )
        # Created, recorded by the setUp, and corrected here: three rows.
        self.assertEqual(events.count(), 3)
        event = events.order_by("at").last()
        self.assertEqual(event.action, GRADE_UPDATED)
        self.assertEqual(str(event.previous_score), "12.00")
        self.assertEqual(str(event.new_score), "15.00")
        self.assertEqual(event.reason, "rechecked")
        self.assertEqual(event.actor, self.owner)

    def test_a_correction_needs_a_score_and_a_like_is_unchanged(self):
        saved, problems, notes = update_result(
            self.owner, self.alpha, self.result.pk, None
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["score_required"])
        saved, problems, notes = update_result(
            self.owner, self.alpha, self.result.pk, 12
        )
        self.assertEqual(saved.pk, self.result.pk)
        self.assertEqual(notes, ["unchanged"])
        self.assertEqual(str(saved.score), "12.00")
        # The two audited actions so far are the only ones kept:
        self.assertEqual(
            GradeAuditEvent.objects.filter(
                organization=self.alpha, assessment=self.assessment
            ).count(),
            2,
        )

    def test_a_correction_above_the_maximum_is_refused(self):
        saved, problems, notes = update_result(
            self.owner, self.alpha, self.result.pk, 25
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["score_above_max"])
        self.assertEqual(self.result.score, Decimal("12.00"))

    def test_a_missing_result_number_is_result_not_found(self):
        saved, problems, notes = update_result(
            self.owner, self.alpha, 99999, 5
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["result_not_found"])

    def test_lowering_the_maximum_below_a_recorded_score_is_refused(self):
        saved, problems, notes = edit_assessment(
            self.owner, self.alpha, self.assessment.pk, "max_score", 5
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["max_below_results"])
        self.assertEqual(self.assessment.max_score, Decimal("20"))

    def test_lowering_the_maximum_that_keeps_the_scores_saved(self):
        saved, problems, notes = edit_assessment(
            self.owner, self.alpha, self.assessment.pk, "max_score", 15
        )
        self.assertIsNotNone(saved)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(saved.max_score, Decimal("15"))

    def test_an_edit_changes_the_named_field_and_nothing_else(self):
        saved, problems, notes = edit_assessment(
            self.owner, self.alpha, self.assessment.pk, "title", "Renamed"
        )
        self.assertEqual(notes, [])
        self.assertEqual(saved.title, "Renamed")
        self.assertEqual(saved.created_by, self.owner)
        self.assertEqual(saved.created_at, self.assessment.created_at)
        self.assertEqual(saved.cohort, self.cohort)

    def test_a_field_outside_the_editable_list_is_refused(self):
        for field in ("cohort", "created_by", "organization"):
            saved, problems, notes = edit_assessment(
                self.owner, self.alpha, self.assessment.pk, field, 1
            )
            self.assertIsNone(saved)
            self.assertEqual(notes, ["field_not_editable"])

    def test_editing_a_value_to_itself_says_unchanged(self):
        saved, problems, notes = edit_assessment(
            self.owner,
            self.alpha,
            self.assessment.pk,
            "title",
            self.assessment.title,
        )
        self.assertIsNotNone(saved)
        self.assertEqual(notes, ["unchanged"])

    def test_an_edit_writes_no_audit_row(self):
        # The three audited actions are the only grade actions: a definition
        # edit is not a grade change (docs/DECISIONS.md's decision).
        before = GradeAuditEvent.objects.filter(
            organization=self.alpha, assessment=self.assessment
        ).count()
        saved, problems, notes = edit_assessment(
            self.owner, self.alpha, self.assessment.pk, "date", date(2026, 11, 2)
        )
        self.assertEqual(notes, [])
        self.assertEqual(
            GradeAuditEvent.objects.filter(
                organization=self.alpha, assessment=self.assessment
            ).count(),
            before,
        )


class InstructorScopeAtTheService(GradingFixture):
    """GRD-004: the instructor acts only on their assigned cohorts."""

    def setUp(self):
        super().setUp()
        self.cohort_two = self.make_cohort(name="Winter A")
        self.assessment_two = self.make_assessment(cohort=self.cohort_two)
        self.learner_two = self.make_student("AC-2")
        self.enrollment_two = self.make_enrollment(
            self.learner_two, cohort=self.cohort_two
        )
        self.make_learner_instructor(assign_to=(self.cohort,))

    def test_an_assigned_instructor_records_their_own_cohorts_score(self):
        saved, problems, notes = record_result(
            self.instructor,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=10,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(saved)
        event = GradeAuditEvent.objects.filter(
            organization=self.alpha, action=GRADE_RECORDED
        ).first()
        self.assertEqual(event.actor, self.instructor)

    def test_an_assigned_instructor_can_also_correct_their_own_score(self):
        saved, _, _ = record_result(
            self.instructor,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=10,
        )
        self.assertIsNotNone(saved)
        again, problems, notes = update_result(
            self.instructor, self.alpha, saved.pk, 12
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(again.score, Decimal("12"))
        self.assertIsNotNone(again.updated_at)
        event = GradeAuditEvent.objects.filter(
            organization=self.alpha, action=GRADE_UPDATED
        ).first()
        self.assertEqual(event.actor, self.instructor)
        self.assertEqual(str(event.previous_score), "10.00")
        self.assertEqual(str(event.new_score), "12.00")

    def test_an_unassigned_cohort_is_refused_to_the_instructor(self):
        saved, problems, notes = record_result(
            self.instructor,
            self.alpha,
            self.assessment_two.pk,
            self.enrollment_two.pk,
            score=10,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["not_assigned"])
        self.assertEqual(
            Result.objects.filter(organization=self.alpha).count(), 0
        )

    def test_an_instructor_cannot_correct_an_unassigned_cohorts_score(self):
        saved, _, _ = record_result(
            self.owner,
            self.alpha,
            self.assessment_two.pk,
            self.enrollment_two.pk,
            score=10,
        )
        self.assertIsNotNone(saved)
        again, problems, notes = update_result(
            self.instructor, self.alpha, saved.pk, 12
        )
        self.assertIsNone(again)
        self.assertEqual(notes, ["not_assigned"])
        self.assertEqual(saved.score, Decimal("10"))


class ServicePermissionsAtTheCall(GradingFixture):
    """require() runs before anything is read or written."""

    def test_the_desk_and_finance_roles_have_no_grading_line(self):
        for person in (self.receptionist, self.finance):
            role = Members.objects.filter(
                user=person, organization=self.alpha
            ).first().role
            for code in ("grading.assessment.view", "grading.assessment.manage",
                         "grading.result.view", "grading.result.record"):
                self.assertFalse(role_has_permission(role, code))
            with self.assertRaises(PermissionDenied):
                record_result(
                    person,
                    self.alpha,
                    self.assessment.pk,
                    self.enrollment.pk,
                    score=10,
                )

    def test_an_instructor_cannot_define_an_assessment(self):
        with self.assertRaises(PermissionDenied):
            create_assessment(
                self.instructor,
                self.alpha,
                self.cohort.pk,
                "Nope",
                "practical",
                date(2026, 10, 5),
                10,
            )

    def test_an_instructor_without_a_staff_record_has_no_scope(self):
        saved, problems, notes = record_result(
            self.instructor,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=10,
        )
        self.assertIsNone(saved)
        self.assertEqual(notes, ["not_assigned"])

    def test_a_coordinator_grades_across_their_whole_centre(self):
        saved, problems, notes = record_result(
            self.coordinator,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=10,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(saved)
        other = self.make_assessment(title="Second practical")
        saved, problems, notes = record_result(
            self.coordinator,
            self.alpha,
            other.pk,
            self.enrollment.pk,
            score=3,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(saved)


class TraceabilityOfGradeChanges(GradingFixture):
    """RULE 6.5: deleting the records a grade change needs is refused."""

    def setUp(self):
        super().setUp()
        self.result, _, _ = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=12,
        )
        self.assertIsNotNone(self.result)

    def test_an_assessment_with_results_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.assessment.delete()

    def test_a_result_cannot_be_deleted_while_its_audit_exists(self):
        with self.assertRaises(ProtectedError):
            self.result.delete()

    def test_a_cohort_with_assessments_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            Cohort.objects.filter(
                organization=self.alpha, pk=self.cohort.pk
            ).delete()

    def test_an_enrollment_with_results_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            Enrollment.objects.filter(
                organization=self.alpha, pk=self.enrollment.pk
            ).delete()

    def test_a_grade_audit_row_keeps_its_records_while_it_exists(self):
        event = GradeAuditEvent.objects.filter(
            organization=self.alpha, action=GRADE_RECORDED
        ).first()
        with self.assertRaises(ProtectedError):
            event.result.delete()
        with self.assertRaises(ProtectedError):
            event.enrollment.delete()
        with self.assertRaises(ProtectedError):
            self.student.delete()
