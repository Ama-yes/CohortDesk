"""Model and service tests for one centre's student and application records.

The rules under test are STU-001..STU-009 and ADM-001..ADM-008 as this
product's services implement them: organization-scoped student numbers
(STU-002), controlled status transitions (STU-007/ADM-003), archive instead
of deletion (STU-008), a duplicate signal that never merges (STU-009), and
conversion without re-entering information (ADM-004). Cross-tenant proof
through the URL layer lives in apps/students/test_access.py.

Every action here is called the way a page calls it (service function, not
model API), because the service layer is the one place the rules live.
"""

from datetime import date, timedelta

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase

from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.roles import (
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)

from .models import Application, Student
from .services import (
    APPLICATION_TRANSITIONS,
    EDITABLE_STUDENT_FIELDS,
    ENTRY_STATUSES,
    STUDENT_TRANSITIONS,
    _application_here,
    _student_here,
    archive_student,
    convert_application,
    create_student,
    duplicate_found,
    edit_student,
    record_application,
    set_application_status,
    set_student_status,
)
from .statuses import (
    APP_STATUS_ACCEPTED,
    APP_STATUS_ARCHIVED,
    APP_STATUS_NEW,
    APP_STATUS_REJECTED,
    APP_STATUS_UNDER_REVIEW,
    APPLICATION_STATUSES,
    STATUS_ACTIVE,
    STATUS_APPLICANT,
    STATUS_ARCHIVED,
    STATUS_GRADUATED,
    STATUS_SUSPENDED,
    STATUS_WITHDRAWN,
    STUDENT_STATUSES,
)


class CenterFixture(TestCase):
    """One centre with an owner, and the record every test starts from."""

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
        Members.objects.create(
            user=self.owner, organization=self.alpha, role=ROLE_OWNER
        )
        self.beta_owner = CohortUser.objects.create_user(
            username="beta-owner", password="x", display_name="Beta Owner"
        )
        Members.objects.create(
            user=self.beta_owner, organization=self.beta, role=ROLE_OWNER
        )

    def make_user(self, username, role, organization=None):
        user = CohortUser.objects.create_user(username=username, password="x")
        Members.objects.create(
            user=user, organization=organization or self.alpha, role=role
        )
        return user


class VocabularyTests(TestCase):
    """STU-007/ADM-003: the two vocabularies are fixed, not free text."""

    def test_student_vocabulary(self):
        self.assertEqual(
            STUDENT_STATUSES,
            (
                STATUS_APPLICANT,
                STATUS_ACTIVE,
                STATUS_SUSPENDED,
                STATUS_GRADUATED,
                STATUS_WITHDRAWN,
                STATUS_ARCHIVED,
            ),
        )

    def test_application_vocabulary(self):
        self.assertEqual(
            APPLICATION_STATUSES,
            (
                APP_STATUS_NEW,
                APP_STATUS_UNDER_REVIEW,
                APP_STATUS_ACCEPTED,
                APP_STATUS_REJECTED,
                "withdrawn",
                APP_STATUS_ARCHIVED,
            ),
        )

    def test_entry_statuses_exclude_archived(self):
        """Archiving is an action on a record, never a way of making one."""
        self.assertEqual(ENTRY_STATUSES, (STATUS_APPLICANT, STATUS_ACTIVE))
        self.assertNotIn(STATUS_ARCHIVED, ENTRY_STATUSES)

    def test_archived_is_terminal_in_both_tables(self):
        self.assertEqual(STUDENT_TRANSITIONS[STATUS_ARCHIVED], ())
        self.assertEqual(APPLICATION_TRANSITIONS[APP_STATUS_ARCHIVED], ())
        self.assertEqual(APPLICATION_TRANSITIONS[APP_STATUS_ACCEPTED], ())


