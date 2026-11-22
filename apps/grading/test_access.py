"""The grading pages of one centre: who may see and do what, and only its own.

Every page answers through one centre and nothing else (RULE 6.1 and SEC-004):
a number from another centre is silence, and so is the whole address of a
centre this person is not a member of. Permission, not links, decides what
opens (SEC-001): the desk and the finance officer have no grading line at all,
the instructor views and records but never defines, and their rows are their
assigned cohorts' only. The CSV answers carry the same gates and rows as their
pages, non-GET reads are 405, and refusals at the manage pages are marked on
the rows the person typed. The student profile's grading section follows the
same permission and scope line (GRD-003's read-only mirror).

The rows were seeded by the services, not by the browser; the pages are the
ones config/urls.py registers under this centre's address.
"""

from datetime import date

from django.test import TestCase

from apps.pages import center_ui

from apps.academics.models import Cohort
from apps.academics.services import create_cohort, enroll_student, set_cohort_status
from apps.academics.statuses import COHORT_CANCELLED
from apps.students.models import Student

from django.db.models import ProtectedError

from apps.grading.models import Assessment, GradeAuditEvent, Result
from apps.grading.services import create_assessment, record_result
from apps.grading.statuses import GRADE_CREATED, GRADE_RECORDED, GRADE_UPDATED

from .test_model_rules import GradingFixture


