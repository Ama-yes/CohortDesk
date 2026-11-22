"""Staff isolation, roles and form contract proved through the URL layer.

AUTH-005/SEC-001/003/004, RULE 6.1 and STAFF-001..STAFF-006: the test client
asks config.urls itself, so every answer below is chosen by the address exactly
as a browser request would be, and every answer is checked by the page kind the
chain records. A staff record of one centre is never reachable from another
centre, even by guessing its staff number, and a number that does not name this
centre's record gets this centre's own not-found page.

The records are seeded with the model API where the test is about what an
address answers; actions that are part of the page contract (create, edit, set
a status, assign, remove) go through POST because that is the only path a
person has. Service rules themselves are proved in apps/staff/tests.py.
"""

from datetime import date

from django.test import TestCase

from apps.academics.models import Cohort, Course
from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.roles import (
    ROLE_ACADEMIC_COORDINATOR,
    ROLE_ADMINISTRATOR,
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)

from .models import CohortInstructor, CourseInstructor, Staff
from .statuses import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_INACTIVE,
    STATUS_ON_LEAVE,
)


class StaffAccessFixture(TestCase):
    """Two centers with a staff record each, and the roles under test."""

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
        Members.objects.create(
            user=self.alpha_owner, organization=self.alpha, role=ROLE_OWNER
        )
        Members.objects.create(
            user=self.beta_owner, organization=self.beta, role=ROLE_OWNER
        )
        self.admin = self.make_user("alpha-admin", ROLE_ADMINISTRATOR)
        self.coordinator = self.make_user("alpha-coord", ROLE_ACADEMIC_COORDINATOR)
        self.desk = self.make_user("alpha-desk", ROLE_RECEPTIONIST)
        self.teacher = self.make_user("alpha-teacher", ROLE_INSTRUCTOR)
        self.money = self.make_user("alpha-money", ROLE_FINANCE_OFFICER)
        self.alpha_course = Course.objects.create(
            organization=self.alpha, title="Alpha Spanish", status="active"
        )
        self.beta_course = Course.objects.create(
            organization=self.beta, title="Beta French", status="active"
        )
        self.alpha_cohort = Cohort.objects.create(
            organization=self.alpha,
            course=self.alpha_course,
            name="Alpha Autumn",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 20),
            capacity=10,
            status="active",
        )
        self.beta_cohort = Cohort.objects.create(
            organization=self.beta,
            course=self.beta_course,
            name="Beta Autumn",
            status="active",
        )
        self.alpha_staff = Staff.objects.create(
            organization=self.alpha,
            staff_number="T-001",
            name="Alpha Instructor",
            job_title="Tutor",
            employment_status=STATUS_ACTIVE,
        )
        self.alpha_leave = Staff.objects.create(
            organization=self.alpha,
            staff_number="T-002",
            name="Alpha On Leave",
            employment_status=STATUS_ON_LEAVE,
        )
        self.beta_staff = Staff.objects.create(
            organization=self.beta,
            staff_number="B-001",
            name="Beta Instructor",
            employment_status=STATUS_ACTIVE,
        )
        self.beta_teacher = self.make_member(
            "beta-teacher", ROLE_INSTRUCTOR, self.beta
        )

    def make_user(self, username, role):
        user = CohortUser.objects.create_user(username=username, password="x")
        Members.objects.create(user=user, organization=self.alpha, role=role)
        return user

    def make_member(self, username, role, organization):
        user = CohortUser.objects.create_user(username=username, password="x")
        Members.objects.create(
            user=user, organization=organization, role=role
        )
        return user

    # -- addresses -------------------------------------------------------

    def url(self, center=None):
        return "/o/{}/center/staff/".format((center or self.alpha).slug)

    def detail(self, number, center=None):
        return "{}{}/".format(self.url(center), number)

    def manage(self, center=None):
        return "{}manage/".format(self.url(center))

    def body(self, response):
        return response.content.decode()

    def post(self, data):
        return self.client.post(self.manage(), data)

    def self_address(self, center=None):
        return "/o/{}/center/self/T-001/".format((center or self.alpha).slug)

    def post_at(self, center, data):
        return self.client.post(self.manage(center), data)

    # -- lists and profiles never cross a tenant -------------------------

    def test_staff_list_carries_this_centres_rows_only(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.url())
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff")
        body = self.body(answer)
        self.assertIn("Alpha Instructor", body)
        self.assertIn("T-001", body)
        self.assertNotIn("Beta Instructor", body)
        self.assertNotIn("B-001", body)
        self.assertNotIn("/o/beta-center/", body)

    def test_staff_profile_shows_the_record_and_its_assignments(self):
        CourseInstructor.objects.create(
            organization=self.alpha,
            staff=self.alpha_staff,
            course=self.alpha_course,
        )
        CohortInstructor.objects.create(
            organization=self.alpha,
            staff=self.alpha_staff,
            cohort=self.alpha_cohort,
        )
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("T-001"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_member")
        body = self.body(answer)
        self.assertIn("Alpha Instructor", body)
        self.assertIn("Tutor", body)
        self.assertIn("Alpha Spanish", body)
        self.assertIn("Alpha Autumn", body)
        self.assertIn("This person teaches the cohort of no session shown here.", body)

    def test_another_centres_staff_number_is_this_centres_not_found(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("B-001"))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        body = self.body(answer)
        self.assertIn("No staff member with that number is here.", body)
        self.assertIn("Alpha Center", body)
        self.assertNotIn("Beta Instructor", body)

    def test_number_unknown_here_is_this_centres_own_not_found_page(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("T-999"))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertIn("No staff member with that number is here.", self.body(answer))

    def test_another_centres_address_is_the_unseen_centre_problem(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.url(self.beta))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Beta Instructor", self.body(answer))

    def test_signed_out_person_sees_no_staff_data(self):
        for address in (self.url(), self.detail("T-001"), self.manage()):
            answer = self.client.get(address)
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")
            self.assertNotIn("Alpha Instructor", self.body(answer))

    def test_suspended_membership_loses_the_staff_pages(self):
        self.client.force_login(self.desk)
        Members.objects.filter(user=self.desk, organization=self.alpha).update(
            status="suspended"
        )
        answer = self.client.get(self.url())
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_post_to_a_read_only_address_is_the_problem_page(self):
        self.client.force_login(self.alpha_owner)
        for address in (self.url(), self.detail("T-001")):
            answer = self.client.post(address, {})
            self.assertEqual(answer.status_code, 405)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    # -- roles -----------------------------------------------------------

    def test_owner_administrator_and_coordinator_read_staff(self):
        for user in (self.alpha_owner, self.admin, self.coordinator):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url()).status_code, 200)
            self.assertEqual(self.client.get(self.detail("T-001")).status_code, 200)

    def test_front_desk_instructor_and_finance_are_denied_staff(self):
        for user in (self.desk, self.teacher, self.money):
            self.client.force_login(user)
            answer = self.client.get(self.url())
            self.assertEqual(answer.status_code, 403)
            self.assertEqual(answer["X-CohortDesk-Page"], "denied")
            self.assertNotIn("Alpha Instructor", self.body(answer))

    def test_only_owner_and_administrator_get_the_management_page(self):
        for user in (self.alpha_owner, self.admin):
            self.client.force_login(user)
            answer = self.client.get(self.manage())
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer["X-CohortDesk-Page"], "staff_manage")
        self.client.force_login(self.coordinator)
        answer = self.client.get(self.manage())
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")

    def test_coordinator_cannot_create_a_staff_record_through_the_form(self):
        self.client.force_login(self.coordinator)
        answer = self.post(
            {"staff_action": "create", "staff_number": "T-010", "name": "No"}
        )
        self.assertEqual(answer.status_code, 403)
        self.assertFalse(Staff.objects.filter(staff_number="T-010").exists())

    # -- linking accounts through the form (Phase 3A) ---------------------

    def test_owner_links_the_instructor_through_the_form(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        record = Staff.objects.get(
            organization=self.alpha, staff_number="T-001"
        )
        self.assertEqual(record.user, self.teacher)
        self.assertEqual(record.employment_status, STATUS_ACTIVE)

    def test_a_link_grants_no_role_and_keeps_the_membership(self):
        # The link is a signpost only: the member keeps their own role, the
        # membership stays active, and the coordinator can see that a link
        # exists but never which account it points at.
        self.client.force_login(self.alpha_owner)
        self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        member = Members.objects.get(user=self.teacher, organization=self.alpha)
        self.assertEqual(member.role, ROLE_INSTRUCTOR)
        self.assertEqual(member.status, "active")
        self.client.force_login(self.coordinator)
        answer = self.client.get(self.detail("T-001"))
        self.assertEqual(answer.status_code, 200)
        body = self.body(answer)
        self.assertIn("Account linked to this staff member: Yes", body)
        self.assertNotIn("alpha-teacher", body)
        self.assertNotIn("alpha-owner", body)
        # Seeing presence is not managing: the coordinator is still refused.
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-002",
                "staff_account": str(self.desk.pk),
            }
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        first = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        self.assertEqual(first.user, self.teacher)
        second = Staff.objects.get(organization=self.alpha, staff_number="T-002")
        self.assertIsNone(second.user)

    def test_an_already_linked_record_is_not_linked_twice(self):
        self.client.force_login(self.alpha_owner)
        self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_account_problem"', self.body(answer))
        self.assertNotIn("Saved.", self.body(answer))
        record = Staff.objects.get(
            organization=self.alpha, staff_number="T-001"
        )
        self.assertEqual(record.user, self.teacher)

    def test_one_account_cannot_take_two_records_at_one_centre(self):
        self.client.force_login(self.alpha_owner)
        self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-002",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_account_problem"', self.body(answer))
        self.assertNotIn("Saved.", self.body(answer))
        second = Staff.objects.get(organization=self.alpha, staff_number="T-002")
        self.assertIsNone(second.user)
        first = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        self.assertEqual(first.user, self.teacher)

    def test_link_and_unlink_are_refused_through_the_form_by_other_roles(self):
        self.client.force_login(self.coordinator)
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        first = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        self.assertIsNone(first.user)
        self.client.force_login(self.desk)
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.client.force_login(self.teacher)
        answer = self.post(
            {
                "staff_action": "unlink",
                "staff_number": "T-001",
            }
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")

    def test_form_cannot_link_another_centres_account_or_staff_number(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.beta_owner.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_account_problem"', self.body(answer))
        self.assertNotIn("Saved.", self.body(answer))
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": "999999",
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_account_problem"', self.body(answer))
        self.assertNotIn("Saved.", self.body(answer))
        # A number that is not a record here is marked on its own row.
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-007",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_number_problem"', self.body(answer))
        self.assertNotIn("Saved.", self.body(answer))
        self.assertFalse(
            Staff.objects.filter(organization=self.alpha).exclude(user=None).exists()
        )

    def test_archived_or_inactive_staff_cannot_take_a_new_link(self):
        # Employment policy at the form layer: a record that is not active or
        # on leave is refused a new link, marked on the account row.
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "set_status",
                "staff_number": "T-001",
                "status": STATUS_INACTIVE,
            }
        )
        self.assertIn("Saved.", self.body(answer))
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_account_problem"', self.body(answer))
        self.assertNotIn("Saved.", self.body(answer))
        answer = self.post(
            {
                "staff_action": "set_status",
                "staff_number": "T-002",
                "status": STATUS_ARCHIVED,
            }
        )
        self.assertIn("Saved.", self.body(answer))
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-002",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_account_problem"', self.body(answer))
        self.assertNotIn("Saved.", self.body(answer))
        self.assertFalse(
            Staff.objects.filter(organization=self.alpha).exclude(user=None).exists()
        )

    def test_self_page_answers_only_about_the_viewer(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertIn("Saved.", self.body(answer))
        self.client.force_login(self.teacher)
        answer = self.client.get(self.self_address())
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        body = self.body(answer)
        self.assertIn("Alpha Instructor", body)
        self.assertNotIn("Beta Instructor", body)
        # A number typed into the address changes nothing: the viewer's own
        # record answers, never the one the address appears to name.
        answer = self.client.get(
            "/o/alpha-center/center/self/T-002/"
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        body = self.body(answer)
        self.assertIn("Alpha Instructor", body)
        self.assertNotIn("Alpha On Leave", body)

    def test_self_page_lists_own_assignments_and_anchors_courses_only(self):
        self.client.force_login(self.alpha_owner)
        self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "assign_course",
                "staff_number": "T-001",
                "target_number": str(self.alpha_course.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        answer = self.post(
            {
                "staff_action": "assign_cohort",
                "staff_number": "T-001",
                "target_number": str(self.alpha_cohort.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        self.client.force_login(self.teacher)
        answer = self.client.get(self.self_address())
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        body = self.body(answer)
        self.assertIn("Alpha Spanish", body)
        self.assertIn("Alpha Autumn", body)
        # An instructor may open their own courses but has no cohort pages,
        # so cohorts are listed here without a way to reach one.
        self.assertIn(
            'href="/o/alpha-center/center/courses/{pk}/"'.format(pk=self.alpha_course.pk),
            body,
        )
        self.assertNotIn('href="/o/alpha-center/center/cohorts/', body)

    def test_self_page_shows_nothing_but_the_prompt_for_an_unlinked_instructor(self):
        self.client.force_login(self.teacher)
        answer = self.client.get(self.self_address())
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        body = self.body(answer)
        self.assertIn("No staff record is linked to your account", body)
        self.assertNotIn("Alpha Instructor", body)
        self.assertNotIn("Alpha Spanish", body)
        # The bare address answers the same way, with nothing typed in it.
        answer = self.client.get("/o/alpha-center/center/self/")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        self.assertIn("No staff record is linked to your account", self.body(answer))

    def test_self_page_for_an_archived_instructor_says_it_is_not_listed(self):
        # Linked first, then archived: the read-only view then says the
        # record is not listed and shows no profile and no assignments.
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertIn("Saved.", self.body(answer))
        answer = self.post(
            {
                "staff_action": "set_status",
                "staff_number": "T-001",
                "status": STATUS_ARCHIVED,
            }
        )
        self.assertIn("Saved.", self.body(answer))
        self.client.force_login(self.teacher)
        answer = self.client.get(self.self_address())
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        body = self.body(answer)
        self.assertIn("This staff record is archived", body)
        self.assertNotIn("Alpha Instructor", body)
        self.assertNotIn("Alpha Spanish", body)

    def test_on_leave_and_inactive_instructors_still_see_their_own_record(self):
        self.client.force_login(self.alpha_owner)
        self.post(
            {
                "staff_action": "link",
                "staff_number": "T-002",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.client.force_login(self.teacher)
        answer = self.client.get(
            "/o/alpha-center/center/self/T-002/"
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        self.assertIn("Alpha On Leave", self.body(answer))
        # Inactive keeps the read-only view too (only archived does not).
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "set_status",
                "staff_number": "T-002",
                "status": STATUS_INACTIVE,
            }
        )
        self.assertIn("Saved.", self.body(answer))
        self.client.force_login(self.teacher)
        answer = self.client.get("/o/alpha-center/center/self/")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        self.assertIn("Alpha On Leave", self.body(answer))

    def test_another_instructor_cannot_reach_a_foreign_self_page(self):
        # beta's instructor is a member there and holds the role, so the
        # self page answers about beta only; alpha's account never reaches
        # beta's address, and the link at beta says nothing about alpha.
        self.client.force_login(self.beta_owner)
        answer = self.post_at(
            self.beta,
            {
                "staff_action": "link",
                "staff_number": "B-001",
                "staff_account": str(self.beta_teacher.pk),
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        self.client.force_login(self.beta_teacher)
        answer = self.client.get("/o/beta-center/center/self/")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        body = self.body(answer)
        self.assertIn("Beta Instructor", body)
        self.assertNotIn("Alpha Instructor", body)
        self.client.force_login(self.teacher)
        answer = self.client.get("/o/beta-center/center/self/")
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        # at alpha the list only says a link exists, and nothing else:
        self.client.force_login(self.coordinator)
        answer = self.client.get(self.detail("T-001"))
        self.assertEqual(answer.status_code, 200)
        body = self.body(answer)
        self.assertIn("Account linked to this staff member: Not linked yet", body)
        self.assertNotIn("beta-teacher", body)
        self.assertNotIn("Beta Instructor", body)
        self.assertNotIn("B-001", body)

    def test_unlink_through_the_form_moves_nothing_and_keeps_history(self):
        self.client.force_login(self.alpha_owner)
        self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "assign_course",
                "staff_number": "T-001",
                "target_number": str(self.alpha_course.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        answer = self.post(
            {
                "staff_action": "unlink",
                "staff_number": "T-001",
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        record = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        self.assertIsNone(record.user)
        self.assertEqual(record.employment_status, STATUS_ACTIVE)
        self.assertEqual(
            CourseInstructor.objects.filter(
                organization=self.alpha, staff=record
            ).count(),
            1,
        )
        self.client.force_login(self.teacher)
        answer = self.client.get("/o/alpha-center/center/self/")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        self.assertIn("No staff record is linked to your account", self.body(answer))

    def test_nav_links_follow_permissions_after_linking(self):
        # The self link appears for the linked instructor and disappears for
        # roles without staff.self.view; the nav table is never authorization.
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertIn("Saved.", self.body(answer))
        self.client.force_login(self.teacher)
        listed = self.body(
            self.client.get(
                "/o/{}/center/courses/{}/".format(
                    self.alpha.slug, self.alpha_course.pk
                )
            )
        )
        self.assertIn('href="/o/alpha-center/center/self/T-001/"', listed)
        self.client.force_login(self.coordinator)
        listed = self.body(self.client.get(self.url()))
        self.assertNotIn("/center/self/", listed)

    def test_nav_for_an_unlinked_instructor_names_the_bare_self_address(self):
        # An instructor with no record linked yet still gets the self link,
        # aimed at the address that answers with the prompt to link.
        self.client.force_login(self.beta_teacher)
        listed = self.body(
            self.client.get(
                "/o/{}/center/courses/{}/".format(
                    self.beta.slug, self.beta_course.pk
                )
            )
        )
        self.assertIn('href="/o/beta-center/center/self/"', listed)
        self.assertNotIn('href="/o/beta-center/center/self/B-001/"', listed)

    def test_roles_without_the_self_permission_are_denied_the_self_address(self):
        # The nav table hides the link from roles that cannot open it, which is
        # not authorization: the address itself must refuse too, and it must
        # answer nothing about a record that exists here.
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "link",
                "staff_number": "T-001",
                "staff_account": str(self.teacher.pk),
            }
        )
        self.assertIn("Saved.", self.body(answer))
        for outsider in (self.coordinator, self.desk, self.money):
            self.client.force_login(outsider)
            for address in (
                "/o/alpha-center/center/self/",
                self.self_address(),
            ):
                answer = self.client.get(address)
                self.assertEqual(answer.status_code, 403)
                self.assertEqual(answer["X-CohortDesk-Page"], "denied")
                body = self.body(answer)
                self.assertNotIn("Alpha Instructor", body)
                self.assertNotIn("T-001", body)

    # -- create, edit, status through the form ---------------------------

    def test_staff_is_created_on_the_same_page_and_then_listed(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "create",
                "staff_number": " t-010 ",
                "name": "  New Teacher ",
                "email": " new@example.test ",
                "job_title": " Language Tutor ",
                "specializations": " French ",
                "status": "active",
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_manage")
        self.assertIn("Saved.", self.body(answer))
        record = Staff.objects.get(organization=self.alpha, staff_number="T-010")
        self.assertEqual(record.name, "New Teacher")
        self.assertEqual(record.job_title, "Language Tutor")
        listed = self.body(self.client.get(self.url()))
        self.assertIn("New Teacher", listed)
        profile = self.body(self.client.get(self.detail("T-010")))
        self.assertIn("new@example.test", profile)
        self.assertIn("French", profile)

    def test_blank_name_is_marked_on_the_name_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {"staff_action": "create", "staff_number": "T-010", "name": "   "}
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="name_problem"', self.body(answer))
        self.assertFalse(Staff.objects.filter(staff_number="T-010").exists())

    def test_duplicate_staff_number_is_marked_on_its_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {"staff_action": "create", "staff_number": "T-001", "name": "Same"}
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_number_problem"', self.body(answer))
        self.assertEqual(
            Staff.objects.filter(
                organization=self.alpha, staff_number="T-001"
            ).count(),
            1,
        )

    def test_bad_staff_number_is_marked_on_its_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {"staff_action": "create", "staff_number": "bad number", "name": "X"}
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_number_problem"', self.body(answer))

    def test_edit_changes_one_part_and_the_profile_shows_it(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "edit",
                "staff_number": "T-001",
                "staff_field": "phone",
                "staff_value": " 0555 0101 ",
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        self.alpha_staff.refresh_from_db()
        self.assertEqual(self.alpha_staff.phone, "0555 0101")
        profile = self.body(self.client.get(self.detail("T-001")))
        self.assertIn("0555 0101", profile)

    def test_status_is_not_editable_through_the_edit_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "edit",
                "staff_number": "T-001",
                "staff_field": "employment_status",
                "staff_value": "archived",
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_field_problem"', self.body(answer))
        self.alpha_staff.refresh_from_db()
        self.assertEqual(self.alpha_staff.employment_status, STATUS_ACTIVE)

    def test_status_moves_and_an_illegal_move_is_marked(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "set_status",
                "staff_number": "T-001",
                "status": STATUS_ON_LEAVE,
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        self.alpha_staff.refresh_from_db()
        self.assertEqual(self.alpha_staff.employment_status, STATUS_ON_LEAVE)
        answer = self.post(
            {
                "staff_action": "set_status",
                "staff_number": "T-001",
                "status": STATUS_ARCHIVED,
            }
        )
        self.assertIn("Saved.", self.body(answer))
        answer = self.post(
            {
                "staff_action": "set_status",
                "staff_number": "T-001",
                "status": STATUS_ACTIVE,
            }
        )
        self.assertIn('id="status_problem"', self.body(answer))
        self.alpha_staff.refresh_from_db()
        self.assertEqual(self.alpha_staff.employment_status, STATUS_ARCHIVED)

    def test_unknown_staff_number_on_the_management_page_is_marked(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "set_status",
                "staff_number": "T-999",
                "status": STATUS_ON_LEAVE,
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_number_problem"', self.body(answer))

    def test_another_centres_staff_number_cannot_be_changed_here(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "set_status",
                "staff_number": "B-001",
                "status": STATUS_ARCHIVED,
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_number_problem"', self.body(answer))
        self.beta_staff.refresh_from_db()
        self.assertEqual(self.beta_staff.employment_status, STATUS_ACTIVE)

    # -- assignments through the form ------------------------------------

    def test_instructor_is_assigned_to_a_course_and_a_cohort(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "assign_course",
                "staff_number": "T-001",
                "target_number": str(self.alpha_course.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        answer = self.post(
            {
                "staff_action": "assign_cohort",
                "staff_number": "T-001",
                "target_number": str(self.alpha_cohort.pk),
            }
        )
        self.assertIn("Saved.", self.body(answer))
        self.assertEqual(CourseInstructor.objects.count(), 1)
        self.assertEqual(CohortInstructor.objects.count(), 1)
        profile = self.body(self.client.get(self.detail("T-001")))
        self.assertIn("Alpha Spanish", profile)
        self.assertIn("Alpha Autumn", profile)
        course = self.body(self.client.get(
            "/o/{}/center/courses/{}/".format(self.alpha.slug,
                                              self.alpha_course.pk)
        ))
        self.assertIn("Alpha Instructor", course)
        cohort = self.body(self.client.get(
            "/o/{}/center/cohorts/{}/".format(self.alpha.slug,
                                              self.alpha_cohort.pk)
        ))
        self.assertIn("Alpha Instructor", cohort)

    def test_a_staff_member_on_leave_cannot_be_assigned(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "assign_course",
                "staff_number": "T-002",
                "target_number": str(self.alpha_course.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_number_problem"', self.body(answer))
        self.assertEqual(CourseInstructor.objects.count(), 0)

    def test_duplicate_assignment_is_marked(self):
        CourseInstructor.objects.create(
            organization=self.alpha,
            staff=self.alpha_staff,
            course=self.alpha_course,
        )
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "assign_course",
                "staff_number": "T-001",
                "target_number": str(self.alpha_course.pk),
            }
        )
        self.assertIn('id="target_number_problem"', self.body(answer))
        self.assertEqual(CourseInstructor.objects.count(), 1)

    def test_unknown_staff_number_on_an_assignment_is_marked_on_its_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "assign_cohort",
                "staff_number": "T-404",
                "target_number": str(self.alpha_cohort.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="staff_number_problem"', self.body(answer))
        self.assertFalse(Staff.objects.filter(staff_number="T-404").exists())

    def test_another_centres_course_or_cohort_cannot_be_assigned(self):
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "assign_course",
                "staff_number": "T-001",
                "target_number": str(self.beta_course.pk),
            }
        )
        self.assertIn('id="target_number_problem"', self.body(answer))
        answer = self.post(
            {
                "staff_action": "assign_cohort",
                "staff_number": "T-001",
                "target_number": str(self.beta_cohort.pk),
            }
        )
        self.assertIn('id="target_number_problem"', self.body(answer))
        self.assertEqual(CourseInstructor.objects.count(), 0)
        self.assertEqual(CohortInstructor.objects.count(), 0)

    def test_assignment_is_removed(self):
        CourseInstructor.objects.create(
            organization=self.alpha,
            staff=self.alpha_staff,
            course=self.alpha_course,
        )
        self.client.force_login(self.alpha_owner)
        answer = self.post(
            {
                "staff_action": "remove_course",
                "staff_number": "T-001",
                "target_number": str(self.alpha_course.pk),
            }
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        self.assertEqual(CourseInstructor.objects.count(), 0)
        profile = self.body(self.client.get(self.detail("T-001")))
        self.assertNotIn("Alpha Spanish", profile)

    def test_coordinator_cannot_assign_through_the_form(self):
        self.client.force_login(self.coordinator)
        answer = self.post(
            {
                "staff_action": "assign_course",
                "staff_number": "T-001",
                "target_number": str(self.alpha_course.pk),
            }
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(CourseInstructor.objects.count(), 0)

    # -- search ----------------------------------------------------------

    def test_search_narrows_within_this_centre(self):
        Staff.objects.create(
            organization=self.alpha,
            staff_number="T-003",
            name="Second Teacher",
            employment_status=STATUS_ACTIVE,
        )
        self.client.force_login(self.alpha_owner)
        body = self.body(self.client.get(self.url() + "?q=Second"))
        self.assertIn("Second Teacher", body)
        self.assertNotIn("Alpha Instructor", body)
        body = self.body(self.client.get(self.url() + "?q=Beta"))
        self.assertIn("No staff record has been made at this center yet.", body)
        self.assertNotIn("Beta Instructor", body)
