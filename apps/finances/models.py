"""Fee agreements, installment plans, payments, receipts, expenses and financial audit.

Every instance hangs directly from one Organization (RULE 6.1: one center, and
every read and write is scoped to it). The relations that reach the records
this slice names - the agreement's enrollment, the payment's receipt, the
expense's recorder - are resolved inside the center by the model's own hooks
and by the services, so a number typed by a browser can never name another
center's record (SEC-004).

The money rule (§13 and FIN-014): every amount is a DecimalField of two
places, never a float; every record carries its explicit currency (FIN-001,
FIN-011, FIN-007); and no record's balance is ever a stored editable field -
the outstanding amount, the collections and the expense totals are computed
from the transactions themselves (FIN-005: "do not rely on a manually
editable balance field as the source of truth").

The traceability rules (§6.8 and FIN-008) keep the history: the relations a
financial transaction depends on use PROTECT FKs, so deleting a payment, a
plan, an expense or an audit row can never erase the history it wrote, and
nothing posted is silently deleted or edited: a payment that was fully
reversed keeps its record and is marked reversed.
"""

import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.organizations.models import Organization
from apps.students.models import Student
from apps.academics.models import Enrollment
from apps.accounts.models import CohortUser

from .statuses import (
    ADJUSTMENT_KIND_CHOICES,
    EXPENSE_STATUS_CHOICES,
    FINANCE_AUDIT_ACTIONS,
    FINANCE_AUDIT_ACTION_CHOICES,
    OBLIGATION_STATUS_CHOICES,
    OBLIGATION_STATUSES,
    PAYMENT_METHOD_CHOICES,
    PAYMENT_STATUSES,
    PAYMENT_STATUS_CHOICES,
    RECEIVED_ADJUSTMENT_KINDS,
    REQUIRED_ADJUSTMENT_KINDS,
)

# FIN-013/FIN-001: money keeps its explicit three-letter ISO currency code,
# exactly as an enrollment's agreement does.
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class FeeObligation(models.Model):
    """One student's fee agreement under one enrollment (FIN-001/FIN-002).

    §7's "FeePlan/FeeObligation: the agreement's payment schedule and
    outstanding balance" row, opened by explicit finance choice at one
    enrollment (convention D81, open item 50: the SRS's FIN-001 anchor names
    the enrollment's agreement, not that an obligation must always exist).
    The agreement snapshot keeps the enrollment's agreed fee and the center's
    primary currency at opening, and a later fee-correction adjustment moves
    the amount the obligation requires (FIN-009, §6.2). One obligation per
    enrollment: the enrollment is the agreement's owner, and §7 puts this
    entity in the singular of the enrollment's row.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="fee_obligations",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.PROTECT,
        related_name="fee_obligations",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="fee_obligations",
    )
    fee_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    currency = models.CharField(max_length=3)
    status = models.CharField(
        max_length=16,
        choices=OBLIGATION_STATUS_CHOICES,
        default="open",
    )
    status_changed_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """The same-center invariants (RULE 6.1) and the money rules."""
        excluded = set(exclude or ())
        if "enrollment" not in excluded and self.enrollment_id is not None:
            if (
                self.enrollment.organization_id != self.organization_id
                or self.enrollment.student.organization_id
                != self.organization_id
            ):
                raise ValidationError(
                    {
                        "enrollment":
                            "That enrollment belongs to another center."
                    }
                )
        if "student" not in excluded and self.student_id is not None:
            if (
                self.student.organization_id != self.organization_id
                or self.enrollment.student_id != self.student_id
            ):
                raise ValidationError(
                    {
                        "student":
                            "That student is not the enrollment's student."
                    }
                )
        if "fee_snapshot" not in excluded and self.fee_snapshot is not None:
            if self.fee_snapshot < 0:
                raise ValidationError(
                    {
                        "fee_snapshot":
                            "An opened agreement cannot be a negative amount."
                    }
                )
        if "currency" not in excluded:
            self.currency = str(self.currency).strip().upper()
            if not CURRENCY_PATTERN.match(self.currency):
                raise ValidationError(
                    {
                        "currency":
                            "The agreement's currency must be a 3-letter "
                            "ISO code."
                    }
                )
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        if "note" not in excluded:
            self.note = str(self.note).strip()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment"],
                name="finances_fee_obligation_one_per_enrollment",
            ),
            models.CheckConstraint(
                condition=models.Q(fee_snapshot__gte=0),
                name="finances_fee_obligation_snapshot_not_negative",
            ),
        ]


class FeePlan(models.Model):
    """One installment plan under an agreement (FIN-002/6.7).

    The SRS's singular "FeePlan" row is the plan entity itself, so an
    obligation is an agreement and the schedules hang under it as plans
    (convention D81, open item 50: whether a center ever needs several
    schedules per agreement). A plan is the schedule an obligation may be
    split into; its installments' dates and amounts are the plan's, and the
    obligation's money rule is what an installment may add up to (§6.2).
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="fee_plans",
    )
    obligation = models.ForeignKey(
        FeeObligation,
        on_delete=models.PROTECT,
        related_name="plans",
    )
    name = models.CharField(max_length=80)
    created_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=300, blank=True)

    def clean_fields(self, exclude=None):
        excluded = set(exclude or ())
        if "obligation" not in excluded and self.obligation_id is not None:
            if (
                self.obligation.organization_id
                != self.organization_id
            ):
                raise ValidationError(
                    {
                        "obligation":
                            "That agreement belongs to another center."
                    }
                )
        if "name" not in excluded:
            self.name = str(self.name).strip()
            if not self.name:
                raise ValidationError(
                    {
                        "name":
                            "An installment plan must have a name."
                    }
                )
        if "note" not in excluded:
            self.note = str(self.note).strip()
        super().clean_fields(exclude=excluded)