class StudentNumberTests(CenterFixture):
    """STU-002: a number is unique here, and only here."""

    def test_number_is_normalized_on_create(self):
        record, problems, notes = create_student(
            self.owner, self.alpha, "  ac-42 ", "Amina", "0550"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.student_number, "AC-42")

    def test_number_pattern_accepts_twenty_characters(self):
        record, problems, notes = create_student(
            self.owner, self.alpha, "A" * 20, "Amina", ""
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.student_number, "A" * 20)

    def test_invalid_number_saves_nothing(self):
        record, problems, notes = create_student(
            self.owner, self.alpha, "A" * 21, "Amina", ""
        )
        self.assertIsNone(record)
        self.assertEqual([field for field, _message in problems], ["student_number"])
        self.assertEqual(notes, [])
        self.assertEqual(Student.objects.count(), 0)

    def test_same_number_in_two_centres_names_two_people(self):
        alpha, _problems, _notes = create_student(
            self.owner, self.alpha, "AC-1", "Alpha Learner", ""
        )
        beta, _problems, _notes = create_student(
            self.beta_owner, self.beta, "AC-1", "Beta Learner", ""
        )
        self.assertNotEqual(alpha.pk, beta.pk)
        self.assertEqual(_student_here(self.alpha, "ac-1").name, "Alpha Learner")
        self.assertEqual(_student_here(self.beta, "ac-1").name, "Beta Learner")

    def test_database_rejects_duplicate_number_in_one_centre(self):
        Student.objects.create(
            organization=self.alpha, student_number="AC-1", name="First"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Student.objects.create(
                    organization=self.alpha, student_number="AC-1", name="Second"
                )
        self.assertEqual(Student.objects.filter(organization=self.alpha).count(), 1)

    def test_unknown_number_is_not_found_and_never_guessed(self):
        create_student(self.owner, self.alpha, "AC-1", "Amina", "")
        self.assertIsNone(_student_here(self.alpha, "AC-2"))
        self.assertIsNone(_student_here(self.beta, "AC-1"))


class StudentCreateTests(CenterFixture):
    """STU-001/STU-003/STU-009 and ADM-005's direct enrolment."""

    def test_entry_status_defaults_to_applicant(self):
        record, problems, notes = create_student(
            self.owner, self.alpha, "AC-1", "Amina", "0550", note="Walk-in"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, STATUS_APPLICANT)
        self.assertEqual(record.note, "Walk-in")
        self.assertIsNotNone(record.registered_at)
        self.assertIsNotNone(record.status_changed_at)

    def test_direct_enrolment_starts_active(self):
        record, problems, notes = create_student(
            self.owner, self.alpha, "AC-1", "Amina", "", status=STATUS_ACTIVE
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, STATUS_ACTIVE)

    def test_archived_is_not_an_entry_status(self):
        record, problems, notes = create_student(
            self.owner, self.alpha, "AC-1", "Amina", "", status=STATUS_ARCHIVED
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["entry"])
        self.assertEqual(Student.objects.count(), 0)

    def test_graduated_is_not_an_entry_status(self):
        _record, _problems, notes = create_student(
            self.owner, self.alpha, "AC-1", "Amina", "", status=STATUS_GRADUATED
        )
        self.assertEqual(notes, ["entry"])

    def test_blank_name_is_rejected_and_saves_nothing(self):
        record, problems, notes = create_student(
            self.owner, self.alpha, "AC-1", "   ", ""
        )
        self.assertIsNone(record)
        self.assertEqual([field for field, _message in problems], ["name"])
        self.assertEqual(notes, [])
        self.assertEqual(Student.objects.count(), 0)

    def test_same_name_and_contact_is_reported_and_never_merged(self):
        first, _problems, _notes = create_student(
            self.owner, self.alpha, "AC-1", "Amina", "0550"
        )
        record, problems, notes = create_student(
            self.owner, self.alpha, "AC-2", "  Amina  ", " 0550 "
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["duplicate"])
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(Student.objects.get().pk, first.pk)

    def test_blank_contact_never_matches(self):
        create_student(self.owner, self.alpha, "AC-1", "Amina", "")
        record, problems, notes = create_student(
            self.owner, self.alpha, "AC-2", "Amina", ""
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(Student.objects.count(), 2)

    def test_duplicate_signal_is_per_centre(self):
        create_student(self.owner, self.alpha, "AC-1", "Amina", "0550")
        self.assertTrue(duplicate_found(self.alpha, "Amina", "0550"))
        self.assertFalse(duplicate_found(self.beta, "Amina", "0550"))
        self.assertFalse(duplicate_found(self.alpha, "Amina", ""))


class StudentEditTests(CenterFixture):
    """STU-001/STU-003: one profile field at a time, validator decides."""

    def setUp(self):
        super().setUp()
        self.record, _problems, _notes = create_student(
            self.owner, self.alpha, "AC-1", "Amina", "0550"
        )

    def test_editable_fields_are_profile_values_only(self):
        self.assertEqual(
            EDITABLE_STUDENT_FIELDS,
            ("name", "contact", "birth_date", "address", "note"),
        )
        self.assertNotIn("student_number", EDITABLE_STUDENT_FIELDS)
        self.assertNotIn("status", EDITABLE_STUDENT_FIELDS)

    def test_one_field_is_changed_and_stored_trimmed(self):
        record, problems, notes = edit_student(
            self.owner, self.alpha, "ac-1", "name", "  Amina B  "
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.pk, self.record.pk)
        self.record.refresh_from_db()
        self.assertEqual(self.record.name, "Amina B")
        self.assertEqual(self.record.contact, "0550")

    def test_rejected_value_changes_nothing(self):
        record, problems, notes = edit_student(
            self.owner, self.alpha, "AC-1", "name", "   "
        )
        self.assertIsNone(record)
        self.assertEqual([field for field, _message in problems], ["name"])
        self.assertEqual(notes, [])
        self.record.refresh_from_db()
        self.assertEqual(self.record.name, "Amina")

    def test_future_birth_date_is_rejected(self):
        tomorrow = date.today() + timedelta(days=1)
        record, problems, notes = edit_student(
            self.owner, self.alpha, "AC-1", "birth_date", tomorrow
        )
        self.assertIsNone(record)
        self.assertEqual([field for field, _message in problems], ["birth_date"])
        self.record.refresh_from_db()
        self.assertIsNone(self.record.birth_date)

    def test_birth_date_is_stored_and_can_be_cleared(self):
        born = date(2005, 4, 3)
        record, problems, notes = edit_student(
            self.owner, self.alpha, "AC-1", "birth_date", born
        )
        self.assertEqual((problems, notes), ([], []))
        record, problems, notes = edit_student(
            self.owner, self.alpha, "AC-1", "birth_date", None
        )
        self.assertEqual((problems, notes), ([], []))
        self.record.refresh_from_db()
        self.assertIsNone(self.record.birth_date)

    def test_identifier_and_status_are_not_editable(self):
        for field in ("student_number", "status", "registered_at"):
            record, problems, notes = edit_student(
                self.owner, self.alpha, "AC-1", field, "changed"
            )
            self.assertIsNone(record)
            self.assertEqual(notes, ["field_not_editable"])

    def test_unknown_record_is_not_found(self):
        record, problems, notes = edit_student(
            self.owner, self.alpha, "AC-9", "name", "Anyone"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])


class StudentTransitionTests(CenterFixture):
    """STU-007/STU-008: only the table moves a status, and archive keeps it."""

    def setUp(self):
        super().setUp()
        self.record, _problems, _notes = create_student(
            self.owner, self.alpha, "AC-1", "Amina", "0550"
        )

    def test_applicant_becomes_active(self):
        record, problems, notes = set_student_status(
            self.owner, self.alpha, "AC-1", STATUS_ACTIVE
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, STATUS_ACTIVE)
        self.assertIsNotNone(record.status_changed_at)

    def test_illegal_move_changes_nothing(self):
        record, problems, notes = set_student_status(
            self.owner, self.alpha, "AC-1", STATUS_GRADUATED
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, STATUS_APPLICANT)

    def test_active_can_graduate_and_graduated_cannot_reopen(self):
        set_student_status(self.owner, self.alpha, "AC-1", STATUS_ACTIVE)
        record, _problems, notes = set_student_status(
            self.owner, self.alpha, "AC-1", STATUS_GRADUATED
        )
        self.assertEqual(notes, [])
        self.assertEqual(record.status, STATUS_GRADUATED)
        record, _problems, notes = set_student_status(
            self.owner, self.alpha, "AC-1", STATUS_SUSPENDED
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])

    def test_archive_sets_the_status_and_the_moment(self):
        record, problems, notes = archive_student(self.owner, self.alpha, "AC-1")
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, STATUS_ARCHIVED)
        self.assertIsNotNone(record.archived_at)

    def test_archiving_twice_changes_nothing_more(self):
        first, _problems, _notes = archive_student(self.owner, self.alpha, "AC-1")
        second, _problems, notes = archive_student(self.owner, self.alpha, "AC-1")
        self.assertEqual(notes, ["already_archived"])
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.archived_at, first.archived_at)

    def test_archived_record_stays_findable(self):
        archive_student(self.owner, self.alpha, "AC-1")
        found = _student_here(self.alpha, "AC-1")
        self.assertIsNotNone(found)
        self.assertEqual(found.status, STATUS_ARCHIVED)
        self.assertEqual(Student.objects.filter(organization=self.alpha).count(), 1)

    def test_archived_cannot_return_to_a_working_status(self):
        archive_student(self.owner, self.alpha, "AC-1")
        record, _problems, notes = set_student_status(
            self.owner, self.alpha, "AC-1", STATUS_ACTIVE
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])

    def test_withdrawn_record_can_be_archived(self):
        set_student_status(self.owner, self.alpha, "AC-1", STATUS_WITHDRAWN)
        record, _problems, notes = archive_student(self.owner, self.alpha, "AC-1")
        self.assertEqual(notes, [])
        self.assertEqual(record.status, STATUS_ARCHIVED)

    def test_archive_needs_its_own_permission(self):
        receptionist = self.make_user("alpha-desk", ROLE_RECEPTIONIST)
        with self.assertRaises(PermissionDenied):
            archive_student(receptionist, self.alpha, "AC-1")
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, STATUS_APPLICANT)


