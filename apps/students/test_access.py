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

from django.test import TestCase

from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.roles import (
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)

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
