"""Phase 3E service tests: rooms, recurring schedules and session conflicts.

SCH-001..SCH-008 and the cohort-date half of SCH-010, called the way a page
calls them: the service functions, never the model API, because the services
are where the rules live. The DST proofs use a real IANA zone (Europe/Berlin),
not a fixed offset, so the fall-back and spring-forward behaviour is the one a
centre with that zone actually gets (SCH-009/D74).
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.organizations.models import SCHEDULE_WARN
from apps.staff.models import CohortInstructor, Staff

from .models import Attendance, AuditLog, Room, SchedulePattern, Session
from .services import (
    archive_room,
    cancel_session,
    correct_attendance,
    create_room,
    create_schedule_pattern,
    create_session,
    edit_room,
    edit_session,
    enroll_student,
    record_attendance,
    set_cohort_room,
    set_session_room,
)
from .statuses import (
    ATTENDANCE_LATE,
    ATTENDANCE_PRESENT,
    ROOM_ARCHIVED,
    SESSION_CANCELLED,
    SESSION_SCHEDULED,
    WEEKDAY_CODES,
)
from .tests import CenterFixture


class SchedulingFixture(CenterFixture):
    """The shared two-centre fixture, in a real IANA timezone."""

    def setUp(self):
        super().setUp()
        self.alpha.display_timezone = "Europe/Berlin"
        self.alpha.save(update_fields=["display_timezone"])

    def make_room(self, name="Room 1", capacity=20, user=None):
        record, problems, notes = create_room(
            user or self.owner, self.alpha, name, capacity
        )
        self.assertEqual((problems, notes), ([], []))
        return record

    def make_staff(self, number="T-1", name="Teacher One"):
        return Staff.objects.create(
            organization=self.alpha, staff_number=number, name=name
        )

    def assign(self, staff, cohort):
        return CohortInstructor.objects.create(
            organization=self.alpha, staff=staff, cohort=cohort
        )


class RoomTests(SchedulingFixture):
    """SCH-001: rooms are archived, never deleted, and never cross a centre."""

    def test_room_name_is_unique_per_centre_and_capacity_is_at_least_one(self):
        room = self.make_room("Studio")
        _record, problems, _notes = create_room(
            self.owner, self.alpha, "Studio"
        )
        self.assertEqual([name for name, _message in problems], ["name"])
        _record, problems, _notes = create_room(
            self.owner, self.alpha, "Lab", capacity=0
        )
        self.assertEqual([name for name, _message in problems], ["capacity"])
        self.assertEqual(Room.objects.filter(organization=self.alpha).count(), 1)
        self.assertEqual(room.capacity, 20)

    def test_a_room_capacity_is_optional(self):
        record, problems, notes = create_room(
            self.owner, self.alpha, "Open hall", None
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNone(record.capacity)

    def test_edit_changes_only_the_named_descriptive_part(self):
        room = self.make_room("Studio")
        record, problems, notes = edit_room(
            self.owner, self.alpha, room.pk, "location", "First floor"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.location, "First floor")
        _record, problems, notes = edit_room(
            self.owner, self.alpha, room.pk, "status", ROOM_ARCHIVED
        )
        self.assertEqual(notes, ["field_not_editable"])

    def test_archiving_keeps_history_and_blocks_new_bookings(self):
        room = self.make_room("Studio")
        cohort = self.make_cohort(name="Autumn A")
        set_cohort_room(self.owner, self.alpha, cohort.pk, room.pk)
        session, _problems, _notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
            room_number=room.pk,
        )
        record, problems, notes = archive_room(
            self.owner, self.alpha, room.pk
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, ROOM_ARCHIVED)
        self.assertIsNotNone(record.archived_at)
        session.refresh_from_db()
        self.assertEqual(session.room_id, room.pk)
        cohort.refresh_from_db()
        self.assertEqual(cohort.default_room_id, room.pk)
        _record, _problems, notes = archive_room(
            self.owner, self.alpha, room.pk
        )
        self.assertEqual(notes, ["already_archived"])

        _record, _problems, notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
            room_number=room.pk,
        )
        self.assertEqual(notes, ["room_archived"])
        # The default room is never silently dropped: relying on it is refused.
        _record, _problems, notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(notes, ["room_archived"])
        # An explicitly selected active room overrides an archived default.
        other = self.make_room("Hall B")
        record, _problems, notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc),
            room_number=other.pk,
        )
        self.assertEqual(notes, [])
        self.assertEqual(record.room_id, other.pk)

    def test_cohort_default_room_and_explicit_override(self):
        room = self.make_room("Studio")
        other = self.make_room("Hall B")
        cohort = self.make_cohort(name="Autumn A")
        set_cohort_room(self.owner, self.alpha, cohort.pk, room.pk)
        session, _problems, _notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(session.room_id, room.pk)
        session, _problems, _notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
            room_number=other.pk,
        )
        self.assertEqual(session.room_id, other.pk)
        set_cohort_room(self.owner, self.alpha, cohort.pk, "")
        cohort.refresh_from_db()
        self.assertIsNone(cohort.default_room_id)

    def test_another_centres_room_number_is_not_found(self):
        beta_room, problems, notes = create_room(
            self.beta_owner, self.beta, "Beta room"
        )
        self.assertEqual((problems, notes), ([], []))
        cohort = self.make_cohort(name="Autumn A")
        _record, _problems, notes = set_cohort_room(
            self.owner, self.alpha, cohort.pk, beta_room.pk
        )
        self.assertEqual(notes, ["room_not_found"])
        _record, _problems, notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
            room_number=beta_room.pk,
        )
        self.assertEqual(notes, ["room_not_found"])


class ConflictTests(SchedulingFixture):
    """SCH-005/SCH-006: half-open overlap, prevent or warn by centre policy."""

    START = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    END = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    def session(self, cohort, start=None, end=None, room=None):
        record, problems, notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            start or self.START,
            end or self.END,
            room_number=room.pk if room is not None else None,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        return record

    def test_two_sessions_touching_at_endpoints_do_not_conflict(self):
        room = self.make_room("Studio")
        cohort = self.make_cohort(name="Autumn A")
        self.session(cohort, room=room)
        record, problems, notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            self.END,
            self.END + timedelta(hours=1),
            room_number=room.pk,
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)

    def test_prevent_refuses_and_names_the_room_and_the_other_session(self):
        room = self.make_room("Studio")
        cohort = self.make_cohort(name="Autumn A")
        other = self.make_cohort(name="Autumn B")
        existing = self.session(cohort, room=room)
        record, problems, notes = create_session(
            self.owner,
            self.alpha,
            other.pk,
            self.START,
            self.END,
            room_number=room.pk,
        )
        self.assertIsNone(record)
        self.assertEqual([name for name, _message in problems], ["room"])
        self.assertIn(str(existing.pk), problems[0][1])
        self.assertEqual(notes, ["room_conflict"])
        self.assertEqual(Session.objects.filter(organization=self.alpha).count(), 1)

    def test_warn_saves_and_returns_the_warning_codes(self):
        self.alpha.schedule_conflict_policy = SCHEDULE_WARN
        self.alpha.save(update_fields=["schedule_conflict_policy"])
        room = self.make_room("Studio")
        cohort = self.make_cohort(name="Autumn A")
        other = self.make_cohort(name="Autumn B")
        self.session(cohort, room=room)
        record, problems, notes = create_session(
            self.owner,
            self.alpha,
            other.pk,
            self.START,
            self.END,
            room_number=room.pk,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["room_warning"])
        self.assertIsNotNone(record)
        self.assertEqual(len(record.conflict_warnings), 1)

    def test_instructor_overlap_comes_from_the_cohorts_assignments(self):
        staff = self.make_staff()
        cohort = self.make_cohort(name="Autumn A")
        other = self.make_cohort(name="Autumn B")
        self.assign(staff, cohort)
        self.assign(staff, other)
        self.session(cohort)
        record, problems, notes = create_session(
            self.owner, self.alpha, other.pk, self.START, self.END
        )
        self.assertIsNone(record)
        self.assertEqual([name for name, _message in problems], ["cohort"])
        self.assertEqual(notes, ["instructor_conflict"])

    def test_cancelled_sessions_never_conflict(self):
        room = self.make_room("Studio")
        cohort = self.make_cohort(name="Autumn A")
        other = self.make_cohort(name="Autumn B")
        first = self.session(cohort, room=room)
        cancel_session(self.owner, self.alpha, first.pk)
        record, problems, notes = create_session(
            self.owner,
            self.alpha,
            other.pk,
            self.START,
            self.END,
            room_number=room.pk,
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)

    def test_editing_a_time_excludes_the_edited_session_itself(self):
        room = self.make_room("Studio")
        cohort = self.make_cohort(name="Autumn A")
        other = self.make_cohort(name="Autumn B")
        first = self.session(cohort, room=room)
        second = self.session(
            other,
            self.START + timedelta(hours=1),
            self.END + timedelta(hours=1),
            room=room,
        )
        record, problems, notes = edit_session(
            self.owner,
            self.alpha,
            second.pk,
            "start_at",
            self.START + timedelta(minutes=30),
        )
        self.assertIsNone(record)
        self.assertEqual([name for name, _message in problems], ["room"])
        self.assertEqual(notes, ["room_conflict"])
        second.refresh_from_db()
        self.assertEqual(
            second.start_at, self.START + timedelta(hours=1)
        )
        record, problems, notes = edit_session(
            self.owner,
            self.alpha,
            first.pk,
            "end_at",
            self.START + timedelta(minutes=45),
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)


class PatternTests(SchedulingFixture):
    """SCH-002/SCH-003: recurring generation, all or nothing, DST-aware."""

    def test_weekdays_are_canonical_and_the_range_is_inclusive(self):
        cohort = self.make_cohort(name="Autumn A")
        pattern, problems, notes = create_schedule_pattern(
            self.owner,
            self.alpha,
            cohort.pk,
            ["fri", "mon"],
            date(2026, 9, 7),
            date(2026, 9, 13),
            time(9, 0),
            time(10, 0),
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(pattern.weekdays, "mon,fri")
        self.assertEqual(
            list(
                pattern.sessions.order_by("start_at").values_list(
                    "start_at", flat=True
                )
            ),
            [
                datetime(2026, 9, 7, 7, 0, tzinfo=timezone.utc),
                datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc),
            ],
        )

    def test_an_invalid_weekday_is_refused(self):
        cohort = self.make_cohort(name="Autumn A")
        record, problems, notes = create_schedule_pattern(
            self.owner,
            self.alpha,
            cohort.pk,
            ["funday"],
            date(2026, 9, 7),
            date(2026, 9, 7),
            time(9, 0),
            time(10, 0),
        )
        self.assertIsNone(record)
        self.assertEqual([name for name, _message in problems], ["weekdays"])
        self.assertIn("mon", WEEKDAY_CODES)
        self.assertEqual(SchedulePattern.objects.count(), 0)

    def test_prevent_writes_neither_the_pattern_nor_any_session(self):
        room = self.make_room("Studio")
        cohort = self.make_cohort(name="Autumn A")
        create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 9, 7, 7, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc),
            room_number=room.pk,
        )
        record, problems, notes = create_schedule_pattern(
            self.owner,
            self.alpha,
            cohort.pk,
            ["mon"],
            date(2026, 9, 7),
            date(2026, 9, 14),
            time(9, 0),
            time(10, 0),
            room_number=room.pk,
        )
        self.assertIsNone(record)
        self.assertEqual([name for name, _message in problems], ["room"])
        self.assertEqual(notes, ["room_conflict"])
        self.assertEqual(SchedulePattern.objects.count(), 0)
        self.assertEqual(Session.objects.count(), 1)

    def test_fall_back_keeps_the_first_fold(self):
        cohort = self.make_cohort(name="Autumn A")
        pattern, problems, notes = create_schedule_pattern(
            self.owner,
            self.alpha,
            cohort.pk,
            ["sun"],
            date(2026, 10, 25),
            date(2026, 10, 25),
            time(2, 30),
            time(3, 30),
        )
        self.assertEqual((problems, notes), ([], []))
        session = pattern.sessions.get()
        # Stored consistently: the first fold (+02:00) is 00:30 UTC.
        self.assertEqual(
            session.start_at, datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(
            session.start_at.astimezone(ZoneInfo("Europe/Berlin")).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "2026-10-25 02:30",
        )

    def test_spring_forward_wall_time_is_refused_not_shifted(self):
        cohort = self.make_cohort(name="Autumn A")
        record, problems, notes = create_schedule_pattern(
            self.owner,
            self.alpha,
            cohort.pk,
            ["sun"],
            date(2026, 3, 29),
            date(2026, 3, 29),
            time(2, 30),
            time(3, 30),
        )
        self.assertIsNone(record)
        self.assertEqual([name for name, _message in problems], ["start_time"])
        self.assertIn("2026-03-29", problems[0][1])
        self.assertEqual(notes, [])
        self.assertEqual(SchedulePattern.objects.count(), 0)
        self.assertEqual(Session.objects.count(), 0)

    def test_another_centres_cohort_cannot_be_scheduled(self):
        record, problems, notes = create_schedule_pattern(
            self.owner,
            self.alpha,
            999999,
            ["mon"],
            date(2026, 9, 7),
            date(2026, 9, 7),
            time(9, 0),
            time(10, 0),
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["cohort_not_found"]))


class CohortDateTests(SchedulingFixture):
    """SCH-010 (partial): a session outside its cohort's dates warns."""

    def test_one_off_outside_the_cohort_dates_warns_but_saves(self):
        cohort = self.make_cohort(
            name="Autumn A", start_date=date(2026, 9, 10)
        )
        record, problems, notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(problems, [])
        self.assertIsNotNone(record)
        self.assertIn("cohort_dates_warning", notes)
        self.assertTrue(record.cohort_dates_warning)

    def test_an_edit_into_the_cohort_dates_clears_the_warning(self):
        cohort = self.make_cohort(
            name="Autumn A", start_date=date(2026, 9, 1)
        )
        record, _problems, notes = create_session(
            self.owner,
            self.alpha,
            cohort.pk,
            datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        )
        self.assertIn("cohort_dates_warning", notes)
        # End first, so the start can be moved without crossing the end; both
        # edits land inside the cohort's dates and the warning disappears.
        moved, problems, notes = edit_session(
            self.owner,
            self.alpha,
            record.pk,
            "end_at",
            datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(problems, [])
        moved, problems, notes = edit_session(
            self.owner,
            self.alpha,
            record.pk,
            "start_at",
            datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(problems, [])
        self.assertNotIn("cohort_dates_warning", notes)


class CancellationTests(SchedulingFixture):
    """SCH-008: cancellation is terminal, history stays, corrections still work."""

    def setUp(self):
        super().setUp()
        self.cohort = self.make_cohort(name="Autumn A")
        self.session, _problems, _notes = create_session(
            self.owner,
            self.alpha,
            self.cohort.pk,
            datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        )

    def record(self):
        return record_attendance(
            self.owner,
            self.alpha,
            self.session.pk,
            self.student.student_number,
            ATTENDANCE_PRESENT,
        )

    def test_cancel_is_terminal_and_a_second_cancel_changes_nothing(self):
        record, problems, notes = cancel_session(
            self.owner, self.alpha, self.session.pk
        )
        self.assertEqual((problems, notes), ([], ["cancelled"]))
        self.assertEqual(record.status, SESSION_CANCELLED)
        self.assertIsNotNone(record.cancelled_at)
        first_cancel = record.cancelled_at
        record, _problems, notes = cancel_session(
            self.owner, self.alpha, self.session.pk
        )
        self.assertEqual(notes, ["already_cancelled"])
        self.assertEqual(record.cancelled_at, first_cancel)

    def test_attendance_after_cancel_is_refused_but_corrections_still_work(self):
        saved, problems, notes = enroll_student(
            self.owner,
            self.alpha,
            self.cohort.pk,
            self.student.student_number,
        )
        self.assertEqual((problems, notes), ([], []))
        record, problems, notes = self.record()
        self.assertEqual((problems, notes), ([], []))
        attendance = Attendance.objects.get(
            session=self.session, student=self.student
        )
        self.assertEqual(attendance.status, ATTENDANCE_PRESENT)
        cancel_session(self.owner, self.alpha, self.session.pk)
        _record, _problems, notes = self.record()
        self.assertEqual(notes, ["session_cancelled"])
        record, problems, notes = correct_attendance(
            self.owner,
            self.alpha,
            self.session.pk,
            self.student.student_number,
            ATTENDANCE_LATE,
        )
        self.assertEqual((problems, notes), ([], []))
        attendance.refresh_from_db()
        self.assertEqual(attendance.status, ATTENDANCE_LATE)

    def test_time_and_room_are_frozen_but_the_note_stays_editable(self):
        room = self.make_room("Studio")
        cancel_session(self.owner, self.alpha, self.session.pk)
        _record, _problems, notes = edit_session(
            self.owner,
            self.alpha,
            self.session.pk,
            "start_at",
            datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(notes, ["session_cancelled"])
        _record, _problems, notes = set_session_room(
            self.owner, self.alpha, self.session.pk, room.pk
        )
        self.assertEqual(notes, ["session_cancelled"])
        record, problems, notes = edit_session(
            self.owner,
            self.alpha,
            self.session.pk,
            "note",
            "Moved to next term",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.note, "Moved to next term")

    def test_no_new_audit_action_is_written_by_cancelling(self):
        before = AuditLog.objects.count()
        cancel_session(self.owner, self.alpha, self.session.pk)
        self.assertEqual(AuditLog.objects.count(), before)