class ApplicationRecordTests(CenterFixture):
    """ADM-001/ADM-002: what an application records, and what it refuses."""

    def test_application_keeps_the_course_as_typed_words(self):
        application, problems, notes = record_application(
            self.owner,
            self.alpha,
            "  Applied Learner ",
            course="  Spanish A1 ",
            contact="0770",
            source="  Poster ",
            note="  Asked for evenings ",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(application.desired_course, "Spanish A1")
        self.assertEqual(application.name, "Applied Learner")
        self.assertEqual(application.contact, "0770")
        self.assertEqual(application.source, "Poster")
        self.assertEqual(application.note, "Asked for evenings")
        self.assertEqual(application.status, APP_STATUS_NEW)
        self.assertIsNone(application.converted_student)
        self.assertIsNotNone(application.created_at)

    def test_application_needs_the_person_it_is_about(self):
        application, problems, notes = record_application(
            self.owner, self.alpha, "   "
        )
        self.assertIsNone(application)
        self.assertEqual([field for field, _message in problems], ["name"])
        self.assertEqual(Application.objects.count(), 0)

    def test_named_number_is_normalized_and_validated(self):
        application, problems, notes = record_application(
            self.owner, self.alpha, "Applied Learner", student_number=" ac-7 "
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(application.student_number, "AC-7")
        application, problems, notes = record_application(
            self.owner, self.alpha, "Other Learner", student_number="no spaces!"
        )
        self.assertIsNone(application)
        self.assertEqual([field for field, _message in problems], ["student_number"])

    def test_application_number_is_the_whole_number_only(self):
        application, _problems, _notes = record_application(
            self.owner, self.alpha, "Applied Learner"
        )
        self.assertEqual(
            _application_here(self.alpha, str(application.pk)).pk, application.pk
        )
        self.assertIsNone(_application_here(self.alpha, "abc"))
        self.assertIsNone(_application_here(self.alpha, "1.0"))
        self.assertIsNone(_application_here(self.beta, str(application.pk)))


class ApplicationTransitionTests(CenterFixture):
    """ADM-003: the application table, including accepted as terminal."""

    def setUp(self):
        super().setUp()
        self.application, _problems, _notes = record_application(
            self.owner, self.alpha, "Applied Learner"
        )

    def number(self):
        return str(self.application.pk)

    def test_review_acceptance_and_terminal_state(self):
        record, problems, notes = set_application_status(
            self.owner, self.alpha, self.number(), APP_STATUS_UNDER_REVIEW
        )
        self.assertEqual((problems, notes), ([], []))
        record, _problems, notes = set_application_status(
            self.owner, self.alpha, self.number(), APP_STATUS_ACCEPTED
        )
        self.assertEqual(notes, [])
        self.assertEqual(record.status, APP_STATUS_ACCEPTED)
        record, _problems, notes = set_application_status(
            self.owner, self.alpha, self.number(), APP_STATUS_REJECTED
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])

    def test_rejected_and_withdrawn_only_archive(self):
        set_application_status(
            self.owner, self.alpha, self.number(), APP_STATUS_REJECTED
        )
        record, _problems, notes = set_application_status(
            self.owner, self.alpha, self.number(), APP_STATUS_ACCEPTED
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])
        record, _problems, notes = set_application_status(
            self.owner, self.alpha, self.number(), APP_STATUS_ARCHIVED
        )
        self.assertEqual(notes, [])
        self.assertIsNotNone(record.archived_at)
        _record, _problems, notes = set_application_status(
            self.owner, self.alpha, self.number(), APP_STATUS_NEW
        )
        self.assertEqual(notes, ["transition"])

    def test_unknown_application_is_not_found(self):
        record, problems, notes = set_application_status(
            self.owner, self.alpha, "999999", APP_STATUS_UNDER_REVIEW
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])

    def test_another_centres_application_is_not_found_here(self):
        record, problems, notes = set_application_status(
            self.beta_owner, self.beta, self.number(), APP_STATUS_UNDER_REVIEW
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])