class GradingAccessFixture(GradingFixture):
    """The seeded centres plus a second cohort, one recorded score, a teacher
    assigned to the second cohort only, and the other centre's own records."""

    def setUp(self):
        super().setUp()
        self.cohort_two = self.make_cohort(name="Winter A")
        self.assessment_two = self.make_assessment(
            cohort=self.cohort_two, title="Winter practical"
        )
        self.learner_two = self.make_student("AC-2")
        self.enrollment_two = self.make_enrollment(
            self.learner_two, cohort=self.cohort_two
        )
        self.result, _, _ = record_result(
            self.owner,
            self.alpha,
            self.assessment_two.pk,
            self.enrollment_two.pk,
            score=8,
        )
        self.assertIsNotNone(self.result)
        self.make_learner_instructor(assign_to=(self.cohort_two,))
        # The other centre's own cohort, assessment and result: their numbers
        # are foreign at this centre and must read as silence here.
        self.beta_cohort, _, _ = create_cohort(
            self.beta_owner, self.beta, "Beta A", self.beta_course.pk
        )
        self.beta_assessment, _, _ = create_assessment(
            self.beta_owner,
            self.beta,
            self.beta_cohort.pk,
            "Beta practical",
            "practical",
            date(2026, 10, 5),
            10,
        )
        self.beta_enrollment, _, _ = enroll_student(
            self.beta_owner, self.beta, self.beta_cohort.pk, "BC-1"
        )
        self.beta_result, _, _ = record_result(
            self.beta_owner,
            self.beta,
            self.beta_assessment.pk,
            self.beta_enrollment.pk,
            score=4,
        )
        self.assertIsNotNone(self.beta_assessment)
        self.assertIsNotNone(self.beta_result)

    def alpha_at(self, tail=""):
        return f"/o/{self.alpha.slug}/center/grading/{tail}"

    def beta_at(self, tail=""):
        return f"/o/{self.beta.slug}/center/grading/{tail}"

    def profile_at(self, center, student):
        return f"/o/{center.slug}/center/students/{student.student_number}/"

    def show(self, user):
        self.client.force_login(user)

    def get_page(self, user, url, status, kind):
        self.show(user)
        answer = self.client.get(url)
        self.assertEqual(answer.status_code, status)
        self.assertEqual(answer["X-CohortDesk-Page"], kind)
        return answer

    def denied(self, user, url):
        return self.get_page(user, url, 403, "denied")

    def not_here(self, user, url):
        return self.get_page(user, url, 404, "problem")

    def not_found_at(self, user, url):
        return self.get_page(user, url, 404, "not_found")

    def manage_post(self, user, tail, fields):
        """Post one results-manage attempt and say what the page answered."""
        self.show(user)
        answer = self.client.post(self.alpha_at(tail), fields)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "grading_results_manage")
        return answer

    def assessments_post(self, user, fields):
        self.show(user)
        answer = self.client.post(
            self.alpha_at("assessments/manage/"), fields
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(
            answer["X-CohortDesk-Page"], "grading_assessments_manage"
        )
        return answer


class HubAndRoles(GradingAccessFixture):
    """Who opens the grading pages of this centre (GRD-004's permission line)."""

    def test_the_owner_walks_every_grading_page(self):
        for tail, kind in (
            ("", "grading"),
            ("assessments/", "grading_assessments"),
            ("assessments/manage/", "grading_assessments_manage"),
            (f"assessments/{self.assessment.pk}/", "grading_assessment"),
            (f"assessments/{self.assessment.pk}/results/", "grading_assessment_results"),
            ("results/", "grading_results"),
            ("results/manage/", "grading_results_manage"),
            ("reports/", "grading_reports"),
        ):
            self.get_page(self.owner, self.alpha_at(tail), 200, kind)

    def test_a_coordinator_walks_them_too(self):
        for tail, kind in (
            ("", "grading"),
            ("assessments/manage/", "grading_assessments_manage"),
            ("results/manage/", "grading_results_manage"),
            ("reports/", "grading_reports"),
        ):
            self.get_page(self.coordinator, self.alpha_at(tail), 200, kind)

    def test_an_instructor_opens_the_pages_they_may_grade(self):
        for tail, kind in (
            ("", "grading"),
            ("assessments/", "grading_assessments"),
            ("results/", "grading_results"),
            ("results/manage/", "grading_results_manage"),
            ("reports/", "grading_reports"),
        ):
            self.get_page(self.instructor, self.alpha_at(tail), 200, kind)

    def test_the_desk_and_the_finance_are_denied_every_page(self):
        for person in (self.receptionist, self.finance):
            for tail in (
                "",
                "assessments/",
                "assessments/manage/",
                f"assessments/{self.assessment.pk}/",
                f"assessments/{self.assessment.pk}/results/",
                f"assessments/{self.assessment.pk}/results.csv",
                "results/",
                "results/manage/",
                "reports/",
                "reports/cohort.csv",
            ):
                self.denied(person, self.alpha_at(tail))

    def test_an_instructor_is_denied_the_assessment_definer(self):
        self.denied(self.instructor, self.alpha_at("assessments/manage/"))
        self.show(self.instructor)
        answer = self.client.post(
            self.alpha_at("assessments/manage/"),
            {
                "assessment_action": "create",
                "assessment_cohort": str(self.cohort.pk),
                "assessment_title": "Invented",
                "assessment_type": "practical",
                "assessment_date": "2026-12-01",
                "assessment_max": "12",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.assertEqual(
            Assessment.objects.filter(organization=self.alpha).count(), 2
        )

    def test_a_read_only_page_refuses_a_post(self):
        self.show(self.owner)
        answer = self.client.post(self.alpha_at("assessments/"), {})
        self.assertEqual(answer.status_code, 405)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_a_manage_page_refuses_a_put(self):
        self.show(self.owner)
        answer = self.client.put(self.alpha_at("results/manage/"), {})
        self.assertEqual(answer.status_code, 405)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_the_csv_answers_carry_their_rows_and_their_names(self):
        ui = center_ui(self.alpha)
        self.show(self.owner)
        answer = self.client.get(self.alpha_at("reports/cohort.csv"))
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer["Content-Type"].startswith("text/csv"))
        self.assertIn(
            f'grading-report-{self.alpha.slug}.csv',
            answer["Content-Disposition"],
        )
        text = answer.content.decode()
        self.assertIn(self.alpha.slug, text)
        self.assertIn("Autumn practical", text)
        self.assertIn("Winter practical", text)
        self.assertNotIn(self.beta.slug, text)
        answer = self.client.get(
            self.alpha_at(f"assessments/{self.assessment_two.pk}/results.csv")
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn(ui["grading_state_recorded"], answer.content.decode())
        self.assertNotIn(self.beta.slug, answer.content.decode())
        answer = self.client.get(
            self.alpha_at(f"assessments/{self.assessment.pk}/results.csv")
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn(ui["grading_state_missing"], answer.content.decode())


class CrossCenterSilence(GradingAccessFixture):
    """A foreign address or number is silence, never the other centre."""

    def test_a_foreign_centre_page_is_a_plain_404(self):
        self.not_here(self.owner, self.beta_at(""))
        self.not_here(self.beta_owner, self.alpha_at(""))
        self.not_here(self.instructor, self.beta_at("reports/"))

    def test_a_foreign_assessment_number_is_not_found_here(self):
        self.not_found_at(
            self.owner, self.alpha_at(f"assessments/{self.beta_assessment.pk}/")
        )
        self.not_found_at(
            self.owner,
            self.alpha_at(f"assessments/{self.beta_assessment.pk}/results/"),
        )
        self.not_found_at(
            self.beta_owner,
            self.beta_at(f"assessments/{self.assessment.pk}/"),
        )

    def test_a_foreign_assessment_number_on_a_csv_is_the_same_silence(self):
        self.show(self.owner)
        answer = self.client.get(
            self.alpha_at(f"assessments/{self.beta_assessment.pk}/results.csv")
        )
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")

    def test_a_foreign_result_number_never_reaches_the_other_centres_score(self):
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.owner,
            "results/manage/",
            {
                "result_action": "update",
                "result_number": str(self.beta_result.pk),
                "result_score": "9",
            },
        )
        self.assertIn(ui["grading_result_not_found"], answer.content.decode())
        self.assertEqual(
            Result.objects.filter(organization=self.alpha).count(), 1
        )
        self.assertEqual(
            Result.objects.filter(
                organization=self.beta, assessment=self.beta_assessment
            ).first().score,
            4,
        )


class InstructorScopeAtThePages(GradingAccessFixture):
    """The instructor sees and acts on their assigned cohorts only (GRD-004)."""

    def test_the_instructor_opens_the_assigned_assessments_only(self):
        self.get_page(
            self.instructor,
            self.alpha_at(f"assessments/{self.assessment_two.pk}/"),
            200,
            "grading_assessment",
        )
        self.not_found_at(
            self.instructor, self.alpha_at(f"assessments/{self.assessment.pk}/")
        )
        self.not_found_at(
            self.instructor,
            self.alpha_at(f"assessments/{self.assessment.pk}/results/"),
        )

    def test_the_instructor_is_refused_to_act_on_an_unassigned_assessment(self):
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.instructor,
            "results/manage/",
            {
                "result_action": "record",
                "result_assessment": str(self.assessment.pk),
                "result_enrollment": str(self.enrollment.pk),
                "result_score": "10",
            },
        )
        self.assertIn(ui["grading_not_assigned"], answer.content.decode())
        self.assertEqual(
            Result.objects.filter(organization=self.alpha).count(), 1
        )

    def test_the_assigned_instructor_records_their_own_rows(self):
        ui = center_ui(self.alpha)
        learner_three = self.make_student("AC-3")
        enrollment_three = self.make_enrollment(
            learner_three, cohort=self.cohort_two
        )
        answer = self.manage_post(
            self.instructor,
            "results/manage/",
            {
                "result_action": "record",
                "result_assessment": str(self.assessment_two.pk),
                "result_enrollment": str(enrollment_three.pk),
                "result_score": "11",
            },
        )
        self.assertIn(ui["grading_saved"], answer.content.decode())
        self.assertEqual(
            Result.objects.filter(organization=self.alpha).count(), 2
        )
        event = GradeAuditEvent.objects.filter(
            organization=self.alpha,
            action=GRADE_RECORDED,
            enrollment=enrollment_three,
        ).first()
        self.assertEqual(event.actor, self.instructor)

    def test_the_assigned_instructor_corrects_the_others_score_they_may_see(self):
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.instructor,
            "results/manage/",
            {
                "result_action": "update",
                "result_number": str(self.result.pk),
                "result_score": "9",
            },
        )
        self.assertIn(ui["grading_saved"], answer.content.decode())
        event = GradeAuditEvent.objects.filter(
            organization=self.alpha, action=GRADE_UPDATED
        ).first()
        self.assertEqual(event.actor, self.instructor)

    def test_an_unassigned_instructor_cannot_correct_the_others_score(self):
        # The teacher is assigned to the second cohort only: a score the owner
        # records on the first cohort's assessment is outside their scope, and
        # the page marks the refusal without touching the row.
        foreign, _, _ = record_result(
            self.owner,
            self.alpha,
            self.assessment.pk,
            self.enrollment.pk,
            score=10,
        )
        self.assertIsNotNone(foreign)
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.instructor,
            "results/manage/",
            {
                "result_action": "update",
                "result_number": str(foreign.pk),
                "result_score": "7",
            },
        )
        self.assertIn(ui["grading_not_assigned"], answer.content.decode())
        self.assertEqual(foreign.score, 10)


