"""Phase 3E access tests through the URL layer.

SCH-001/SCH-007/SCH-008 and the schedule conflict policy proved the way every
tenant page is proved in this project: permission gates per role, another
centre's numbers naming nothing here (RULE 6.1), the real page header
(X-CohortDesk-Page), and the form answered on the page the person is on
(RULE 6.3). Nothing here reads a template's hidden state.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.roles import (
    ROLE_ACADEMIC_COORDINATOR,
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)
from apps.staff.models import CohortInstructor, Staff
from apps.students.models import Student
from apps.students.statuses import STATUS_ACTIVE

from .models import Attendance, Cohort, Course, Enrollment, Room, Session
from .statuses import SESSION_CANCELLED


class SchedulingAccess(TestCase):
    """Rooms, the session workflow and the day/week schedule pages."""

    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Alpha Center",
            slug="alpha-center",
            primary_currency="DZD",
            display_timezone="Europe/Berlin",
        )
        self.beta = Organization.objects.create(
            name="Beta Center", slug="beta-center", primary_currency="EUR"
        )
        self.owner = self.user("alpha-owner", self.alpha, ROLE_OWNER)
        self.beta_owner = self.user("beta-owner", self.beta, ROLE_OWNER)
        self.coordinator = self.user(
            "alpha-coord", self.alpha, ROLE_ACADEMIC_COORDINATOR
        )
        self.desk = self.user("alpha-desk", self.alpha, ROLE_RECEPTIONIST)
        self.teacher = self.user("alpha-teacher", self.alpha, ROLE_INSTRUCTOR)
        self.money = self.user("alpha-money", self.alpha, ROLE_FINANCE_OFFICER)
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
            status="active",
        )
        self.beta_cohort = Cohort.objects.create(
            organization=self.beta,
            course=self.beta_course,
            name="Beta Autumn",
            start_date=date(2026, 9, 1),
            status="active",
        )
        self.student = Student.objects.create(
            organization=self.alpha,
            student_number="AC-1",
            name="Alpha Learner",
            status=STATUS_ACTIVE,
        )
        Enrollment.objects.create(
            organization=self.alpha,
            cohort=self.alpha_cohort,
            student=self.student,
            fee_currency="DZD",
        )
        self.room = Room.objects.create(
            organization=self.alpha, name="Studio", capacity=20
        )
        self.beta_room = Room.objects.create(
            organization=self.beta, name="Beta studio"
        )
        self.session = Session.objects.create(
            organization=self.alpha,
            cohort=self.alpha_cohort,
            room=self.room,
            start_at=datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc),
        )
        self.beta_session = Session.objects.create(
            organization=self.beta,
            cohort=self.beta_cohort,
            start_at=datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc),
        )

    def user(self, username, organization, role):
        user = CohortUser.objects.create_user(username=username, password="x")
        Members.objects.create(user=user, organization=organization, role=role)
        return user

    def get(self, user, path):
        self.client.force_login(user)
        return self.client.get(path)

    def post(self, user, path, data):
        self.client.force_login(user)
        return self.client.post(path, data)

    # -- rooms -----------------------------------------------------------

    def test_rooms_page_is_permission_gated_and_names_its_page(self):
        url = "/o/alpha-center/center/rooms/"
        response = self.get(self.owner, url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-CohortDesk-Page"], "rooms")
        self.assertIn("Studio", response.content.decode())
        self.assertEqual(self.get(self.desk, url).status_code, 200)
        self.assertEqual(self.get(self.coordinator, url).status_code, 200)
        self.assertEqual(self.get(self.teacher, url).status_code, 403)
        self.assertEqual(self.get(self.money, url).status_code, 403)
        self.assertEqual(self.get(self.beta_owner, url).status_code, 404)

    def test_rooms_manage_makes_and_archives_a_room(self):
        url = "/o/alpha-center/center/rooms/manage/"
        self.assertEqual(self.get(self.owner, url).status_code, 200)
        self.assertEqual(self.get(self.desk, url).status_code, 403)
        self.assertEqual(self.get(self.teacher, url).status_code, 403)
        response = self.post(
            self.owner,
            url,
            {
                "rooms_action": "create",
                "room_name": "Hall B",
                "room_capacity": "15",
                "room_location": "First floor",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-CohortDesk-Page"], "rooms_manage")
        self.assertTrue(
            Room.objects.filter(organization=self.alpha, name="Hall B").exists()
        )
        created = Room.objects.get(organization=self.alpha, name="Hall B")
        response = self.post(
            self.coordinator,
            url,
            {"rooms_action": "archive", "rooms_number": str(created.pk)},
        )
        created.refresh_from_db()
        self.assertEqual(created.status, "archived")
        listed = self.get(self.coordinator, "/o/alpha-center/center/rooms/")
        self.assertIn("Archived", listed.content.decode())

    def test_a_desk_role_cannot_make_a_room(self):
        before = Room.objects.filter(organization=self.alpha).count()
        response = self.post(
            self.desk,
            "/o/alpha-center/center/rooms/manage/",
            {"rooms_action": "create", "room_name": "Hall C"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Room.objects.filter(organization=self.alpha).count(), before)

    def test_a_room_rejection_stays_on_the_page_with_its_problem(self):
        response = self.post(
            self.owner,
            "/o/alpha-center/center/rooms/manage/",
            {"rooms_action": "create", "room_name": "Studio"},
        )
        self.assertIn("room_name_problem", response.content.decode())
        self.assertEqual(
            Room.objects.filter(organization=self.alpha, name="Studio").count(), 1
        )

    # -- sessions --------------------------------------------------------

    def test_sessions_page_shows_the_room_and_status(self):
        response = self.get(
            self.desk, "/o/alpha-center/center/sessions/"
        )
        page = response.content.decode()
        self.assertIn("Studio", page)
        self.assertIn("Scheduled", page)
        self.assertIn("2026-09-01 09:00", page)

    def test_sessions_manage_is_closed_to_the_desk_role(self):
        url = "/o/alpha-center/center/sessions/manage/"
        self.assertEqual(self.get(self.desk, url).status_code, 403)
        response = self.post(
            self.desk,
            url,
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                "start_time": "2026-09-02 09:00",
                "end_time": "2026-09-02 10:00",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Session.objects.filter(organization=self.alpha).count(), 1)

    def test_a_one_off_session_is_scheduled_with_a_room(self):
        response = self.post(
            self.coordinator,
            "/o/alpha-center/center/sessions/manage/",
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                "start_time": "2026-09-02 09:00",
                "end_time": "2026-09-02 10:00",
                "room_number": str(self.room.pk),
                "note": "First lesson",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("sessions_manage", response["X-CohortDesk-Page"])
        created = Session.objects.get(
            organization=self.alpha,
            start_at=datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(created.room_id, self.room.pk)
        self.assertEqual(created.note, "First lesson")

    def test_a_prevented_conflict_keeps_the_person_on_the_form(self):
        response = self.post(
            self.owner,
            "/o/alpha-center/center/sessions/manage/",
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                "start_time": "2026-09-01 09:00",
                "end_time": "2026-09-01 10:00",
                "room_number": str(self.room.pk),
            },
        )
        self.assertIn("room_number_problem", response.content.decode())
        self.assertEqual(Session.objects.filter(organization=self.alpha).count(), 1)

    def test_a_warn_policy_saves_and_says_so(self):
        self.alpha.schedule_conflict_policy = "warn"
        self.alpha.save(update_fields=["schedule_conflict_policy"])
        response = self.post(
            self.owner,
            "/o/alpha-center/center/sessions/manage/",
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                "start_time": "2026-09-01 09:00",
                "end_time": "2026-09-01 10:00",
                "room_number": str(self.room.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("warning", response.content.decode())
        self.assertEqual(Session.objects.filter(organization=self.alpha).count(), 2)

    def test_a_recurring_pattern_generates_its_sessions(self):
        response = self.post(
            self.coordinator,
            "/o/alpha-center/center/sessions/manage/",
            {
                "sessions_action": "create_pattern",
                "cohort_number": str(self.alpha_cohort.pk),
                "pattern_start_date": "2026-09-07",
                "pattern_end_date": "2026-09-13",
                "pattern_start_time": "09:00",
                "pattern_end_time": "10:00",
                "pattern_weekdays": ["mon", "fri"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Session.objects.filter(organization=self.alpha).count(), 3)

    def test_the_pattern_form_offers_the_codes_the_service_reads(self):
        response = self.get(
            self.owner, "/o/alpha-center/center/sessions/manage/"
        )
        page = response.content.decode()
        self.assertIn('name="pattern_weekdays"', page)
        self.assertIn('value="mon"', page)
        self.assertNotIn('value="weekday_mon"', page)

    def test_a_nonexistent_spring_forward_time_is_refused_with_its_field(self):
        response = self.post(
            self.owner,
            "/o/alpha-center/center/sessions/manage/",
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                "start_time": "2026-03-29 02:30",
                "end_time": "2026-03-29 04:00",
            },
        )
        page = response.content.decode()
        self.assertIn("start_time_problem", page)
        self.assertEqual(Session.objects.filter(organization=self.alpha).count(), 1)

    def test_a_repeated_fall_back_time_keeps_the_first_fold(self):
        response = self.post(
            self.owner,
            "/o/alpha-center/center/sessions/manage/",
            {
                "sessions_action": "create",
                "cohort_number": str(self.alpha_cohort.pk),
                "start_time": "2026-10-25 02:30",
                "end_time": "2026-10-25 04:30",
            },
        )
        self.assertEqual(response.status_code, 200)
        created = Session.objects.get(
            organization=self.alpha,
            start_at=datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            created.start_at.astimezone(ZoneInfo("Europe/Berlin")).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "2026-10-25 02:30",
        )

    def test_an_instructor_conflict_marks_the_cohort_row(self):
        staff = Staff.objects.create(
            organization=self.alpha, staff_number="T-9", name="Teacher Nine"
        )
        other = Cohort.objects.create(
            organization=self.alpha,
            course=self.alpha_course,
            name="Alpha Spring",
            start_date=date(2026, 9, 1),
            status="active",
        )
        for cohort in (self.alpha_cohort, other):
            CohortInstructor.objects.create(
                organization=self.alpha, staff=staff, cohort=cohort
            )
        response = self.post(
            self.owner,
            "/o/alpha-center/center/sessions/manage/",
            {
                "sessions_action": "create",
                "cohort_number": str(other.pk),
                "start_time": "2026-09-01 09:00",
                "end_time": "2026-09-01 10:00",
            },
        )
        page = response.content.decode()
        self.assertIn("cohort_number_problem", page)
        self.assertEqual(Session.objects.filter(organization=self.alpha).count(), 1)

    def test_cancelling_one_occurrence_leaves_the_others_alone(self):
        second = Session.objects.create(
            organization=self.alpha,
            cohort=self.alpha_cohort,
            start_at=datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc),
        )
        url = "/o/alpha-center/center/sessions/manage/"
        response = self.post(
            self.owner,
            url,
            {"sessions_action": "cancel", "sessions_number": str(self.session.pk)},
        )
        self.session.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.session.status, SESSION_CANCELLED)
        self.assertNotEqual(second.status, SESSION_CANCELLED)
        self.assertIn("cancelled", response.content.decode())
        response = self.post(
            self.owner,
            url,
            {"sessions_action": "cancel", "sessions_number": str(self.session.pk)},
        )
        self.assertIn("That session was already cancelled.", response.content.decode())

    def test_setting_a_room_refuses_an_archived_one(self):
        Room.objects.filter(pk=self.room.pk).update(status="archived")
        response = self.post(
            self.owner,
            "/o/alpha-center/center/sessions/manage/",
            {
                "sessions_action": "set_room",
                "sessions_number": str(self.session.pk),
                "room_number": str(self.room.pk),
            },
        )
        self.assertIn("room_number_problem", response.content.decode())
        self.session.refresh_from_db()
        self.assertEqual(self.session.room_id, self.room.pk)

    # -- day and week views ---------------------------------------------

    def test_schedule_day_and_week_are_permission_gated(self):
        day = "/o/alpha-center/center/schedule/day/?date=2026-09-01"
        week = "/o/alpha-center/center/schedule/week/?date=2026-09-01"
        response = self.get(self.owner, day)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-CohortDesk-Page"], "schedule_day")
        self.assertIn("Alpha Autumn", response.content.decode())
        self.assertIn("2026-09-01 09:00", response.content.decode())
        response = self.get(self.desk, week)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-CohortDesk-Page"], "schedule_week")
        self.assertIn("Alpha Autumn", response.content.decode())
        self.assertEqual(self.get(self.teacher, day).status_code, 403)
        self.assertEqual(self.get(self.money, week).status_code, 403)

    def test_a_bad_date_is_the_problem_page(self):
        response = self.get(
            self.owner, "/o/alpha-center/center/schedule/day/?date=not-a-date"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["X-CohortDesk-Page"], "problem")

    def test_another_centres_timetable_is_unreachable(self):
        url = "/o/alpha-center/center/schedule/week/?date=2026-09-01"
        self.assertEqual(self.get(self.beta_owner, url).status_code, 404)
        beta_page = self.get(
            self.beta_owner, "/o/beta-center/center/schedule/week/?date=2026-09-01"
        )
        self.assertEqual(beta_page.status_code, 200)
        page = beta_page.content.decode()
        self.assertIn("Beta Autumn", page)
        self.assertNotIn("Alpha Autumn", page)
        self.assertNotIn("Studio", page)

    def test_cancelled_sessions_stay_visible_in_the_timetable(self):
        Session.objects.filter(pk=self.session.pk).update(
            status=SESSION_CANCELLED
        )
        response = self.get(
            self.owner, "/o/alpha-center/center/schedule/day/?date=2026-09-01"
        )
        page = response.content.decode()
        self.assertIn("Cancelled", page)
        self.assertIn("Alpha Autumn", page)

    def test_attendance_on_a_cancelled_session_is_refused_with_its_own_copy(self):
        Session.objects.filter(pk=self.session.pk).update(
            status=SESSION_CANCELLED
        )
        response = self.post(
            self.owner,
            "/o/alpha-center/center/attendance/manage/",
            {
                "attendance_action": "record",
                "attendance_number": str(self.session.pk),
                "attendance_student": "AC-1",
                "attendance_status": "present",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-CohortDesk-Page"], "attendance_manage")
        page = response.content.decode()
        self.assertIn(
            "That session is cancelled; no new attendance can be recorded.",
            page,
        )
        self.assertNotIn("The attendance was saved.", page)
        self.assertFalse(
            Attendance.objects.filter(session=self.session).exists()
        )
