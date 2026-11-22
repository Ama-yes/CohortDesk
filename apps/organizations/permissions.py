"""Central authorization layer: permission codes and which roles hold them.

Every protected operation calls require_permission(); templates only hide UI
(SEC-001: hiding is not authorization). Codes are `domain.action` strings.
M0 foundation matrix only; extended as features land (see TEST_PLAN).
"""

from .roles import (
    ROLE_OWNER,
    ROLE_ADMINISTRATOR,
    ROLE_RECEPTIONIST,
    ROLE_ACADEMIC_COORDINATOR,
    ROLE_INSTRUCTOR,
    ROLE_FINANCE_OFFICER,
)


PERMISSIONS = {
    "organization.settings.view": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    "organization.settings.change": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    "organization.members.view": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    "organization.members.manage": {ROLE_OWNER},
    "organization.members.invite": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    "students.view": {ROLE_OWNER, ROLE_ADMINISTRATOR, ROLE_RECEPTIONIST,
                      ROLE_ACADEMIC_COORDINATOR, ROLE_INSTRUCTOR},
    "students.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR, ROLE_RECEPTIONIST},
    "students.archive": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    "students.application.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                  ROLE_RECEPTIONIST},
    "students.application.accept": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                    ROLE_RECEPTIONIST},
    # Academic structure (ACAD-001/COH-001/COH-003). Instructors are given
    # the course catalogue only: SRS 4.4 scopes them to cohorts they are
    # assigned to, and staff assignment (STAFF-003) now records the links, but
    # the instructor's own scoped read view is not built yet, so cohort and
    # enrollment data stays closed to the instructor role until it is.
    "academics.course.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                              ROLE_ACADEMIC_COORDINATOR, ROLE_RECEPTIONIST,
                              ROLE_INSTRUCTOR},
    "academics.course.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                ROLE_ACADEMIC_COORDINATOR},
    "academics.cohort.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                              ROLE_ACADEMIC_COORDINATOR, ROLE_RECEPTIONIST},
    "academics.cohort.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                ROLE_ACADEMIC_COORDINATOR},
    "academics.enrollment.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                  ROLE_ACADEMIC_COORDINATOR,
                                  ROLE_RECEPTIONIST},
    "academics.enrollment.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                    ROLE_ACADEMIC_COORDINATOR,
                                    ROLE_RECEPTIONIST},
    # Program grouping (ACAD-004) is academic structure, read by exactly the
    # roles that read the course catalogue it groups. Making or changing one
    # belongs to the academic staff line SRS 4.4 names for courses, programs
    # and cohorts: owner, administrator and academic coordinator.
    "academics.program.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                               ROLE_ACADEMIC_COORDINATOR, ROLE_RECEPTIONIST,
                               ROLE_INSTRUCTOR},
    "academics.program.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                 ROLE_ACADEMIC_COORDINATOR},
    # Academic periods (ACAD-006) are the centre's own academic years. The
    # coordinator reads them to place academic work in a year; the desk reads
    # them the way it reads cohorts and enrollments. Making or changing one is
    # centre-configuration data kept to owner and administrator: the SRS does
    # not name the roles that manage periods, so docs/OPEN_QUESTIONS.md asks
    # the owner to confirm this line.
    "academics.period.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                              ROLE_ACADEMIC_COORDINATOR, ROLE_RECEPTIONIST},
    "academics.period.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    # Sessions (the one-off part of SCH-003/SCH-004): the desk role reads the
    # centre's class occurrences ("basic scheduling", SRS 4.2), the academic
    # coordinator reads and manages schedules (SRS 4.4 "schedules"), owner and
    # administrator hold the full centre. Making or changing a session is kept
    # to the academic staff line; the SRS does not say the desk may create one,
    # so docs/OPEN_QUESTIONS.md asks the owner, and the desk reads only.
    "sessions.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                      ROLE_ACADEMIC_COORDINATOR, ROLE_RECEPTIONIST},
    "sessions.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                        ROLE_ACADEMIC_COORDINATOR},
    # SCH-001: rooms are read by the same roles that read the centre's class
    # occurrences, and made, changed or archived by the academic staff line
    # that manages the timetable. No SRS line names room administration
    # separately, so this follows the schedule decision (D75).
    "rooms.view": {ROLE_OWNER, ROLE_ADMINISTRATOR, ROLE_ACADEMIC_COORDINATOR,
                   ROLE_RECEPTIONIST},
    "rooms.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR, ROLE_ACADEMIC_COORDINATOR},
    # Attendance (ATT-004/ATT-005, SRS 4.4): owner, administrator, the
    # coordinator and the instructor read and record attendance. An
    # instructor is scoped server-side to the sessions of the cohorts they are
    # assigned to - hiding links is not authorization (SEC-001). Corrections
    # after the normal attendance window are permission-controlled and audited
    # (RULE 6.5) and kept to owner, administrator and coordinator; the desk
    # role and the finance officer have no attendance access at all.
    "attendance.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                        ROLE_ACADEMIC_COORDINATOR, ROLE_INSTRUCTOR},
    "attendance.record": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                          ROLE_ACADEMIC_COORDINATOR, ROLE_INSTRUCTOR},
    "attendance.correct": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                           ROLE_ACADEMIC_COORDINATOR},
    # Academic reports (REP-003/REP-004) read the enrollment and cohort data
    # the roles above already read; SRS 4.4 gives the academic coordinator
    # "academic reports" explicitly, the owner and administrator hold the full
    # centre, and the receptionist already reads enrollments and cohorts. The
    # instructor's scoped read view is not built, and financial reports belong
    # to the finance role, so neither is given this code.
    "academics.report.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                              ROLE_ACADEMIC_COORDINATOR, ROLE_RECEPTIONIST},
    # Staff records are people-management data: the academic coordinator may
    # read them (to know who teaches what) but only the owner and administrator
    # make or change a staff record or an instructor assignment.
    "staff.view": {ROLE_OWNER, ROLE_ADMINISTRATOR, ROLE_ACADEMIC_COORDINATOR},
    "staff.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    "staff.assign": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    "staff.link": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    "staff.self.view": {ROLE_INSTRUCTOR},
    "finance.view": {ROLE_OWNER, ROLE_ADMINISTRATOR, ROLE_FINANCE_OFFICER},
    "finance.record": {ROLE_OWNER, ROLE_ADMINISTRATOR, ROLE_FINANCE_OFFICER},
    "audit.view": {ROLE_OWNER, ROLE_ADMINISTRATOR},
    # Phase 3F (FIN-001..FIN-015): the fine codes the finance pages and
    # services actually check, split view/record like the coarse rows above.
    # SRS 4.4 keeps the fee line to the finance officer; the desk role, the
    # instructor and the academic coordinator have no finance access at all,
    # and every finance read or write is scoped to the caller's own center.
    "finances.summary.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                              ROLE_FINANCE_OFFICER},
    "finances.history.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                              ROLE_FINANCE_OFFICER},
    "finances.plan.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                           ROLE_FINANCE_OFFICER},
    "finances.plan.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                             ROLE_FINANCE_OFFICER},
    "finances.payment.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                              ROLE_FINANCE_OFFICER},
    "finances.payment.record": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                ROLE_FINANCE_OFFICER},
    "finances.adjustment.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                 ROLE_FINANCE_OFFICER},
    "finances.adjustment.record": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                   ROLE_FINANCE_OFFICER},
    "finances.expense.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                              ROLE_FINANCE_OFFICER},
    "finances.expense.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                ROLE_FINANCE_OFFICER},
    "finances.audit.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                            ROLE_FINANCE_OFFICER},
    # Phase 3G (GRD-001..GRD-007): the fine codes the grading pages and
    # services actually check, split view/record and manage like the rows
    # above. SRS 4.4 gives the instructor "results" explicitly, so the
    # instructor reads and records assessments and results, but only for the
    # cohorts assigned to their staff record, enforced at the server - the
    # same line ATT-004 and STU-004 keep. Making or changing an assessment
    # definition is centre data kept to owner, administrator and the
    # academic coordinator (SRS 4.4 "assessment definition"); the desk roles
    # have no grading access at all, and every grading read or write is
    # scoped to the caller's own center (SEC-004, GRD-004).
    "grading.assessment.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                ROLE_ACADEMIC_COORDINATOR, ROLE_INSTRUCTOR},
    "grading.assessment.manage": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                                  ROLE_ACADEMIC_COORDINATOR},
    "grading.result.view": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                            ROLE_ACADEMIC_COORDINATOR, ROLE_INSTRUCTOR},
    "grading.result.record": {ROLE_OWNER, ROLE_ADMINISTRATOR,
                              ROLE_ACADEMIC_COORDINATOR, ROLE_INSTRUCTOR},
}

def roles_allowed_for(code):
    try:
        return set(PERMISSIONS[code])
    except KeyError:
        raise PermissionNotDefined("Unknown permission code: {}".format(code))


def role_has_permission(role, code):
    return role in roles_allowed_for(code)


class PermissionNotDefined(Exception):
    """Fail-closed: a permission code must be declared before use."""
