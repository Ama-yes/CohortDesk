"""Course, cohort, program and academic period isolation proved through the URL layer.

AUTH-005/SEC-001/003/004, RULE 6.1 and COH-003: the test client asks
config.urls itself, so every answer below is chosen by the address exactly as
a browser request would be, and every answer is checked by the page kind the
chain records. A course, cohort or enrollment of one centre is never reachable
from another centre, even by guessing its database number, and a number that
does not name this centre's record gets this centre's own not-found page.

The records are seeded with the model API here on purpose: these tests are
about what the addresses answer, not about the service rules, which
apps/academics/tests.py proves. Actions that are part of the page contract
(create, edit, add a module, set a status, enroll) go through POST because that
is the only path a person has.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.accounts.models import CohortUser
from apps.staff.models import CohortInstructor, Staff
from apps.organizations.models import Members, Organization
from apps.organizations.roles import (
    ROLE_ACADEMIC_COORDINATOR,
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)
from apps.students.models import Student
from apps.students.statuses import STATUS_ACTIVE, STATUS_ARCHIVED

from .models import (
    AcademicPeriod,
    Attendance,
    AuditLog,
    Cohort,
    Course,
    CourseModule,
    Enrollment,
    Program,
    Session,
)
from .statuses import (
    ATTENDANCE_ABSENT,
    ATTENDANCE_EXCUSED,
    ATTENDANCE_LATE,
    ATTENDANCE_PRESENT,
    AUDIT_ATTENDANCE_CORRECTED,
    COHORT_ACTIVE,
    COHORT_COMPLETED,
    ENROLLMENT_WITHDRAWN,
)
from .services import create_session


class TwoCenters(TestCase):
    """Two centres, each with its own course, cohort and student."""

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
        self.coordinator = CohortUser.objects.create_user(
            username="alpha-coord", password="x", display_name="Alpha Coord"
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
            user=self.coordinator,
            organization=self.alpha,
            role=ROLE_ACADEMIC_COORDINATOR,
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
        self.alpha_course = Course.objects.create(
            organization=self.alpha,
            title="Alpha Spanish",
            default_fee=Decimal("100.00"),
            status="active",
        )
        self.beta_course = Course.objects.create(
            organization=self.beta,
            title="Beta French",
            status="active",
        )
        self.alpha_cohort = Cohort.objects.create(
            organization=self.alpha,
            course=self.alpha_course,
            name="Alpha Autumn",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 20),
            capacity=10,
            status=COHORT_ACTIVE,
        )
        self.beta_cohort = Cohort.objects.create(
            organization=self.beta,
            course=self.beta_course,
            name="Beta Autumn",
            status=COHORT_ACTIVE,
        )
        self.alpha_student = Student.objects.create(
            organization=self.alpha,
            student_number="AC-1",
            name="Alpha Learner",
            status=STATUS_ACTIVE,
        )
        self.beta_student = Student.objects.create(
            organization=self.beta,
            student_number="BC-1",
            name="Beta Learner",
            status=STATUS_ACTIVE,
        )
        self.alpha_enrollment = Enrollment.objects.create(
            organization=self.alpha,
            cohort=self.alpha_cohort,
            student=self.alpha_student,
            agreed_fee=Decimal("100.00"),
            fee_currency="DZD",
        )
        # Phase 3B: programs and academic periods (ACAD-004/ACAD-006), so the
        # addresses, roles and isolation below hold for them too.
        self.alpha_program = Program.objects.create(
            organization=self.alpha, title="Alpha reading program"
        )
        self.late_alpha_program = Program.objects.create(
            organization=self.alpha, title="Alpha early program"
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, title="Beta reading program"
        )
        self.alpha_course.program = self.alpha_program
        self.alpha_course.save(update_fields=["program"])
        self.alpha_period = AcademicPeriod.objects.create(
            organization=self.alpha,
            name="Alpha first term",
            start_date=date(2026, 1, 5),
            end_date=date(2026, 6, 30),
        )
        self.late_alpha_period = AcademicPeriod.objects.create(
            organization=self.alpha, name="Alpha planning window"
        )
        self.beta_period = AcademicPeriod.objects.create(
            organization=self.beta, name="Beta first term"
        )

    # -- addresses -------------------------------------------------------

    def url(self, kind, center=None):
        return "/o/{}/center/{}/".format((center or self.alpha).slug, kind)

    def detail(self, kind, number, center=None):
        return "{}{}/".format(self.url(kind, center), number)

    def manage(self, kind, center=None):
        return "{}manage/".format(self.url(kind, center))

    def body(self, response):
        return response.content.decode()

    # -- the same number means two records, never one -------------------

    def test_this_centres_course_is_here(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("courses", self.alpha_course.pk))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "course")
        body = self.body(answer)
        self.assertIn("Alpha Spanish", body)
        self.assertNotIn("Beta French", body)

    def test_another_centres_course_is_not_reachable_by_number(self):
        """A member of one centre never reaches another centre's course."""
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("courses", self.beta_course.pk))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        body = self.body(answer)
        self.assertIn("No course with that number is here.", body)
        self.assertIn("Alpha Center", body)
        self.assertNotIn("Beta French", body)

    def test_another_centres_cohort_is_not_reachable_by_number(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("cohorts", self.beta_cohort.pk))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertIn("No cohort with that number is here.", self.body(answer))
        self.assertNotIn("Beta Autumn", self.body(answer))

    def test_number_unknown_here_is_this_centres_own_not_found_page(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("courses", 99999))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertIn("No course with that number is here.", self.body(answer))

    def test_another_centres_address_is_the_unseen_centre_problem(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.url("courses", self.beta))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Beta French", self.body(answer))

    # -- lists never carry another centre -------------------------------

    def test_course_list_carries_this_centres_rows_only(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.url("courses"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "courses")
        body = self.body(answer)
        self.assertIn("Alpha Spanish", body)
        self.assertNotIn("Beta French", body)
        self.assertNotIn("/o/beta-center/", body)

    def test_cohort_list_carries_this_centres_rows_only(self):
        self.client.force_login(self.alpha_owner)
        body = self.body(self.client.get(self.url("cohorts")))
        self.assertIn("Alpha Autumn", body)
        self.assertNotIn("Beta Autumn", body)

    def test_enrollment_list_carries_this_centres_rows_only(self):
        self.client.force_login(self.alpha_owner)
        body = self.body(self.client.get(self.url("enrollments")))
        self.assertIn("Alpha Learner", body)
        self.assertNotIn("Beta Learner", body)

    def test_cohort_page_shows_only_its_own_enrollments(self):
        Student.objects.create(
            organization=self.alpha,
            student_number="AC-2",
            name="Other Alpha Learner",
            status=STATUS_ACTIVE,
        )
        enrollment = Enrollment.objects.create(
            organization=self.alpha,
            cohort=Cohort.objects.create(
                organization=self.alpha,
                course=self.alpha_course,
                name="Another Cohort",
            ),
            student=Student.objects.get(
                organization=self.alpha, student_number="AC-2"
            ),
        )
        self.client.force_login(self.alpha_owner)
        body = self.body(
            self.client.get(self.detail("cohorts", self.alpha_cohort.pk))
        )
        self.assertIn("Alpha Learner", body)
        self.assertNotIn("Other Alpha Learner", body)
        self.assertNotIn("enrollment-{}".format(enrollment.pk), body)

    def test_signed_out_person_sees_no_academic_data(self):
        for kind in ("courses", "cohorts", "enrollments"):
            answer = self.client.get(self.url(kind))
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")
            self.assertNotIn("Alpha Spanish", self.body(answer))
        answer = self.client.get(self.detail("courses", self.alpha_course.pk))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_suspended_membership_loses_the_academic_pages(self):
        self.client.force_login(self.desk)
        Members.objects.filter(user=self.desk, organization=self.alpha).update(
            status="suspended"
        )
        answer = self.client.get(self.url("enrollments"))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Alpha Learner", self.body(answer))

    # -- roles -----------------------------------------------------------

    def test_finance_is_denied_academic_data(self):
        self.client.force_login(self.money)
        for kind in ("courses", "cohorts", "enrollments"):
            answer = self.client.get(self.url(kind))
            self.assertEqual(answer.status_code, 403)
            self.assertEqual(answer["X-CohortDesk-Page"], "denied")
            self.assertNotIn("Alpha Spanish", self.body(answer))
            self.assertNotIn("Alpha Learner", self.body(answer))

    def test_instructor_reads_courses_but_not_cohorts_or_enrollments(self):
        """SRS 4.4 scopes instructors to assigned cohorts; assignment is absent."""
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(self.url("courses")).status_code, 200)
        self.assertEqual(
            self.client.get(
                self.detail("courses", self.alpha_course.pk)
            ).status_code,
            200,
        )
        for kind in ("cohorts", "enrollments"):
            answer = self.client.get(self.url(kind))
            self.assertEqual(answer.status_code, 403)
            self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.post(
            self.manage("courses"),
            {"courses_action": "create", "title": "Not Allowed"},
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(
            Course.objects.filter(organization=self.alpha).count(), 1
        )

    def test_front_desk_enrolls_but_cannot_manage_cohorts(self):
        self.client.force_login(self.desk)
        self.assertEqual(self.client.get(self.url("enrollments")).status_code, 200)
        self.assertEqual(self.client.get(self.url("cohorts")).status_code, 200)
        answer = self.client.get(self.manage("cohorts"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "create",
                "name": "Not Allowed",
                "course_number": str(self.alpha_course.pk),
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(
            Cohort.objects.filter(organization=self.alpha).count(), 1
        )

    def test_coordinator_manages_courses_and_cohorts(self):
        self.client.force_login(self.coordinator)
        for kind in ("courses", "cohorts", "enrollments"):
            self.assertEqual(self.client.get(self.manage(kind)).status_code, 200)

    # -- the read addresses answer a method they do not read -------------

    def test_post_to_a_read_only_address_is_the_problem_page(self):
        self.client.force_login(self.alpha_owner)
        addresses = [self.url(kind) for kind in
                     ("courses", "cohorts", "enrollments")]
        addresses.append(self.detail("courses", self.alpha_course.pk))
        addresses.append(self.detail("cohorts", self.alpha_cohort.pk))
        for address in addresses:
            answer = self.client.post(address, {})
            self.assertEqual(answer.status_code, 405)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    # -- courses through the form ---------------------------------------

    def test_course_is_created_on_the_same_page_and_then_listed(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("courses"),
            {
                "courses_action": "create",
                "title": "  Alpha French ",
                "description": "Evenings",
                "category": "Languages",
                "level": "A1",
                "duration": "10 weeks",
                "learning_objectives": "Order food.",
                "prerequisites": "None",
                "default_fee": "2500.00",
                "status": "active",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "courses_manage")
        self.assertIn("Saved.", self.body(answer))
        record = Course.objects.get(organization=self.alpha, title="Alpha French")
        self.assertEqual(record.status, "active")
        self.assertEqual(record.default_fee, Decimal("2500.00"))
        listed = self.body(self.client.get(self.url("courses")))
        self.assertIn("Alpha French", listed)
        profile = self.body(
            self.client.get(self.detail("courses", record.pk))
        )
        self.assertIn("2500.00 DZD", profile)

    def test_unparseable_fee_is_marked_on_the_fee_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("courses"),
            {
                "courses_action": "create",
                "title": "Bad Fee Course",
                "default_fee": "two thousand",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="default_fee_problem"', self.body(answer))
        self.assertFalse(
            Course.objects.filter(
                organization=self.alpha, title="Bad Fee Course"
            ).exists()
        )

    def test_blank_title_is_marked_on_the_title_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("courses"),
            {"courses_action": "create", "title": "   "},
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="title_problem"', self.body(answer))
        self.assertEqual(
            Course.objects.filter(organization=self.alpha).count(), 1
        )

    def test_edit_changes_one_part_and_the_profile_shows_it(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("courses"),
            {
                "courses_action": "edit",
                "courses_number": str(self.alpha_course.pk),
                "courses_field": "level",
                "courses_value": "B1",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.level, "B1")
        profile = self.body(
            self.client.get(self.detail("courses", self.alpha_course.pk))
        )
        self.assertIn("B1", profile)

    def test_status_is_not_editable_through_the_edit_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("courses"),
            {
                "courses_action": "edit",
                "courses_number": str(self.alpha_course.pk),
                "courses_field": "status",
                "courses_value": "archived",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="courses_field_problem"', self.body(answer))
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.status, "active")

    def test_module_is_added_and_shown_on_the_course(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("courses"),
            {
                "courses_action": "add_module",
                "courses_number": str(self.alpha_course.pk),
                "module_title": "Greetings",
                "module_description": "Hello and goodbye",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        profile = self.body(
            self.client.get(self.detail("courses", self.alpha_course.pk))
        )
        self.assertIn("Greetings", profile)
        self.assertIn("Hello and goodbye", profile)

    def test_course_status_moves_and_an_illegal_move_is_marked(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("courses"),
            {
                "courses_action": "set_status",
                "courses_number": str(self.alpha_course.pk),
                "status": "archived",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Saved.", self.body(answer))
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.status, "archived")
        answer = self.client.post(
            self.manage("courses"),
            {
                "courses_action": "set_status",
                "courses_number": str(self.alpha_course.pk),
                "status": "active",
            },
        )
        self.assertIn('id="status_problem"', self.body(answer))
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.status, "archived")

    def test_another_centres_course_number_cannot_be_edited(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("courses"),
            {
                "courses_action": "edit",
                "courses_number": str(self.beta_course.pk),
                "courses_field": "title",
                "courses_value": "Stolen",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="courses_number_problem"', self.body(answer))
        self.beta_course.refresh_from_db()
        self.assertEqual(self.beta_course.title, "Beta French")

    # -- cohorts through the form ---------------------------------------

    def test_cohort_is_created_for_this_centres_course(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "create",
                "name": "  Winter Intake ",
                "course_number": str(self.alpha_course.pk),
                "start_date": "2027-01-10",
                "end_date": "2027-03-20",
                "capacity": "15",
                "status": "planned",
                "note": "Evenings",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "cohorts_manage")
        self.assertIn("Saved.", self.body(answer))
        record = Cohort.objects.get(
            organization=self.alpha, name="Winter Intake"
        )
        self.assertEqual(record.start_date, date(2027, 1, 10))
        self.assertEqual(record.capacity, 15)
        self.assertEqual(record.course, self.alpha_course)
        listed = self.body(self.client.get(self.url("cohorts")))
        self.assertIn("Winter Intake", listed)

    def test_another_centres_course_cannot_make_a_cohort(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "create",
                "name": "Stolen Course Cohort",
                "course_number": str(self.beta_course.pk),
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="course_number_problem"', self.body(answer))
        self.assertFalse(
            Cohort.objects.filter(
                organization=self.alpha, name="Stolen Course Cohort"
            ).exists()
        )

    def test_end_before_start_is_marked_on_the_end_row(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "create",
                "name": "Backwards",
                "course_number": str(self.alpha_course.pk),
                "start_date": "2027-05-01",
                "end_date": "2027-01-01",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="end_date_problem"', self.body(answer))
        self.assertFalse(
            Cohort.objects.filter(
                organization=self.alpha, name="Backwards"
            ).exists()
        )

    def test_unparseable_date_is_the_pages_own_rejection(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "create",
                "name": "Bad Date",
                "course_number": str(self.alpha_course.pk),
                "start_date": "31/12/2026",
            },
        )
        self.assertIn('id="start_date_problem"', self.body(answer))
        self.assertFalse(
            Cohort.objects.filter(
                organization=self.alpha, name="Bad Date"
            ).exists()
        )

    def test_cohort_edit_and_lifecycle_move_through_the_page(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "edit",
                "cohorts_number": str(self.alpha_cohort.pk),
                "cohorts_field": "capacity",
                "cohorts_value": "12",
            },
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_cohort.refresh_from_db()
        self.assertEqual(self.alpha_cohort.capacity, 12)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "set_status",
                "cohorts_number": str(self.alpha_cohort.pk),
                "status": "completed",
            },
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_cohort.refresh_from_db()
        self.assertEqual(self.alpha_cohort.status, COHORT_COMPLETED)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "set_status",
                "cohorts_number": str(self.alpha_cohort.pk),
                "status": "active",
            },
        )
        self.assertIn('id="status_problem"', self.body(answer))

    def test_archived_course_refuses_a_new_cohort_through_the_page(self):
        Course.objects.filter(pk=self.alpha_course.pk).update(status="archived")
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "create",
                "name": "Late Intake",
                "course_number": str(self.alpha_course.pk),
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="course_number_problem"', self.body(answer))
        self.assertIn(
            "That course is archived, so a new cohort cannot be made for it.",
            self.body(answer),
        )

    # -- enrollments through the form -----------------------------------

    def test_enrollment_is_created_for_an_existing_student(self):
        newcomer = Student.objects.create(
            organization=self.alpha,
            student_number="AC-2",
            name="Newcomer Learner",
            status=STATUS_ACTIVE,
        )
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "enroll",
                "cohort_number": str(self.alpha_cohort.pk),
                "student_number": newcomer.student_number,
                "agreed_fee": "175.50",
                "status": "",
                "note": "Paid in two parts",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "enrollments_manage")
        self.assertIn("Saved.", self.body(answer))
        record = Enrollment.objects.get(
            organization=self.alpha, student=newcomer
        )
        self.assertEqual(record.agreed_fee, Decimal("175.50"))
        self.assertEqual(record.fee_currency, "DZD")
        listed = self.body(self.client.get(self.url("enrollments")))
        self.assertIn("Newcomer Learner", listed)

    def test_blank_fee_takes_the_course_default(self):
        newcomer = Student.objects.create(
            organization=self.alpha,
            student_number="AC-3",
            name="Default Fee Learner",
            status=STATUS_ACTIVE,
        )
        self.client.force_login(self.desk)
        self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "enroll",
                "cohort_number": str(self.alpha_cohort.pk),
                "student_number": newcomer.student_number,
                "agreed_fee": "",
            },
        )
        record = Enrollment.objects.get(
            organization=self.alpha, student=newcomer
        )
        self.assertEqual(record.agreed_fee, Decimal("100.00"))

    def test_duplicate_enrollment_is_marked_and_saves_nothing(self):
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "enroll",
                "cohort_number": str(self.alpha_cohort.pk),
                "student_number": self.alpha_student.student_number,
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="student_number_problem"', self.body(answer))
        self.assertIn(
            "That student is already enrolled in that cohort.",
            self.body(answer),
        )
        self.assertEqual(
            Enrollment.objects.filter(organization=self.alpha).count(), 1
        )

    def test_another_centres_cohort_and_student_are_never_enrolled(self):
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "enroll",
                "cohort_number": str(self.beta_cohort.pk),
                "student_number": self.alpha_student.student_number,
            },
        )
        self.assertIn('id="cohort_number_problem"', self.body(answer))
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "enroll",
                "cohort_number": str(self.alpha_cohort.pk),
                "student_number": self.beta_student.student_number,
            },
        )
        self.assertIn('id="student_number_problem"', self.body(answer))
        self.assertEqual(
            Enrollment.objects.filter(organization=self.alpha).count(), 1
        )

    def test_archived_student_is_refused_through_the_page(self):
        archived = Student.objects.create(
            organization=self.alpha,
            student_number="AC-9",
            name="Archived Learner",
            status=STATUS_ARCHIVED,
        )
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "enroll",
                "cohort_number": str(self.alpha_cohort.pk),
                "student_number": archived.student_number,
            },
        )
        self.assertIn('id="student_number_problem"', self.body(answer))
        self.assertIn(
            "That student record is archived, so it cannot be enrolled.",
            self.body(answer),
        )

    def test_closed_cohort_takes_no_enrollment_through_the_page(self):
        Cohort.objects.filter(pk=self.alpha_cohort.pk).update(
            status=COHORT_COMPLETED
        )
        newcomer = Student.objects.create(
            organization=self.alpha,
            student_number="AC-4",
            name="Late Learner",
            status=STATUS_ACTIVE,
        )
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "enroll",
                "cohort_number": str(self.alpha_cohort.pk),
                "student_number": newcomer.student_number,
            },
        )
        self.assertIn('id="cohort_number_problem"', self.body(answer))
        self.assertIn(
            "That cohort is completed or cancelled, so no student can be "
            "enrolled in it.",
            self.body(answer),
        )

    def test_capacity_policy_is_the_centres_own_choice_through_the_page(self):
        Cohort.objects.filter(pk=self.alpha_cohort.pk).update(capacity=1)
        newcomer = Student.objects.create(
            organization=self.alpha,
            student_number="AC-5",
            name="Second Learner",
            status=STATUS_ACTIVE,
        )
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "enroll",
                "cohort_number": str(self.alpha_cohort.pk),
                "student_number": newcomer.student_number,
            },
        )
        self.assertIn('id="cohort_number_problem"', self.body(answer))
        self.assertIn("That cohort is full. Nothing was enrolled.", self.body(answer))
        self.assertEqual(
            Enrollment.objects.filter(
                organization=self.alpha, cohort=self.alpha_cohort
            ).count(),
            1,
        )
        Organization.objects.filter(pk=self.alpha.pk).update(
            capacity_policy="warn"
        )
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "enroll",
                "cohort_number": str(self.alpha_cohort.pk),
                "student_number": newcomer.student_number,
            },
        )
        self.assertIn("warns instead of preventing", self.body(answer))
        self.assertEqual(
            Enrollment.objects.filter(
                organization=self.alpha, cohort=self.alpha_cohort
            ).count(),
            2,
        )

    def test_enrollment_status_moves_and_reinstatement_is_checked(self):
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "set_status",
                "enrollments_number": str(self.alpha_enrollment.pk),
                "status": ENROLLMENT_WITHDRAWN,
            },
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_enrollment.refresh_from_db()
        self.assertEqual(self.alpha_enrollment.status, ENROLLMENT_WITHDRAWN)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "set_status",
                "enrollments_number": str(self.alpha_enrollment.pk),
                "status": "enrolled",
            },
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_enrollment.refresh_from_db()
        self.assertEqual(self.alpha_enrollment.status, "enrolled")

    def test_illegal_enrollment_status_is_marked(self):
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "set_status",
                "enrollments_number": str(self.alpha_enrollment.pk),
                "status": "completed",
            },
        )
        self.assertIn("Saved.", self.body(answer))
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "set_status",
                "enrollments_number": str(self.alpha_enrollment.pk),
                "status": "cancelled",
            },
        )
        self.assertIn('id="status_problem"', self.body(answer))

    def test_another_centres_enrollment_number_is_not_moved(self):
        beta_enrollment = Enrollment.objects.create(
            organization=self.beta,
            cohort=self.beta_cohort,
            student=self.beta_student,
        )
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("enrollments"),
            {
                "enrollments_action": "set_status",
                "enrollments_number": str(beta_enrollment.pk),
                "status": ENROLLMENT_WITHDRAWN,
            },
        )
        self.assertIn('id="enrollments_number_problem"', self.body(answer))
        beta_enrollment.refresh_from_db()
        self.assertEqual(beta_enrollment.status, "enrolled")

    def test_enrollment_manage_page_states_the_fee_rule(self):
        self.client.force_login(self.desk)
        answer = self.client.get(self.manage("enrollments"))
        self.assertEqual(answer.status_code, 200)
        self.assertIn(
            "If no agreed fee is filled",
            self.body(answer),
        )

    # -- programs and academic periods through the form ------------------

    def test_a_program_is_made_and_listed_with_its_own_courses_only(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("programs"),
            {
                "programs_action": "create",
                "title": "  Alpha writing program ",
                "description": "Daily writing",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "programs_manage")
        self.assertIn("Saved.", self.body(answer))
        record = Program.objects.get(
            organization=self.alpha, title="Alpha writing program"
        )
        self.assertEqual(record.description, "Daily writing")
        listed = self.body(self.client.get(self.url("programs")))
        self.assertIn("Alpha writing program", listed)
        self.assertNotIn("Beta reading program", listed)

    def test_a_program_needs_a_title_and_the_title_has_a_limit(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("programs"),
            {"programs_action": "create", "title": "   "},
        )
        self.assertIn('id="title_problem"', self.body(answer))
        self.assertIn("A program must have a title.", self.body(answer))
        self.assertIn("Not saved.", self.body(answer))
        answer = self.client.post(
            self.manage("programs"),
            {"programs_action": "create", "title": "t" * 151},
        )
        self.assertIn('id="title_problem"', self.body(answer))
        self.assertIn("at most 150 characters", self.body(answer))
        self.assertEqual(
            Program.objects.filter(organization=self.alpha).count(), 2
        )

    def test_one_part_of_a_program_changes_and_its_made_on_part_does_not(self):
        self.client.force_login(self.coordinator)
        answer = self.client.post(
            self.manage("programs"),
            {
                "programs_action": "edit",
                "programs_number": str(self.alpha_program.pk),
                "programs_field": "description",
                "programs_value": "Now evenings",
            },
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_program.refresh_from_db()
        self.assertEqual(self.alpha_program.description, "Now evenings")
        answer = self.client.post(
            self.manage("programs"),
            {
                "programs_action": "edit",
                "programs_number": str(self.alpha_program.pk),
                "programs_field": "created_at",
                "programs_value": "2026-01-01",
            },
        )
        self.assertIn('id="programs_field_problem"', self.body(answer))
        self.assertIn("Not saved.", self.body(answer))

    def test_a_course_is_put_into_a_program_then_taken_out(self):
        """ACAD-004: courses can be grouped, and the grouping can be removed."""
        self.client.force_login(self.coordinator)
        answer = self.client.post(
            self.manage("programs"),
            {
                "programs_action": "set_course",
                "programs_number": str(self.late_alpha_program.pk),
                "course_number": str(self.alpha_course.pk),
            },
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.program, self.late_alpha_program)
        moved = self.body(
            self.client.get(self.detail("programs", self.late_alpha_program.pk))
        )
        self.assertIn("Alpha Spanish", moved)
        self.assertIn("Courses in this program", moved)
        self.assertNotIn("Beta French", moved)
        answer = self.client.post(
            self.manage("programs"),
            {
                "programs_action": "set_course",
                "programs_number": str(self.late_alpha_program.pk),
                "course_number": str(self.alpha_course.pk),
            },
        )
        self.assertIn('id="course_number_problem"', self.body(answer))
        self.assertIn(
            "That course already belongs to that program.", self.body(answer)
        )
        answer = self.client.post(
            self.manage("programs"),
            {"programs_action": "unassign", "course_number": str(self.alpha_course.pk)},
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_course.refresh_from_db()
        self.assertIsNone(self.alpha_course.program_id)
        answer = self.client.post(
            self.manage("programs"),
            {"programs_action": "unassign", "course_number": str(self.alpha_course.pk)},
        )
        self.assertIn('id="course_number_problem"', self.body(answer))
        self.assertIn(
            "That course does not belong to a program here.", self.body(answer)
        )

    def test_program_actions_never_move_another_centres_course(self):
        """RULE 6.1: both numbers are this centre's own or nothing is changed."""
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("programs"),
            {
                "programs_action": "set_course",
                "programs_number": str(self.alpha_program.pk),
                "course_number": str(self.beta_course.pk),
            },
        )
        self.assertIn('id="course_number_problem"', self.body(answer))
        self.assertIn("No course with that number is here.", self.body(answer))
        answer = self.client.post(
            self.manage("programs"),
            {
                "programs_action": "set_course",
                "programs_number": str(self.beta_program.pk),
                "course_number": str(self.alpha_course.pk),
            },
        )
        self.assertIn('id="programs_number_problem"', self.body(answer))
        self.assertIn(
            "No program with that number is here.", self.body(answer)
        )
        answer = self.client.post(
            self.manage("programs"),
            {"programs_action": "unassign", "course_number": str(self.beta_course.pk)},
        )
        self.assertIn('id="programs_number_problem"', self.body(answer))
        self.assertIn(
            "No program with that number is here.", self.body(answer)
        )
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.program, self.alpha_program)
        self.assertFalse(Course.objects.filter(program=self.beta_program).exists())

    def test_a_cohort_is_linked_to_a_program_and_unlinked(self):
        self.client.force_login(self.coordinator)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "set_program",
                "cohorts_number": str(self.alpha_cohort.pk),
                "program_number": str(self.alpha_program.pk),
            },
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_cohort.refresh_from_db()
        self.assertEqual(self.alpha_cohort.program, self.alpha_program)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "set_program",
                "cohorts_number": str(self.alpha_cohort.pk),
                "program_number": str(self.alpha_program.pk),
            },
        )
        self.assertIn('id="program_number_problem"', self.body(answer))
        self.assertIn(
            "That cohort already belongs to that program.", self.body(answer)
        )
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "set_program",
                "cohorts_number": str(self.alpha_cohort.pk),
                "program_number": "",
            },
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_cohort.refresh_from_db()
        self.assertIsNone(self.alpha_cohort.program_id)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "set_program",
                "cohorts_number": str(self.alpha_cohort.pk),
                "program_number": "",
            },
        )
        self.assertIn('id="program_number_problem"', self.body(answer))
        self.assertIn(
            "That cohort belongs to no program, so nothing was unlinked.",
            self.body(answer),
        )

    def test_a_cohort_never_links_to_another_centres_program(self):
        self.client.force_login(self.coordinator)
        answer = self.client.post(
            self.manage("cohorts"),
            {
                "cohorts_action": "set_program",
                "cohorts_number": str(self.alpha_cohort.pk),
                "program_number": str(self.beta_program.pk),
            },
        )
        self.assertIn('id="program_number_problem"', self.body(answer))
        self.assertIn(
            "No program with that number is here.", self.body(answer)
        )
        self.assertIsNone(self.alpha_cohort.program_id)
        self.assertEqual(
            Cohort.objects.filter(organization=self.beta).count(), 1
        )

    def test_a_period_is_made_and_listed_with_its_dates(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("periods"),
            {
                "periods_action": "create",
                "name": " Spring term ",
                "start_date": "2027-02-01",
                "end_date": "2027-05-30",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "periods_manage")
        self.assertIn("Saved.", self.body(answer))
        record = AcademicPeriod.objects.get(
            organization=self.alpha, name="Spring term"
        )
        self.assertEqual(record.start_date, date(2027, 2, 1))
        self.assertEqual(record.end_date, date(2027, 5, 30))
        listed = self.body(self.client.get(self.url("periods")))
        self.assertIn("Spring term", listed)
        self.assertIn("2027-02-01", listed)
        self.assertNotIn("Beta first term", listed)

    def test_a_period_needs_a_name_and_dates_that_are_dates_in_order(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("periods"),
            {
                "periods_action": "create",
                "name": "Bad Date",
                "start_date": "31/12/2027",
            },
        )
        self.assertIn('id="start_date_problem"', self.body(answer))
        self.assertIn("Not saved.", self.body(answer))
        self.assertFalse(
            AcademicPeriod.objects.filter(
                organization=self.alpha, name="Bad Date"
            ).exists()
        )
        answer = self.client.post(
            self.manage("periods"),
            {
                "periods_action": "create",
                "name": "Backwards term",
                "start_date": "2027-05-01",
                "end_date": "2027-01-01",
            },
        )
        self.assertIn('id="end_date_problem"', self.body(answer))
        self.assertIn("cannot end before it starts", self.body(answer))
        self.assertFalse(
            AcademicPeriod.objects.filter(
                organization=self.alpha, name="Backwards term"
            ).exists()
        )
        answer = self.client.post(
            self.manage("periods"),
            {"periods_action": "create", "name": "   ", "start_date": ""},
        )
        self.assertIn('id="name_problem"', self.body(answer))
        self.assertIn("An academic period must have a name.", self.body(answer))

    def test_one_part_of_a_period_changes_and_its_dates_must_not_cross(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("periods"),
            {
                "periods_action": "edit",
                "periods_number": str(self.alpha_period.pk),
                "periods_field": "end_date",
                "periods_value": "2026-06-15",
            },
        )
        self.assertIn("Saved.", self.body(answer))
        self.alpha_period.refresh_from_db()
        self.assertEqual(self.alpha_period.end_date, date(2026, 6, 15))
        answer = self.client.post(
            self.manage("periods"),
            {
                "periods_action": "edit",
                "periods_number": str(self.alpha_period.pk),
                "periods_field": "start_date",
                "periods_value": "2026-09-01",
            },
        )
        self.assertIn('id="periods_value_problem"', self.body(answer))
        self.assertIn("cannot end before it starts", self.body(answer))
        self.alpha_period.refresh_from_db()
        self.assertEqual(
            (self.alpha_period.start_date, self.alpha_period.end_date),
            (date(2026, 1, 5), date(2026, 6, 15)),
        )

    def test_period_actions_never_move_another_centres_period(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("periods"),
            {
                "periods_action": "edit",
                "periods_number": str(self.beta_period.pk),
                "periods_field": "name",
                "periods_value": "Stolen term",
            },
        )
        self.assertIn('id="periods_number_problem"', self.body(answer))
        self.assertIn(
            "No academic period with that number is here.", self.body(answer)
        )
        self.beta_period.refresh_from_db()
        self.assertEqual(self.beta_period.name, "Beta first term")

    def test_the_period_manage_page_refuses_a_made_on_part_being_edited(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("periods"),
            {
                "periods_action": "edit",
                "periods_number": str(self.alpha_period.pk),
                "periods_field": "organization",
                "periods_value": "beta-center",
            },
        )
        self.assertIn('id="periods_field_problem"', self.body(answer))

    # -- programs and academic periods: the addresses answer -------------

    def test_this_centres_program_is_here(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("programs", self.alpha_program.pk))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "program")
        body = self.body(answer)
        self.assertIn("Alpha reading program", body)
        self.assertNotIn("Beta reading program", body)
        self.assertNotIn("Beta French", body)

    def test_this_centres_period_is_here_with_its_dates(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("periods", self.alpha_period.pk))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "period")
        body = self.body(answer)
        self.assertIn("Alpha first term", body)
        self.assertIn("2026-01-05", body)
        self.assertIn("2026-06-30", body)
        self.assertNotIn("Beta first term", body)

    def test_this_centres_program_and_period_lists_show_their_own_rows(self):
        self.client.force_login(self.alpha_owner)
        body = self.body(self.client.get(self.url("programs")))
        self.assertIn("Alpha reading program", body)
        self.assertIn("Alpha early program", body)
        self.assertNotIn("Beta reading program", body)
        body = self.body(self.client.get(self.url("periods")))
        self.assertIn("Alpha first term", body)
        self.assertIn("Alpha planning window", body)
        self.assertNotIn("Beta first term", body)

    def test_newest_programs_and_periods_come_first_in_the_lists(self):
        self.client.force_login(self.alpha_owner)
        programs = self.body(self.client.get(self.url("programs")))
        self.assertLess(
            programs.index("Alpha early program"),
            programs.index("Alpha reading program"),
        )
        periods = self.body(self.client.get(self.url("periods")))
        self.assertLess(
            periods.index("Alpha planning window"),
            periods.index("Alpha first term"),
        )

    def test_period_lists_show_the_dates_they_have_or_say_not_recorded(self):
        self.client.force_login(self.alpha_owner)
        body = self.body(self.client.get(self.url("periods")))
        self.assertIn("2026-01-05", body)
        self.assertIn("2026-06-30", body)
        self.assertIn("Not recorded", body)

    def test_the_courses_of_a_program_are_shown_on_its_own_page(self):
        self.client.force_login(self.coordinator)
        body = self.body(
            self.client.get(self.detail("programs", self.alpha_program.pk))
        )
        self.assertIn("Courses in this program", body)
        self.assertIn("Alpha Spanish", body)
        self.assertNotIn("Beta French", body)

    def test_a_program_with_no_courses_says_so_on_its_page(self):
        self.client.force_login(self.coordinator)
        body = self.body(
            self.client.get(self.detail("programs", self.late_alpha_program.pk))
        )
        self.assertIn("No course belongs to this program yet.", body)

    def test_the_course_list_shows_the_program_a_course_belongs_to(self):
        self.client.force_login(self.coordinator)
        self.assertIn(
            "Alpha reading program", self.body(self.client.get(self.url("courses")))
        )

    def test_a_number_this_centre_has_no_record_for_is_its_own_not_found(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("programs", 99999))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertIn("No program with that number is here.", self.body(answer))
        answer = self.client.get(self.detail("periods", 99999))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertIn(
            "No academic period with that number is here.", self.body(answer)
        )

    def test_another_centres_program_or_period_is_not_reachable_by_number(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.detail("programs", self.beta_program.pk))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertIn("No program with that number is here.", self.body(answer))
        self.assertNotIn("Beta reading program", self.body(answer))
        answer = self.client.get(self.detail("periods", self.beta_period.pk))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertIn(
            "No academic period with that number is here.", self.body(answer)
        )
        self.assertNotIn("Alpha first term", self.body(answer))

    def test_another_centres_program_or_period_address_is_the_unseen_problem(self):
        self.client.force_login(self.alpha_owner)
        for kind in ("programs", "periods"):
            answer = self.client.get(self.url(kind, self.beta))
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")
            answer = self.client.get(
                self.detail(kind, self.alpha_program.pk, self.beta)
            )
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_signed_out_person_sees_no_program_or_period_data(self):
        for kind in ("programs", "periods"):
            answer = self.client.get(self.url(kind))
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")
            self.assertNotIn("Alpha reading program", self.body(answer))
            self.assertNotIn("Alpha first term", self.body(answer))
        answer = self.client.get(self.detail("programs", self.alpha_program.pk))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_suspended_membership_loses_the_program_and_period_pages(self):
        self.client.force_login(self.desk)
        Members.objects.filter(user=self.desk, organization=self.alpha).update(
            status="suspended"
        )
        answer = self.client.get(self.url("programs"))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Alpha reading program", self.body(answer))

    def test_the_program_and_period_addresses_that_read_answer_post_nothing(self):
        self.client.force_login(self.alpha_owner)
        addresses = [
            self.url(kind) for kind in ("programs", "periods")
        ] + [
            self.detail("programs", self.alpha_program.pk),
            self.detail("periods", self.alpha_period.pk),
        ]
        for address in addresses:
            answer = self.client.post(address, {})
            self.assertEqual(answer.status_code, 405)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    # -- programs and academic periods: who is offered what --------------

    def test_the_front_desk_reads_programs_but_cannot_make_one(self):
        self.client.force_login(self.desk)
        self.assertEqual(self.client.get(self.url("programs")).status_code, 200)
        answer = self.client.get(self.manage("programs"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.post(
            self.manage("programs"),
            {"programs_action": "create", "title": "Not Allowed"},
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(
            Program.objects.filter(organization=self.alpha).count(), 2
        )

    def test_the_front_desk_reads_periods_but_cannot_make_one(self):
        self.client.force_login(self.desk)
        self.assertEqual(self.client.get(self.url("periods")).status_code, 200)
        self.assertEqual(self.client.get(self.manage("periods")).status_code, 403)
        self.assertEqual(
            AcademicPeriod.objects.filter(organization=self.alpha).count(), 2
        )

    def test_an_instructor_reads_programs_but_not_period_lists(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(self.url("programs")).status_code, 200)
        self.assertEqual(
            self.client.get(
                self.detail("programs", self.alpha_program.pk)
            ).status_code,
            200,
        )
        answer = self.client.get(self.url("periods"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        for kind in ("programs", "periods"):
            answer = self.client.get(self.manage(kind))
            self.assertEqual(answer.status_code, 403)
            self.assertEqual(answer["X-CohortDesk-Page"], "denied")

    def test_a_coordinator_makes_programs_but_never_periods(self):
        self.client.force_login(self.coordinator)
        self.assertEqual(self.client.get(self.manage("programs")).status_code, 200)
        answer = self.client.post(
            self.manage("programs"),
            {"programs_action": "create", "title": "Coordinator program"},
        )
        self.assertIn("Saved.", self.body(answer))
        answer = self.client.post(
            self.manage("periods"),
            {"periods_action": "create", "name": "Not Allowed term"},
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.assertEqual(
            AcademicPeriod.objects.filter(organization=self.alpha).count(), 2
        )

    def test_the_finance_officer_is_denied_programs_and_periods(self):
        self.client.force_login(self.money)
        for kind in ("programs", "periods"):
            for address in (self.url(kind), self.manage(kind)):
                answer = self.client.get(address)
                self.assertEqual(answer.status_code, 403)
                self.assertEqual(answer["X-CohortDesk-Page"], "denied")
            self.assertNotIn(
                "Alpha reading program",
                self.body(self.client.get(self.url(kind))),
            )
            self.assertNotIn(
                "Alpha first term", self.body(self.client.get(self.url(kind)))
            )

    def test_the_program_manage_page_offers_its_own_actions(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.manage("programs"))
        self.assertEqual(answer.status_code, 200)
        body = self.body(answer)
        self.assertIn("Add or change a program", body)
        for words in (
            "Make a program",
            "Change one part of a program",
            "Put a course into a program",
            "Take a course out of a program",
        ):
            self.assertIn(words, body)

    def test_the_period_manage_page_offers_its_own_actions(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.manage("periods"))
        self.assertEqual(answer.status_code, 200)
        body = self.body(answer)
        self.assertIn("Add or change an academic period", body)
        self.assertIn("Make an academic period", body)
        self.assertIn("Change one part of an academic period", body)

    def test_the_cohorts_page_offers_putting_a_cohort_into_a_program(self):
        self.client.force_login(self.coordinator)
        body = self.body(self.client.get(self.manage("cohorts")))
        self.assertIn("Set the program a cohort belongs to", body)

    # -- programs and academic periods: empty centres and navigation -----

    def test_an_empty_centre_says_its_programs_and_periods_are_missing(self):
        empty = Organization.objects.create(
            name="Empty Center", slug="empty-center", primary_currency="USD"
        )
        user = CohortUser.objects.create_user(username="empty-owner", password="x")
        Members.objects.create(user=user, organization=empty, role=ROLE_OWNER)
        self.client.force_login(user)
        self.assertIn(
            "No program has been made at this center yet.",
            self.body(self.client.get(self.url("programs", empty))),
        )
        self.assertIn(
            "No academic period has been made at this center yet.",
            self.body(self.client.get(self.url("periods", empty))),
        )

    def test_the_centre_navigation_names_programs_and_periods(self):
        self.client.force_login(self.alpha_owner)
        body = self.body(self.client.get("/o/alpha-center/center/dashboard/"))
        self.assertIn(self.url("programs"), body)
        self.assertIn(self.url("periods"), body)
        self.assertIn('data-kind="programs"', body)
        self.assertIn('data-kind="periods"', body)

    # -- empty centres and navigation -----------------------------------

    def test_empty_centres_say_so(self):
        empty = Organization.objects.create(
            name="Empty Center", slug="empty-center", primary_currency="USD"
        )
        user = CohortUser.objects.create_user(username="empty-owner", password="x")
        Members.objects.create(user=user, organization=empty, role=ROLE_OWNER)
        self.client.force_login(user)
        self.assertIn(
            "No course has been made at this center yet.",
            self.body(self.client.get(self.url("courses", empty))),
        )
        self.assertIn(
            "No cohort has been made at this center yet.",
            self.body(self.client.get(self.url("cohorts", empty))),
        )
        self.assertIn(
            "No student has been enrolled at this center yet.",
            self.body(self.client.get(self.url("enrollments", empty))),
        )

    def test_tenant_navigation_names_courses_cohorts_and_enrollments(self):
        self.client.force_login(self.alpha_owner)
        body = self.body(
            self.client.get("/o/alpha-center/center/dashboard/")
        )
        self.assertIn(self.url("courses"), body)
        self.assertIn(self.url("cohorts"), body)
        self.assertIn(self.url("enrollments"), body)
        self.assertIn('data-kind="courses"', body)
        self.assertIn('data-kind="cohorts"', body)
        self.assertIn('data-kind="enrollments"', body)

    # -- academic reports (Phase 3C: ACAD-006/REP-003/REP-004/REP-005) ----

    def report_url(self, query="", center=None):
        base = self.url("reports", center)
        return base + ("?" + query if query else "")

    def test_the_report_page_lists_this_centres_enrollments_and_capacity(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.report_url())
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "reports")
        body = self.body(answer)
        self.assertIn("Alpha Learner", body)
        self.assertIn("Alpha Autumn", body)
        self.assertIn("Alpha Spanish", body)
        self.assertNotIn("Beta Learner", body)
        self.assertNotIn("Beta Autumn", body)
        self.assertNotIn("Beta French", body)
        self.assertIn('data-kind="reports_csv"', body)

    def test_the_period_filter_groups_by_the_stored_start_date(self):
        self.client.force_login(self.alpha_owner)
        # The alpha period covers January to June; the cohort starts in
        # September, so this period has no matching row.
        answer = self.client.get(
            self.report_url("period={}".format(self.alpha_period.pk))
        )
        self.assertEqual(answer.status_code, 200)
        body = self.body(answer)
        self.assertIn("No enrollment matches this report.", body)
        self.assertIn("No cohort matches this report.", body)
        # The undated period contains everything, because it has no boundary.
        body = self.body(
            self.client.get(
                self.report_url("period={}".format(self.late_alpha_period.pk))
            )
        )
        self.assertIn("Alpha Learner", body)
        self.assertIn("2026-09-01", body)

    def test_a_foreign_period_course_or_cohort_filter_is_not_found(self):
        self.client.force_login(self.alpha_owner)
        for name, number, message in (
            ("period", self.beta_period.pk,
             "No academic period with that number is here."),
            ("course", self.beta_course.pk,
             "No course with that number is here."),
            ("cohort", self.beta_cohort.pk,
             "No cohort with that number is here."),
        ):
            answer = self.client.get(
                self.report_url("{}={}".format(name, number))
            )
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
            self.assertIn(message, self.body(answer))
            self.assertNotIn("Beta Learner", self.body(answer))

    def test_nonsense_filter_numbers_are_this_centres_not_found(self):
        self.client.force_login(self.alpha_owner)
        for query in ("period=x", "course=99999", "cohort=0"):
            answer = self.client.get(self.report_url(query))
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "not_found")

    def test_report_roles_are_owner_coordinator_and_desk(self):
        for user in (self.alpha_owner, self.coordinator, self.desk):
            self.client.force_login(user)
            answer = self.client.get(self.report_url())
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer["X-CohortDesk-Page"], "reports")
        for user in (self.teacher, self.money):
            self.client.force_login(user)
            answer = self.client.get(self.report_url())
            self.assertEqual(answer.status_code, 403)
            self.assertEqual(answer["X-CohortDesk-Page"], "denied")

    def test_the_report_address_answers_post_nothing(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(self.report_url())
        self.assertEqual(answer.status_code, 405)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        answer = self.client.post(
            self.url("reports") + "enrollments.csv"
        )
        self.assertEqual(answer.status_code, 405)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_signed_out_or_foreign_centre_reads_no_report(self):
        answer = self.client.get(self.report_url())
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.report_url(center=self.beta))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_another_centres_csv_and_row_is_never_in_this_centres_export(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.url("reports") + "enrollments.csv")
        self.assertEqual(answer.status_code, 200)
        self.assertIn("text/csv", answer["Content-Type"])
        self.assertIn("attachment", answer["Content-Disposition"])
        body = self.body(answer)
        self.assertIn("Enrollment number", body)
        self.assertIn("Alpha Learner", body)
        self.assertIn("Alpha Autumn", body)
        self.assertNotIn("Beta Learner", body)
        self.assertNotIn("Beta Autumn", body)

    def test_the_csv_carries_the_same_filters_as_the_page(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(
            self.url("reports")
            + "enrollments.csv?period={}".format(self.alpha_period.pk)
        )
        self.assertEqual(answer.status_code, 200)
        body = self.body(answer)
        self.assertIn("Enrollment number", body)
        self.assertNotIn("Alpha Learner", body)
        answer = self.client.get(
            self.url("reports")
            + "enrollments.csv?period={}".format(self.beta_period.pk)
        )
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")

    def test_the_csv_asks_the_same_permission(self):
        for user in (self.teacher, self.money):
            self.client.force_login(user)
            answer = self.client.get(self.url("reports") + "enrollments.csv")
            self.assertEqual(answer.status_code, 403)
            self.assertEqual(answer["X-CohortDesk-Page"], "denied")

    def test_the_centre_navigation_names_reports_only_for_report_roles(self):
        for user in (self.alpha_owner, self.coordinator, self.desk):
            self.client.force_login(user)
            body = self.body(self.client.get(self.url("dashboard")))
            self.assertIn('data-kind="reports"', body)
        for user in (self.teacher, self.money):
            self.client.force_login(user)
            body = self.body(self.client.get(self.url("dashboard")))
            self.assertNotIn('data-kind="reports"', body)

class SessionAttendanceAccess(TestCase):
    """SCH-002/003/004 and ATT-001..ATT-010 proved through the URL layer.

    The discipline the phases above already use: permission gates per role,
    another centre's numbers naming nothing here (RULE 6.1), the instructor's
    scope computed on the server and never from the address typed (SEC-001),
    the centre's own timezone terms for session times (SCH-009), the
    eligibility rule (ATT-008), the one-record-per-pair rule at the page too,
    the audited correction window (ATT-005/ATT-009), the CSV gates, and the
    centre's history sections answering with shape. The fixture links the
    teacher account to its staff record and makes it teach the Alpha Autumn
    cohort, schedules one session there and one at an untaught Alpha Winter
    cohort, records one attendance, and plants one audited correction so the
    history parts have something to name.
    """

    def setUp(self):
        # The fixture above copied here on purpose: this class stands on its
        # own so the pages tests there run only against their own records,
        # and the sessions, attendance and history added below cannot
        # collide with them.
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
        self.coordinator = CohortUser.objects.create_user(
            username="alpha-coord", password="x", display_name="Alpha Coord"
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
            user=self.coordinator,
            organization=self.alpha,
            role=ROLE_ACADEMIC_COORDINATOR,
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
        self.alpha_course = Course.objects.create(
            organization=self.alpha,
            title="Alpha Spanish",
            default_fee=Decimal("100.00"),
            status="active",
        )
        self.beta_course = Course.objects.create(
            organization=self.beta,
            title="Beta French",
            status="active",
        )
        self.alpha_cohort = Cohort.objects.create(
            organization=self.alpha,
            course=self.alpha_course,
            name="Alpha Autumn",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 20),
            capacity=10,
            status=COHORT_ACTIVE,
        )
        self.beta_cohort = Cohort.objects.create(
            organization=self.beta,
            course=self.beta_course,
            name="Beta Autumn",
            status=COHORT_ACTIVE,
        )
        self.alpha_student = Student.objects.create(
            organization=self.alpha,
            student_number="AC-1",
            name="Alpha Learner",
            status=STATUS_ACTIVE,
        )
        self.beta_student = Student.objects.create(
            organization=self.beta,
            student_number="BC-1",
            name="Beta Learner",
            status=STATUS_ACTIVE,
        )
        self.alpha_enrollment = Enrollment.objects.create(
            organization=self.alpha,
            cohort=self.alpha_cohort,
            student=self.alpha_student,
            agreed_fee=Decimal("100.00"),
            fee_currency="DZD",
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha, title="Alpha reading program"
        )
        self.late_alpha_program = Program.objects.create(
            organization=self.alpha, title="Alpha early program"
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, title="Beta reading program"
        )
        self.alpha_course.program = self.alpha_program
        self.alpha_course.save(update_fields=["program"])
        self.alpha_period = AcademicPeriod.objects.create(
            organization=self.alpha,
            name="Alpha first term",
            start_date=date(2026, 1, 5),
            end_date=date(2026, 6, 30),
        )
        self.late_alpha_period = AcademicPeriod.objects.create(
            organization=self.alpha, name="Alpha planning window"
        )
        self.beta_period = AcademicPeriod.objects.create(
            organization=self.beta, name="Beta first term"
        )
        self.teacher_staff = Staff.objects.create(
            organization=self.alpha,
            user=self.teacher,
            staff_number="AC-T1",
            name="Alpha Teacher",
        )
        CohortInstructor.objects.create(
            organization=self.alpha,
            staff=self.teacher_staff,
            cohort=self.alpha_cohort,
        )
        self.winter_cohort = Cohort.objects.create(
            organization=self.alpha,
            course=self.alpha_course,
            name="Alpha Winter",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 12, 31),
            capacity=10,
            status=COHORT_ACTIVE,
        )
        # The pair rule and the eligibility rule need a second active student,
        # and the archived half of the service answer names a third one that
        # cannot be enrolled at all (STU-006).
        self.second_student = Student.objects.create(
            organization=self.alpha,
            student_number="AC-2",
            name="Alpha Second Learner",
            status=STATUS_ACTIVE,
        )
        Enrollment.objects.create(
            organization=self.alpha,
            cohort=self.alpha_cohort,
            student=self.second_student,
            agreed_fee=Decimal("100.00"),
            fee_currency="DZD",
        )
        self.third_student = Student.objects.create(
            organization=self.alpha,
            student_number="AC-3",
            name="Alpha Third Learner",
            status=STATUS_ACTIVE,
        )
        Enrollment.objects.create(
            organization=self.alpha,
            cohort=self.alpha_cohort,
            student=self.third_student,
            agreed_fee=Decimal("100.00"),
            fee_currency="DZD",
        )
        self.archived_student = Student.objects.create(
            organization=self.alpha,
            student_number="AC-9",
            name="Alpha Archived Learner",
            status=STATUS_ARCHIVED,
        )
        self.autumn_session = Session.objects.create(
            organization=self.alpha,
            cohort=self.alpha_cohort,
            start_at=datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 10, 5, 15, 30, tzinfo=timezone.utc),
        )
        self.winter_session = Session.objects.create(
            organization=self.alpha,
            cohort=self.winter_cohort,
            start_at=datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 10, 12, 10, 30, tzinfo=timezone.utc),
        )
        self.beta_session = Session.objects.create(
            organization=self.beta,
            cohort=self.beta_cohort,
            start_at=datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 10, 6, 15, 30, tzinfo=timezone.utc),
        )
        Attendance.objects.create(
            organization=self.alpha,
            session=self.autumn_session,
            student=self.alpha_student,
            status=ATTENDANCE_ABSENT,
            note="Arrived late and sat apart",
        )
        AuditLog.objects.create(
            organization=self.alpha,
            session=self.autumn_session,
            student=self.alpha_student,
            actor=self.teacher,
            action=AUDIT_ATTENDANCE_CORRECTED,
            previous_status=ATTENDANCE_ABSENT,
            new_status=ATTENDANCE_PRESENT,
            at=datetime(2026, 10, 5, 15, 45, tzinfo=timezone.utc),
        )

    # -- addresses -------------------------------------------------------

    def url(self, kind, center=None):
        return "/o/{}/center/{}/".format((center or self.alpha).slug, kind)

    def detail(self, kind, number, center=None):
        return "{}{}/".format(self.url(kind, center), number)

    def manage(self, kind, center=None):
        return "{}manage/".format(self.url(kind, center))

    def body(self, response):
        return response.content.decode()

    def test_the_pages_answer_exactly_the_roles_that_read_them(self):
        self.client.force_login(self.alpha_owner)
        for kind in ("sessions", "attendance"):
            answer = self.client.get(self.url(kind))
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer["X-CohortDesk-Page"], kind)
        answer = self.client.get(self.manage("attendance"))
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.client.force_login(self.coordinator)
        for kind in ("sessions", "attendance"):
            answer = self.client.get(self.url(kind))
            self.assertEqual(answer["X-CohortDesk-Page"], kind)
        for kind, page in (
            ("sessions", "sessions_manage"),
            ("attendance", "attendance_manage"),
        ):
            answer = self.client.get(self.manage(kind))
            self.assertEqual(answer["X-CohortDesk-Page"], page)
        self.client.force_login(self.desk)
        answer = self.client.get(self.url("sessions"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "sessions")
        # The desk reads the session list but is not academic staff: the
        # manage address of a page it may only read answers denied, at the
        # same address the role that manages would open.
        answer = self.client.get(self.manage("sessions"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.get(self.url("attendance"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.get(self.manage("attendance"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.client.force_login(self.teacher)
        answer = self.client.get(self.url("attendance"))
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance")
        # Making or reading the session list is the academic staff line: the
        # instructor reads and records attendance only, so both session pages
        # answer denied here (SEC-001).
        answer = self.client.get(self.url("sessions"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.get(self.manage("sessions"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.client.force_login(self.money)
        for kind in ("sessions", "attendance"):
            answer = self.client.get(self.url(kind))
            self.assertEqual(answer.status_code, 403)
            self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        for kind, page in (
            ("sessions", "sessions_manage"),
            ("attendance", "attendance_manage"),
        ):
            answer = self.client.get(self.manage(kind))
            self.assertEqual(answer.status_code, 403)
            self.assertEqual(answer["X-CohortDesk-Page"], "denied")

    def test_a_created_session_needs_a_real_cohort_and_this_centres_time_words(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("sessions"),
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                # A free slot: the fixture already has one occurrence of this
                # cohort at 2026-10-05 14:00, and SCH-005/SCH-006 now refuse a
                # second class for the same cohort and instructor there.
                "start_time": "2026-10-06 14:00",
                "end_time": "2026-10-06 15:30",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "sessions_manage")
        self.assertIn("The session was saved.", self.body(answer))
        self.assertEqual(
            Session.objects.filter(organization=self.alpha).count(),
            3,
        )
        answer = self.client.post(
            self.manage("sessions"),
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                "start_time": "Not a time",
                "end_time": "Nope",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "sessions_manage")
        self.assertIn("Invalid value: Not a time", self.body(answer))
        self.assertNotIn("The session was saved.", self.body(answer))
        self.assertEqual(
            Session.objects.filter(organization=self.alpha).count(),
            3,
        )
        answer = self.client.post(
            self.manage("sessions"),
            {
                "sessions_action": "create",
                "cohort_number": "99999",
                "start_time": "2026-10-05 14:00",
                "end_time": "2026-10-05 15:30",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "sessions_manage")
        self.assertIn("No cohort with that number is here.", self.body(answer))
        self.assertNotIn("The session was saved.", self.body(answer))
        self.assertEqual(
            Session.objects.filter(organization=self.alpha).count(),
            3,
        )

    def test_a_session_never_answers_across_the_centres_it_was_made_at(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("sessions"),
            {
                "sessions_action": "edit",
                "sessions_number": str(self.beta_session.pk),
                "sessions_field": "note",
                "sessions_value": "Reading circle",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "sessions_manage")
        self.assertIn("No session here has that number.", self.body(answer))
        self.assertNotIn("The session was saved.", self.body(answer))
        self.assertEqual(
            Session.objects.get(
                organization=self.beta, pk=self.beta_session.pk
            ).note,
            "",
        )
        answer = self.client.get(self.detail("attendance", self.beta_session.pk))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertIn("No session here has that number.", self.body(answer))
        self.assertNotIn("Beta Autumn", self.body(answer))
        self.client.force_login(self.beta_owner)
        answer = self.client.get(self.url("sessions", self.beta))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "sessions")
        self.assertIn("Beta Autumn", self.body(answer))
        self.assertNotIn("Alpha Autumn", self.body(answer))

    def test_only_the_note_and_the_times_of_a_session_can_be_edited_here(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("sessions"),
            {
                "sessions_action": "edit",
                "sessions_number": str(self.autumn_session.pk),
                "sessions_field": "note",
                "sessions_value": "Winter note",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "sessions_manage")
        self.assertIn("The session was saved.", self.body(answer))
        session = Session.objects.get(
            organization=self.alpha, pk=self.autumn_session.pk
        )
        self.assertEqual(session.note, "Winter note")
        answer = self.client.post(
            self.manage("sessions"),
            {
                "sessions_action": "edit",
                "sessions_number": str(self.autumn_session.pk),
                "sessions_field": "cohort",
                "sessions_value": "9",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "sessions_manage")
        self.assertIn(
            "That part of a record cannot be changed.", self.body(answer)
        )
        self.assertNotIn("The session was saved.", self.body(answer))
        session = Session.objects.get(
            organization=self.alpha, pk=self.autumn_session.pk
        )
        self.assertEqual(session.note, "Winter note")
        self.assertEqual(session.cohort, self.alpha_cohort)

    def test_recording_answers_from_the_eligible_and_the_already_recorded(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-9",
                "attendance_status": "present",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn(
            "That student record is archived, so it cannot be enrolled.",
            self.body(answer),
        )
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(),
            1,
        )
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": str(self.winter_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "present",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn(
            "That student is not enrolled in this session",
            self.body(answer),
        )
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(),
            1,
        )
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "present",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn(
            "attendance is already recorded. Use the correction action.",
            self.body(answer),
        )
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(),
            1,
        )
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "sneaky",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn("Not one of the attendance statuses.", self.body(answer))
        self.assertNotIn("The attendance was saved.", self.body(answer))
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(),
            1,
        )
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": "99999",
                "attendance_student": "AC-1",
                "attendance_status": "present",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn("No session here has that number.", self.body(answer))
        self.assertNotIn("The attendance was saved.", self.body(answer))
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(),
            1,
        )
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-77",
                "attendance_status": "present",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn(
            "No record with that student number is here.", self.body(answer)
        )
        self.assertNotIn("The attendance was saved.", self.body(answer))
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(),
            1,
        )
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-2",
                "attendance_status": "late",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn("The attendance was saved.", self.body(answer))
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(),
            2,
        )
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-2",
                "attendance_status": "absent",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn(
            "attendance is already recorded. Use the correction action.",
            self.body(answer),
        )
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(),
            2,
        )

    def test_a_correction_moves_the_record_and_writes_history_the_pages_show(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "correct",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "late",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn("The attendance was saved.", self.body(answer))
        record = Attendance.objects.filter(session=self.autumn_session).first()
        self.assertEqual(record.status, ATTENDANCE_LATE)
        self.assertEqual(AuditLog.objects.filter(organization=self.alpha).count(), 2)
        answer = self.client.get(self.manage("attendance"))
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "correct",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "present",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn("The attendance was saved.", self.body(answer))
        record = Attendance.objects.filter(session=self.autumn_session).first()
        self.assertEqual(record.status, ATTENDANCE_PRESENT)
        self.assertEqual(AuditLog.objects.filter(organization=self.alpha).count(), 3)
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "correct",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "present",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn("Choose a different status.", self.body(answer))
        self.assertNotIn("The attendance was saved.", self.body(answer))
        record = Attendance.objects.filter(session=self.autumn_session).first()
        self.assertEqual(record.status, ATTENDANCE_PRESENT)
        self.assertEqual(AuditLog.objects.filter(organization=self.alpha).count(), 3)
        answer = self.client.get(self.detail("attendance", self.autumn_session.pk))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_session")
        self.assertIn("Alpha Autumn", self.body(answer))
        self.assertIn("attendance_session_corrections", self.body(answer))
        self.assertIn("Alpha Teacher", self.body(answer))
        answer = self.client.get(self.detail("students", "AC-1"))
        self.assertEqual(answer["X-CohortDesk-Page"], "student")
        self.assertIn("attendance_history", self.body(answer))
        self.assertIn("Absent", self.body(answer))
        self.assertIn("Present", self.body(answer))
        answer = self.client.get(self.detail("students", "AC-9"))
        self.assertEqual(answer["X-CohortDesk-Page"], "student")
        self.assertNotIn("attendance_history", self.body(answer))

    def test_an_instructor_only_ever_sees_the_sessions_of_the_cohorts_they_teach(self):
        self.client.force_login(self.teacher)
        answer = self.client.get(self.url("attendance"))
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance")
        self.assertIn("AC-1", self.body(answer))
        answer = self.client.get(
            self.detail("attendance", self.winter_session.pk)
        )
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertIn("No session here has that number.", self.body(answer))
        self.assertNotIn("Alpha Winter", self.body(answer))
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": str(self.winter_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "present",
            },
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "attendance_manage")
        self.assertIn(
            "That session is outside the cohorts this person teaches.",
            self.body(answer),
        )
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(), 1,
        )

    def test_the_instructor_self_page_names_only_the_cohorts_they_teach(self):
        self.client.force_login(self.teacher)
        answer = self.client.get(self.url("self"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "staff_self")
        self.assertIn("self_sessions_heading", self.body(answer))
        self.assertIn("Alpha Autumn", self.body(answer))
        self.assertNotIn("Alpha Winter", self.body(answer))
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.url("self"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.assertNotIn("self_sessions", self.body(answer))

    def test_the_desk_reads_sessions_and_the_finance_officer_reads_nothing_academic(self):
        self.client.force_login(self.desk)
        answer = self.client.get(self.url("sessions"))
        self.assertEqual(answer["X-CohortDesk-Page"], "sessions")
        answer = self.client.get(self.manage("sessions"))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "record",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "late",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.assertEqual(
            Attendance.objects.filter(organization=self.alpha).count(),
            1,
        )
        for address in (
            self.url("attendance"),
            "/o/{}/center/attendance/{}/register.csv".format(
                self.alpha.slug, self.winter_session.pk
            ),
            "/o/{}/center/reports/attendance.csv".format(self.alpha.slug),
        ):
            answer = self.client.get(address)
            self.assertEqual(answer.status_code, 403)
        self.client.force_login(self.money)
        for address in (
            self.url("attendance"),
            "/o/{}/center/attendance/{}/register.csv".format(
                self.alpha.slug, self.winter_session.pk
            ),
            "/o/{}/center/reports/attendance.csv".format(self.alpha.slug),
            self.url("reports"),
        ):
            answer = self.client.get(address)
            self.assertEqual(answer.status_code, 403)
        answer = self.client.post(
            self.manage("sessions"),
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                "start_time": "2026-10-05 14:00",
                "end_time": "2026-10-05 15:30",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.assertNotIn("The session was saved.", self.body(answer))
        self.assertEqual(
            Session.objects.filter(organization=self.alpha).count(),
            2,
        )

    def test_the_csvs_answer_their_gates_and_carry_exactly_the_rows_the_pages_show(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(
            "/o/{}/center/attendance/{}/register.csv".format(
                self.alpha.slug, self.autumn_session.pk
            )
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["Content-Type"], "text/csv; charset=utf-8")
        lines = self.body(answer).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(
            lines[1].startswith(f"{self.autumn_session.pk},AC-1,")
        )
        answer = self.client.get(
            "/o/{}/center/reports/attendance.csv".format(self.alpha.slug)
        )
        self.assertEqual(answer.status_code, 200)
        lines = self.body(answer).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith(f"{self.autumn_session.pk},"))
        self.assertIn("Alpha Autumn", lines[1])
        self.assertIn("AC-1", lines[1])
        # The report CSV asks for the report permission and the attendance one
        # together, so a coordinator holding both gets the same single row.
        self.client.force_login(self.coordinator)
        answer = self.client.get(
            "/o/{}/center/reports/attendance.csv".format(self.alpha.slug)
        )
        self.assertEqual(answer.status_code, 200)
        lines = self.body(answer).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("AC-1", lines[1])

    def test_the_reports_page_shows_the_attendance_parts_only_to_who_may_read_them(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.url("reports"))
        self.assertEqual(answer["X-CohortDesk-Page"], "reports")
        self.assertIn("report_attendance", self.body(answer))
        self.assertIn("AC-1", self.body(answer))
        # The desk reads the report but not the attendance, so the whole
        # attendance part of the report page is hidden from it (SEC-001).
        self.client.force_login(self.desk)
        answer = self.client.get(self.url("reports"))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "reports")
        self.assertNotIn("report_attendance", self.body(answer))

    def test_a_suspended_membership_loses_the_session_and_attendance_pages(self):
        self.client.force_login(self.teacher)
        Members.objects.filter(
            user=self.teacher, organization=self.alpha
        ).update(status="suspended")
        for kind in ("attendance", "sessions"):
            answer = self.client.get(self.url(kind))
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")
            self.assertNotIn("AC-1", self.body(answer))
        answer = self.client.get(self.manage("attendance"))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_a_post_without_permission_changes_nothing(self):
        self.client.force_login(self.money)
        answer = self.client.post(
            self.manage("sessions"),
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                "start_time": "2026-10-05 14:00",
                "end_time": "2026-10-05 15:30",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.assertEqual(
            Session.objects.filter(organization=self.alpha).count(),
            2,
        )
        self.client.force_login(self.desk)
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "correct",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "late",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        record = Attendance.objects.filter(session=self.autumn_session).first()
        self.assertEqual(record.status, ATTENDANCE_ABSENT)
        self.assertEqual(AuditLog.objects.filter(organization=self.alpha).count(), 1)
        # The instructor may record but never correct: the action gate at the
        # same address answers denied, and nothing moves or writes.
        self.client.force_login(self.teacher)
        answer = self.client.post(
            self.manage("attendance"),
            {
                "attendance_action": "correct",
                "attendance_number": str(self.autumn_session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "late",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        record = Attendance.objects.filter(session=self.autumn_session).first()
        self.assertEqual(record.status, ATTENDANCE_ABSENT)
        self.assertEqual(AuditLog.objects.filter(organization=self.alpha).count(), 1)
        answer = self.client.post(
            self.manage("sessions"),
            {
                "sessions_action": "edit",
                "sessions_number": str(self.autumn_session.pk),
                "sessions_field": "note",
                "sessions_value": "Changed by an instructor",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        session = Session.objects.get(
            organization=self.alpha, pk=self.autumn_session.pk
        )
        self.assertEqual(session.note, "")
