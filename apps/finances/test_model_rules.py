"""Finance rules as the services answer them (FIN-001..FIN-015 without FIN-016).

The rules under test are §6.2's and §6.7's financial rules and FIN-001..FIN-015
as this product's services implement them: organization-scoped records (RULE 6.1),
one agreement per enrollment opened at the enrollment's own agreed amount
(FIN-001), installment plans whose installments never add above the amount the
agreement requires (6.2's plan rule), payments allocated exactly to that payment
against outstanding installments (FIN-004/6.7), reversals that undo allocations
in the stored order and never take more than a payment holds (FIN-008/6.2),
adjustments that move the required amount or the received money (FIN-009/6.2),
expense records kept apart from the collections (FIN-011/FIN-012), the audit
trail FIN-009 names, the receipt numbering that never reuses a number even
under a busy centre (convention D81), and the derived figures that are never a
stored balance (FIN-005/RULE 6.2). Cross-tenant proof through the URL layer
lives in apps/finances/test_access.py.

Every action here is called the way a page calls it (the service function, not
the model API), because the service layer is the one place the rules live.
"""

from datetime import date, timedelta
from decimal import Decimal
import threading
from unittest import skipUnless

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from apps.academics.models import AuditLog, Enrollment
from apps.academics.services import create_course, create_cohort, enroll_student
from apps.academics.statuses import ENROLLMENT_CANCELLED
from apps.organizations.permissions import role_has_permission
from apps.organizations.roles import (
    ROLE_ACADEMIC_COORDINATOR,
    ROLE_ADMINISTRATOR,
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)
from apps.organizations.models import Members, Organization
from apps.students.models import Student
from apps.students.statuses import STATUS_ACTIVE, STATUS_ARCHIVED
from apps.accounts.models import CohortUser
from apps.academics.tests import CenterFixture

from .models import (
    Allocation,
    Expense,
    FeeInstallment,
    FeeObligation,
    FeePlan,
    FinanceAuditEvent,
    FinanceSubmission,
    FinancialAdjustment,
    Payment,
    Receipt,
    ReceiptCounter,
)
from .reporting import (
    financial_rows,
    obligation_figures,
    payment_figures,
    reversal_take_backs,
)
from .services import (
    create_fee_installment,
    create_fee_plan,
    open_fee_obligation,
    post_payment,
    record_financial_adjustment,
    record_financial_expense,
    reverse_payment,
    set_financial_obligation_status,
    withdraw_financial_expense,
)
from .statuses import (
    ADJ_CORRECTION,
    ADJ_DISCOUNT,
    ADJ_REFUND,
    ADJ_REVERSAL,
    ADJ_WAIVER,
    ADJUSTMENT_KINDS,
    AUDIT_ADJUSTMENT_RECORDED,
    AUDIT_EXPENSE_RECORDED,
    AUDIT_EXPENSE_WITHDRAWN,
    AUDIT_OBLIGATION_CLOSED,
    AUDIT_OBLIGATION_OPENED,
    AUDIT_PLAN_SAVED,
    AUDIT_PAYMENT_POSTED,
    AUDIT_PAYMENT_REVERSED,
    EXPENSE_RECORDED,
    EXPENSE_STATUSES,
    EXPENSE_TRANSITIONS,
    EXPENSE_WITHDRAWN,
    FINANCE_AUDIT_ACTIONS,
    METHOD_BANK,
    METHOD_CASH,
    METHOD_OTHER,
    OBLIGATION_CLOSED,
    OBLIGATION_OPEN,
    OBLIGATION_PAID,
    OBLIGATION_STATUSES,
    OBLIGATION_TRANSITIONS,
    PAYMENT_METHODS,
    PAYMENT_RECORDED,
    PAYMENT_REVERSED,
    PAYMENT_STATUSES,
    RECEIPT_COUNTER_NAME,
)

# The fine permission codes the finance pages and services check.
FINANCE_PERMISSION_CODES = (
    "finances.summary.view",
    "finances.history.view",
    "finances.plan.view",
    "finances.plan.manage",
    "finances.payment.view",
    "finances.payment.record",
    "finances.adjustment.view",
    "finances.adjustment.record",
    "finances.expense.view",
    "finances.expense.manage",
    "finances.audit.view",
)

IN_DAYS = 10
OUT_DAYS = 40