class FeeInstallment(models.Model):
    """One scheduled part of an installment plan (FIN-002/FIN-010).

    §7's "Installment: one scheduled part and its due date" row: an amount
    and a due date. The installment's outstanding part is never a stored
    field: it is the amount minus what the allocations under it hold, minus
    what reversals of payments that allocated it took back (FIN-005, FIN-004,
    §6.2, and the rule that an installment's outstanding must never go
    negative).
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="fee_installments",
    )
    plan = models.ForeignKey(
        FeePlan,
        on_delete=models.PROTECT,
        related_name="installments",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    due_date = models.DateField()
    position = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=300, blank=True)

    def clean_fields(self, exclude=None):
        """The same-plan invariant and the amount rule (FIN-002/6.2)."""
        excluded = set(exclude or ())
        if "plan" not in excluded and self.plan_id is not None:
            if (
                self.plan.organization_id != self.organization_id
                or self.plan.obligation.organization_id
                != self.organization_id
            ):
                raise ValidationError(
                    {
                        "plan":
                            "That installment plan belongs to another center."
                    }
                )
        if "amount" not in excluded and self.amount is not None:
            if self.amount <= 0:
                raise ValidationError(
                    {
                        "amount":
                            "An installment must be a positive amount."
                    }
                )
        if "position" not in excluded and self.position is not None:
            if self.position < 1:
                raise ValidationError(
                    {
                        "position":
                            "An installment position starts at 1."
                    }
                )
        if "due_date" not in excluded and self.due_date is None:
            raise ValidationError(
                {
                    "due_date":
                        "An installment must name a due date."
                }
            )
        if "note" not in excluded:
            self.note = str(self.note).strip()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "position"],
                name="finances_fee_installment_one_per_plan_position",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="finances_fee_installment_amount_positive",
            ),
        ]


class Payment(models.Model):
    """One payment recorded at this center (FIN-003/FIN-006/FIN-008).

    §7's "Payment: a payment transaction posted at this center" row and the
    FIN-003 fields exactly: amount, the moment it was recorded, a fixed
    method, an optional reference, the user who recorded it, and notes. It
    is transaction recording, not payment processing (FIN-006's own note),
    and a posted payment is never silently deleted or edited: a payment
    whose whole amount has been reversed keeps its record and is marked
    reversed (FIN-008, and §13's database-transaction guarantee).
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    obligation = models.ForeignKey(
        FeeObligation,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    method = models.CharField(
        max_length=16,
        choices=PAYMENT_METHOD_CHOICES,
    )
    reference = models.CharField(max_length=100, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        CohortUser,
        on_delete=models.PROTECT,
        related_name="recorded_payments",
    )
    status = models.CharField(
        max_length=16,
        choices=PAYMENT_STATUS_CHOICES,
        default="recorded",
    )
    status_changed_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """FIN-003/FIN-008 rules; the same-center invariants first."""
        excluded = set(exclude or ())
        if "obligation" not in excluded and self.obligation_id is not None:
            if (
                self.obligation.organization_id != self.organization_id
            ):
                raise ValidationError(
                    {
                        "obligation":
                            "That agreement belongs to another center."
                    }
                )
        if "recorded_by" not in excluded and self.recorded_by_id is not None:
            if not self.recorded_by.is_authenticated:
                raise ValidationError(
                    {
                        "recorded_by":
                            "A payment must name an authenticated person.",
                    }
                )
        if "amount" not in excluded and self.amount is not None:
            if self.amount <= 0:
                raise ValidationError(
                    {
                        "amount":
                            "A payment must be a positive amount.",
                    }
                )
        if "reference" not in excluded:
            self.reference = str(self.reference).strip()
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        if "note" not in excluded:
            self.note = str(self.note).strip()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="finances_payment_amount_positive",
            ),
        ]


class Receipt(models.Model):
    """The one receipt a posted payment writes (FIN-007/6.7).

    §7's "Receipt: a one-to-one receipt record tied to its payment" row and
    FIN-007's fields: the center identity (its organization), the receipt
    number, the student, the amount and its explicit currency, the date and
    the method. The number is kept unique by the numbering convention
    (D81: a per-center counter record, so concurrency never reuses one; §6.7
    requires it). A posted payment never loses its receipt and a receipt is
    never posted twice: the one-to-one tie is the constraint, and a
    reversal writes its own traceable transaction (FIN-008) and never
    touches this receipt or adds a second one: the payment keeps exactly
    the one receipt its posting wrote (§7's one-to-one row).
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="receipt",
        unique=True,
    )
    number = models.PositiveIntegerField()
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    currency = models.CharField(max_length=3)
    date = models.DateTimeField()
    method = models.CharField(
        max_length=16,
        choices=PAYMENT_METHOD_CHOICES,
    )
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """The one-tie identity, and the money rules (FIN-007)."""
        excluded = set(exclude or ())
        if "payment" not in excluded and self.payment_id is not None:
            if (
                self.payment.organization_id != self.organization_id
                or self.payment.obligation.organization_id
                != self.organization_id
            ):
                raise ValidationError(
                    {
                        "payment":
                            "That payment belongs to another center.",
                    }
                )
        if "student" not in excluded and self.student_id is not None:
            if (
                self.student.organization_id != self.organization_id
                or self.student_id != self.payment.obligation.student_id
            ):
                raise ValidationError(
                    {
                        "student":
                            "A receipt must name its payment's agreement's "
                            "own student.",
                    }
                )
        if "currency" not in excluded:
            self.currency = str(self.currency).strip().upper()
            if not CURRENCY_PATTERN.match(self.currency):
                raise ValidationError(
                    {
                        "currency":
                            "A receipt must carry its agreement's 3-letter "
                            "currency.",
                    }
                )
        if "amount" not in excluded and self.amount is not None:
            if self.amount != self.payment.amount:
                raise ValidationError(
                    {
                        "amount":
                            "A receipt must show its payment's own amount.",
                    }
                )
        if "date" not in excluded and self.date is not None:
            if self.date != self.payment.recorded_at:
                raise ValidationError(
                    {
                        "date":
                            "A receipt must show its payment's own moment.",
                    }
                )
        if "method" not in excluded and self.method:
            if self.method != self.payment.method:
                raise ValidationError(
                    {
                        "method":
                            "A receipt must show its payment's own method.",
                    }
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                name="finances_receipt_number_one_per_center",
            ),
        ]


class Allocation(models.Model):
    """One payment's part put against one installment (FIN-004/6.7).

    §7's "Allocation: one payment's part against one installment" row and
    §6.7's rule: the payment's allocations must add exactly to the payment,
    and no installment may hold more of a payment's part than it still has
    outstanding. Each allocation names the payment and the installment, so
    the payment's collections (6.7) read it, and a reversal's accounting
    reads it too (6.2): the installment whose part a reversed payment
    allocated is the installment whose outstanding the reversal puts back.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    installment = models.ForeignKey(
        FeeInstallment,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """The same-obligation invariant, and FIN-004's amount rule."""
        excluded = set(exclude or ())
        if "payment" not in excluded and self.payment_id is not None:
            if (
                self.payment.organization_id != self.organization_id
                or self.payment.obligation.organization_id
                != self.organization_id
            ):
                raise ValidationError(
                    {
                        "payment":
                            "That payment belongs to another center.",
                    }
                )
        if "installment" not in excluded and self.installment_id is not None:
            if (
                self.installment.organization_id != self.organization_id
                or self.installment.plan.obligation_id
                != self.payment.obligation_id
            ):
                raise ValidationError(
                    {
                        "installment":
                            "That installment does not belong to this "
                            "payment's agreement.",
                    }
                )
        if "amount" not in excluded and self.amount is not None:
            if self.amount <= 0:
                raise ValidationError(
                    {
                        "amount":
                            "An allocation must be a positive amount.",
                    }
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="finances_allocation_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["payment", "installment"],
                name="finances_allocation_one_part_per_installment",
            ),
        ]


class FinancialAdjustment(models.Model):
    """One financial transaction that is not a payment (FIN-008/FIN-009).

    §7's "FinancialAdjustment: a financial transaction that is not a
    payment" row and the five kinds the SRS names (FIN-008's corrections,
    refunds and reversals, FIN-009's discounts, waivers and fee
    adjustments). Each kind carries the amount at stake, the reason, and the
    person who acted (FIN-009's "with reason, authorizing user and audit
    record"), and the reversals name the payment whose posting they cancel
    (FIN-008's and §6.2's reversal rule: "reversing a payment's posting is
    not the same as reducing the obligation").
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="financial_adjustments",
    )
    obligation = models.ForeignKey(
        FeeObligation,
        on_delete=models.PROTECT,
        related_name="financial_adjustments",
    )
    kind = models.CharField(
        max_length=16,
        choices=ADJUSTMENT_KIND_CHOICES,
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="reversals",
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=300, blank=True)
    actor = models.ForeignKey(
        CohortUser,
        on_delete=models.PROTECT,
        related_name="financial_adjustments",
    )
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """The per-kind rules (§6.2/FIN-008/FIN-009), field-keyed."""
        excluded = set(exclude or ())
        if "obligation" not in excluded and self.obligation_id is not None:
            if (
                self.obligation.organization_id
                != self.organization_id
            ):
                raise ValidationError(
                    {
                        "obligation":
                            "That agreement belongs to another center.",
                    }
                )
        if "payment" not in excluded and self.payment_id is not None:
            if (
                self.payment.organization_id != self.organization_id
                or self.payment.obligation_id != self.obligation_id
            ):
                raise ValidationError(
                    {
                        "payment":
                            "That payment belongs to another agreement.",
                    }
                )
        if "kind" not in excluded:
            self.kind = str(self.kind).strip().lower()
            if self.kind not in (
                *REQUIRED_ADJUSTMENT_KINDS,
                *RECEIVED_ADJUSTMENT_KINDS,
            ):
                raise ValidationError(
                    {
                        "kind":
                            "The adjustment kind must be one of discount, "
                            "waiver, correction, refund, reversal.",
                    }
                )
        if "amount" not in excluded and self.amount is not None:
            if self.kind in REQUIRED_ADJUSTMENT_KINDS:
                if self.amount <= 0:
                    raise ValidationError(
                        {
                            "amount":
                                "A discount, waiver or fee correction must "
                                "have an amount at stake.",
                        }
                    )
            elif self.amount <= 0:
                raise ValidationError(
                    {
                        "amount":
                            "A refund or reversal's amount must be the money "
                            "it takes back.",
                    }
                )
        if "reason" not in excluded:
            self.reason = str(self.reason).strip()
        if "actor" not in excluded and self.actor_id is not None:
            if not self.actor.is_authenticated:
                raise ValidationError(
                    {
                        "actor":
                            "The authorizing user must be an authenticated "
                            "person.",
                    }
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(payment__isnull=True)
                    | models.Q(payment__isnull=False, kind="reversal")
                ),
                name="finances_adjustment_payment_only_a_reversal_names_it",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="reversal")
                    | models.Q(payment__isnull=True)
                ),
                name="finances_adjustment_reversal_needs_a_payment",
            ),
        ]


class Expense(models.Model):
    """One basic operating expense (FIN-011).

    §7's "Expense: a basic operating expense record" row and FIN-011's
    fields exactly: category, the amount and its currency, a date, a payee
    or description, an optional attachment reference and who recorded it.
    An expense record never moves money: it is tracked apart, and its
    category, description and attachment reference are the record's own
    text (6.7's rule and FIN-011's optional attachment).
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    category = models.CharField(max_length=80)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    currency = models.CharField(max_length=3)
    date = models.DateField()
    payee = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=300, blank=True)
    attachment_reference = models.CharField(
        max_length=200,
        blank=True,
    )
    recorded_by = models.ForeignKey(
        CohortUser,
        on_delete=models.PROTECT,
        related_name="expense_records",
    )
    status = models.CharField(
        max_length=16,
        choices=EXPENSE_STATUS_CHOICES,
        default="recorded",
    )
    status_changed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """FIN-011's own rules; the same-center invariant is the FK's."""
        excluded = set(exclude or ())
        if "category" not in excluded:
            self.category = str(self.category).strip()
            if not self.category:
                raise ValidationError(
                    {
                        "category":
                            "An expense must have a category.",
                    }
                )
        if "amount" not in excluded and self.amount is not None:
            if self.amount <= 0:
                raise ValidationError(
                    {
                        "amount":
                            "A recorded expense must be a positive amount.",
                    }
                )
        if "currency" not in excluded:
            self.currency = str(self.currency).strip().upper()
            if not CURRENCY_PATTERN.match(self.currency):
                raise ValidationError(
                    {
                        "currency":
                            "An expense must name a 3-letter currency code.",
                    }
                )
            if (
                self._state.adding
                and self.organization_id is not None
                and str(self.organization.primary_currency).strip().upper()
                != self.currency
            ):
                raise ValidationError(
                    {
                        "currency":
                            "An expense must be recorded in this center's "
                            "own currency.",
                    }
                )
        if "payee" not in excluded:
            self.payee = str(self.payee).strip()
        if "description" not in excluded:
            self.description = str(self.description).strip()
        if "attachment_reference" not in excluded:
            self.attachment_reference = str(
                self.attachment_reference
            ).strip()
        if "recorded_by" not in excluded and self.recorded_by_id is not None:
            if not self.recorded_by.is_authenticated:
                raise ValidationError(
                    {
                        "recorded_by":
                            "An expense record must name an authenticated "
                            "person.",
                    }
                )
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="finances_expense_amount_positive",
            ),
        ]


