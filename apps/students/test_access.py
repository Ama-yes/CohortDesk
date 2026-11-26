"""Student and application isolation proved through the URL layer.

AUTH-005/SEC-001/003/004 and STU-002: the test client asks config.urls
itself, so every answer below is chosen by the address exactly as a browser
request would be, and every answer is checked by the page kind the chain
records. The same student number at two centres must name two different
people, and no page of one centre may carry a record of another.

The records are seeded with the model API here on purpose: these tests are
about what the addresses answer, not about the service rules, which
apps/students/tests.py proves. Actions that are part of the page contract
(create, edit, archive, record, accept, convert) go through POST because
that is the only path a person has.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.roles import (
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)
from apps.academics.models import (
    Attendance,
    Cohort,
    Course,
    Enrollment,
    Session,
)
from apps.grading.models import Assessment, Result
from apps.staff.models import CohortInstructor, Staff

from .models import Application, Student


class TwoCenters(TestCase):
    """Two centres that each use the same student number for one person."""

    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Alpha Center", slug="alpha-center", primary_currency="DZD"
        )
        self.beta = Organization.objects.create(
            name="Beta Center", slug="beta-center", primary_currency="EUR"
        )
        self.alpha_owner = CohortUser.objects.create_user(
            username="alpha-owner", password="x", display_name="Alpha Owner"
        )
        self.beta_owner = CohortUser.objects.create_user(
            username="beta-owner", password="x", display_name="Beta Owner"
        )
        self.desk = CohortUser.objects.create_user(
            username="alpha-desk", password="x", display_name="Alpha Desk"
        )
        self.teacher = CohortUser.objects.create_user(
            username="alpha-teacher", password="x", display_name="Alpha Teacher"
        )
        self.money = CohortUser.objects.create_user(
            username="alpha-money", password="x", display_name="Alpha Money"
        )
        Members.objects.create(
            user=self.alpha_owner, organization=self.alpha, role=ROLE_OWNER
        )
        Members.objects.create(
            user=self.beta_owner, organization=self.beta, role=ROLE_OWNER
        )
        Members.objects.create(
            user=self.desk, organization=self.alpha, role=ROLE_RECEPTIONIST
        )
        Members.objects.create(
            user=self.teacher, organization=self.alpha, role=ROLE_INSTRUCTOR
        )
        Members.objects.create(
            user=self.money, organization=self.alpha, role=ROLE_FINANCE_OFFICER
        )
        Student.objects.create(
            organization=self.alpha,
            student_number="AC-1",
            name="Alpha Learner",
            contact="alpha@example.test",
        )
        Student.objects.create(
            organization=self.beta,
            student_number="AC-1",
            name="Beta Learner",
            contact="beta@example.test",
        )

    def students_url(self, center=None):
        return "/o/{}/center/students/".format((center or self.alpha).slug)

    def profile_url(self, number, center=None):
        return "{}{}/".format(self.students_url(center), number)

    def manage_url(self, center=None):
        return "{}{}".format(self.students_url(center), "manage/")

    def applications_url(self, center=None):
        return "/o/{}/center/applications/".format((center or self.alpha).slug)

    def applications_manage_url(self, center=None):
        return "{}{}".format(self.applications_url(center), "manage/")

    def body(self, response):
        return response.content.decode()

    # -- the same number is two people ----------------------------------

    def test_same_number_here_names_this_centres_person(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.profile_url("AC-1"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "student")
        body = self.body(answer)
        self.assertIn("Alpha Learner", body)
        self.assertNotIn("Beta Learner", body)
        self.assertNotIn("beta@example.test", body)

    def test_same_number_elsewhere_names_the_other_person(self):
        self.client.force_login(self.beta_owner)
        answer = self.client.get(self.profile_url("AC-1", self.beta))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "student")
        body = self.body(answer)
        self.assertIn("Beta Learner", body)
        self.assertNotIn("Alpha Learner", body)

    def test_other_centres_record_is_not_reachable_by_number(self):
        """A member of one centre gets the unseen-centre answer, never a row."""
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.profile_url("AC-1", self.beta))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Beta Learner", self.body(answer))

    def test_number_unknown_here_is_this_centres_own_not_found_page(self):
        """An unseen number gets this centre's page, not a guess about others."""
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.profile_url("ZZ-9"))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        body = self.body(answer)
        self.assertIn("No record with that student number is here.", body)
        self.assertIn("Alpha Center", body)
        self.assertNotIn("Beta Learner", body)

    # -- lists never carry another centre -------------------------------

    def test_student_list_carries_this_centres_rows_only(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.students_url())
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "students")
        body = self.body(answer)
        self.assertIn("Alpha Learner", body)
        self.assertNotIn("Beta Learner", body)
        self.assertNotIn("beta@example.test", body)
        self.assertNotIn("/o/beta-center/", body)

    def test_signed_out_person_sees_no_student_data(self):
        answer = self.client.get(self.students_url())
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        body = self.body(answer)
        self.assertNotIn("Alpha Learner", body)
        self.assertNotIn("alpha@example.test", body)

    def test_suspended_membership_loses_the_students_pages(self):
        self.client.force_login(self.desk)
        Members.objects.filter(user=self.desk, organization=self.alpha).update(
            status="suspended"
        )
        answer = self.client.get(self.students_url())
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Alpha Learner", self.body(answer))

    # -- a member, but not for this page --------------------------------

    def test_role_without_read_permission_is_denied_not_absent(self):
        self.client.force_login(self.money)
        answer = self.client.get(self.students_url())
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        body = self.body(answer)
        self.assertIn("Alpha Center", body)
        self.assertNotIn("Alpha Learner", body)
        answer = self.client.get(self.applications_url())
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")

    def test_instructor_reads_students_but_cannot_manage(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(self.students_url()).status_code, 200)
        answer = self.client.get(self.manage_url())
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.post(
            self.manage_url(),
            {
                "students_action": "create",
                "student_number": "AC-50",
                "name": "Not Allowed",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(
            Student.objects.filter(organization=self.alpha).count(), 1
        )
        answer = self.client.get(self.applications_url())
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.post(
            self.applications_manage_url(),
            {"applications_action": "record", "name": "Not Allowed"},
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(Application.objects.count(), 0)

    def test_receptionist_may_write_but_archive_stays_closed(self):
        """The page opens for a manager; the action itself still re-checks."""
        self.client.force_login(self.desk)
        self.assertEqual(self.client.get(self.manage_url()).status_code, 200)
        answer = self.client.post(
            self.manage_url(),
            {"students_action": "archive", "student_number": "AC-1"},
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        record = Student.objects.get(
            organization=self.alpha, student_number="AC-1"
        )
        self.assertEqual(record.status, "applicant")

    # -- the read addresses answer a method they do not read ------------

    def test_post_to_a_read_only_address_is_the_problem_page(self):
        self.client.force_login(self.alpha_owner)
        for address in (self.students_url(), self.applications_url()):
            answer = self.client.post(address, {})
            self.assertEqual(answer.status_code, 405)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    # -- the forms, through the addresses -------------------------------

    def test_create_is_saved_on_the_same_page_and_then_listed(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage_url(),
            {
                "students_action": "create",
                "student_number": "  ac-20 ",
                "name": "  New Learner ",
                "contact": "new@example.test",
                "status": "",
                "note": "",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "students_manage")
        self.assertIn("Saved.", self.body(answer))
        record = Student.objects.get(
            organization=self.alpha, student_number="AC-20"
        )
        self.assertEqual(record.name, "New Learner")
        self.assertEqual(record.status, "applicant")
        listed = self.body(self.client.get(self.students_url()))
        self.assertIn("New Learner", listed)

    def test_duplicate_is_marked_and_saves_nothing(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage_url(),
            {
                "students_action": "create",
                "student_number": "AC-21",
                "name": "Alpha Learner",
                "contact": "alpha@example.test",
                "status": "applicant",
            },
        )
        self.assertEqual(answer.status_code, 200)
        body = self.body(answer)
        self.assertIn('id="name_problem"', body)
        self.assertIn("Nothing was merged.", body)
        self.assertEqual(
            Student.objects.filter(organization=self.alpha).count(), 1
        )

    def test_rejected_value_is_marked_on_the_row_the_person_typed(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage_url(),
            {
                "students_action": "edit",
                "student_number": "AC-1",
                "students_field": "name",
                "students_value": "   ",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="students_value_problem"', self.body(answer))
        record = Student.objects.get(
            organization=self.alpha, student_number="AC-1"
        )
        self.assertEqual(record.name, "Alpha Learner")

    def test_unparseable_date_is_the_pages_own_rejection(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage_url(),
            {
                "students_action": "edit",
                "student_number": "AC-1",
                "students_field": "birth_date",
                "students_value": "31/12/2020",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "students_manage")
        self.assertIn('id="students_value_problem"', self.body(answer))
        record = Student.objects.get(
            organization=self.alpha, student_number="AC-1"
        )
        self.assertIsNone(record.birth_date)

    def test_edit_changes_the_one_part_and_the_profile_shows_it(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage_url(),
            {
                "students_action": "edit",
                "student_number": "ac-1",
                "students_field": "contact",
                "students_value": "0555 00 11 22",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        record = Student.objects.get(
            organization=self.alpha, student_number="AC-1"
        )
        self.assertEqual(record.contact, "0555 00 11 22")
        profile = self.body(self.client.get(self.profile_url("AC-1")))
        self.assertIn("0555 00 11 22", profile)

    def test_archive_keeps_the_record_findable(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage_url(),
            {"students_action": "archive", "student_number": "AC-1"},
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        profile = self.client.get(self.profile_url("AC-1"))
        self.assertEqual(profile.status_code, 200)
        self.assertIn("Archived", self.body(profile))
        listed = self.body(self.client.get(self.students_url()))
        self.assertIn("Alpha Learner", listed)
        again = self.client.post(
            self.manage_url(),
            {"students_action": "archive", "student_number": "AC-1"},
        )
        self.assertEqual(again.status_code, 200)
        self.assertIn("That record is already archived.", self.body(again))

    def test_illegal_transition_is_marked_on_the_status_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage_url(),
            {
                "students_action": "set_status",
                "student_number": "AC-1",
                "status": "graduated",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="status_problem"', self.body(answer))
        record = Student.objects.get(
            organization=self.alpha, student_number="AC-1"
        )
        self.assertEqual(record.status, "applicant")

    # -- admissions ------------------------------------------------------

    def test_applications_page_says_the_course_is_typed_words(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.applications_url())
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "applications")
        body = self.body(answer)
        self.assertIn(
            "The course asked for is words typed by center staff. "
            "It is not a course record.",
            body,
        )
        self.assertIn(
            "No application has been recorded at this center yet.", body
        )

    def test_application_is_recorded_and_accepted_through_the_page(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.applications_manage_url(),
            {
                "applications_action": "record",
                "name": "Applied Learner",
                "contact": "app@example.test",
                "source": "Poster",
                "desired_course": "Spanish A1",
                "student_number": "",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        application = Application.objects.get(organization=self.alpha)
        self.assertEqual(application.desired_course, "Spanish A1")
        listed = self.body(self.client.get(self.applications_url()))
        self.assertIn("Applied Learner", listed)
        self.assertIn("Spanish A1", listed)
        answer = self.client.post(
            self.applications_manage_url(),
            {
                "applications_action": "set_status",
                "applications_number": str(application.pk),
                "status": "accepted",
            },
        )
        self.assertEqual(answer.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.status, "accepted")

    def test_conversion_without_a_number_waits_for_one(self):
        application = Application.objects.create(
            organization=self.alpha,
            name="Applied Learner",
            contact="app@example.test",
            status="accepted",
        )
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.applications_manage_url(),
            {
                "applications_action": "convert",
                "applications_number": str(application.pk),
            },
        )
        self.assertEqual(answer.status_code, 200)
        body = self.body(answer)
        self.assertIn('id="student_number_problem"', body)
        self.assertIn("does not name a student number", body)
        self.assertFalse(
            Student.objects.filter(
                organization=self.alpha, name="Applied Learner"
            ).exists()
        )

    def test_conversion_creates_the_record_and_links_the_application(self):
        application = Application.objects.create(
            organization=self.alpha,
            student_number="AC-77",
            name="Applied Learner",
            contact="app@example.test",
            desired_course="Spanish A1",
            status="accepted",
        )
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.applications_manage_url(),
            {
                "applications_action": "convert",
                "applications_number": str(application.pk),
            },
        )
        self.assertEqual(answer.status_code, 200)
        record = Student.objects.get(
            organization=self.alpha, student_number="AC-77"
        )
        self.assertEqual(record.status, "active")
        application.refresh_from_db()
        self.assertEqual(application.converted_student_id, record.pk)
        profile = self.body(self.client.get(self.profile_url("AC-77")))
        self.assertIn("Applied Learner", profile)
        self.assertIn("Spanish A1", profile)
        self.assertIn('id="applications_made"', profile)
        again = self.client.post(
            self.applications_manage_url(),
            {
                "applications_action": "convert",
                "applications_number": str(application.pk),
            },
        )
        self.assertEqual(again.status_code, 200)
        self.assertIn(
            "That application was converted already. Nothing was changed.",
            self.body(again),
        )
        self.assertEqual(
            Student.objects.filter(organization=self.alpha).count(), 2
        )

    def test_another_centres_application_is_never_converted(self):
        beta_application = Application.objects.create(
            organization=self.beta,
            student_number="BC-9",
            name="Beta Applicant",
            contact="beta-app@example.test",
            status="accepted",
        )
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.applications_manage_url(),
            {
                "applications_action": "convert",
                "applications_number": str(beta_application.pk),
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="applications_number_problem"', self.body(answer))
        beta_application.refresh_from_db()
        self.assertIsNone(beta_application.converted_student)
        self.assertFalse(
            Student.objects.filter(
                organization=self.alpha, name="Beta Applicant"
            ).exists()
        )
        self.assertNotIn(
            "Beta Applicant", self.body(self.client.get(self.applications_url()))
        )

    def test_applications_list_carries_this_centres_rows_only(self):
        Application.objects.create(
            organization=self.alpha, name="Alpha Applicant"
        )
        Application.objects.create(
            organization=self.beta, name="Beta Applicant"
        )
        self.client.force_login(self.alpha_owner)
        body = self.body(self.client.get(self.applications_url()))
        self.assertIn("Alpha Applicant", body)
        self.assertNotIn("Beta Applicant", body)

    # -- the centre's own navigation -------------------------------------

    def test_tenant_navigation_names_students_and_admissions(self):
        self.client.force_login(self.alpha_owner)
        body = self.body(
            self.client.get("/o/alpha-center/center/dashboard/")
        )
        self.assertIn(self.students_url(), body)
        self.assertIn(self.applications_url(), body)
        self.assertIn('data-kind="students"', body)
        self.assertIn('data-kind="applications"', body)


class InstructorCohortScope(TestCase):
    """D110: an instructor reads only the learners of their assigned cohorts.

    The owner's authorization decision of 2026-11-24: an instructor may open a
    student profile only when that learner has an enrollment in a cohort the
    instructor is assigned to, never centre-wide. The fixture is one centre with
    two cohorts, each assigned to its own instructor, and four learners: one in
    cohort A only, one in cohort B only, one in both (A enrolled, B withdrawn),
    and one with no enrollment at all. Every read of personal information - the
    list, the profile, a direct number, a query, a CSV - is checked against that
    rule, and the owner and the receptionist are checked to keep their centre-
    wide access.
    """

    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Alpha Center", slug="alpha-center", primary_currency="DZD"
        )
        self.beta = Organization.objects.create(
            name="Beta Center", slug="beta-center", primary_currency="EUR"
        )
        self.owner = CohortUser.objects.create_user(
            username="alpha-owner", password="x", display_name="Alpha Owner"
        )
        self.desk = CohortUser.objects.create_user(
            username="alpha-desk", password="x", display_name="Alpha Desk"
        )
        self.teacher_a = CohortUser.objects.create_user(
            username="alpha-teacher-a", password="x", display_name="A Teacher"
        )
        self.teacher_b = CohortUser.objects.create_user(
            username="alpha-teacher-b", password="x", display_name="B Teacher"
        )
        self.lone_teacher = CohortUser.objects.create_user(
            username="alpha-teacher-none", password="x", display_name="No Cohorts"
        )
        self.beta_owner = CohortUser.objects.create_user(
            username="beta-owner", password="x", display_name="Beta Owner"
        )
        Members.objects.create(
            user=self.owner, organization=self.alpha, role=ROLE_OWNER
        )
        Members.objects.create(
            user=self.desk, organization=self.alpha, role=ROLE_RECEPTIONIST
        )
        for teacher in (self.teacher_a, self.teacher_b, self.lone_teacher):
            Members.objects.create(
                user=teacher, organization=self.alpha, role=ROLE_INSTRUCTOR
            )
        Members.objects.create(
            user=self.beta_owner, organization=self.beta, role=ROLE_OWNER
        )
        self.course = Course.objects.create(
            organization=self.alpha, title="Alpha Course"
        )
        self.cohort_a = Cohort.objects.create(
            organization=self.alpha, course=self.course, name="Morning Cohort"
        )
        self.cohort_b = Cohort.objects.create(
            organization=self.alpha, course=self.course, name="Evening Cohort"
        )
        self.staff_a = Staff.objects.create(
            organization=self.alpha, staff_number="SA-1", name="A Teacher", user=self.teacher_a
        )
        self.staff_b = Staff.objects.create(
            organization=self.alpha, staff_number="SB-1", name="B Teacher", user=self.teacher_b
        )
        CohortInstructor.objects.create(
            organization=self.alpha, cohort=self.cohort_a, staff=self.staff_a
        )
        CohortInstructor.objects.create(
            organization=self.alpha, cohort=self.cohort_b, staff=self.staff_b
        )
        self.assigned = Student.objects.create(
            organization=self.alpha,
            student_number="AT-A1",
            name="Assigned Learner",
            contact="assigned@example.test",
            address="9 Morning Road",
            status="active",
        )
        self.unassigned = Student.objects.create(
            organization=self.alpha,
            student_number="AT-B1",
            name="Unassigned Learner",
            contact="unassigned@example.test",
            status="active",
        )
        self.no_cohort = Student.objects.create(
            organization=self.alpha,
            student_number="AT-N1",
            name="No Cohort Learner",
            contact="none@example.test",
            status="active",
        )
        self.both = Student.objects.create(
            organization=self.alpha,
            student_number="AT-AB1",
            name="Both Cohorts Learner",
            contact="both@example.test",
            status="active",
        )
        self.enroll_a = Enrollment.objects.create(
            organization=self.alpha, cohort=self.cohort_a, student=self.assigned
        )
        Enrollment.objects.create(
            organization=self.alpha, cohort=self.cohort_b, student=self.unassigned
        )
        self.both_a = Enrollment.objects.create(
            organization=self.alpha, cohort=self.cohort_a, student=self.both
        )
        self.both_b = Enrollment.objects.create(
            organization=self.alpha,
            cohort=self.cohort_b,
            student=self.both,
            status="withdrawn",
        )
        self.beta_learner = Student.objects.create(
            organization=self.beta,
            student_number="AT-A",
            name="Beta Learner",
            contact="beta@example.test",
        )
        self.session_a = Session.objects.create(
            organization=self.alpha,
            cohort=self.cohort_a,
            start_at=datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc),
        )
        self.session_b = Session.objects.create(
            organization=self.alpha,
            cohort=self.cohort_b,
            start_at=datetime(2026, 1, 6, 14, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 6, 17, 0, tzinfo=timezone.utc),
        )
        Attendance.objects.create(
            organization=self.alpha,
            session=self.session_a,
            student=self.assigned,
            status="present",
        )
        Attendance.objects.create(
            organization=self.alpha,
            session=self.session_a,
            student=self.both,
            status="present",
        )
        Attendance.objects.create(
            organization=self.alpha,
            session=self.session_b,
            student=self.unassigned,
            status="present",
        )
        Attendance.objects.create(
            organization=self.alpha,
            session=self.session_b,
            student=self.both,
            status="present",
        )
        self.assessment_b = Assessment.objects.create(
            organization=self.alpha,
            cohort=self.cohort_b,
            title="Evening Assessment",
            assessment_type="exam",
            date=date(2026, 1, 20),
            max_score=Decimal("10.00"),
            created_by=self.owner,
        )
        Result.objects.create(
            organization=self.alpha,
            assessment=self.assessment_b,
            enrollment=self.both_b,
            student=self.both,
            score=Decimal("7.00"),
            recorded_by=self.owner,
        )

    def students_url(self, center=None):
        return "/o/{}/center/students/".format((center or self.alpha).slug)

    def profile_url(self, number, center=None):
        return "{}{}/".format(self.students_url(center), number)

    def manage_url(self, center=None):
        return "{}{}".format(self.students_url(center), "manage/")

    def register_url(self, number):
        return "/o/{}/center/attendance/{}/register.csv".format(
            self.alpha.slug, number
        )

    def reports_url(self):
        return "/o/{}/center/reports/enrollments.csv".format(self.alpha.slug)

    def body(self, answer):
        return answer.content.decode()

    def names_shown(self, body):
        """Which of the four learners this centre's page actually shows."""
        return [
            name
            for name in (
                "Assigned Learner",
                "Unassigned Learner",
                "No Cohort Learner",
                "Both Cohorts Learner",
            )
            if name in body
        ]

    # -- instructor A: assigned cohort A only ----------------------------

    def test_instructor_a_opens_assigned_learner(self):
        """AT-A1 is enrolled in cohort A, which instructor A teaches."""
        self.client.force_login(self.teacher_a)
        answer = self.client.get(self.profile_url("AT-A1"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "student")
        body = self.body(answer)
        self.assertIn("Assigned Learner", body)
        self.assertIn("assigned@example.test", body)
        self.assertIn("9 Morning Road", body)

    def test_instructor_a_opens_both_cohorts_learner(self):
        """AT-AB1 is enrolled in cohort A (and withdrawn in B): A's scope allows
        it because the enrollment in A is the link, whatever its status."""
        self.client.force_login(self.teacher_a)
        answer = self.client.get(self.profile_url("AT-AB1"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "student")
        self.assertIn("Both Cohorts Learner", self.body(answer))

    def test_instructor_a_denied_unassigned_learner(self):
        """AT-B1 is enrolled only in cohort B, which instructor A does not teach.
        The answer is this centre's not-found page, never a hint that a record
        exists (SEC-003)."""
        self.client.force_login(self.teacher_a)
        answer = self.client.get(self.profile_url("AT-B1"))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertNotIn("Unassigned Learner", self.body(answer))
        self.assertNotIn("unassigned@example.test", self.body(answer))

    def test_instructor_a_denied_no_cohort_learner(self):
        """AT-N1 has no enrollment at all: denied exactly as a number with no
        record here."""
        self.client.force_login(self.teacher_a)
        answer = self.client.get(self.profile_url("AT-N1"))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertNotIn("No Cohort Learner", self.body(answer))

    def test_instructor_a_list_narrowed_to_assigned_cohort(self):
        """The list page shows only the learners of cohort A: Assigned and Both
        Cohorts. Unassigned and No Cohort names never appear."""
        self.client.force_login(self.teacher_a)
        body = self.body(self.client.get(self.students_url()))
        self.assertIn("Assigned Learner", body)
        self.assertIn("Both Cohorts Learner", body)
        self.assertNotIn("Unassigned Learner", body)
        self.assertNotIn("No Cohort Learner", body)
        self.assertNotIn("unassigned@example.test", body)
        self.assertNotIn("none@example.test", body)

    def test_instructor_a_query_params_reveal_nothing(self):
        """Forged query strings on the list page never add a denied learner."""
        self.client.force_login(self.teacher_a)
        for query in ("?q=Unassigned", "?name=No+Cohort", "?number=AT-B1"):
            body = self.body(self.client.get(self.students_url() + query))
            self.assertNotIn("Unassigned Learner", body)
            self.assertNotIn("No Cohort Learner", body)

    def test_instructor_a_attendance_register_csv_scoped(self):
        """Session A belongs to cohort A: instructor A may export it. Session B
        belongs to cohort B: the CSV address answers this centre's not-found."""
        self.client.force_login(self.teacher_a)
        answer = self.client.get(self.register_url(str(self.session_a.pk)))
        self.assertEqual(answer.status_code, 200)
        self.assertIn("text/csv", answer["Content-Type"])
        csv_body = self.body(answer)
        self.assertIn("Assigned Learner", csv_body)
        self.assertIn("Both Cohorts Learner", csv_body)
        self.assertNotIn("Unassigned Learner", csv_body)
        answer = self.client.get(self.register_url(str(self.session_b.pk)))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertNotIn("Unassigned Learner", self.body(answer))

    def test_instructor_a_grading_assessment_denied(self):
        """Assessment B belongs to cohort B: _grade_scope denies it to
        instructor A exactly as the attendance scope does."""
        self.client.force_login(self.teacher_a)
        answer = self.client.get(
            "/o/alpha-center/center/grading/assessments/{}/".format(
                self.assessment_b.pk
            )
        )
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertNotIn("Evening Assessment", self.body(answer))
        self.assertNotIn("Both Cohorts Learner", self.body(answer))

    def test_instructor_a_cross_centre_denied(self):
        """Beta centre: instructor A has no membership there, so the address
        answers the project problem page, never a beta record."""
        self.client.force_login(self.teacher_a)
        answer = self.client.get(self.profile_url("AT-A", self.beta))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Beta Learner", self.body(answer))

    def test_instructor_a_post_to_manage_denied(self):
        """Instructor A has no students.manage permission: the manage page and
        every POST to it answer 403, and nothing is saved."""
        self.client.force_login(self.teacher_a)
        answer = self.client.get(self.manage_url())
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.post(
            self.manage_url(),
            {
                "students_action": "create",
                "student_number": "AT-99",
                "name": "Not Allowed",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(
            Student.objects.filter(organization=self.alpha).count(), 4
        )

    # -- instructor B: assigned cohort B only ----------------------------

    def test_instructor_b_opens_assigned_learner(self):
        self.client.force_login(self.teacher_b)
        answer = self.client.get(self.profile_url("AT-B1"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "student")
        self.assertIn("Unassigned Learner", self.body(answer))

    def test_instructor_b_opens_both_cohorts_learner(self):
        """AT-AB1 has a withdrawn enrollment in cohort B: the enrollment row is
        the link, so instructor B may read the profile."""
        self.client.force_login(self.teacher_b)
        answer = self.client.get(self.profile_url("AT-AB1"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "student")
        self.assertIn("Both Cohorts Learner", self.body(answer))

    def test_instructor_b_denied_cohort_a_learner(self):
        self.client.force_login(self.teacher_b)
        answer = self.client.get(self.profile_url("AT-A1"))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertNotIn("Assigned Learner", self.body(answer))
        self.assertNotIn("9 Morning Road", self.body(answer))

    def test_instructor_b_list_narrowed_to_cohort_b(self):
        self.client.force_login(self.teacher_b)
        body = self.body(self.client.get(self.students_url()))
        self.assertIn("Unassigned Learner", body)
        self.assertIn("Both Cohorts Learner", body)
        self.assertNotIn("Assigned Learner", body)
        self.assertNotIn("No Cohort Learner", body)
        self.assertNotIn("assigned@example.test", body)

    # -- instructor with no cohort assignment ----------------------------

    def test_lone_instructor_sees_no_students(self):
        """An instructor with no staff record and no cohort assignment sees an
        empty list and cannot open any profile."""
        self.client.force_login(self.lone_teacher)
        body = self.body(self.client.get(self.students_url()))
        self.assertIn("No student record has been made at this center yet.", body)
        self.assertNotIn("Assigned Learner", body)
        self.assertNotIn("Unassigned Learner", body)
        self.assertNotIn("No Cohort Learner", body)
        self.assertNotIn("Both Cohorts Learner", body)
        for number in ("AT-A1", "AT-B1", "AT-N1", "AT-AB1"):
            answer = self.client.get(self.profile_url(number))
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "not_found")

    # -- owner and receptionist keep centre-wide access ------------------

    def test_owner_sees_every_learner_in_list(self):
        self.client.force_login(self.owner)
        body = self.body(self.client.get(self.students_url()))
        for name in (
            "Assigned Learner",
            "Unassigned Learner",
            "No Cohort Learner",
            "Both Cohorts Learner",
        ):
            self.assertIn(name, body)

    def test_owner_opens_every_profile(self):
        self.client.force_login(self.owner)
        for number in ("AT-A1", "AT-B1", "AT-N1", "AT-AB1"):
            answer = self.client.get(self.profile_url(number))
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer["X-CohortDesk-Page"], "student")

    def test_receptionist_sees_every_learner_in_list(self):
        self.client.force_login(self.desk)
        body = self.body(self.client.get(self.students_url()))
        for name in (
            "Assigned Learner",
            "Unassigned Learner",
            "No Cohort Learner",
            "Both Cohorts Learner",
        ):
            self.assertIn(name, body)

    def test_receptionist_opens_every_profile(self):
        self.client.force_login(self.desk)
        for number in ("AT-A1", "AT-B1", "AT-N1", "AT-AB1"):
            answer = self.client.get(self.profile_url(number))
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer["X-CohortDesk-Page"], "student")

    def test_receptionist_enrollment_csv_allowed(self):
        """The desk role has academics.report.view: the enrollment CSV carries
        every centre row, unlike an instructor who is denied the CSV outright."""
        self.client.force_login(self.desk)
        answer = self.client.get(self.reports_url())
        self.assertEqual(answer.status_code, 200)
        self.assertIn("text/csv", answer["Content-Type"])
        csv_body = self.body(answer)
        self.assertIn("Assigned Learner", csv_body)
        self.assertIn("Unassigned Learner", csv_body)

    def test_instructor_denied_enrollment_csv(self):
        """Instructor A has no academics.report.view: the enrollment CSV answers
        403 denied, never a CSV body."""
        self.client.force_login(self.teacher_a)
        answer = self.client.get(self.reports_url())
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.assertNotIn("Assigned Learner", self.body(answer))
        self.assertNotIn("Unassigned Learner", self.body(answer))