class AgreementSetup(CenterFixture):
    """One center's fee line, opened the way the pages open it."""

    def setUp(self):
        super().setUp()
        self.cohort = self.make_cohort()

    def enroll(self, student=None, cohort=None, **kwargs):
        record, problems, notes = enroll_student(
            self.owner,
            self.alpha,
            (cohort or self.cohort).pk,
            (student or self.student).student_number,
            **kwargs,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def open_obligation(self, enrollment, user=None):
        record, problems, notes = open_fee_obligation(
            user or self.finance, self.alpha, enrollment.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def plan(self, obligation, name="The autumn split"):
        record, problems, notes = create_fee_plan(
            self.finance, self.alpha, obligation.pk, name
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def installment(self, plan, amount, due_in, position_expected):
        record, problems, notes = create_fee_installment(
            self.finance,
            self.alpha,
            plan.pk,
            amount,
            date.today() + timedelta(days=due_in),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        self.assertEqual(record.position, position_expected)
        return record

    def pay(self, obligation, amount, allocations, method=METHOD_CASH):
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            obligation.pk,
            amount,
            method,
            allocations=allocations,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def figures(self, obligation):
        """The figures always read the agreement as the centre last saved it.

        A service may have moved the status on its own fresh instance (FIN-010),
        so the row is re-read by number before the derived figures answer.
        """
        return obligation_figures(
            FeeObligation.objects.filter(pk=obligation.pk).first()
            or obligation,
            self.alpha,
        )

    def rows(self):
        return {
            row["id"]: row["value"] for row in financial_rows(self.alpha)
        }


class VocabularyTests(AgreementSetup):
    """The fixed vocabularies only: a free word never reaches a record."""

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("1000"), fee_currency="eur")
        self.obligation = self.open_obligation(self.enrollment)

    def test_open_agreement_is_an_open_one(self):
        self.assertEqual(self.obligation.status, OBLIGATION_OPEN)

    def test_free_status_never_reaches_an_agreement(self):
        record = FeeObligation(
            organization=self.alpha,
            enrollment=self.enrollment,
            student=self.enrollment.student,
            fee_snapshot=Decimal("100.00"),
            currency="DZD",
            status="nearly paid",
            opened_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            record.full_clean(exclude=["organization"])

    def test_free_method_never_reaches_a_payment(self):
        payment = Payment(
            organization=self.alpha,
            obligation=self.obligation,
            amount=Decimal("60.00"),
            method="crypto",
            recorded_by=self.finance,
            recorded_at=timezone.now(),
            status=PAYMENT_RECORDED,
        )
        with self.assertRaises(ValidationError):
            payment.full_clean(exclude=["organization"])

    def test_free_kind_never_reaches_an_adjustment(self):
        adjustment = FinancialAdjustment(
            organization=self.alpha,
            obligation=self.obligation,
            kind="blackmail",
            amount=Decimal("10.00"),
            actor=self.finance,
        )
        with self.assertRaises(ValidationError):
            adjustment.full_clean(exclude=["organization"])

    def test_free_expense_status_never_reaches_an_expense(self):
        expense = Expense(
            organization=self.alpha,
            category="supplies",
            amount=Decimal("10.00"),
            currency="DZD",
            date=date.today(),
            recorded_by=self.finance,
            status="retired",
        )
        with self.assertRaises(ValidationError):
            expense.full_clean(exclude=["organization"])

    def test_service_refuses_a_free_method(self):
        payment, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("60"),
            "crypto",
            allocations=(),
        )
        self.assertIsNone(payment)
        self.assertEqual(notes, ["method_unknown"])

    def test_service_refuses_a_free_kind(self):
        adjustment, problems, notes = record_financial_adjustment(
            self.finance, self.alpha, self.obligation.pk, "blackmail",
            Decimal("10"),
        )
        self.assertIsNone(adjustment)
        self.assertEqual(notes, ["kind_unknown"])

    def test_service_refuses_a_free_status(self):
        record, problems, notes = set_financial_obligation_status(
            self.finance, self.alpha, self.obligation.pk, "nearly paid"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])

    def test_the_two_vocabulary_tables_agree(self):
        self.assertEqual(OBLIGATION_STATUSES, (OBLIGATION_OPEN,
                                               OBLIGATION_PAID,
                                               OBLIGATION_CLOSED))
        self.assertEqual(PAYMENT_STATUSES, (PAYMENT_RECORDED,
                                            PAYMENT_REVERSED))
        self.assertEqual(EXPENSE_STATUSES, (EXPENSE_RECORDED,
                                            EXPENSE_WITHDRAWN))
        self.assertEqual(ADJUSTMENT_KINDS, (
            ADJ_DISCOUNT, ADJ_WAIVER, ADJ_CORRECTION, ADJ_REFUND,
            ADJ_REVERSAL,
        ))
        self.assertEqual(
            PAYMENT_METHODS, (METHOD_CASH, METHOD_BANK, METHOD_OTHER)
        )


class PermissionRoleTests(AgreementSetup):
    """SRS 4.4's split: only the finance line's roles may act here."""

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("1000"), fee_currency="eur")
        self.obligation = self.open_obligation(self.enrollment)
        self.plan_row = self.plan(self.obligation)
        self.first = self.installment(
            self.plan_row, Decimal("600"), IN_DAYS, 1
        )
        self.second = self.installment(
            self.plan_row, Decimal("400"), 2 * IN_DAYS, 2
        )

    def test_the_role_rows_are_the_permission_table_agreeing(self):
        for role, allowed in (
            (ROLE_OWNER, True),
            (ROLE_ADMINISTRATOR, True),
            (ROLE_FINANCE_OFFICER, True),
            (ROLE_RECEPTIONIST, False),
            (ROLE_INSTRUCTOR, False),
            (ROLE_ACADEMIC_COORDINATOR, False),
        ):
            for code in FINANCE_PERMISSION_CODES:
                self.assertEqual(
                    role_has_permission(role, code), allowed
                )

    def test_the_desk_clerk_never_posts_a_payment(self):
        with self.assertRaises(PermissionDenied):
            post_payment(
                self.receptionist,
                self.alpha,
                self.obligation.pk,
                Decimal("600"),
                METHOD_CASH,
                allocations=[(self.first.pk, Decimal("600"))],
            )
        self.is_zero(Payment.objects.filter(
            organization=self.alpha, obligation=self.obligation
        ))

    def test_the_instructor_never_opens_an_agreement(self):
        other = self.enroll(
            student=self.make_student("AC-17"), agreed_fee=Decimal("400")
        )
        with self.assertRaises(PermissionDenied):
            open_fee_obligation(self.instructor, self.alpha, other.pk)
        self.is_zero(
            FeeObligation.objects.filter(
                organization=self.alpha, enrollment=other
            )
        )

    def test_the_coordinator_never_records_an_expense(self):
        with self.assertRaises(PermissionDenied):
            record_financial_expense(
                self.coordinator,
                self.alpha,
                "supplies",
                Decimal("30"),
                date.today(),
            )

    def is_zero(self, rows):
        self.assertEqual(rows.count(), 0)


class AgreementOpeningTests(AgreementSetup):
    """FIN-001: one agreement, opened once, at the enrollment's own anchor."""

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("1000"), fee_currency="eur")
        self.obligation = self.open_obligation(self.enrollment)

    def test_agreement_snapshots_the_agreed_amount_and_currency(self):
        self.assertEqual(
            self.obligation.fee_snapshot, Decimal("1000.00")
        )
        self.assertEqual(self.obligation.currency, "EUR")

    def test_agreement_defaults_to_the_centers_currency(self):
        enrollment = self.enroll(
            student=self.make_student("AC-11"), agreed_fee=Decimal("700")
        )
        obligation, problems, notes = open_fee_obligation(
            self.finance, self.alpha, enrollment.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(obligation.currency, "DZD")

    def test_archived_student_never_opens_an_agreement(self):
        enrollment = self.enroll(student=self.make_student("AC-12"))
        enrollment.student.status = STATUS_ARCHIVED
        enrollment.student.save(update_fields=["status"])
        record, problems, notes = open_fee_obligation(
            self.finance, self.alpha, enrollment.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["student_archived"])
        self.is_zero(
            FeeObligation.objects.filter(
                organization=self.alpha, enrollment=enrollment
            )
        )

    def test_closed_enrollment_never_opens_an_agreement(self):
        enrollment = self.enroll(
            student=self.make_student("AC-13"), agreed_fee=Decimal("500")
        )
        enrollment.status = ENROLLMENT_CANCELLED
        enrollment.save(update_fields=["status"])
        record, problems, notes = open_fee_obligation(
            self.finance, self.alpha, enrollment.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["enrollment_closed"])
        self.is_zero(
            FeeObligation.objects.filter(
                organization=self.alpha, enrollment=enrollment
            )
        )

    def test_enrollment_without_an_agreed_amount_never_opens(self):
        enrollment = self.enroll(
            student=self.make_student("AC-14"), agreed_fee=None
        )
        self.assertIsNone(enrollment.agreed_fee)
        record, problems, notes = open_fee_obligation(
            self.finance, self.alpha, enrollment.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["fee_not_agreed"])

    def test_enrollment_of_another_center_is_unknown_here(self):
        beta_cohort, problems, notes = create_cohort(
            self.beta_owner, self.beta, "Beta A", self.beta_course.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        beta_enrollment, problems, notes = enroll_student(
            self.beta_owner,
            self.beta,
            beta_cohort.pk,
            self.beta_student.student_number,
            agreed_fee=Decimal("300"),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        record, problems, notes = open_fee_obligation(
            self.finance, self.alpha, beta_enrollment.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["enrollment_not_found"])

    def test_one_agreement_per_enrollment(self):
        record, problems, notes = open_fee_obligation(
            self.finance, self.alpha, self.enrollment.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["already_open"])
        self.assertEqual(
            FeeObligation.objects.filter(
                organization=self.alpha, enrollment=self.enrollment
            ).count(),
            1,
        )

    def is_zero(self, rows):
        self.assertEqual(rows.count(), 0)


class PlanAndInstallmentTests(AgreementSetup):
    """FIN-002/6.2: plans split an agreement, installments never overshoot."""

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("1000"))
        self.obligation = self.open_obligation(self.enrollment)
        self.plan_row = self.plan(self.obligation)
        self.first = self.installment(
            self.plan_row, Decimal("600"), IN_DAYS, 1
        )
        self.second = self.installment(
            self.plan_row, Decimal("400"), 2 * IN_DAYS, 2
        )

    def test_plan_and_installments_hang_from_their_agreement(self):
        self.assertEqual(self.plan_row.obligation_id, self.obligation.pk)
        self.assertEqual(self.first.plan_id, self.plan_row.pk)
        self.assertEqual(self.second.plan_id, self.plan_row.pk)

    def test_two_plans_may_split_one_agreement(self):
        second_plan = self.plan(self.obligation, name="The spring split")
        self.assertEqual(
            FeePlan.objects.filter(
                organization=self.alpha, obligation=self.obligation
            ).count(),
            2,
        )
        # Both plan saves and the two scheduled installments audit once each.
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                organization=self.alpha,
                action=AUDIT_PLAN_SAVED,
                plan__obligation=self.obligation,
            ).count(),
            4,
        )
        self.assertNotEqual(second_plan.name, self.plan_row.name)

    def test_installments_never_add_over_the_required_amount(self):
        record, problems, notes = create_fee_installment(
            self.finance,
            self.alpha,
            self.plan_row.pk,
            Decimal("0.01"),
            date.today() + timedelta(days=3 * IN_DAYS),
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["plan_over_fee"])
        self.assertEqual(
            FeeInstallment.objects.filter(plan=self.plan_row).count(), 2
        )

    def test_the_plan_rule_reads_every_plan_of_the_agreement(self):
        enrollment = self.enroll(
            student=self.make_student("AC-15"), agreed_fee=Decimal("2000")
        )
        obligation = self.open_obligation(enrollment)
        autumn = self.plan(obligation, name="Autumn half")
        spring = self.plan(obligation, name="Spring half")
        self.installment(autumn, Decimal("1000"), IN_DAYS, 1)
        self.installment(spring, Decimal("900"), 2 * IN_DAYS, 1)
        record, problems, notes = create_fee_installment(
            self.finance,
            self.alpha,
            spring.pk,
            Decimal("100.01"),
            date.today() + timedelta(days=3 * IN_DAYS),
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["plan_over_fee"])
        self.assertEqual(
            FeeInstallment.objects.filter(plan__obligation=obligation).count(
            ),
            2,
        )

    def test_closed_agreement_never_gets_a_plan_or_an_installment(self):
        record, problems, notes = set_financial_obligation_status(
            self.finance, self.alpha, self.obligation.pk, OBLIGATION_CLOSED
        )
        self.is_not_none(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        plan, problems, notes = create_fee_plan(
            self.finance, self.alpha, self.obligation.pk, "Late plan"
        )
        self.is_none(plan)
        self.assertEqual(notes, ["obligation_closed"])
        installment, problems, notes = create_fee_installment(
            self.finance,
            self.alpha,
            self.plan_row.pk,
            Decimal("10"),
            date.today() + timedelta(days=IN_DAYS),
        )
        self.is_none(installment)
        self.assertEqual(notes, ["obligation_closed"])

    def test_bad_installment_amount_names_the_field(self):
        record, problems, notes = create_fee_installment(
            self.finance,
            self.alpha,
            self.plan_row.pk,
            Decimal("-5"),
            date.today() + timedelta(days=IN_DAYS),
        )
        self.is_none(record)
        self.assertEqual(notes, [])
        self.assertEqual(problems[0][0], "amount")
        self.assertEqual(
            FeeInstallment.objects.filter(plan=self.plan_row).count(), 2
        )

    def test_bad_installment_due_date_names_the_field(self):
        enrollment = self.enroll(
            student=self.make_student("AC-16"), agreed_fee=Decimal("3000")
        )
        obligation = self.open_obligation(enrollment)
        plan = self.plan(obligation, name="Room to schedule")
        record, problems, notes = create_fee_installment(
            self.finance,
            self.alpha,
            plan.pk,
            Decimal("10"),
            "next moon",
        )
        self.is_none(record)
        self.assertEqual(notes, [])
        self.assertEqual(problems[0][0], "due_date")
        self.assertEqual(FeeInstallment.objects.filter(plan=plan).count(), 0)

    def is_none(self, record):
        self.assertIsNone(record)

    def is_not_none(self, record):
        self.assertIsNotNone(record)

class PaymentAndReceiptTests(AgreementSetup):
    """FIN-003/FIN-004/FIN-007: payments, their exact allocations, receipts."""

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("1000"))
        self.obligation = self.open_obligation(self.enrollment)
        self.fee_plan = self.plan(self.obligation)
        self.first = self.installment(self.fee_plan, Decimal("600"), IN_DAYS, 1)
        self.second = self.installment(
            self.fee_plan, Decimal("400"), OUT_DAYS, 2
        )

    def test_payment_writes_its_one_receipt_and_is_audited(self):
        payment = self.pay(
            self.obligation,
            Decimal("500"),
            ((self.first.pk, Decimal("500")),),
        )
        receipts = Receipt.objects.filter(payment=payment)
        self.assertEqual(receipts.count(), 1)
        receipt = receipts.first()
        self.assertEqual(receipt.amount, Decimal("500"))
        self.assertEqual(receipt.currency, "DZD")
        self.assertEqual(receipt.method, METHOD_CASH)
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                payment=payment, action=AUDIT_PAYMENT_POSTED
            ).count(),
            1,
        )
        figures = payment_figures(payment)
        self.assertEqual(figures["amount"], Decimal("500"))
        self.assertEqual(figures["taken_back"], Decimal("0"))
        self.assertEqual(figures["received"], Decimal("500"))
        self.assertEqual(figures["reversed"], False)

    def test_partial_payment_keeps_the_agreement_open(self):
        payment = self.pay(
            self.obligation,
            Decimal("500"),
            ((self.first.pk, Decimal("500")),),
        )
        figures = self.figures(self.obligation)
        self.assertEqual(figures["status"], OBLIGATION_OPEN)
        self.assertEqual(figures["payments"], 1)
        self.assertEqual(figures["required"], Decimal("1000"))
        self.assertEqual(figures["allocated"], Decimal("500"))
        self.assertEqual(figures["outstanding"], Decimal("500"))
        self.assertEqual(figures["collections"], Decimal("500"))
        self.assertEqual(figures["installments_left"], 2)
        self.assertEqual(payment.status, PAYMENT_RECORDED)

    def test_allocation_must_cover_the_whole_payment(self):
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("500"),
            METHOD_CASH,
            allocations=((self.first.pk, Decimal("400")),),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["allocation_not_whole_payment"])
        self.assertEqual(
            Payment.objects.filter(obligation=self.obligation).count(), 0
        )

    def test_allocation_needs_a_positive_part(self):
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("500"),
            METHOD_CASH,
            allocations=((self.first.pk, Decimal("-5")),),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["allocation_not_positive"])
        self.assertEqual(
            Payment.objects.filter(obligation=self.obligation).count(), 0
        )

    def test_installment_listed_twice_never_splits_itself(self):
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("600"),
            METHOD_CASH,
            allocations=(
                (self.first.pk, Decimal("300")),
                (self.first.pk, Decimal("300")),
            ),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["installment_repeated"])
        self.assertEqual(
            Payment.objects.filter(obligation=self.obligation).count(), 0
        )

    def test_fully_allocated_installment_takes_no_more(self):
        self.pay(
            self.obligation,
            Decimal("600"),
            ((self.first.pk, Decimal("600")),),
        )
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("100"),
            METHOD_CASH,
            allocations=((self.first.pk, Decimal("100")),),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["installment_fully_allocated"])
        self.assertEqual(
            Payment.objects.filter(obligation=self.obligation).count(), 1
        )

    def test_installment_of_another_agreement_never_pays_here(self):
        other_student = self.make_student("AC-21")
        other_enrollment = self.enroll(
            student=other_student, agreed_fee=Decimal("500")
        )
        other_obligation = self.open_obligation(other_enrollment)
        other_plan = self.plan(other_obligation)
        other_first = self.installment(
            other_plan, Decimal("500"), IN_DAYS, 1
        )
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("200"),
            METHOD_CASH,
            allocations=((other_first.pk, Decimal("200")),),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["installment_not_here"])
        self.assertEqual(
            Payment.objects.filter(
                obligation__in=(self.obligation, other_obligation)
            ).count(),
            0,
        )

    def test_payment_method_outside_the_vocabulary_never_saves(self):
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("500"),
            "crypto",
            allocations=((self.first.pk, Decimal("500")),),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["method_unknown"])
        self.assertEqual(
            Payment.objects.filter(obligation=self.obligation).count(), 0
        )

    def test_closed_agreement_takes_no_new_payment(self):
        closed, problems, notes = set_financial_obligation_status(
            self.finance, self.alpha, self.obligation.pk, "closed"
        )
        self.assertIsNotNone(closed)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(closed.status, OBLIGATION_CLOSED)
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("100"),
            METHOD_CASH,
            allocations=((self.first.pk, Decimal("100")),),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["obligation_closed"])
        self.assertEqual(
            Payment.objects.filter(obligation=self.obligation).count(), 0
        )