class FinanceAuditEvent(models.Model):
    """One audited financial action (FIN-009/6.5 and the §13 audit trail).

    §13 names financial transactions audited, FIN-009 requires a discount,
    waiver or fee adjustment with an audit record, and §6.5 says the audited
    financial actions are the financial transactions. The row says what
    happened and where, and who did it: the action name, the center's own
    agreement, payment, expense or receipt record the action belongs to, and
    the person who acted. Like the attendance corrections' audit rows, the
    action name is the only vocabulary the services add to.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="finance_audit_events",
    )
    action = models.CharField(
        max_length=40,
        choices=FINANCE_AUDIT_ACTION_CHOICES,
    )
    actor = models.ForeignKey(
        CohortUser,
        on_delete=models.PROTECT,
        related_name="finance_audit_events",
    )
    obligation = models.ForeignKey(
        FeeObligation,
        on_delete=models.PROTECT,
        related_name="audit_events",
        null=True,
        blank=True,
    )
    plan = models.ForeignKey(
        FeePlan,
        on_delete=models.PROTECT,
        related_name="audit_events",
        null=True,
        blank=True,
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="audit_events",
        null=True,
        blank=True,
    )
    expense = models.ForeignKey(
        Expense,
        on_delete=models.PROTECT,
        related_name="audit_events",
        null=True,
        blank=True,
    )
    at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """The audited record and its center (6.5), one source for the set."""
        excluded = set(exclude or ())
        if "action" not in excluded:
            self.action = str(self.action).strip().lower()
            if self.action not in FINANCE_AUDIT_ACTIONS:
                raise ValidationError(
                    {
                        "action":
                            "The audited action must be one this slice names.",
                    }
                )
        if "actor" not in excluded and self.actor_id is not None:
            if not self.actor.is_authenticated:
                raise ValidationError(
                    {
                        "actor":
                            "An audited financial action must name an "
                            "authenticated person.",
                    }
                )
        for name in ("obligation", "plan", "payment", "expense"):
            record = getattr(self, name)
            if name in excluded or record is None:
                continue
            if record.organization_id != self.organization_id:
                raise ValidationError(
                    {
                        name:
                            "That audited record belongs to another center.",
                    }
                )
        named = [
            name
            for name in ("obligation", "plan", "payment", "expense")
            if getattr(self, name) is not None
        ]
        if len(named) > 1:
            raise ValidationError(
                {
                    "payment":
                        "One audited financial action names one audited "
                        "record.",
                }
            )
        super().clean_fields(exclude=excluded)


class ReceiptCounter(models.Model):
    """The one per-centre receipt-numbering counter (convention D81, 6.7).

    §6.7 requires receipt numbers unique in their scope and §13 requires
    financial transactions to be safe: the next number is handed out from
    this single record per center, saved under the same transactional block
    as the receipt it writes, so two postings never number the same receipt
    and an unprotected MAX() plus one is never the rule. The counter never
    moves money; it only numbers.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="receipt_counters",
    )
    name = models.CharField(max_length=40)
    running_value = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="finances_receipt_counter_one_per_center",
            ),
        ]