class ConversionTests(CenterFixture):
    """ADM-004: an accepted application creates its record, once."""

    def accepted(self, student_number="AC-7", name="Applied Learner",
                 contact="0770"):
        application, _problems, _notes = record_application(
            self.owner, self.alpha, name, contact=contact,
            student_number=student_number,
        )
        set_application_status(
            self.owner, self.alpha, str(application.pk), APP_STATUS_ACCEPTED
        )
        return application

    def test_unaccepted_application_is_not_converted(self):
        application, _problems, _notes = record_application(
            self.owner, self.alpha, "Applied Learner", student_number="AC-7"
        )
        record, problems, notes = convert_application(
            self.owner, self.alpha, str(application.pk)
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_accepted"])
        self.assertEqual(Student.objects.count(), 0)

    def test_accepted_application_without_a_number_waits(self):
        application = self.accepted(student_number="")
        record, problems, notes = convert_application(
            self.owner, self.alpha, str(application.pk)
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["needs_number"])
        self.assertEqual(Student.objects.count(), 0)
        application.refresh_from_db()
        self.assertIsNone(application.converted_student)

    def test_conversion_carries_the_information_over(self):
        application = self.accepted()
        record, problems, notes = convert_application(
            self.owner, self.alpha, str(application.pk)
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.student_number, "AC-7")
        self.assertEqual(record.name, "Applied Learner")
        self.assertEqual(record.contact, "0770")
        self.assertEqual(record.status, STATUS_ACTIVE)
        application.refresh_from_db()
        self.assertEqual(application.converted_student_id, record.pk)
        self.assertEqual(
            [link.pk for link in record.converted_applications.all()],
            [application.pk],
        )

    def test_converting_twice_changes_nothing(self):
        application = self.accepted()
        first, _problems, _notes = convert_application(
            self.owner, self.alpha, str(application.pk)
        )
        second, problems, notes = convert_application(
            self.owner, self.alpha, str(application.pk)
        )
        self.assertEqual(notes, ["converted_already"])
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(Student.objects.count(), 1)

    def test_duplicate_person_blocks_conversion(self):
        create_student(self.owner, self.alpha, "AC-1", "Applied Learner", "0770")
        application = self.accepted()
        record, problems, notes = convert_application(
            self.owner, self.alpha, str(application.pk)
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["duplicate"])
        self.assertEqual(Student.objects.count(), 1)
        application.refresh_from_db()
        self.assertIsNone(application.converted_student)

    def test_another_centres_application_is_never_converted(self):
        application = self.accepted()
        record, problems, notes = convert_application(
            self.beta_owner, self.beta, str(application.pk)
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])
        self.assertEqual(Student.objects.count(), 0)

    def test_conversion_is_open_to_the_front_desk(self):
        receptionist = self.make_user("alpha-desk", ROLE_RECEPTIONIST)
        application = self.accepted()
        record, problems, notes = convert_application(
            receptionist, self.alpha, str(application.pk)
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)