class ReceiptNumberingTests(AgreementSetup):
    """D81: the centre's receipt counter never hands a number out twice."""

    def counter(self):
        return ReceiptCounter.objects.filter(
            organization=self.alpha, name=RECEIPT_COUNTER_NAME
        )

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("3000"))
        self.obligation = self.open_obligation(self.enrollment)
        self.fee_plan = self.plan(self.obligation)
        self.first = self.installment(
            self.fee_plan, Decimal("1000"), IN_DAYS, 1
        )
        self.second = self.installment(
            self.fee_plan, Decimal("2000"), OUT_DAYS, 2
        )
        self.numbers = []
        for amount in (Decimal("600"), Decimal("300"), Decimal("100")):
            payment = self.pay(
                self.obligation,
                amount,
                ((self.first.pk, amount),),
            )
            self.numbers.append(
                Receipt.objects.filter(payment=payment).first().number
            )
        self.assertEqual(
            self.numbers,
            list(range(self.numbers[0], self.numbers[0] + 3)),
        )

    def test_beta_centre_numbers_its_own_receipts(self):
        cohort, problems, notes = create_cohort(
            self.beta_owner, self.beta, "Beta A", self.beta_course.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(cohort)
        enrollment, problems, notes = enroll_student(
            self.beta_owner,
            self.beta,
            cohort.pk,
            self.beta_student.student_number,
            agreed_fee=Decimal("500"),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(enrollment)
        obligation, problems, notes = open_fee_obligation(
            self.beta_owner, self.beta, enrollment.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(obligation)
        fee_plan, problems, notes = create_fee_plan(
            self.beta_owner, self.beta, obligation.pk, "The winter line"
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(fee_plan)
        installment, problems, notes = create_fee_installment(
            self.beta_owner,
            self.beta,
            fee_plan.pk,
            Decimal("500"),
            date.today() + timedelta(days=IN_DAYS),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(installment)
        payment, problems, notes = post_payment(
            self.beta_owner,
            self.beta,
            obligation.pk,
            Decimal("500"),
            METHOD_CASH,
            allocations=((installment.pk, Decimal("500")),),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(payment)
        receipts = Receipt.objects.filter(organization=self.beta)
        self.assertEqual(receipts.count(), 1)
        self.assertEqual(receipts.first().number, 1)
        self.assertEqual(receipts.first().currency, "EUR")
        self.assertEqual(self.counter().first().running_value, self.numbers[-1])

    def test_next_payment_takes_the_next_number(self):
        payment = self.pay(
            self.obligation,
            Decimal("2000"),
            ((self.second.pk, Decimal("2000")),),
        )
        receipt = Receipt.objects.filter(payment=payment).first()
        self.assertEqual(receipt.number, self.numbers[-1] + 1)
        self.assertEqual(
            self.counter().first().running_value, self.numbers[-1] + 1
        )

    def test_tampered_counter_never_reuses_a_number(self):
        self.counter().update(running_value=0)
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("500"),
            METHOD_CASH,
            allocations=((self.second.pk, Decimal("500")),),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["receipt_busy"])
        self.assertEqual(
            Payment.objects.filter(obligation=self.obligation).count(), 3
        )
        rows = self.counter()
        self.assertTrue(rows.exists())
        rows.update(running_value=self.numbers[-1])
        payment = self.pay(
            self.obligation,
            Decimal("500"),
            ((self.second.pk, Decimal("500")),),
        )
        receipt = Receipt.objects.filter(payment=payment).first()
        self.assertEqual(receipt.number, self.numbers[-1] + 1)

    def test_deleted_counter_row_never_restarts_the_counting(self):
        self.counter().delete()
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("500"),
            METHOD_CASH,
            allocations=((self.second.pk, Decimal("500")),),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["receipt_busy"])
        self.assertEqual(
            Payment.objects.filter(obligation=self.obligation).count(), 3
        )
        rows = self.counter()
        if rows.exists():
            rows.update(running_value=self.numbers[-1])
        else:
            ReceiptCounter.objects.create(
                organization=self.alpha,
                name=RECEIPT_COUNTER_NAME,
                running_value=self.numbers[-1],
            )
        payment = self.pay(
            self.obligation,
            Decimal("500"),
            ((self.second.pk, Decimal("500")),),
        )
        receipt = Receipt.objects.filter(payment=payment).first()
        self.assertEqual(receipt.number, self.numbers[-1] + 1)


class ReversalTests(AgreementSetup):
    """FIN-008: reversals undo allocations in the stored order, never more."""

    def agreement(self, tag, fee, first_amount, second_amount, payment_amount):
        enrollment = self.enroll(
            student=self.make_student(f"AC-{tag}"), agreed_fee=fee
        )
        obligation = self.open_obligation(enrollment)
        fee_plan = self.plan(obligation)
        first = self.installment(fee_plan, first_amount, IN_DAYS, 1)
        second = self.installment(fee_plan, second_amount, OUT_DAYS, 2)
        payment = self.pay(
            obligation,
            payment_amount,
            ((first.pk, first_amount), (second.pk, second_amount)),
        )
        return obligation, first, second, payment

    def test_one_reversal_undoes_allocations_in_the_stored_order(self):
        obligation, first, second, payment = self.agreement(
            401,
            Decimal("1000"),
            Decimal("700"),
            Decimal("300"),
            Decimal("1000"),
        )
        adjustment, problems, notes = reverse_payment(
            self.finance,
            self.alpha,
            payment.pk,
            Decimal("700"),
            reason="The family moved",
        )
        self.assertIsNotNone(adjustment)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(adjustment.kind, ADJ_REVERSAL)
        self.assertEqual(adjustment.payment, payment)
        self.assertEqual(adjustment.obligation, obligation)
        self.assertEqual(
            reversal_take_backs(adjustment),
            [{"installment_id": first.pk, "amount": Decimal("700")}],
        )
        figures = payment_figures(payment)
        self.assertEqual(figures["taken_back"], Decimal("700"))
        self.assertEqual(figures["received"], Decimal("300"))
        self.assertEqual(figures["reversed"], False)
        figures = self.figures(obligation)
        self.assertEqual(figures["status"], OBLIGATION_PAID)
        self.assertEqual(figures["outstanding"], Decimal("700"))
        self.assertEqual(figures["taken_back"], Decimal("700"))
        self.assertEqual(figures["collections"], Decimal("300"))
        self.assertEqual(figures["installments_left"], 1)
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                payment=payment, action=AUDIT_PAYMENT_REVERSED
            ).count(),
            1,
        )

    def test_two_reversals_take_the_rest_in_the_stored_order(self):
        obligation, first, second, payment = self.agreement(
            402,
            Decimal("1000"),
            Decimal("700"),
            Decimal("300"),
            Decimal("1000"),
        )
        first_reversal, problems, notes = reverse_payment(
            self.finance, self.alpha, payment.pk, Decimal("700"), "One"
        )
        self.assertIsNotNone(first_reversal)
        self.assertEqual(notes, [])
        second_reversal, problems, notes = reverse_payment(
            self.finance, self.alpha, payment.pk, Decimal("300"), "Two"
        )
        self.assertIsNotNone(second_reversal)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(
            reversal_take_backs(second_reversal),
            [{"installment_id": second.pk, "amount": Decimal("300")}],
        )
        payment = Payment.objects.get(pk=payment.pk)
        self.assertEqual(payment.status, PAYMENT_REVERSED)
        figures = payment_figures(payment)
        self.assertEqual(figures["taken_back"], Decimal("1000"))
        self.assertEqual(figures["received"], Decimal("0"))
        self.assertEqual(figures["reversed"], True)
        figures = self.figures(obligation)
        self.assertEqual(figures["status"], OBLIGATION_PAID)
        self.assertEqual(figures["outstanding"], Decimal("1000"))
        self.assertEqual(figures["taken_back"], Decimal("1000"))
        self.assertEqual(figures["collections"], Decimal("0"))
        self.assertEqual(figures["installments_left"], 2)
        self.assertEqual(
            Receipt.objects.filter(payment=payment).count(), 1
        )
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                payment=payment, action=AUDIT_PAYMENT_REVERSED
            ).count(),
            2,
        )

    def test_reversal_never_takes_more_than_the_payment_holds(self):
        obligation, first, second, payment = self.agreement(
            403,
            Decimal("1000"),
            Decimal("700"),
            Decimal("300"),
            Decimal("1000"),
        )
        reversal, problems, notes = reverse_payment(
            self.finance, self.alpha, payment.pk, Decimal("700"), "One"
        )
        self.assertIsNotNone(reversal)
        record, problems, notes = reverse_payment(
            self.finance, self.alpha, payment.pk, Decimal("400"), "More"
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["over_taken"])
        self.assertEqual(
            FinancialAdjustment.objects.filter(
                organization=self.alpha, payment=payment, kind=ADJ_REVERSAL
            ).count(),
            1,
        )
        self.assertEqual(
            Receipt.objects.filter(payment=payment).count(), 1
        )

    def test_fully_reversed_payment_is_not_reversed_again(self):
        obligation, first, second, payment = self.agreement(
            404,
            Decimal("1000"),
            Decimal("700"),
            Decimal("300"),
            Decimal("1000"),
        )
        reversal, problems, notes = reverse_payment(
            self.finance, self.alpha, payment.pk, Decimal("1000"), "All"
        )
        self.assertIsNotNone(reversal)
        record, problems, notes = reverse_payment(
            self.finance, self.alpha, payment.pk, Decimal("10"), "More"
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["already_reversed"])

    def test_reversal_needs_a_positive_amount(self):
        obligation, first, second, payment = self.agreement(
            405,
            Decimal("1000"),
            Decimal("700"),
            Decimal("300"),
            Decimal("1000"),
        )
        record, problems, notes = reverse_payment(
            self.finance, self.alpha, payment.pk, Decimal("-5"), "Cold"
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["amount_not_positive"])

    def test_number_naming_nothing_here_is_no_posting(self):
        obligation, first, second, payment = self.agreement(
            406,
            Decimal("1000"),
            Decimal("700"),
            Decimal("300"),
            Decimal("1000"),
        )
        record, problems, notes = reverse_payment(
            self.finance, self.alpha, 10 ** 8, Decimal("100"), "Cold"
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["payment_not_found"])
        self.assertEqual(payment.status, PAYMENT_RECORDED)

    def test_reversal_never_needs_the_agreement_to_be_open(self):
        obligation, first, second, payment = self.agreement(
            407,
            Decimal("1000"),
            Decimal("700"),
            Decimal("300"),
            Decimal("1000"),
        )
        closed, problems, notes = set_financial_obligation_status(
            self.finance, self.alpha, obligation.pk, "closed"
        )
        self.assertIsNotNone(closed)
        self.assertEqual(notes, [])
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                obligation=obligation, action=AUDIT_OBLIGATION_CLOSED
            ).count(),
            1,
        )
        reversal, problems, notes = reverse_payment(
            self.finance, self.alpha, payment.pk, Decimal("400"), "Later"
        )
        self.assertIsNotNone(reversal)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        figures = self.figures(obligation)
        self.assertEqual(figures["status"], OBLIGATION_CLOSED)
        self.assertEqual(figures["outstanding"], Decimal("400"))
        self.assertEqual(figures["collections"], Decimal("600"))

    def test_reversal_as_a_bare_adjustment_never_names_no_payment(self):
        obligation, first, second, payment = self.agreement(
            408,
            Decimal("1000"),
            Decimal("700"),
            Decimal("300"),
            Decimal("1000"),
        )
        record, problems, notes = record_financial_adjustment(
            self.finance, self.alpha, obligation.pk, ADJ_REVERSAL,
            Decimal("100"),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["reversal_needs_payment"])
        self.assertEqual(
            FinancialAdjustment.objects.filter(
                organization=self.alpha, obligation=obligation
            ).count(),
            0,
        )