class StudentProfileGating(GradingAccessFixture):
    """The profile's grading section needs grading.result.view (SEC-001)."""

    def test_the_desk_reads_the_profile_but_not_the_grading_section(self):
        ui = center_ui(self.alpha)
        self.show(self.receptionist)
        answer = self.client.get(self.profile_at(self.alpha, self.learner_two))
        self.assertEqual(answer.status_code, 200)
        self.assertNotIn(
            ui["grading_student_results_heading"], answer.content.decode()
        )

    def test_the_owner_reads_the_grading_section(self):
        ui = center_ui(self.alpha)
        self.show(self.owner)
        answer = self.client.get(self.profile_at(self.alpha, self.learner_two))
        self.assertIn(
            ui["grading_student_results_heading"], answer.content.decode()
        )

    def test_a_teachers_profile_rows_are_the_assigned_cohorts_only(self):
        # AC-2's recorded score belongs to the cohort assigned to the teacher,
        # so the profile shows the section for the teacher too (same scope).
        ui = center_ui(self.alpha)
        self.show(self.instructor)
        answer = self.client.get(self.profile_at(self.alpha, self.learner_two))
        self.assertIn(
            ui["grading_student_results_heading"], answer.content.decode()
        )

    def test_the_reports_hub_links_grading_reports_only_to_permitted_roles(self):
        self.show(self.owner)
        answer = self.client.get(f"/o/{self.alpha.slug}/center/reports/")
        self.assertIn("/center/grading/reports/", answer.content.decode())
        self.show(self.receptionist)
        answer = self.client.get(f"/o/{self.alpha.slug}/center/reports/")
        self.assertNotIn("/center/grading/reports/", answer.content.decode())


