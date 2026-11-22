"""The one page chain: every response this product returns is built here.

One call builds any page: a kind (which page) and the data it shows. A
tenant kind also needs the resolved center; a project kind never does, and a
project page can never show anything about a center it was not handed (SEC-002).
All product copy comes from the central glossary and is injected here, so no
caller can skip it and no template holds literal product copy:

- center pages use that center's preferred_locale (ORG-002: a center speaks
  in its own language, and its pages use it whichever person opens them),
- project pages use the language of the center the person is working in,
  falling back to the project language when no working center is set or the
  session's value no longer names an existing center (the session is never
  trusted as authorization - only re-checked - DECISIONS).

The X-CohortDesk-Page header records the page the view chose. It is not
authorization (SEC-001: every request re-decides on the server); it exists so
tests can prove which page answered, which no hidden browser state does.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.template.loader import render_to_string

from apps.i18n import ui_strings
from apps.accounts.services import working_center as session_working_center
from apps.organizations.models import Organization
from apps.organizations.permissions import role_has_permission
from apps.organizations.services import membership
from apps.staff.models import Staff

# The account layer's own pages, at addresses no tenant is under.
PROJECT_PAGES = {
    "landing": "landing.html",
    "problem": "problem.html",
    "denied": "denied.html",
    "sign_in": "sign_in.html",
    "choose": "choose.html",
}

# The chain every tenant centre has, in that centre's own language.
CENTER_PAGES = {
    "dashboard": "center/dashboard.html",
    "settings": "center/settings.html",
    "settings_change": "center/settings_change.html",
    "members": "center/members.html",
    "members_manage": "center/members_manage.html",
    "students": "center/students.html",
    "student": "center/student.html",
    "students_manage": "center/students_manage.html",
    "applications": "center/applications.html",
    "applications_manage": "center/applications_manage.html",
    "courses": "center/courses.html",
    "course": "center/course.html",
    "courses_manage": "center/courses_manage.html",
    "cohorts": "center/cohorts.html",
    "cohort": "center/cohort.html",
    "cohorts_manage": "center/cohorts_manage.html",
    "enrollments": "center/enrollments.html",
    "enrollments_manage": "center/enrollments_manage.html",
    "programs": "center/programs.html",
    "program": "center/program.html",
    "programs_manage": "center/programs_manage.html",
    "periods": "center/periods.html",
    "period": "center/period.html",
    "periods_manage": "center/periods_manage.html",
    "reports": "center/reports.html",
    "sessions": "center/sessions.html",
    "sessions_manage": "center/sessions_manage.html",
    "schedule_day": "center/schedule_day.html",
    "schedule_week": "center/schedule_week.html",
    "rooms": "center/rooms.html",
    "rooms_manage": "center/rooms_manage.html",
    "attendance": "center/attendance.html",
    "attendance_manage": "center/attendance_manage.html",
    "attendance_session": "center/attendance_session.html",
    "staff": "center/staff.html",
    "staff_member": "center/staff_member.html",
    "staff_manage": "center/staff_manage.html",
    "staff_self": "center/staff_self.html",
    "finances": "center/finances.html",
    "finances_obligations": "center/finances_obligations.html",
    "finances_obligation": "center/finances_obligation.html",
    "finances_obligations_manage": "center/finances_obligations_manage.html",
    "finances_plans": "center/finances_plans.html",
    "finances_installments": "center/finances_installments.html",
    "finances_payments": "center/finances_payments.html",
    "finances_payment": "center/finances_payment.html",
    "finances_payments_manage": "center/finances_payments_manage.html",
    "finances_adjustments": "center/finances_adjustments.html",
    "finances_adjustment": "center/finances_adjustment.html",
    "finances_adjustments_manage": "center/finances_adjustments_manage.html",
    "finances_expenses": "center/finances_expenses.html",
    "finances_expense": "center/finances_expense.html",
    "finances_expenses_manage": "center/finances_expenses_manage.html",
    "finances_receipts": "center/finances_receipts.html",
    "finances_receipt": "center/finances_receipt.html",
    "finances_history": "center/finances_history.html",
    "finances_audit": "center/finances_audit.html",
    "grading": "center/grading.html",
    "grading_assessments": "center/grading_assessments.html",
    "grading_assessments_manage": "center/grading_assessments_manage.html",
    "grading_assessment": "center/grading_assessment.html",
    "grading_results": "center/grading_results.html",
    "grading_results_manage": "center/grading_results_manage.html",
    "grading_assessment_results": "center/grading_assessment_results.html",
    "grading_reports": "center/grading_reports.html",
    "not_found": "center/not_found.html",
    "denied": "center/denied.html",
}


def center_ui(center):
    """The glossary a tenant centre's pages speak, in the centre's own language."""
    return ui_strings(center.preferred_locale)


# Which permission each tenant navigation name needs. None means any active
# member of this centre. Hiding a name is presentation only (SEC-001): an
# address a role cannot reach is answered 403 by the view, and an address a
# role can reach answers whatever the role may see, shown or hidden alike.
NAV_LINKS = {
    "dashboard": None,
    "settings": "organization.settings.view",
    "members": "organization.members.view",
    "students": "students.view",
    "applications": "students.application.view",
    "courses": "academics.course.view",
    "cohorts": "academics.cohort.view",
    "enrollments": "academics.enrollment.view",
    "sessions": "sessions.view",
    "schedule": "sessions.view",
    "rooms": "rooms.view",
    "attendance": "attendance.view",
    "programs": "academics.program.view",
    "periods": "academics.period.view",
    "reports": "academics.report.view",
    "staff": "staff.view",
    "my_staff": "staff.self.view",
    "finances": "finances.summary.view",
    "grading": "grading.assessment.view",
}


def center_nav(request, center):
    """Which tenant page names this person's own navigation shows here."""
    nav = {}
    role = None
    try:
        role = membership(request.user, center).role
    except PermissionDenied:
        role = None
    for kind, code in NAV_LINKS.items():
        if code is None:
            nav[kind] = role is not None
        else:
            nav[kind] = role_has_permission(role, code) if role else False
    # The self address always answers about the viewer, so the nav link names
    # the viewer's own linked record when there is one (presentation only:
    # the view re-decides everything and ignores any number typed in here).
    nav["self_href"] = ""
    if nav.get("my_staff"):
        record = Staff.objects.filter(organization=center, user=request.user).first()
        nav["self_href"] = (
            "center/self/" + record.staff_number + "/" if record else "center/self/"
        )
    return nav