class AdjustmentTests(AgreementSetup):
    """FIN-009: adjustments move the required amount or the money back."""

    def agreement(self, tag, fee, first_amount, second_amount):
        enrollment = self.enroll(
            student=self.make_student(f"AC-{tag}"), agreed_fee=fee
        )
        obligation = self.open_obligation(enrollment)
        fee_plan = self.plan(obligation)
        first = self.installment(fee_plan, first_amount, IN_DAYS, 1)
        second = self.installment(fee_plan, second_amount, OUT_DAYS, 2)
        return obligation, first, second

    def test_discount_moves_the_agreement_down(self):
        obligation, first, second = self.agreement(
            411, Decimal("1000"), Decimal("600"), Decimal("400")
        )
        adjustment, problems, notes = record_financial_adjustment(
            self.finance,
            self.alpha,
            obligation.pk,
            ADJ_DISCOUNT,
            Decimal("100"),
            reason="The family joined late",
        )
        self.assertIsNotNone(adjustment)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(adjustment.kind, ADJ_DISCOUNT)
        self.assertEqual(adjustment.reason, "The family joined late")
        self.assertEqual(adjustment.actor, self.finance)
        figures = self.figures(obligation)
        self.assertEqual(figures["required"], Decimal("900"))
        self.assertEqual(figures["outstanding"], Decimal("900"))
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                obligation=obligation, action=AUDIT_ADJUSTMENT_RECORDED
            ).count(),
            1,
        )

    def test_waiver_moves_the_agreement_down(self):
        obligation, first, second = self.agreement(
            412, Decimal("1000"), Decimal("600"), Decimal("400")
        )
        adjustment, problems, notes = record_financial_adjustment(
            self.finance,
            self.alpha,
            obligation.pk,
            ADJ_WAIVER,
            Decimal("100"),
            reason="The school forgave it",
        )
        self.assertIsNotNone(adjustment)
        self.assertEqual(notes, [])
        figures = self.figures(obligation)
        self.assertEqual(figures["required"], Decimal("900"))
        self.assertEqual(figures["outstanding"], Decimal("900"))

    def test_fee_correction_moves_the_agreement_up(self):
        obligation, first, second = self.agreement(
            413, Decimal("1000"), Decimal("600"), Decimal("400")
        )
        adjustment, problems, notes = record_financial_adjustment(
            self.finance,
            self.alpha,
            obligation.pk,
            ADJ_CORRECTION,
            Decimal("200"),
            reason="The rule changed for it",
        )
        self.assertIsNotNone(adjustment)
        self.assertEqual(notes, [])
        figures = self.figures(obligation)
        self.assertEqual(figures["required"], Decimal("1200"))
        self.assertEqual(figures["outstanding"], Decimal("1200"))

    def test_discount_cannot_move_more_than_required(self):
        obligation, first, second = self.agreement(
            414, Decimal("1000"), Decimal("600"), Decimal("400")
        )
        record, problems, notes = record_financial_adjustment(
            self.finance,
            self.alpha,
            obligation.pk,
            ADJ_DISCOUNT,
            Decimal("1100"),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["over_required"])
        self.assertEqual(
            FinancialAdjustment.objects.filter(
                organization=self.alpha, obligation=obligation
            ).count(),
            0,
        )
        self.assertEqual(
            self.figures(obligation)["required"], Decimal("1000")
        )

    def test_adjustment_needs_a_positive_amount(self):
        obligation, first, second = self.agreement(
            415, Decimal("1000"), Decimal("600"), Decimal("400")
        )
        record, problems, notes = record_financial_adjustment(
            self.finance,
            self.alpha,
            obligation.pk,
            ADJ_CORRECTION,
            Decimal("-100"),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["amount_not_positive"])

    def test_free_adjustment_kind_never_saves(self):
        obligation, first, second = self.agreement(
            416, Decimal("1000"), Decimal("600"), Decimal("400")
        )
        record, problems, notes = record_financial_adjustment(
            self.finance,
            self.alpha,
            obligation.pk,
            "blackmail",
            Decimal("100"),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["kind_unknown"])

    def test_refund_needs_money_the_payments_received(self):
        obligation, first, second = self.agreement(
            417, Decimal("1000"), Decimal("600"), Decimal("400")
        )
        record, problems, notes = record_financial_adjustment(
            self.finance,
            self.alpha,
            obligation.pk,
            ADJ_REFUND,
            Decimal("100"),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["over_received"])
        self.assertEqual(
            self.figures(obligation)["collections"], Decimal("0")
        )

    def test_refund_puts_money_back_after_a_payment(self):
        obligation, first, second = self.agreement(
            418, Decimal("1000"), Decimal("600"), Decimal("400")
        )
        self.pay(
            obligation, Decimal("600"), ((first.pk, Decimal("600")),)
        )
        adjustment, problems, notes = record_financial_adjustment(
            self.finance,
            self.alpha,
            obligation.pk,
            ADJ_REFUND,
            Decimal("200"),
            reason="The family left",
        )
        self.assertIsNotNone(adjustment)
        self.assertEqual(notes, [])
        figures = self.figures(obligation)
        self.assertEqual(figures["payment_amount"], Decimal("600"))
        self.assertEqual(figures["taken_back"], Decimal("200"))
        self.assertEqual(figures["collections"], Decimal("400"))
        self.assertEqual(figures["outstanding"], Decimal("600"))


