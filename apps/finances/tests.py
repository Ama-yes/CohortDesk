"""Fee agreements, installment plans, payments and expenses, as the services
answer them (FIN-001..FIN-015 without the gateways FIN-016).

The rules under test are §6.2's and §6.7's financial rules and FIN-001..FIN-015
as this product's services implement them: organization-scoped records (RULE 6.1),
one agreement per enrollment opened at the enrollment's own agreed amount
(FIN-001), installment plans whose installments never add above the amount the
agreement requires (6.2's plan rule), payments allocated exactly to that payment
against outstanding installments (FIN-004/6.7), reversals that undo allocations
in the stored order and never take more than a payment holds (FIN-008/6.2),
adjustments that move the required amount or the received money (FIN-009/6.2),
expense records kept apart from collections (FIN-011/FIN-012), the audit trail
FIN-008 names, the receipt numbering that never reuses a number even under a
busy centre (§13's guarantee, convention D81), and the derived figures that are
never a stored balance (FIN-005/RULE 6.2). Cross-tenant proof through the URL
layer lives in apps/finances/test_access.py.

Every action here is called the way a page calls it (service function, not
model API), because the service layer is the one place the rules live.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.permissions import role_has_permission
from apps.organizations.roles import (
    ROLE_ACADEMIC_COORDINATOR,
    ROLE_ADMINISTRATOR,
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)
from apps.students.models import Student
from apps.students.statuses import STATUS_ACTIVE, STATUS_ARCHIVED
from apps.academics.services import create_course, create_cohort, enroll_student
from apps.academics.statuses import COHORT_ACTIVE, ENROLLMENT_CANCELLED

from .models import (
    Allocation,
    Expense,
    FeeInstallment,
    FeeObligation,
    FeePlan,
    FinanceAuditEvent,
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
    OPEN_OBLIGATION_STATUSES,
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
    RECEIVED_ADJUSTMENT_KINDS,
    REQUIRED_ADJUSTMENT_KINDS,
)