class ManagePermissionTests(CenterFixture):
    """Every write re-asks the central matrix; an instructor may only read."""

    def test_instructor_cannot_write_records(self):
        instructor = self.make_user("alpha-teacher", ROLE_INSTRUCTOR)
        with self.assertRaises(PermissionDenied):
            create_student(instructor, self.alpha, "AC-1", "Amina", "")
        with self.assertRaises(PermissionDenied):
            record_application(instructor, self.alpha, "Applied Learner")
        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(Application.objects.count(), 0)

    def test_receptionist_can_write_but_not_archive(self):
        receptionist = self.make_user("alpha-desk", ROLE_RECEPTIONIST)
        record, problems, notes = create_student(
            receptionist, self.alpha, "AC-1", "Amina", "0550"
        )
        self.assertEqual((problems, notes), ([], []))
        record, problems, notes = edit_student(
            receptionist, self.alpha, "AC-1", "contact", "0551"
        )
        self.assertEqual((problems, notes), ([], []))
        with self.assertRaises(PermissionDenied):
            archive_student(receptionist, self.alpha, "AC-1")


class NumberUsedTests(TransactionTestCase):
    """The database constraint is the guarantee behind the service's answer.

    These run without a wrapping transaction because both actions catch the
    constraint's error themselves, exactly as they do when a page posts a
    number that is already taken (a plain TestCase transaction would stay
    broken after that caught error, which no page does in production).
    """

    def setUp(self):
        self.center = Organization.objects.create(
            name="Alpha Center", slug="alpha-center", primary_currency="DZD"
        )
        self.owner = CohortUser.objects.create_user(username="alpha-owner", password="x")
        Members.objects.create(
            user=self.owner, organization=self.center, role=ROLE_OWNER
        )

    def test_create_reports_a_taken_number(self):
        Student.objects.create(
            organization=self.center, student_number="AC-9", name="Taken Person",
            contact="taken@example.test",
        )
        record, problems, notes = create_student(
            self.owner, self.center, "ac-9", "A New Person", ""
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["number_used"])
        self.assertEqual(
            Student.objects.filter(organization=self.center).count(), 1
        )

    def test_conversion_reports_a_taken_number(self):
        Student.objects.create(
            organization=self.center, student_number="AC-9", name="Taken Person",
            contact="taken@example.test",
        )
        application = Application.objects.create(
            organization=self.center,
            student_number="AC-9",
            name="Applied Learner",
            contact="app@example.test",
            status=APP_STATUS_ACCEPTED,
        )
        record, problems, notes = convert_application(
            self.owner, self.center, str(application.pk)
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["number_used"])
        application.refresh_from_db()
        self.assertIsNone(application.converted_student)
        self.assertEqual(Student.objects.count(), 1)