class ExpenseTests(AgreementSetup):
    """FIN-011/FIN-012: expense records are kept apart from the fee money."""

    def record(self, amount):
        record, problems, notes = record_financial_expense(
            self.finance,
            self.alpha,
            "Classroom supplies",
            amount,
            date.today(),
            payee="The corner shop",
            description="Notebooks for the group",
            attachment_reference="paper-1",
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def test_expense_record_keeps_its_own_fields_and_is_audited(self):
        expense = self.record(Decimal("30.50"))
        self.assertEqual(expense.category, "Classroom supplies")
        self.assertEqual(expense.amount, Decimal("30.50"))
        self.assertEqual(expense.currency, "DZD")
        self.assertEqual(expense.payee, "The corner shop")
        self.assertEqual(expense.description, "Notebooks for the group")
        self.assertEqual(expense.attachment_reference, "paper-1")
        self.assertEqual(expense.recorded_by, self.finance)
        self.assertEqual(expense.status, EXPENSE_RECORDED)
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                expense=expense, action=AUDIT_EXPENSE_RECORDED
            ).count(),
            1,
        )

    def test_withdrawal_is_a_status_move_not_a_deletion(self):
        expense = self.record(Decimal("30.50"))
        withdrawn, problems, notes = withdraw_financial_expense(
            self.finance, self.alpha, expense.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(withdrawn, expense)
        self.assertEqual(withdrawn.status, EXPENSE_WITHDRAWN)
        self.assertTrue(Expense.objects.filter(organization=self.alpha, pk=expense.pk).exists())
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                expense=expense, action=AUDIT_EXPENSE_WITHDRAWN
            ).count(),
            1,
        )

    def test_withdrawn_expense_never_withdraws_again(self):
        expense = self.record(Decimal("12"))
        first, problems, notes = withdraw_financial_expense(
            self.finance, self.alpha, expense.pk
        )
        self.assertEqual(notes, [])
        record, problems, notes = withdraw_financial_expense(
            self.finance, self.alpha, expense.pk
        )
        self.assertEqual(record, expense)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["already_withdrawn"])
        self.assertEqual(record.status, EXPENSE_WITHDRAWN)

    def test_expense_needs_a_positive_amount(self):
        record, problems, notes = record_financial_expense(
            self.finance, self.alpha, "Cold", Decimal("0"), date.today()
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["amount_not_positive"])

    def test_expense_is_never_part_of_the_fee_records(self):
        expense = self.record(Decimal("9"))
        field_names = [field.name for field in Expense._meta.get_fields()]
        self.assertNotIn("obligation", field_names)
        self.assertNotIn("payment", field_names)
        self.assertNotIn("allocation", field_names)