def project_ui(request):
    """The glossary an account page speaks.

    The working centre recorded in the session decides it, but only after
    re-verification: an old session value that no longer names an existing
    centre never brings a language in, and the project language is used.
    """
    slug = session_working_center(request)
    if slug:
        center = Organization.objects.filter(slug=slug).first()
        if center:
            return ui_strings(center.preferred_locale)
    return ui_strings(settings.LANGUAGE_CODE)


def page(request, kind, data, center=None, status=200):
    """Build one response: the page of this kind, showing data, at status.

    kind is a key of PROJECT_PAGES, or of CENTER_PAGES when center is given.
    Every view answers through this one call, so the glossary, the language
    choice, and the page-kind header can never be skipped by a caller.
    """
    if center is not None:
        templates = CENTER_PAGES
        ui = center_ui(center)
    else:
        templates = PROJECT_PAGES
        ui = project_ui(request)
    context = {
        "ui": ui,
        "admin_enabled": settings.ADMIN_ENABLED,
        "center": center,
        # The shell needs to know whether to offer sign-in or sign-out. It is
        # presentation only: every page still re-decides authorization.
        "signed_in": request.user.is_authenticated,
    }
    if center is not None:
        # Hiding names is presentation only: every view still re-decides.
        context["nav"] = center_nav(request, center)
    context.update(data)
    # The request is passed through so Django's context processors run:
    # {% csrf_token %} needs the CSRF processor, and without a token the form
    # a browser shows can only be answered 403 (found by browser smoke test).
    html = render_to_string(templates[kind], context=context, request=request)
    response = HttpResponse(html, status=status)
    response["X-CohortDesk-Page"] = kind
    return response