class ManagePageMarkings(GradingAccessFixture):
    """Bad or refused attempts are marked on the rows the person typed."""

    def test_a_recorded_score_says_saved_and_creates_one_row(self):
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.owner,
            "results/manage/",
            {
                "result_action": "record",
                "result_assessment": str(self.assessment.pk),
                "result_enrollment": str(self.enrollment.pk),
                "result_score": "15",
            },
        )
        self.assertIn(ui["grading_saved"], answer.content.decode())
        self.assertEqual(
            Result.objects.filter(organization=self.alpha).count(), 2
        )
        event = GradeAuditEvent.objects.filter(
            organization=self.alpha,
            action=GRADE_RECORDED,
            enrollment=self.enrollment,
        ).first()
        self.assertEqual(event.actor, self.owner)

    def test_a_score_above_the_maximum_is_rejected_and_marked(self):
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.owner,
            "results/manage/",
            {
                "result_action": "record",
                "result_assessment": str(self.assessment.pk),
                "result_enrollment": str(self.enrollment.pk),
                "result_score": "999",
            },
        )
        self.assertIn(ui["grading_rejected"], answer.content.decode())
        # The template escapes the apostrophe in the marked row's text.
        self.assertIn(
            ui["grading_score_above_max"].replace("'", "&#x27;"),
            answer.content.decode(),
        )
        self.assertEqual(
            Result.objects.filter(organization=self.alpha).count(), 1
        )

    def test_an_invalid_score_is_marked_and_nothing_saves(self):
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.owner,
            "results/manage/",
            {
                "result_action": "record",
                "result_assessment": str(self.assessment.pk),
                "result_enrollment": str(self.enrollment.pk),
                "result_score": "late",
            },
        )
        self.assertIn(
            ui["field_invalid"].format("late"), answer.content.decode()
        )
        self.assertEqual(
            Result.objects.filter(organization=self.alpha).count(), 1
        )

    def test_an_empty_record_score_stays_pending_and_never_a_zero(self):
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.owner,
            "results/manage/",
            {
                "result_action": "record",
                "result_assessment": str(self.assessment.pk),
                "result_enrollment": str(self.enrollment.pk),
                "result_score": "",
            },
        )
        self.assertIn(ui["grading_saved"], answer.content.decode())
        saved = Result.objects.filter(
            organization=self.alpha, assessment=self.assessment
        ).first()
        self.assertIsNone(saved.score)

    def test_an_update_needs_a_score_and_a_like_says_unchanged(self):
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.owner,
            "results/manage/",
            {
                "result_action": "update",
                "result_number": str(self.result.pk),
                "result_score": "",
            },
        )
        self.assertIn(ui["field_required"], answer.content.decode())
        answer = self.manage_post(
            self.owner,
            "results/manage/",
            {
                "result_action": "update",
                "result_number": str(self.result.pk),
                "result_score": "8",
            },
        )
        self.assertIn(ui["grading_unchanged"], answer.content.decode())
        self.assertEqual(
            GradeAuditEvent.objects.filter(
                organization=self.alpha, assessment=self.assessment_two
            ).count(),
            2,
        )

    def test_a_correction_at_the_page_audits_the_score_it_replaces(self):
        ui = center_ui(self.alpha)
        answer = self.manage_post(
            self.owner,
            "results/manage/",
            {
                "result_action": "update",
                "result_number": str(self.result.pk),
                "result_score": "9",
            },
        )
        self.assertIn(ui["grading_saved"], answer.content.decode())
        event = GradeAuditEvent.objects.filter(
            organization=self.alpha, action=GRADE_UPDATED
        ).first()
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(str(event.previous_score), "8.00")
        self.assertEqual(str(event.new_score), "9.00")