class OverdueTests(AgreementSetup):
    """FIN-005/FIN-006: a late installment is counted, unpaid money waits."""

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("1000"))
        self.obligation = self.open_obligation(self.enrollment)
        self.fee_plan = self.plan(self.obligation)
        self.late = self.installment(
            self.fee_plan, Decimal("400"), -5, 1
        )
        self.later = self.installment(
            self.fee_plan, Decimal("600"), OUT_DAYS, 2
        )

    def test_past_due_installment_counts_as_overdue(self):
        figures = self.figures(self.obligation)
        self.assertEqual(figures["overdue"], 1)
        self.assertEqual(figures["overdue_amount"], Decimal("400"))
        self.assertEqual(figures["outstanding"], Decimal("1000"))

    def test_partly_paid_late_installment_shows_the_rest(self):
        self.pay(
            self.obligation,
            Decimal("200"),
            ((self.late.pk, Decimal("200")),),
        )
        figures = self.figures(self.obligation)
        self.assertEqual(figures["overdue"], 1)
        self.assertEqual(figures["overdue_amount"], Decimal("200"))
        self.assertEqual(figures["outstanding"], Decimal("800"))

    def test_settled_late_installment_leaves_the_count(self):
        self.pay(
            self.obligation,
            Decimal("400"),
            ((self.late.pk, Decimal("400")),),
        )
        figures = self.figures(self.obligation)
        self.assertEqual(figures["overdue"], 0)
        self.assertEqual(figures["overdue_amount"], Decimal("0"))

    def test_future_installments_are_never_overdue(self):
        figures = self.figures(self.obligation)
        self.assertEqual(figures["overdue"], 1)
        self.assertEqual(figures["overdue_amount"], Decimal("400"))
        late_date = self.late.due_date
        self.assertTrue(late_date < date.today())
        self.assertTrue(self.later.due_date > date.today())


class ClosingTests(AgreementSetup):
    """FIN-010: closing keeps the figures; a closed agreement stays closed."""

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("1000"))
        self.obligation = self.open_obligation(self.enrollment)
        self.fee_plan = self.plan(self.obligation)
        self.first = self.installment(
            self.fee_plan, Decimal("600"), IN_DAYS, 1
        )
        self.second = self.installment(
            self.fee_plan, Decimal("400"), OUT_DAYS, 2
        )

    def test_closing_a_untouched_agreement_is_allowed_and_keeps_zero(self):
        closed, problems, notes = set_financial_obligation_status(
            self.finance, self.alpha, self.obligation.pk, "closed"
        )
        self.assertIsNotNone(closed)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        figures = self.figures(self.obligation)
        self.assertEqual(figures["status"], OBLIGATION_CLOSED)
        self.assertEqual(figures["required"], Decimal("1000"))
        self.assertEqual(figures["outstanding"], Decimal("1000"))
        self.assertEqual(figures["collections"], Decimal("0"))
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                obligation=self.obligation,
                action=AUDIT_OBLIGATION_CLOSED,
            ).count(),
            1,
        )

    def test_closed_agreement_never_opens_again(self):
        closed, problems, notes = set_financial_obligation_status(
            self.finance, self.alpha, self.obligation.pk, "closed"
        )
        self.assertIsNotNone(closed)
        record, problems, notes = set_financial_obligation_status(
            self.finance, self.alpha, self.obligation.pk, "open"
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["transition"])
        self.assertEqual(closed.status, OBLIGATION_CLOSED)

    def test_paid_agreement_closes_through_the_paid_step(self):
        payment = self.pay(
            self.obligation,
            Decimal("1000"),
            ((self.first.pk, Decimal("600")), (self.second.pk, Decimal("400"))),
        )
        self.obligation = FeeObligation.objects.get(pk=self.obligation.pk)
        self.assertEqual(self.obligation.status, OBLIGATION_PAID)
        closed, problems, notes = set_financial_obligation_status(
            self.finance, self.alpha, self.obligation.pk, "closed"
        )
        self.assertIsNotNone(closed)
        self.assertEqual(notes, [])
        self.assertEqual(closed.status, OBLIGATION_CLOSED)

    def test_closing_settles_nothing_and_hides_nothing(self):
        self.pay(
            self.obligation,
            Decimal("600"),
            ((self.first.pk, Decimal("600")),),
        )
        closed, problems, notes = set_financial_obligation_status(
            self.finance, self.alpha, self.obligation.pk, "closed"
        )
        self.assertIsNotNone(closed)
        self.assertEqual(notes, [])
        figures = self.figures(self.obligation)
        self.assertEqual(figures["status"], OBLIGATION_CLOSED)
        self.assertEqual(figures["required"], Decimal("1000"))
        self.assertEqual(figures["outstanding"], Decimal("400"))
        self.assertEqual(figures["collections"], Decimal("600"))
        self.assertEqual(figures["payments"], 1)
        self.assertEqual(
            Receipt.objects.filter(
                organization=self.alpha, payment__obligation=self.obligation
            ).count(),
            1,
        )
        record, problems, notes = post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            Decimal("100"),
            METHOD_CASH,
            allocations=((self.second.pk, Decimal("100")),),
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["obligation_closed"])
        self.assertEqual(
            Payment.objects.filter(obligation=self.obligation).count(), 1
        )


