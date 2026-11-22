"""The address table of the whole product (AUTH-001..AUTH-005, SEC-002).

The scheme is the on-disk form: every pattern ends at a directory name, and
the name= value of each pattern is a clean identifier the page chain and the
test client resolve through (reverse()/resolve()). Nothing here is a page:
each view answers one kind of address, and the tenant views re-check the
membership at the server (AUTH-005). o/<slug>/center/... is the only place
a tenant is ever addressed; the account layer's addresses (/accounts/...)
are not inside any tenant.

healthz/ is the deployed health path (the README once said /health/; the
docs are being corrected to match this file, not the other way around).
"""

from django.urls import path

from apps.accounts.views import choose, landing, sign_in, sign_out
from apps.organizations.views import dashboard as tenant_dashboard
from apps.organizations.views import members_page as tenant_members
from apps.organizations.views import members_manage
from apps.organizations.views import settings_page as tenant_settings
from apps.organizations.views import settings_change

from apps.students.views import applications_manage
from apps.students.views import applications_page as tenant_applications
from apps.students.views import student_profile as tenant_student
from apps.students.views import students_page as tenant_students
from apps.students.views import students_manage

from apps.academics.views import attendance_manage
from apps.academics.views import attendance_page as tenant_attendance
from apps.academics.views import attendance_register_csv
from apps.academics.views import attendance_report_csv
from apps.academics.views import attendance_session_page as tenant_attendance_session
from apps.academics.views import rooms_manage
from apps.academics.views import rooms_page as tenant_rooms
from apps.academics.views import schedule_day
from apps.academics.views import schedule_week
from apps.academics.views import sessions_manage
from apps.academics.views import sessions_page as tenant_sessions
from apps.academics.views import cohort_page as tenant_cohort
from apps.academics.views import cohorts_manage
from apps.academics.views import cohorts_page as tenant_cohorts
from apps.academics.views import course_page as tenant_course
from apps.academics.views import courses_manage
from apps.academics.views import courses_page as tenant_courses
from apps.academics.views import enrollments_manage
from apps.academics.views import enrollments_page as tenant_enrollments
from apps.academics.views import period_page as tenant_period
from apps.academics.views import periods_manage
from apps.academics.views import periods_page as tenant_periods
from apps.academics.views import program_page as tenant_program
from apps.academics.views import programs_manage
from apps.academics.views import programs_page as tenant_programs
from apps.academics.views import reports_csv
from apps.academics.views import reports_page as tenant_reports

from apps.staff.views import staff_member as tenant_staff_member
from apps.staff.views import staff_manage
from apps.staff.views import staff_page as tenant_staff
from apps.staff.views import staff_self_view

from apps.finances.views import adjustments_manage
from apps.finances.views import adjustments_page as tenant_adjustments
from apps.finances.views import adjustment_detail as tenant_adjustment
from apps.finances.views import audit_page as tenant_audit
from apps.finances.views import expenses_manage
from apps.finances.views import expenses_page as tenant_expenses
from apps.finances.views import expense_detail as tenant_expense
from apps.finances.views import finances_page as tenant_finances
from apps.finances.views import history_page as tenant_history
from apps.finances.views import installments_page as tenant_installments
from apps.finances.views import obligation_detail as tenant_obligation
from apps.finances.views import obligations_manage
from apps.finances.views import obligations_page as tenant_obligations
from apps.finances.views import payment_detail as tenant_payment
from apps.finances.views import payments_manage
from apps.finances.views import payments_page as tenant_payments
from apps.finances.views import plans_page as tenant_plans
from apps.finances.views import receipt_csv as tenant_receipt_csv
from apps.finances.views import receipts_page as tenant_receipts
from apps.finances.views import receipt_detail as tenant_receipt

from apps.grading.views import assessment_detail as tenant_grading_assessment
from apps.grading.views import assessment_results_csv as tenant_assessment_results_csv
from apps.grading.views import assessment_results_page as tenant_assessment_results
from apps.grading.views import assessments_manage as tenant_assessments_manage
from apps.grading.views import assessments_page as tenant_grading_assessments
from apps.grading.views import cohort_report_csv as tenant_cohort_report_csv
from apps.grading.views import grading_page as tenant_grading
from apps.grading.views import reports_page as tenant_grading_reports
from apps.grading.views import results_manage as tenant_results_manage
from apps.grading.views import results_page as tenant_grading_results

from config.views import health