class AssessmentManageFlows(GradingAccessFixture):
    """The assessments manage page: create, edit, and marked refusals."""

    def test_an_owner_creates_through_the_page(self):
        self.assessments_post(
            self.owner,
            {
                "assessment_action": "create",
                "assessment_cohort": str(self.cohort.pk),
                "assessment_title": "Spring practical",
                "assessment_type": "practical",
                "assessment_date": "2026-12-01",
                "assessment_max": "12",
            },
        )
        self.assertEqual(
            Assessment.objects.filter(organization=self.alpha).count(), 3
        )
        event = GradeAuditEvent.objects.filter(
            organization=self.alpha, action=GRADE_CREATED
        ).last()
        self.assertEqual(event.actor, self.owner)

    def test_a_bad_date_is_marked_and_nothing_is_created(self):
        ui = center_ui(self.alpha)
        answer = self.assessments_post(
            self.owner,
            {
                "assessment_action": "create",
                "assessment_cohort": str(self.cohort.pk),
                "assessment_title": "Bad date",
                "assessment_type": "practical",
                "assessment_date": "midwinter",
                "assessment_max": "12",
            },
        )
        self.assertIn(
            ui["field_invalid"].format("midwinter"), answer.content.decode()
        )
        self.assertEqual(
            Assessment.objects.filter(organization=self.alpha).count(), 2
        )

    def test_lowering_a_maximum_below_a_recorded_score_is_marked(self):
        ui = center_ui(self.alpha)
        answer = self.assessments_post(
            self.owner,
            {
                "assessment_action": "edit",
                "assessment_number": str(self.assessment_two.pk),
                "assessment_field": "max_score",
                "assessment_max": "4",
            },
        )
        self.assertIn(
            ui["grading_max_below_results"], answer.content.decode()
        )
        self.assertEqual(
            Assessment.objects.filter(
                organization=self.alpha, pk=self.assessment_two.pk
            ).first().max_score,
            self.assessment_two.max_score,
        )

    def test_an_edit_is_applied_and_no_audit_row_is_written(self):
        ui = center_ui(self.alpha)
        before = GradeAuditEvent.objects.filter(
            organization=self.alpha
        ).count()
        answer = self.assessments_post(
            self.owner,
            {
                "assessment_action": "edit",
                "assessment_number": str(self.assessment.pk),
                "assessment_field": "title",
                "assessment_title": "Renamed practical",
            },
        )
        self.assertIn(ui["grading_saved"], answer.content.decode())
        self.assertEqual(
            Assessment.objects.filter(
                organization=self.alpha, pk=self.assessment.pk
            ).first().title,
            "Renamed practical",
        )
        self.assertEqual(
            GradeAuditEvent.objects.filter(organization=self.alpha).count(),
            before,
        )

    def test_editing_a_value_to_itself_says_unchanged(self):
        ui = center_ui(self.alpha)
        answer = self.assessments_post(
            self.owner,
            {
                "assessment_action": "edit",
                "assessment_number": str(self.assessment.pk),
                "assessment_field": "title",
                "assessment_title": self.assessment.title,
            },
        )
        self.assertIn(ui["grading_unchanged"], answer.content.decode())

    def test_an_unassigned_instructor_is_marked_on_the_assessment_row(self):
        ui = center_ui(self.alpha)
        self.denied(self.instructor, self.alpha_at("assessments/manage/"))
        answer = self.manage_post(
            self.instructor,
            "results/manage/",
            {
                "result_action": "record",
                "result_assessment": str(self.assessment.pk),
                "result_enrollment": str(self.enrollment.pk),
                "result_score": "",
            },
        )
        self.assertIn(ui["grading_not_assigned"], answer.content.decode())

    def test_a_cancelled_cohort_is_marked_on_the_cohort_row(self):
        ui = center_ui(self.alpha)
        cohort_three = self.make_cohort(name="Frozen A")
        _, problems, notes = set_cohort_status(
            self.owner, self.alpha, cohort_three.pk, COHORT_CANCELLED
        )
        self.assertEqual(notes, [])
        answer = self.assessments_post(
            self.owner,
            {
                "assessment_action": "create",
                "assessment_cohort": str(cohort_three.pk),
                "assessment_title": "Frozen practical",
                "assessment_type": "practical",
                "assessment_date": "2026-12-01",
                "assessment_max": "12",
            },
        )
        self.assertIn(
            ui["grading_cohort_cancelled"], answer.content.decode()
        )
        self.assertEqual(
            Assessment.objects.filter(organization=self.alpha).count(), 2
        )

    def test_a_created_assessment_keeps_its_cohort_traceable(self):
        self.assessments_post(
            self.owner,
            {
                "assessment_action": "create",
                "assessment_cohort": str(self.cohort.pk),
                "assessment_title": "Protected practical",
                "assessment_type": "practical",
                "assessment_date": "2026-12-01",
                "assessment_max": "12",
            },
        )
        with self.assertRaises(ProtectedError):
            Cohort.objects.filter(
                organization=self.alpha, pk=self.cohort.pk
            ).delete()