class ProtectedHistoryTests(AgreementSetup):
    """FIN-015: the ties that protect financial history are enforced."""

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("1000"))
        self.obligation = self.open_obligation(self.enrollment)
        self.fee_plan = self.plan(self.obligation)
        self.first = self.installment(
            self.fee_plan, Decimal("600"), IN_DAYS, 1
        )
        self.payment = self.pay(
            self.obligation,
            Decimal("600"),
            ((self.first.pk, Decimal("600")),),
        )
        self.expense = self.record(Decimal("20"))

    def record(self, amount):
        record, problems, notes = record_financial_expense(
            self.finance, self.alpha, "Rental books", amount, date.today()
        )
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def test_payment_can_never_be_deleted_outright(self):
        with self.assertRaises(ProtectedError):
            Payment.objects.filter(pk=self.payment.pk).delete()

    def test_the_agreement_never_leaves_its_payments_behind(self):
        with self.assertRaises(ProtectedError):
            FeeObligation.objects.filter(pk=self.obligation.pk).delete()

    def test_the_plan_never_drops_its_agreement(self):
        with self.assertRaises(ProtectedError):
            FeePlan.objects.filter(pk=self.fee_plan.pk).delete()

    def test_expense_row_never_deletes_its_audit(self):
        with self.assertRaises(ProtectedError):
            Expense.objects.filter(pk=self.expense.pk).delete()

    def test_finance_audits_never_reach_the_attendance_logs(self):
        self.assertEqual(
            AuditLog.objects.filter(
                action__in=FINANCE_AUDIT_ACTIONS
            ).count(),
            0,
        )
        self.assertTrue(
            FinanceAuditEvent.objects.filter(payment=self.payment).exists()
        )