class FinanceSubmission(models.Model):
    """One person's one submitted financial action, by its token (D86).

    Idempotency for the posting pages: a browser that posts the same
    submission twice - a dropped response, a pressed button, a replayed
    request - must not move money twice, so the page gives each new
    submission a fresh token and this row records that the person at
    this centre claimed that token once. The (organization, recorded_by,
    token) uniqueness is what makes the claim one-per-person, and the
    outcome FK - the saved payment or the saved reversal - links in the
    same transaction that saves the outcome, so a committed row always
    names one saved outcome and a replay answers with it. Refusals
    roll this row back, so a token is only consumed by an action that
    was actually taken. Both outcome FKs stay non-unique on purpose: a
    payment can legitimately be the outcome of its own posting
    submission only, and uniqueness there would name nothing the
    model does not already keep (D86).
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="finance_submissions",
    )
    recorded_by = models.ForeignKey(
        CohortUser,
        on_delete=models.PROTECT,
        related_name="finance_submissions",
    )
    token = models.CharField(max_length=80)
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="finance_submissions",
        null=True,
        blank=True,
    )
    adjustment = models.ForeignKey(
        FinancialAdjustment,
        on_delete=models.PROTECT,
        related_name="finance_submissions",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """The token claim itself, and the one-outcome discipline."""
        excluded = set(exclude or ())
        if "token" not in excluded:
            self.token = str(self.token)
            if not self.token:
                raise ValidationError(
                    {
                        "token":
                            "A submission must name its token.",
                    }
                )
        if "payment" not in excluded and "adjustment" not in excluded:
            if (
                self.payment_id is not None
                and self.adjustment_id is not None
            ):
                raise ValidationError(
                    {
                        "adjustment":
                            "One submission names one saved outcome.",
                    }
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "recorded_by", "token"],
                name="finances_finance_submission_one_per_token",
            ),
        ]