urlpatterns = [
    path("", landing, name="landing"),
    path("healthz/", health, name="health"),
    path("accounts/sign-in/", sign_in, name="sign_in"),
    path("accounts/sign-out/", sign_out, name="sign_out"),
    path("accounts/choose/", choose, name="choose"),
    path("o/<str:slug>/center/dashboard/", tenant_dashboard, name="tenant_dashboard"),
    path("o/<str:slug>/center/settings/", tenant_settings, name="tenant_settings"),
    path("o/<str:slug>/center/settings/change/", settings_change, name="settings_change"),
    path("o/<str:slug>/center/members/", tenant_members, name="tenant_members"),
    path("o/<str:slug>/center/members/manage/", members_manage, name="members_manage"),
    path("o/<str:slug>/center/students/", tenant_students, name="tenant_students"),
    # The literal manage/ address before the <str:number> catch-all: a number
    # named "manage" would otherwise be read as a person, not as the page
    # records are made on.
    path(
        "o/<str:slug>/center/students/manage/",
        students_manage,
        name="students_manage",
    ),
    path(
        "o/<str:slug>/center/students/<str:number>/",
        tenant_student,
        name="tenant_student",
    ),
    path("o/<str:slug>/center/applications/", tenant_applications, name="tenant_applications"),
    path(
        "o/<str:slug>/center/applications/manage/",
        applications_manage,
        name="applications_manage",
    ),
    path("o/<str:slug>/center/courses/", tenant_courses, name="tenant_courses"),
    # Literal manage/ before the number catch-all, exactly as students does.
    path(
        "o/<str:slug>/center/courses/manage/",
        courses_manage,
        name="courses_manage",
    ),
    path(
        "o/<str:slug>/center/courses/<str:number>/",
        tenant_course,
        name="tenant_course",
    ),
    path("o/<str:slug>/center/cohorts/", tenant_cohorts, name="tenant_cohorts"),
    path(
        "o/<str:slug>/center/cohorts/manage/",
        cohorts_manage,
        name="cohorts_manage",
    ),
    path(
        "o/<str:slug>/center/cohorts/<str:number>/",
        tenant_cohort,
        name="tenant_cohort",
    ),
    path(
        "o/<str:slug>/center/enrollments/",
        tenant_enrollments,
        name="tenant_enrollments",
    ),
    path(
        "o/<str:slug>/center/enrollments/manage/",
        enrollments_manage,
        name="enrollments_manage",
    ),
    # Sessions and the attendance recorded on them (SCH-002/003/004, ATT-001..010).
    # The one-record attendance address is the centre's own number: the register
    # CSV form comes before the bare record form, so "register.csv" is never read
    # as a session number, and the literal manage/ form comes before the number
    # catch-all, exactly as every other record page does.
    path("o/<str:slug>/center/sessions/", tenant_sessions, name="tenant_sessions"),
    path(
        "o/<str:slug>/center/sessions/manage/",
        sessions_manage,
        name="sessions_manage",
    ),
    # SCH-007: the day and week timetable views filter by a ?date= the page
    # itself writes, so no record number is ever read from these addresses.
    path(
        "o/<str:slug>/center/schedule/day/",
        schedule_day,
        name="schedule_day",
    ),
    path(
        "o/<str:slug>/center/schedule/week/",
        schedule_week,
        name="schedule_week",
    ),
    # SCH-001: the centre's bookable rooms.
    path("o/<str:slug>/center/rooms/", tenant_rooms, name="tenant_rooms"),
    path(
        "o/<str:slug>/center/rooms/manage/",
        rooms_manage,
        name="rooms_manage",
    ),
    path("o/<str:slug>/center/attendance/", tenant_attendance, name="tenant_attendance"),
    path(
        "o/<str:slug>/center/attendance/manage/",
        attendance_manage,
        name="attendance_manage",
    ),
    path(
        "o/<str:slug>/center/attendance/<str:number>/register.csv",
        attendance_register_csv,
        name="attendance_register_csv",
    ),
    path(
        "o/<str:slug>/center/attendance/<str:number>/",
        tenant_attendance_session,
        name="tenant_attendance_session",
    ),
    path("o/<str:slug>/center/programs/", tenant_programs, name="tenant_programs"),
    # Literal manage/ before the number catch-all, as the other record pages do.
    path(
        "o/<str:slug>/center/programs/manage/",
        programs_manage,
        name="programs_manage",
    ),
    path(
        "o/<str:slug>/center/programs/<str:number>/",
        tenant_program,
        name="tenant_program",
    ),
    path("o/<str:slug>/center/periods/", tenant_periods, name="tenant_periods"),
    path(
        "o/<str:slug>/center/periods/manage/",
        periods_manage,
        name="periods_manage",
    ),
    path(
        "o/<str:slug>/center/periods/<str:number>/",
        tenant_period,
        name="tenant_period",
    ),
    # Reports are read-only addresses; the CSV export carries the same filters
    # and answers under the same permission (REP-005/REP-007).
    path("o/<str:slug>/center/reports/", tenant_reports, name="tenant_reports"),
    path(
        "o/<str:slug>/center/reports/enrollments.csv",
        reports_csv,
        name="reports_csv",
    ),
    # The attendance statistics report as CSV (ATT-009): the same read permission
    # the reports page asks, the same filters, no export of the people's own data.
    path(
        "o/<str:slug>/center/reports/attendance.csv",
        attendance_report_csv,
        name="attendance_report_csv",
    ),
    path("o/<str:slug>/center/staff/", tenant_staff, name="tenant_staff"),
    # Literal manage/ before the <str:number> catch-all, as the other
    # record pages do: "manage" must never be read as a staff number.
    path(
        "o/<str:slug>/center/staff/manage/",
        staff_manage,
        name="staff_manage",
    ),
    path(
        "o/<str:slug>/center/staff/<str:number>/",
        tenant_staff_member,
        name="tenant_staff_member",
    ),
    # An instructor's own record: the bare address first, so a number typed
    # after self/ still answers about the viewer, never about a record
    # picked by the URL (the viewer's identity is the one that counts).
    path(
        "o/<str:slug>/center/self/",
        staff_self_view,
        name="staff_self",
    ),
    path(
        "o/<str:slug>/center/self/<str:number>/",
        staff_self_view,
        name="staff_self_number",
    ),
    # The finance pages (FIN-001..FIN-015). The hub first, then one address
    # group per record kind; inside each group the literal manage/ comes
    # before the number catch-all, and receipts answer their .csv address
    # before their HTML one: a typed number must never be read as a word
    # (SEC-002's address contract, kept the same as every other area's).
    path("o/<str:slug>/center/finances/", tenant_finances, name="finances_hub"),
    path(
        "o/<str:slug>/center/finances/obligations/",
        tenant_obligations,
        name="finances_obligations",
    ),
    path(
        "o/<str:slug>/center/finances/obligations/manage/",
        obligations_manage,
        name="finances_obligations_manage",
    ),
    path(
        "o/<str:slug>/center/finances/obligations/<str:number>/",
        tenant_obligation,
        name="finances_obligation_detail",
    ),
    path(
        "o/<str:slug>/center/finances/plans/",
        tenant_plans,
        name="finances_plans",
    ),
    path(
        "o/<str:slug>/center/finances/installments/",
        tenant_installments,
        name="finances_installments",
    ),
    path(
        "o/<str:slug>/center/finances/payments/",
        tenant_payments,
        name="finances_payments",
    ),
    path(
        "o/<str:slug>/center/finances/payments/manage/",
        payments_manage,
        name="finances_payments_manage",
    ),
    path(
        "o/<str:slug>/center/finances/payments/<str:number>/",
        tenant_payment,
        name="finances_payment_detail",
    ),
    path(
        "o/<str:slug>/center/finances/adjustments/",
        tenant_adjustments,
        name="finances_adjustments",
    ),
    path(
        "o/<str:slug>/center/finances/adjustments/manage/",
        adjustments_manage,
        name="finances_adjustments_manage",
    ),
    path(
        "o/<str:slug>/center/finances/adjustments/<str:number>/",
        tenant_adjustment,
        name="finances_adjustment_detail",
    ),
    path(
        "o/<str:slug>/center/finances/expenses/",
        tenant_expenses,
        name="finances_expenses",
    ),
    path(
        "o/<str:slug>/center/finances/expenses/manage/",
        expenses_manage,
        name="finances_expenses_manage",
    ),
    path(
        "o/<str:slug>/center/finances/expenses/<str:number>/",
        tenant_expense,
        name="finances_expense_detail",
    ),
    path(
        "o/<str:slug>/center/finances/receipts/",
        tenant_receipts,
        name="finances_receipts",
    ),
    path(
        "o/<str:slug>/center/finances/receipts/<str:number>.csv",
        tenant_receipt_csv,
        name="finances_receipt_csv",
    ),
    path(
        "o/<str:slug>/center/finances/receipts/<str:number>/",
        tenant_receipt,
        name="finances_receipt_detail",
    ),
    path(
        "o/<str:slug>/center/finances/history/",
        tenant_history,
        name="finances_history",
    ),
    path(
        "o/<str:slug>/center/finances/audit/",
        tenant_audit,
        name="finances_audit",
    ),
    # Phase 3G (GRD-001..GRD-007): the grading pages of a tenant centre. The
    # literal manage/ and reports/ addresses come before the <str:number>
    # catch-alls, so a number named "manage" is still read as the page and
    # not as a record (the ordering rule every other area follows).
    path("o/<str:slug>/center/grading/", tenant_grading, name="grading"),
    path(
        "o/<str:slug>/center/grading/assessments/",
        tenant_grading_assessments,
        name="grading_assessments",
    ),
    path(
        "o/<str:slug>/center/grading/assessments/manage/",
        tenant_assessments_manage,
        name="grading_assessments_manage",
    ),
    path(
        "o/<str:slug>/center/grading/assessments/<str:number>/results.csv",
        tenant_assessment_results_csv,
        name="grading_assessment_results_csv",
    ),
    path(
        "o/<str:slug>/center/grading/assessments/<str:number>/results/",
        tenant_assessment_results,
        name="grading_assessment_results",
    ),
    path(
        "o/<str:slug>/center/grading/assessments/<str:number>/",
        tenant_grading_assessment,
        name="grading_assessment_detail",
    ),
    path(
        "o/<str:slug>/center/grading/results/",
        tenant_grading_results,
        name="grading_results",
    ),
    path(
        "o/<str:slug>/center/grading/results/manage/",
        tenant_results_manage,
        name="grading_results_manage",
    ),
    path(
        "o/<str:slug>/center/grading/reports/cohort.csv",
        tenant_cohort_report_csv,
        name="grading_cohort_report_csv",
    ),
    path(
        "o/<str:slug>/center/grading/reports/",
        tenant_grading_reports,
        name="grading_reports",
    ),
]