class DerivedFiguresTests(TestCase):
    """FIN-001/FIN-002: one fresh center reconciles page rows with figures."""

    def fresh_center(self, tag):
        organization = Organization.objects.create(
            name=f"Gamma {tag} Center",
            slug=f"gamma-{tag}",
            primary_currency="DZD",
        )
        owner = CohortUser.objects.create_user(
            username=f"gamma-{tag}-owner", password="x", display_name=f"Gamma {tag} Owner"
        )
        Members.objects.create(
            user=owner, organization=organization, role=ROLE_OWNER
        )
        course, problems, notes = create_course(
            owner, organization, f"Gamma {tag} course"
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(course)
        cohort, problems, notes = create_cohort(
            owner, organization, f"Gamma {tag} A", course.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(cohort)
        student = Student.objects.create(
            organization=organization,
            student_number=f"GF-{tag}-1",
            name=f"Gamma Learner {tag}",
            contact="gamma@example.test",
            status=STATUS_ACTIVE,
        )
        enrollment, problems, notes = enroll_student(
            owner,
            organization,
            cohort.pk,
            student.student_number,
            agreed_fee=Decimal("1000"),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(enrollment)
        obligation, problems, notes = open_fee_obligation(
            owner, organization, enrollment.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(obligation)
        fee_plan, problems, notes = create_fee_plan(
            owner, organization, obligation.pk, "The gamma split"
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(fee_plan)
        first, problems, notes = create_fee_installment(
            owner,
            organization,
            fee_plan.pk,
            Decimal("600"),
            date.today() + timedelta(days=10),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(first)
        second, problems, notes = create_fee_installment(
            owner,
            organization,
            fee_plan.pk,
            Decimal("400"),
            date.today() + timedelta(days=30),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        payment, problems, notes = post_payment(
            owner,
            organization,
            obligation.pk,
            Decimal("600"),
            METHOD_CASH,
            allocations=((first.pk, Decimal("600")),),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(payment)
        refund, problems, notes = record_financial_adjustment(
            owner,
            organization,
            obligation.pk,
            ADJ_REFUND,
            Decimal("100"),
            reason="The family left early",
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(refund)
        return organization, owner, cohort, obligation

    def test_summary_rows_reconcile_one_agreement(self):
        organization, owner, cohort, obligation = self.fresh_center("1")
        figures = obligation_figures(obligation, organization)
        self.assertEqual(figures["required"], Decimal("1000"))
        self.assertEqual(figures["allocated"], Decimal("600"))
        self.assertEqual(figures["taken_back"], Decimal("100"))
        self.assertEqual(figures["outstanding"], Decimal("500"))
        self.assertEqual(figures["collections"], Decimal("500"))
        self.assertEqual(figures["payments"], 1)
        kept = record_financial_expense(
            owner, organization, "Hall rental", Decimal("30.00"), date.today()
        )
        self.assertEqual(kept[1], [])
        self.assertEqual(kept[2], [])
        self.assertIsNotNone(kept[0])
        gone = record_financial_expense(
            owner, organization, "Old posters", Decimal("50.00"), date.today()
        )[0]
        self.assertIsNotNone(gone)
        withdrawn, problems, notes = withdraw_financial_expense(
            owner, organization, gone.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        rows = {row["id"]: row["value"] for row in financial_rows(organization)}
        self.assertEqual(rows["obligations"], 1)
        self.assertEqual(rows["required"], Decimal("1000"))
        self.assertEqual(rows["received"], Decimal("500"))
        self.assertEqual(rows["outstanding"], Decimal("500"))
        self.assertEqual(rows["collections"], Decimal("500"))
        self.assertEqual(rows["expenses"], 1)
        self.assertEqual(rows["expense_total"], Decimal("30.00"))
        self.assertEqual(rows["overdue"], 0)

    def test_overdue_row_counts_the_late_installment_once(self):
        organization, owner, cohort, obligation = self.fresh_center("2")
        student = Student.objects.create(
            organization=organization,
            student_number="GF-2-2",
            name="Gamma Learner late",
            contact="gamma2@example.test",
            status=STATUS_ACTIVE,
        )
        enrollment, problems, notes = enroll_student(
            owner,
            organization,
            cohort.pk,
            student.student_number,
            agreed_fee=Decimal("400"),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(enrollment)
        late_obligation, problems, notes = open_fee_obligation(
            owner, organization, enrollment.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(late_obligation)
        late_plan, problems, notes = create_fee_plan(
            owner, organization, late_obligation.pk, "The late split"
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(late_plan)
        late_installment, problems, notes = create_fee_installment(
            owner,
            organization,
            late_plan.pk,
            Decimal("400"),
            date.today() + timedelta(days=-1),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(late_installment)
        rows = {row["id"]: row["value"] for row in financial_rows(organization)}
        self.assertEqual(rows["overdue"], 1)
        self.assertEqual(rows["overdue_amount"], Decimal("400"))
        self.assertEqual(rows["obligations"], 2)
        figures = obligation_figures(late_obligation, organization)
        self.assertEqual(figures["overdue"], 1)
        self.assertEqual(figures["overdue_amount"], Decimal("400"))



class FinancialIntegrityTests(AgreementSetup):
    """D85/D86: the locked rows answer the money checks, and one code
    posts the money only once.

    The agreement and its installments are re-read under the locks (D85),
    so an over-allocation is judged from the locked outstanding figures.
    The submission code (D86) is consumed only by a posting that was
    actually taken: a refusal rolls its row back and the code can try
    again whole, the replay of a taken code answers with the saved
    payment and never moves the money a second time, a busy receipt
    counter rolls the row back with the posting, and a saved row always
    names exactly one saved outcome.
    """

    def setUp(self):
        super().setUp()
        self.enrollment = self.enroll(agreed_fee=Decimal("1000"))
        self.obligation = self.open_obligation(self.enrollment)
        self.fee_plan = self.plan(self.obligation)
        self.first = self.installment(
            self.fee_plan, Decimal("600"), IN_DAYS, 1
        )
        self.second = self.installment(
            self.fee_plan, Decimal("400"), OUT_DAYS, 2
        )
        self.payment = self.pay(
            self.obligation,
            Decimal("200"),
            ((self.first.pk, Decimal("200")),),
        )

    def counter(self):
        return ReceiptCounter.objects.filter(
            organization=self.alpha, name=RECEIPT_COUNTER_NAME
        )

    def post(self, amount, allocations, token=""):
        return post_payment(
            self.finance,
            self.alpha,
            self.obligation.pk,
            amount,
            METHOD_CASH,
            allocations=allocations,
            token=token,
        )

    def payments_here(self):
        return Payment.objects.filter(organization=self.alpha)

    def test_a_refusal_never_consumes_the_submission_code(self):
        """installment_fully_allocated answers from the locked rows."""
        record, problems, notes = self.post(
            Decimal("600"),
            ((self.first.pk, Decimal("600")),),
            token="tok-try",
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["installment_fully_allocated"])
        self.assertEqual(self.payments_here().count(), 1)
        self.assertEqual(FinanceSubmission.objects.count(), 0)
        record, problems, notes = self.post(
            Decimal("400"),
            ((self.first.pk, Decimal("400")),),
            token="tok-try",
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        self.assertEqual(self.payments_here().count(), 2)
        self.assertEqual(
            FinanceSubmission.objects.filter(token="tok-try").count(), 1
        )
        submission = FinanceSubmission.objects.filter(token="tok-try").first()
        self.assertEqual(submission.payment.pk, record.pk)
        self.assertEqual(Receipt.objects.filter(payment=record).count(), 1)

    def test_one_submission_code_posts_the_money_once(self):
        """The replay answers with the saved payment, not new money."""
        payment, problems, notes = self.post(
            Decimal("300"),
            ((self.second.pk, Decimal("300")),),
            token="tok-keep",
        )
        self.assertEqual(notes, [])
        self.assertIsNotNone(payment)
        again, problems, notes = self.post(
            Decimal("300"),
            ((self.second.pk, Decimal("300")),),
            token="tok-keep",
        )
        self.assertEqual(notes, ["duplicate_submission"])
        self.assertEqual(again.pk, payment.pk)
        self.assertEqual(self.payments_here().count(), 2)
        self.assertEqual(
            Allocation.objects.filter(organization=self.alpha).count(), 2
        )
        self.assertEqual(
            FinanceSubmission.objects.filter(token="tok-keep").count(), 1
        )
        submission = FinanceSubmission.objects.filter(token="tok-keep").first()
        self.assertEqual(submission.payment.pk, payment.pk)
        receipt = Receipt.objects.filter(payment=payment).first()
        self.assertEqual(receipt.number, 2)
        self.assertEqual(self.counter().first().running_value, 2)
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                payment=payment, action=AUDIT_PAYMENT_POSTED
            ).count(),
            1,
        )

    def test_a_fresh_code_still_posts_its_own_money(self):
        """After a taken code, a new one moves money and takes a number."""
        third, problems, notes = self.post(
            Decimal("100"),
            ((self.second.pk, Decimal("100")),),
            token="tok-fresh",
        )
        self.assertEqual(notes, [])
        self.assertIsNotNone(third)
        self.assertEqual(self.payments_here().count(), 2)
        receipt = Receipt.objects.filter(payment=third).first()
        self.assertEqual(receipt.number, 2)
        self.assertEqual(self.counter().first().running_value, 2)

    def test_receipt_busy_rolls_the_submission_back_as_well(self):
        """A busy counter consumes neither payment nor code."""
        self.counter().update(running_value=0)
        record, problems, notes = self.post(
            Decimal("400"),
            ((self.first.pk, Decimal("400")),),
            token="tok-try",
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["receipt_busy"])
        self.assertEqual(self.payments_here().count(), 1)
        self.assertEqual(FinanceSubmission.objects.count(), 0)
        self.assertEqual(self.counter().first().running_value, 0)
        self.counter().update(running_value=1)
        payment, problems, notes = self.post(
            Decimal("400"),
            ((self.first.pk, Decimal("400")),),
            token="tok-try",
        )
        self.assertEqual(notes, [])
        self.assertIsNotNone(payment)
        receipt = Receipt.objects.filter(payment=payment).first()
        self.assertEqual(receipt.number, 2)
        self.assertEqual(self.counter().first().running_value, 2)
        self.assertEqual(
            FinanceSubmission.objects.filter(token="tok-try").count(), 1
        )
        submission = FinanceSubmission.objects.filter(token="tok-try").first()
        self.assertEqual(submission.payment.pk, payment.pk)

    def test_a_submission_row_names_a_token_and_one_outcome(self):
        """The row itself is validated and its claim is one-per-person."""
        blank = FinanceSubmission(
            organization=self.alpha,
            recorded_by=self.finance,
            token="",
        )
        with self.assertRaises(ValidationError) as error:
            blank.full_clean(exclude=["organization"])
        self.assertIn("token", error.exception.error_dict)
        adjustment, problems, notes = reverse_payment(
            self.finance,
            self.alpha,
            self.payment.pk,
            Decimal("100"),
            reason="A family moved away",
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(adjustment)
        greedy = FinanceSubmission(
            organization=self.alpha,
            recorded_by=self.finance,
            token="two-outcomes",
            payment=self.payment,
            adjustment=adjustment,
        )
        with self.assertRaises(ValidationError) as error:
            greedy.full_clean(exclude=["organization"])
        self.assertIn("adjustment", error.exception.error_dict)
        FinanceSubmission.objects.create(
            organization=self.alpha,
            recorded_by=self.finance,
            token="twice-claimed",
        )
        with self.assertRaises(IntegrityError):
            FinanceSubmission.objects.create(
                organization=self.alpha,
                recorded_by=self.finance,
                token="twice-claimed",
            )

    @skipUnless(
        connection.vendor == "postgresql",
        "row locking needs postgresql; sqlite keeps the whole transaction",
    )
    def test_two_threads_never_both_take_the_same_outstanding(self):
        """On postgresql the second thread waits and re-reads the money."""
        results = {}

        def post(key):
            results[key] = post_payment(
                self.finance,
                self.alpha,
                self.obligation.pk,
                Decimal("400"),
                METHOD_CASH,
                allocations=((self.first.pk, Decimal("400")),),
                token="tok-race-" + key,
            )

        threads = tuple(
            threading.Thread(target=post, args=(name,))
            for name in ("one", "two")
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        posted = tuple(
            results[name][0] for name in ("one", "two")
            if results[name][0] is not None
        )
        refused = tuple(
            results[name][2] for name in ("one", "two")
            if results[name][0] is None
        )
        self.assertEqual(len(posted), 1)
        self.assertEqual(refused, (["installment_fully_allocated"],))
        self.assertEqual(
            Allocation.objects.filter(
                organization=self.alpha, installment=self.first
            ).count(),
            2,
        )


class ExpenseCurrencyTests(AgreementSetup):
    """FIN-011: the expense currency is the center's own (FIN-001's anchor).

    A new expense offered in another currency is refused, the service
    derives the center's own, the model rule guards a new record, and a
    historical row keeps a currency that has since changed.
    """

    def record(self, amount, **kwargs):
        record, problems, notes = record_financial_expense(
            self.finance,
            self.alpha,
            "Classroom supplies",
            amount,
            date.today(),
            payee="The corner shop",
            description="Notebooks for the group",
            attachment_reference="paper-1",
            **kwargs,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def test_an_offered_currency_of_another_center_is_refused(self):
        """currency_mismatch answers and nothing at all is saved."""
        record, problems, notes = record_financial_expense(
            self.finance,
            self.alpha,
            "Classroom supplies",
            Decimal("30.00"),
            date.today(),
            payee="The corner shop",
            currency="EUR",
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["currency_mismatch"])
        self.assertEqual(
            Expense.objects.filter(organization=self.alpha).count(), 0
        )

    def test_the_center_currency_is_recorded_with_the_expense(self):
        expense = self.record(Decimal("30.00"), currency="DZD")
        self.assertEqual(expense.currency, "DZD")

    def test_a_new_record_cannot_name_another_currency(self):
        expense = Expense(
            organization=self.alpha,
            recorded_by=self.finance,
            category="Classroom supplies",
            amount=Decimal("30.00"),
            currency="EUR",
            date=date.today(),
        )
        with self.assertRaises(ValidationError) as error:
            expense.full_clean(exclude=["organization"])
        self.assertIn("currency", error.exception.error_dict)

    def test_a_recorded_expense_keeps_its_historical_currency_clean(self):
        expense = self.record(Decimal("30.00"))
        Expense.objects.filter(pk=expense.pk).update(currency="EUR")
        loaded = Expense.objects.filter(pk=expense.pk).first()
        self.assertEqual(loaded.currency, "EUR")
        loaded.full_clean(exclude=["organization"])


# === NEXT CHUNK GOES HERE ===
