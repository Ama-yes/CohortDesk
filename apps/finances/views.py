"""The finance pages: where a centre's fee money is read and recorded.

Same error model as every other tenant address (the students pages):
- unknown centre / not signed in / no active membership here -> the project
  problem page at 404 (SEC-003/004: the address never reveals whether an
  unseen centre exists),
- a member here but the role is not permitted for this page -> this centre's
  denied page at 403,
- a non-read method at a read-only address -> the problem page at 405,
- a number this centre has no record for -> this centre's own not-found page
  at 404, which says nothing about any other centre.

Data rule: every row is built here from this centre's own queryset (RULE 6.1),
and every identifier typed on a page is resolved by the services inside this
centre, so a number a browser typed can never name another centre's
obligation, payment or expense (FIN-015: financial reports and exports obey
finance permissions and organization isolation exactly as every other page
does).

The money rule: every derived figure on these pages is computed from the
transactions themselves, never read from a stored balance field (FIN-005 and
6.2: "do not rely on a manually editable balance field as the source of
truth"), and every money row carries its explicit currency (FIN-001, FIN-007
and FIN-014: fixed-precision decimals and their currency, never floats).
FIN-012's line is what the pages speak: the collections, the receivables and
the expenses each keep their own name, and nothing here is ever called
profit or statutory accounts.

FIN-010's summaries and FIN-007's receipts are built here; FIN-002..FIN-011's
recording happens on the manage pages, whose forms share one definition with
the rows they show (the manage-page pattern every other area uses)."""

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import secrets

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone

from apps.organizations.permissions import role_has_permission
from apps.organizations.views import READ_METHODS, here
from apps.pages import center_ui, page

from .models import (
    Allocation,
    Expense,
    FeeInstallment,
    FeeObligation,
    FeePlan,
    FinancialAdjustment,
    Payment,
    Receipt,
    FinanceAuditEvent,
)
from .reporting import (
    financial_rows,
    obligation_figures,
    obligation_rows,
    payment_figures,
    _installment_outstanding,
    _obligation_payments,
    _obligation_today,
)
from .services import (
    _record_at,
    _submission_row,
    create_fee_plan,
    create_fee_installment,
    open_fee_obligation,
    post_payment,
    record_financial_adjustment,
    record_financial_expense,
    reverse_payment,
    set_financial_obligation_status,
    withdraw_financial_expense,
)
from .statuses import (
    OBLIGATION_CLOSED,
    OBLIGATION_OPEN,
    OBLIGATION_PAID,
    OBLIGATION_STATUSES,
    PAYMENT_METHODS,
    PAYMENT_RECORDED,
    PAYMENT_REVERSED,
    ADJ_CORRECTION,
    ADJ_DISCOUNT,
    ADJ_REFUND,
    ADJ_REVERSAL,
    ADJ_WAIVER,
    ADJUSTMENT_KINDS,
    EXPENSE_RECORDED,
    EXPENSE_WITHDRAWN,
)

# The actions the obligation manage page takes, and the glossary key that
# names each (the manage-page pattern: nothing is shown untranslated). The
# four fee-agreement actions share one page, so a person works down one
# agreement - open it, give it a plan, schedule its installments, move its
# status - without chasing four different addresses.
OBLIGATION_ACTIONS = ("open", "set_status", "create_plan", "add_installment")
OBLIGATION_ACTION_UI = {
    "open": "obligation_action_open",
    "set_status": "obligation_action_set_status",
    "create_plan": "obligation_action_create_plan",
    "add_installment": "obligation_action_add_installment",
}
OBLIGATION_FIELDS = (
    "obligation_action",
    "obligation_target",
    "obligation_status",
    "obligation_name",
    "obligation_amount",
    "obligation_date",
    "obligation_note",
)
OBLIGATION_FIELD_UI = {
    "obligation_action": "obligation_action",
    "obligation_target": "field_target_number",
    "obligation_status": "field_obligation_status",
    "obligation_name": "field_plan_name",
    "obligation_amount": "field_installment_amount",
    "obligation_date": "field_due_date",
    "obligation_note": "field_note",
}

PAYMENT_ACTIONS = ("record", "reverse")
PAYMENT_ACTION_UI = {
    "record": "payment_action_record",
    "reverse": "payment_action_reverse",
}
PAYMENT_FIELDS = (
    "payment_action",
    "payment_target",
    "payment_amount",
    "payment_method",
    "payment_reference",
    "payment_allocations",
    "payment_payment",
    "payment_reason",
    "payment_note",
    "payment_token",
    "payment_confirm",
)
PAYMENT_FIELD_UI = {
    "payment_action": "payment_action",
    "payment_target": "field_target_number",
    "payment_amount": "field_amount",
    "payment_method": "field_payment_method",
    "payment_reference": "field_payment_reference",
    "payment_allocations": "field_allocations",
    "payment_payment": "field_payment_number",
    "payment_reason": "field_reason",
    "payment_note": "field_note",
    "payment_token": "field_payment_token",
    "payment_confirm": "payment_confirm",
}

ADJUSTMENT_ACTIONS = (ADJ_DISCOUNT, ADJ_WAIVER, ADJ_CORRECTION, ADJ_REFUND)
ADJUSTMENT_ACTION_UI = {
    ADJ_DISCOUNT: "adjustment_action_discount",
    ADJ_WAIVER: "adjustment_action_waiver",
    ADJ_CORRECTION: "adjustment_action_correction",
    ADJ_REFUND: "adjustment_action_refund",
}
ADJUSTMENT_FIELDS = (
    "adjustment_action",
    "adjustment_target",
    "adjustment_amount",
    "adjustment_reason",
)
ADJUSTMENT_FIELD_UI = {
    "adjustment_action": "adjustment_action",
    "adjustment_target": "field_target_number",
    "adjustment_amount": "field_amount",
    "adjustment_reason": "field_reason",
}

EXPENSE_ACTIONS = ("record", "withdraw")
EXPENSE_ACTION_UI = {
    "record": "expense_action_record",
    "withdraw": "expense_action_withdraw",
}
EXPENSE_FIELDS = (
    "expense_action",
    "expense_target",
    "expense_category",
    "expense_amount",
    "expense_date",
    "expense_payee",
    "expense_description",
    "expense_attachment",
)
EXPENSE_FIELD_UI = {
    "expense_action": "expense_action",
    "expense_target": "field_target_number",
    "expense_category": "field_expense_category",
    "expense_amount": "field_amount",
    "expense_date": "field_date",
    "expense_payee": "field_payee",
    "expense_description": "field_description",
    "expense_attachment": "field_attachment_reference",
}

# Which summary rows the hub page shows as money with the centre's currency
# (FIN-001/6.2: every figure carries its explicit currency).
FINANCIAL_MONEY_ROWS = (
    "required",
    "received",
    "outstanding",
    "overdue_amount",
    "collections",
    "expense_total",
)

# Which agreement rows the detail page shows as money with the agreement's
# own currency (FIN-001/FIN-014: every figure carries its explicit currency).
OBLIGATION_MONEY_ROWS = (
    "required",
    "allocated",
    "taken_back",
    "outstanding",
    "overdue_amount",
    "collections",
    "payment_amount",
)

# The form select choices each manage page offers, with the glossary key
# naming each (the manage-page pattern again: the service owns what a status
# move may do, and a code the service refuses shows as its own problem row).
OBLIGATION_STATUS_SELECT = tuple(
    (value, "obligation_status_" + value) for value in OBLIGATION_STATUSES
)
PAYMENT_METHOD_SELECT = tuple(
    (value, "payment_method_" + value) for value in PAYMENT_METHODS
)
OBLIGATION_ACTION_SELECT = tuple(
    (value, OBLIGATION_ACTION_UI[value]) for value in OBLIGATION_ACTIONS
)
PAYMENT_ACTION_SELECT = tuple(
    (value, PAYMENT_ACTION_UI[value]) for value in PAYMENT_ACTIONS
)
PAYMENT_CONFIRM_SELECT = (
    ("no", "payment_confirm_no"),
    ("yes", "payment_confirm_yes"),
)
ADJUSTMENT_ACTION_SELECT = tuple(
    (value, ADJUSTMENT_ACTION_UI[value])
    for value in ADJUSTMENT_KINDS
    if value != ADJ_REVERSAL
)
EXPENSE_ACTION_SELECT = tuple(
    (value, EXPENSE_ACTION_UI[value]) for value in EXPENSE_ACTIONS
)

# Which form field a service's business code marks (the row the person typed
# gets the problem: RULE 6.3's page contract, one source for every manage
# page here). The codes are exactly the ones services.py answers with.
OBLIGATION_PROBLEM_UI = {
    "enrollment_not_found": ("obligation_target", "not_found_enrollment"),
    "student_archived": ("obligation_target", "obligation_student_archived"),
    "enrollment_closed": ("obligation_target", "enrollment_closed"),
    "fee_not_agreed": ("obligation_target", "fee_not_agreed"),
    "already_open": ("obligation_target", "obligation_already_open"),
    "obligation_not_found": ("obligation_target", "not_found_obligation"),
    "obligation_closed": ("obligation_target", "obligation_closed"),
    "plan_not_found": ("obligation_target", "not_found_plan"),
    "plan_over_fee": ("obligation_amount", "plan_over_fee"),
    "already_closed": ("obligation_target", "obligation_already_closed"),
    "transition": ("obligation_status", "transition_not_allowed"),
}
PAYMENT_PROBLEM_UI = {
    "obligation_not_found": ("payment_target", "not_found_obligation"),
    "obligation_closed": ("payment_target", "obligation_closed"),
    "amount_not_positive": ("payment_amount", "amount_not_positive"),
    "method_unknown": ("payment_method", "method_unknown"),
    "installment_not_here": ("payment_allocations", "installment_not_here"),
    "installment_repeated": ("payment_allocations", "installment_repeated"),
    "allocation_not_positive": ("payment_allocations", "allocation_not_positive"),
    "allocation_not_whole_payment": (
        "payment_allocations",
        "allocation_not_whole_payment",
    ),
    "installment_fully_allocated": (
        "payment_allocations",
        "installment_fully_allocated",
    ),
    "payment_not_found": ("payment_payment", "not_found_payment"),
    "already_reversed": ("payment_payment", "payment_already_reversed"),
    "over_taken": ("payment_amount", "over_taken"),
    "payment_token_required": ("payment_token", "payment_token_required"),
}
ADJUSTMENT_PROBLEM_UI = {
    "obligation_not_found": ("adjustment_target", "not_found_obligation"),
    "kind_unknown": ("adjustment_action", "kind_unknown"),
    "reversal_needs_payment": ("adjustment_action", "reversal_needs_payment"),
    "amount_not_positive": ("adjustment_amount", "amount_not_positive"),
    "over_required": ("adjustment_amount", "over_required"),
    "over_received": ("adjustment_amount", "over_received"),
}
EXPENSE_PROBLEM_UI = {
    "expense_not_found": ("expense_target", "not_found_expense"),
    "already_withdrawn": ("expense_target", "expense_already_withdrawn"),
    "amount_not_positive": ("expense_amount", "amount_not_positive"),
    "transition": ("expense_action", "transition_not_allowed"),
    "currency_mismatch": ("expense_amount", "currency_mismatch"),
}

# Codes that answer with a note instead of marking a field row: already_closed
# and already_withdrawn come back with the record itself (nothing changed),
# and receipt_busy is a centre whose numbering is busy - nothing was saved.
OBLIGATION_UNCHANGED_UI = {
    "already_closed": "obligation_already_closed",
}
PAYMENT_RETRY_UI = ("receipt_busy",)
EXPENSE_UNCHANGED_UI = {
    "already_withdrawn": "expense_already_withdrawn",
}

# Which page row a model-field problem names (full_clean answers with model
# field names; the page marks the row the person typed).
OBLIGATION_MODEL_FIELDS = {
    "note": "obligation_note",
    "name": "obligation_name",
    "amount": "obligation_amount",
    "due_date": "obligation_date",
    "status": "obligation_status",
}
PAYMENT_MODEL_FIELDS = {
    "amount": "payment_amount",
    "method": "payment_method",
    "reference": "payment_reference",
    "note": "payment_note",
    "reason": "payment_reason",
}
ADJUSTMENT_MODEL_FIELDS = {
    "kind": "adjustment_action",
    "amount": "adjustment_amount",
    "reason": "adjustment_reason",
}
EXPENSE_MODEL_FIELDS = {
    "category": "expense_category",
    "amount": "expense_amount",
    "date": "expense_date",
    "payee": "expense_payee",
    "description": "expense_description",
    "attachment_reference": "expense_attachment",
}


def money_text(ui, amount, currency):
    """An amount with its explicit currency, or the page's not-recorded words."""
    if amount is None:
        return ui["not_recorded"]
    text = "{:.2f}".format(amount)
    if currency:
        return "{} {}".format(text, currency)
    return text


def date_text(value):
    """A date in the ISO form the form asks for."""
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def moment_text(center, value):
    """A recorded moment, shown in this centre's own timezone (ORG-002)."""
    zone = center.display_timezone or "UTC"
    try:
        return value.astimezone(_zone(zone)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value.strftime("%Y-%m-%d %H:%M")


def _zone(name):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name)
    except LookupError:
        return ZoneInfo("UTC")


def parse_decimal(text):
    """(Decimal or None, accepted) - a page parses before a service is called."""
    text = str(text).strip()
    if not text:
        return None, False
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, False
    if not value.is_finite():
        return None, False
    return value, True


def parse_date(text):
    """(date or None, accepted) - a page parses before a service is called."""
    text = str(text).strip()
    if not text:
        return None, False
    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), True
    except ValueError:
        return None, False


def parse_allocations(text):
    """One installment's number and amount per line: "5=300.00".

    FIN-004's allocation of one payment to one or more outstanding
    installments: the rows the person typed, as (installment_number,
    Decimal) pairs. A row that does not parse is refused whole (RULE 6.3:
    nothing half-taken), and the service then owns the allocation rules
    (6.7: one allocation per installment, the payments of a payment add to
    the payment itself, never more than the installment's amount).
    """
    rows = []
    for line in str(text).splitlines():
        line = line.strip()
        if not line:
            continue
        head, _sep, tail = line.partition("=")
        installment_number = head.strip()
        amount, ok = parse_decimal(tail)
        if not ok or not installment_number:
            return None, False
        rows.append((installment_number, amount))
    return rows, True


def rows_from_spec(ui, sent, problems, fields, field_ui):
    """One manage page's rows: what the page is, and what was not accepted."""
    return [
        {
            "id": name,
            "title": ui[field_ui[name]],
            "value": sent.get(name, ""),
            "problem": problems.get(name, ""),
        }
        for name in fields
    ]


def form_at(action, ui, sent, fields, field_ui, selects):
    """The manage page's form, built from the same rows it shows."""
    fields_out = []
    for name in fields:
        field = {
            "id": name,
            "title": ui[field_ui[name]],
            "type": "text",
            "value": sent.get(name, ""),
        }
        if name in selects:
            field["type"] = "select"
            field["current"] = sent.get(name, "")
            field["options"] = [
                {"value": code, "label": ui[key]} for code, key in selects[name]
            ]
        fields_out.append(field)
    return {
        "id": action.rstrip("/"),
        "method": "post",
        "action": action,
        "fields": fields_out,
    }


def _field_problem(ui, problems, codes, table):
    """Mark the fields a service refused (the rows the person typed)."""
    for code in codes:
        target = table.get(code)
        if target is None:
            continue
        field_id, key = target
        problems[field_id] = ui[key]


def _model_problems(ui, problems, field_problems, model_fields):
    """Mark the rows full_clean named (DECISIONS D20: the English wording of
    a validator is shown as text only, and its translation is open)."""
    for name, message in field_problems:
        row = model_fields.get(name, name)
        problems[row] = ui["field_invalid"].format(message)


def receipt_at(center, number):
    """This centre's receipt with that number, or None (RULE 6.1).

    The receipt's own number is what its pages show and address it by
    (FIN-007's receipt identifier), and it comes from the centre's counter.
    """
    text = str(number).strip()
    if not text.isdigit():
        return None
    return Receipt.objects.filter(
        organization=center, number=int(text)
    ).first()


def _record_here(model, center, number):
    """One of this centre's records by its number, or None (RULE 6.1).

    The number is resolved inside this centre only, so a number a browser
    typed can never name another centre's record (FIN-015's isolation rule).
    """
    text = str(number).strip()
    if not text.isdigit():
        return None
    return model.objects.filter(organization=center, pk=int(text)).first()


def _installment_rows(ui, center, obligation):
    """One agreement's installments: amount, date, and what is still owed.

    FIN-004/FIN-011: each installment's outstanding part is derived from its
    allocations and a reversal's taking back, never stored (FIN-005: no
    balance field), and an installment whose own part is still owed past its
    date is overdue in this centre's own today (ORG-002).
    """
    today = _obligation_today(center)
    rows = []
    for installment in FeeInstallment.objects.filter(
        plan__obligation=obligation, organization=center
    ).order_by("plan__created_at", "position"):
        left = _installment_outstanding(installment)
        rows.append(
            {
                "id": str(installment.pk),
                "plan": str(installment.plan_id),
                "amount": money_text(ui, installment.amount, obligation.currency),
                "due": date_text(installment.due_date),
                "outstanding": money_text(ui, left, obligation.currency),
                "overdue": (
                    ui["installment_overdue_yes"]
                    if installment.due_date < today and left > 0
                    else ui["installment_overdue_no"]
                ),
                "note": installment.note or ui["not_recorded"],
                "at": moment_text(center, installment.created_at),
            }
        )
    return rows


def _plan_rows(ui, center, obligation):
    """One agreement's plans, as the pages name them (FIN-002)."""
    rows = []
    for plan in FeePlan.objects.filter(
        obligation=obligation, organization=center
    ).order_by("created_at"):
        installments = FeeInstallment.objects.filter(
            plan=plan, organization=center
        )
        scheduled = sum(
            (installment.amount for installment in installments),
            Decimal("0.00"),
        )
        rows.append(
            {
                "id": str(plan.pk),
                "name": plan.name,
                "installments": str(installments.count()),
                "scheduled": money_text(
                    ui, scheduled, obligation.currency
                ),
                "note": plan.note or ui["not_recorded"],
                "at": moment_text(center, plan.created_at),
            }
        )
    return rows


def _payment_rows(ui, center, obligation):
    """One agreement's payments, with what each one really received."""
    rows = []
    for payment in _obligation_payments(obligation).filter(
        organization=center
    ).order_by("created_at"):
        figures = payment_figures(payment)
        receipt = Receipt.objects.filter(
            payment=payment, organization=center
        ).first()
        rows.append(
            {
                "id": str(payment.pk),
                "amount": money_text(
                    ui, figures["amount"], obligation.currency
                ),
                "received": money_text(
                    ui, figures["received"], obligation.currency
                ),
                "method": ui["payment_method_" + payment.method],
                "status": ui["payment_status_" + payment.status],
                "receipt": (
                    str(receipt.number)
                    if receipt is not None
                    else ui["receipt_none"]
                ),
                "at": moment_text(center, payment.created_at),
                "actor": payment.recorded_by.display_name
                or payment.recorded_by.username,
            }
        )
    return rows


def _adjustment_rows(ui, center, obligation=None):
    """Adjustment rows for one agreement, or for the whole centre.

    Reversals are shown on the payment's own pages and in history; here the
    agreement's adjustments list keeps the discounts, waivers, corrections
    and refunds that move its requirement and its receivables (FIN-009).
    """
    adjustments = FinancialAdjustment.objects.filter(organization=center)
    if obligation is not None:
        adjustments = adjustments.filter(obligation=obligation)
    rows = []
    for adjustment in adjustments.order_by("created_at"):
        rows.append(
            {
                "id": str(adjustment.pk),
                "agreement": str(adjustment.obligation.pk),
                "kind": ui["adjustment_kind_" + adjustment.kind],
                "amount": money_text(
                    ui,
                    adjustment.amount,
                    obligation.currency
                    if obligation is not None
                    else adjustment.obligation.currency,
                ),
                "payment": str(adjustment.payment_id)
                if adjustment.payment_id is not None
                else ui["not_recorded"],
                "reason": adjustment.reason or ui["not_recorded"],
                "actor": adjustment.actor.display_name
                or adjustment.actor.username,
                "at": moment_text(center, adjustment.created_at),
            }
        )
    return rows


def _expense_rows(ui, center):
    """This centre's expenses, kept apart from its fee money (FIN-012)."""
    rows = []
    for expense in Expense.objects.filter(organization=center).order_by(
        "created_at"
    ):
        rows.append(
            {
                "id": str(expense.pk),
                "category": expense.category or ui["not_recorded"],
                "amount": money_text(ui, expense.amount, expense.currency),
                "date": date_text(expense.date),
                "payee": expense.payee or ui["not_recorded"],
                "description": expense.description or ui["not_recorded"],
                "attachment": expense.attachment_reference
                or ui["not_recorded"],
                "status": ui["expense_status_" + expense.status],
                "at": moment_text(center, expense.created_at),
            }
        )
    return rows


def _receipt_rows(ui, center):
    """This centre's receipts, in their own numbering (FIN-007)."""
    rows = []
    for receipt in Receipt.objects.filter(organization=center).order_by(
        "number"
    ):
        payment = Payment.objects.filter(
            organization=center, receipt=receipt
        ).first()
        rows.append(
            {
                "id": str(receipt.number),
                "payment": str(payment.pk)
                if payment is not None
                else ui["not_recorded"],
                "amount": money_text(
                    ui,
                    payment.amount,
                    payment.obligation.currency,
                )
                if payment is not None
                else ui["not_recorded"],
                "date": moment_text(center, receipt.created_at),
            }
        )
    return rows


def _audit_rows(ui, center, obligation=None):
    """This centre's audited financial actions, newest kept in order (6.5).

    What happened, where and who did it, with the records it happened to.
    An agreement's page sees the rows its own agreements, plans, payments
    and expenses lead to; the centre's audit page sees them all (FIN-015:
    this centre's own rows only).
    """
    events = FinanceAuditEvent.objects.filter(organization=center)
    if obligation is not None:
        events = events.filter(
            Q(obligation=obligation)
            | Q(plan__obligation=obligation)
            | Q(payment__obligation=obligation)
        )
    rows = []
    for event in events.order_by("at"):
        references = []
        for model, name in (
            (FeeObligation, "obligation"),
            (FeePlan, "plan"),
            (Payment, "payment"),
            (Expense, "expense"),
        ):
            record = getattr(event, name)
            if record is not None:
                references.append(
                    ui["audit_ref_" + name] + " #" + str(record.pk)
                )
        rows.append(
            {
                "id": str(event.pk),
                "action": ui["audit_action_" + event.action],
                "records": ", ".join(references) or ui["not_recorded"],
                "actor": event.actor.display_name or event.actor.username,
                "at": moment_text(center, event.at),
            }
        )
    return rows


def not_found(request, center, ui, kind):
    """This centre's own not-found page, saying nothing about any other."""
    return page(
        request,
        "not_found",
        {
            "message": ui[kind + "_not_found"],
            "back": {
                "href": f"/o/{center.slug}/center/finances/{kind}s/",
                "kind": kind + "s",
                "title": ui[kind + "s_heading"],
            },
        },
        center=center,
        status=404,
    )


# --------------------------------------------------------------------- hub --


def finances_page(request, slug):
    """The centre's finance hub: FIN-010's summaries and the way down."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.summary.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request,
        "finances",
        {
            "heading": ui["finances_heading"],
            "hint": ui["finances_hint"],
            "summary": [
                {
                    "id": row["id"],
                    "title": ui["summary_" + row["id"]],
                    "value": (
                        money_text(ui, row["value"], center.primary_currency)
                        if row["id"] in FINANCIAL_MONEY_ROWS
                        else str(row["value"])
                    ),
                }
                for row in financial_rows(center)
            ],
        },
        center=center,
    )


# ------------------------------------------------------------ obligations --


def obligations_page(request, slug):
    """This centre's fee agreements, with their derived figures (FIN-005/6.2)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.plan.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    rows = []
    for obligation in FeeObligation.objects.filter(organization=center).order_by("pk"):
        figures = obligation_figures(obligation, center)
        rows.append(
            {
                "number": str(obligation.pk),
                "code": student_code(obligation),
                "status": ui["obligation_status_" + obligation.status],
                "opened": moment_text(center, obligation.opened_at),
                "required": money_text(ui, figures["required"], obligation.currency),
                "allocated": money_text(ui, figures["allocated"], obligation.currency),
                "taken_back": money_text(ui, figures["taken_back"], obligation.currency),
                "outstanding": money_text(ui, figures["outstanding"], obligation.currency),
                "overdue": str(figures["overdue"]),
                "overdue_amount": money_text(ui, figures["overdue_amount"], obligation.currency),
            }
        )
    return page(
        request,
        "finances_obligations",
        {
            "heading": ui["obligations_heading"],
            "hint": ui["obligations_hint"],
            "rows": rows,
            "none": ui["obligations_none"],
        },
        center=center,
    )


def student_shown(student):
    """A student as the finance pages name them: number plus name together.

    The attendance rows (D70) and the students pages show both, so a centre's
    own people read the same way here: two centres may share a student number,
    but never a person.
    """
    if student.student_number and student.name:
        return f"{student.student_number} ({student.name})"
    return student.student_number or student.name


def student_code(obligation):
    """Who this agreement belongs to, as the pages name them (never a raw id)."""
    return student_shown(obligation.student)


def obligation_detail(request, slug, number):
    """One agreement's whole figures and audit rows (FIN-005/6.2)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.plan.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    obligation = _record_at(FeeObligation, center, number)
    if obligation is None:
        return not_found(request, center, ui, "obligation")
    rows = [
        {
            "id": row["id"],
            "title": ui["obligation_row_" + row["id"]],
            "value": (
                money_text(ui, row["value"], obligation.currency)
                if row["id"] in OBLIGATION_MONEY_ROWS
                else ui["obligation_status_" + row["value"]]
                if row["id"] == "status"
                else moment_text(center, row["value"])
                if row["id"] == "opened_at"
                else str(row["value"])
            ),
        }
        for row in obligation_rows(obligation, center)
    ]
    installments = _installment_rows(ui, center, obligation)
    adjustments = _adjustment_rows(ui, center, obligation)
    payments = _payment_rows(ui, center, obligation)
    return page(
        request,
        "finances_obligation",
        {
            "heading": ui["obligation_heading"].format(str(obligation.pk)),
            "number": str(obligation.pk),
            "code": student_code(obligation),
            "rows": rows,
            "figures": [
                {
                    "id": row["id"],
                    "title": ui["obligation_row_" + row["id"]],
                    "value": row["value"],
                }
                for row in obligation_rows(obligation, center)
            ],
            "installments": installments,
            "installments_none": ui["obligation_installments_none"],
            "adjustments": adjustments,
            "adjustments_none": ui["obligation_adjustments_none"],
            "payments": payments,
            "payments_none": ui["obligation_payments_none"],
            "audit": _audit_rows(ui, center, obligation=obligation),
            "audit_none": ui["audit_none"],
            "back": {
                "href": f"/o/{center.slug}/center/finances/obligations/",
                "kind": "obligations",
                "title": ui["obligations_heading"],
            },
        },
        center=center,
    )


def obligations_manage(request, slug):
    """The page agreements are opened on and worked down (FIN-001/002/006).

    One page takes all four actions, so a person works down one agreement -
    opening it, giving it a plan, scheduling one installment, moving its
    status - without chasing four different addresses (the manage-page
    pattern every other area uses). The page parses dates and amounts first,
    and the service owns what the centre allows (6.2, RULE 6.3: a refused
    change marks the row the person typed, and nothing half-saved stays).
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "finances.plan.manage")
    action_at = f"/o/{center.slug}/center/finances/obligations/manage/"
    selects = {
        "obligation_action": tuple(
            (value, OBLIGATION_ACTION_UI[value]) for value in OBLIGATION_ACTIONS
        ),
        "obligation_status": OBLIGATION_STATUS_SELECT,
    }
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "finances_obligations_manage",
            {
                "fields": rows_from_spec(ui, sent, {}, OBLIGATION_FIELDS, OBLIGATION_FIELD_UI),
                "form": form_at(action_at, ui, sent, OBLIGATION_FIELDS, OBLIGATION_FIELD_UI, selects),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {name: request.POST.get(name, "") for name in OBLIGATION_FIELDS}
    problems = {}
    action = sent["obligation_action"].strip()
    note = ""
    kind = ""
    unchanged = False
    if action == "" or action not in OBLIGATION_ACTIONS:
        if action != "":
            problems["obligation_action"] = ui["field_invalid"].format(action)
        saved, field_problems, codes = None, [], []
    else:
        target = sent["obligation_target"]
        amount_value = None
        date_value = None
        bad_input = False
        if action == "add_installment":
            amount, amount_ok = parse_decimal(sent["obligation_amount"])
            when, when_ok = parse_date(sent["obligation_date"])
            if amount_ok and when_ok:
                amount_value = amount
                date_value = when
            else:
                bad_input = True
                if not amount_ok and sent["obligation_amount"]:
                    problems["obligation_amount"] = ui["field_invalid"].format(
                        sent["obligation_amount"]
                    )
                elif not amount_ok:
                    problems["obligation_amount"] = ui["field_required"]
                if not when_ok:
                    problems["obligation_date"] = ui["field_invalid"].format(
                        sent["obligation_date"]
                    )
        if bad_input:
            saved, field_problems, codes = None, [], []
        else:
            try:
                if action == "open":
                    saved, field_problems, codes = open_fee_obligation(
                        request.user,
                        center,
                        sent["obligation_target"],
                        note=sent["obligation_note"],
                    )
                elif action == "set_status":
                    saved, field_problems, codes = set_financial_obligation_status(
                        request.user,
                        center,
                        sent["obligation_target"],
                        sent["obligation_status"],
                    )
                elif action == "create_plan":
                    saved, field_problems, codes = create_fee_plan(
                        request.user,
                        center,
                        sent["obligation_target"],
                        sent["obligation_name"],
                        note=sent["obligation_note"],
                    )
                else:
                    saved, field_problems, codes = create_fee_installment(
                        request.user,
                        center,
                        sent["obligation_target"],
                        amount_value,
                        date_value,
                        note=sent["obligation_note"],
                    )
            except PermissionDenied:
                return page(request, "denied", {}, center=center, status=403)
        for code in codes:
            if code in OBLIGATION_UNCHANGED_UI:
                note, kind = ui[OBLIGATION_UNCHANGED_UI[code]], "unchanged"
                unchanged = True
                break
        if not unchanged:
            _field_problem(ui, problems, codes, OBLIGATION_PROBLEM_UI)
            _model_problems(
                ui, problems, field_problems, OBLIGATION_MODEL_FIELDS
            )
        if not unchanged:
            if saved is not None:
                note, kind = ui["finances_saved"], "saved"
            elif problems:
                note, kind = ui["finances_rejected"], "rejected"
            else:
                note, kind = ui["nothing_changed"], "unchanged"
    return page(
        request,
        "finances_obligations_manage",
        {
            "fields": rows_from_spec(ui, sent, problems, OBLIGATION_FIELDS, OBLIGATION_FIELD_UI),
            "form": form_at(action_at, ui, sent, OBLIGATION_FIELDS, OBLIGATION_FIELD_UI, selects),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


# ------------------------------------------------------------------ plans --


def plans_page(request, slug):
    """This centre's plans, one agreement's plans under their agreement."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.plan.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    rows = []
    for obligation in FeeObligation.objects.filter(organization=center).order_by(
        "pk"
    ):
        plans = _plan_rows(ui, center, obligation)
        if not plans:
            continue
        rows.append(
            {
                "agreement": str(obligation.pk),
                "code": student_code(obligation),
                "plans": plans,
            }
        )
    return page(
        request,
        "finances_plans",
        {
            "heading": ui["plans_heading"],
            "hint": ui["plans_hint"],
            "rows": rows,
            "none": ui["plans_none"],
        },
        center=center,
    )


# --------------------------------------------------------- installments --


def installments_page(request, slug):
    """This centre's installments and what is still owed on each (FIN-011)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.plan.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    rows = []
    for obligation in FeeObligation.objects.filter(organization=center).order_by(
        "pk"
    ):
        installments = _installment_rows(ui, center, obligation)
        if not installments:
            continue
        rows.append(
            {
                "agreement": str(obligation.pk),
                "code": student_code(obligation),
                "currency": obligation.currency,
                "installments": installments,
            }
        )
    return page(
        request,
        "finances_installments",
        {
            "heading": ui["installments_heading"],
            "hint": ui["installments_hint"],
            "rows": rows,
            "none": ui["installments_none"],
            "overdue_yes": ui["installment_overdue_yes"],
            "overdue_no": ui["installment_overdue_no"],
        },
        center=center,
    )


# ---------------------------------------------------------------- payments --


def payments_page(request, slug):
    """The payments the centre has posted, kept with their agreements."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.payment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    rows = []
    for obligation in FeeObligation.objects.filter(organization=center).order_by(
        "pk"
    ):
        payments = _payment_rows(ui, center, obligation)
        if not payments:
            continue
        rows.append(
            {
                "agreement": str(obligation.pk),
                "code": student_code(obligation),
                "currency": obligation.currency,
                "payments": payments,
            }
        )
    return page(
        request,
        "finances_payments",
        {
            "heading": ui["payments_heading"],
            "hint": ui["payments_hint"],
            "rows": rows,
            "none": ui["payments_none"],
        },
        center=center,
    )


def payment_detail(request, slug, number):
    """One posted payment: the money it moved and what became of it (FIN-003/008)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.payment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    payment = _record_at(Payment, center, number)
    if payment is None:
        return not_found(request, center, ui, "payment")
    obligation = payment.obligation
    figures = payment_figures(payment)
    receipt = Receipt.objects.filter(
        payment=payment, organization=center
    ).first()
    rows = [
        {
            "id": "number",
            "title": ui["payment_row_number"],
            "value": str(payment.pk),
        },
        {
            "id": "agreement",
            "title": ui["payment_row_agreement"],
            "value": "#" + str(obligation.pk),
        },
        {
            "id": "code",
            "title": ui["payment_row_code"],
            "value": student_code(obligation),
        },
        {
            "id": "amount",
            "title": ui["payment_row_amount"],
            "value": money_text(ui, figures["amount"], obligation.currency),
        },
        {
            "id": "received",
            "title": ui["payment_row_received"],
            "value": money_text(
                ui, figures["received"], obligation.currency
            ),
        },
        {
            "id": "taken_back",
            "title": ui["payment_row_taken_back"],
            "value": money_text(
                ui, figures["taken_back"], obligation.currency
            ),
        },
        {
            "id": "method",
            "title": ui["payment_row_method"],
            "value": ui["payment_method_" + payment.method],
        },
        {
            "id": "status",
            "title": ui["payment_row_status"],
            "value": ui["payment_status_" + payment.status],
        },
        {
            "id": "receipt",
            "title": ui["payment_row_receipt"],
            "value": (
                str(receipt.number)
                if receipt is not None
                else ui["receipt_none"]
            ),
        },
        {
            "id": "reference",
            "title": ui["payment_row_reference"],
            "value": payment.reference or ui["not_recorded"],
        },
        {
            "id": "note",
            "title": ui["payment_row_note"],
            "value": payment.note or ui["not_recorded"],
        },
        {
            "id": "actor",
            "title": ui["payment_row_actor"],
            "value": payment.recorded_by.display_name
            or payment.recorded_by.username,
        },
        {
            "id": "at",
            "title": ui["payment_row_at"],
            "value": moment_text(center, payment.created_at),
        },
    ]
    allocations = []
    for allocation in Allocation.objects.filter(payment=payment).order_by(
        "installment"
    ):
        allocations.append(
            {
                "installment": str(allocation.installment.pk),
                "agreement": str(allocation.installment.plan.obligation.pk),
                "amount": money_text(
                    ui, allocation.amount, obligation.currency
                ),
            }
        )
    reversals = []
    for reversal in FinancialAdjustment.objects.filter(
        organization=center, payment=payment
    ).order_by("created_at"):
        reversals.append(
            {
                "id": str(reversal.pk),
                "kind": ui["adjustment_kind_" + reversal.kind],
                "amount": money_text(
                    ui, reversal.amount, obligation.currency
                ),
                "reason": reversal.reason or ui["not_recorded"],
                "actor": reversal.actor.display_name
                or reversal.actor.username,
                "at": moment_text(center, reversal.created_at),
            }
        )
    return page(
        request,
        "finances_payment",
        {
            "heading": ui["payment_heading"].format(str(payment.pk)),
            "number": str(payment.pk),
            "code": student_code(obligation),
            "rows": rows,
            "allocations": allocations,
            "allocations_none": ui["payment_allocations_none"],
            "reversals": reversals,
            "reversals_none": ui["payment_reversals_none"],
            "audit": _audit_rows(ui, center, obligation=obligation),
            "audit_none": ui["audit_none"],
            "back": {
                "href": f"/o/{center.slug}/center/finances/payments/",
                "kind": "payments",
                "title": ui["payments_heading"],
            },
        },
        center=center,
    )


def payments_manage(request, slug):
    """The page payments are posted and reversed on (FIN-006/FIN-008)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "finances.payment.record")
    action_at = f"/o/{center.slug}/center/finances/payments/manage/"
    selects = {
        "payment_action": PAYMENT_ACTION_SELECT,
        "payment_method": PAYMENT_METHOD_SELECT,
        "payment_confirm": PAYMENT_CONFIRM_SELECT,
    }
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {"payment_token": secrets.token_hex(16)}
        return page(
            request,
            "finances_payments_manage",
            {
                "fields": rows_from_spec(
                    ui, sent, {}, PAYMENT_FIELDS, PAYMENT_FIELD_UI
                ),
                "form": form_at(
                    action_at,
                    ui,
                    sent,
                    PAYMENT_FIELDS,
                    PAYMENT_FIELD_UI,
                    selects,
                ),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {name: request.POST.get(name, "") for name in PAYMENT_FIELDS}
    problems = {}
    action = sent["payment_action"].strip()
    note = ""
    kind = ""
    unchanged = False
    if action == "" or action not in PAYMENT_ACTIONS:
        if action != "":
            problems["payment_action"] = ui["field_invalid"].format(action)
        saved, field_problems, codes = None, [], []
    else:
        amount_value = None
        allocations_value = ()
        bad_input = False
        amount, amount_ok = parse_decimal(sent["payment_amount"])
        if amount_ok:
            amount_value = amount
        else:
            problems["payment_amount"] = (
                ui["field_invalid"].format(sent["payment_amount"])
                if sent["payment_amount"]
                else ui["field_required"]
            )
            bad_input = True
        if action == "record":
            allocations, alloc_ok = parse_allocations(
                sent["payment_allocations"]
            )
            if alloc_ok:
                allocations_value = tuple(allocations)
            else:
                problems["payment_allocations"] = ui["field_invalid"].format(
                    sent["payment_allocations"]
                )
                bad_input = True
        token = sent["payment_token"].strip()
        if not token:
            problems["payment_token"] = ui["payment_token_required"]
            bad_input = True
        if sent["payment_confirm"] != "yes":
            problems["payment_confirm"] = ui["payment_confirmation_note"]
            bad_input = True
        replay = _submission_row(center, request.user, token) if token else None
        if replay is not None:
            saved, field_problems, codes = (
                replay.payment or replay.adjustment,
                [],
                ["duplicate_submission"],
            )
        elif bad_input:
            saved, field_problems, codes = None, [], []
        else:
            try:
                if action == "record":
                    saved, field_problems, codes = post_payment(
                        request.user,
                        center,
                        sent["payment_target"],
                        amount_value,
                        sent["payment_method"],
                        reference=sent["payment_reference"],
                        allocations=allocations_value,
                        note=sent["payment_note"],
                        token=token,
                    )
                else:
                    saved, field_problems, codes = reverse_payment(
                        request.user,
                        center,
                        sent["payment_payment"],
                        amount_value,
                        reason=sent["payment_reason"],
                        token=token,
                    )
            except PermissionDenied:
                return page(request, "denied", {}, center=center, status=403)
        if "duplicate_submission" in codes:
            note, kind = ui["payment_replayed"], "unchanged"
            unchanged = True
        for code in codes:
            if code in PAYMENT_RETRY_UI:
                note, kind = ui["receipt_busy"], "rejected"
                unchanged = True
                break
        if not unchanged:
            _field_problem(ui, problems, codes, PAYMENT_PROBLEM_UI)
            _model_problems(
                ui, problems, field_problems, PAYMENT_MODEL_FIELDS
            )
        if not unchanged:
            if saved is not None:
                note, kind = ui["finances_saved"], "saved"
            elif problems:
                note, kind = ui["finances_rejected"], "rejected"
            else:
                note, kind = ui["nothing_changed"], "unchanged"
    if "duplicate_submission" in codes or (
        saved is not None
        and not any(code in PAYMENT_RETRY_UI for code in codes)
    ):
        sent = dict(sent, payment_token=secrets.token_hex(16))
    return page(
        request,
        "finances_payments_manage",
        {
            "fields": rows_from_spec(
                ui, sent, problems, PAYMENT_FIELDS, PAYMENT_FIELD_UI
            ),
            "form": form_at(
                action_at,
                ui,
                sent,
                PAYMENT_FIELDS,
                PAYMENT_FIELD_UI,
                selects,
            ),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


# ---------------------------------------------------------- adjustments --


def adjustments_page(request, slug):
    """This centre's discounts, waivers, corrections and refunds (FIN-009)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.adjustment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    rows = _adjustment_rows(ui, center)
    return page(
        request,
        "finances_adjustments",
        {
            "heading": ui["adjustments_heading"],
            "hint": ui["adjustments_hint"],
            "rows": rows,
            "none": ui["adjustments_none"],
        },
        center=center,
    )


def adjustment_detail(request, slug, number):
    """One financial adjustment: what it moved and why (FIN-009)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.adjustment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    adjustment = _record_at(FinancialAdjustment, center, number)
    if adjustment is None:
        return not_found(request, center, ui, "adjustment")
    rows = [
        {
            "id": "number",
            "title": ui["adjustment_row_number"],
            "value": str(adjustment.pk),
        },
        {
            "id": "agreement",
            "title": ui["adjustment_row_agreement"],
            "value": "#" + str(adjustment.obligation.pk),
        },
        {
            "id": "code",
            "title": ui["adjustment_row_code"],
            "value": student_code(adjustment.obligation),
        },
        {
            "id": "kind",
            "title": ui["adjustment_row_kind"],
            "value": ui["adjustment_kind_" + adjustment.kind],
        },
        {
            "id": "amount",
            "title": ui["adjustment_row_amount"],
            "value": money_text(
                ui, adjustment.amount, adjustment.obligation.currency
            ),
        },
        {
            "id": "payment",
            "title": ui["adjustment_row_payment"],
            "value": (
                str(adjustment.payment_id)
                if adjustment.payment_id is not None
                else ui["not_recorded"]
            ),
        },
        {
            "id": "reason",
            "title": ui["adjustment_row_reason"],
            "value": adjustment.reason or ui["not_recorded"],
        },
        {
            "id": "actor",
            "title": ui["adjustment_row_actor"],
            "value": adjustment.actor.display_name
            or adjustment.actor.username,
        },
        {
            "id": "at",
            "title": ui["adjustment_row_at"],
            "value": moment_text(center, adjustment.created_at),
        },
    ]
    return page(
        request,
        "finances_adjustment",
        {
            "heading": ui["adjustment_heading"].format(str(adjustment.pk)),
            "number": str(adjustment.pk),
            "rows": rows,
            "audit": _audit_rows(ui, center, obligation=adjustment.obligation),
            "audit_none": ui["audit_none"],
            "back": {
                "href": f"/o/{center.slug}/center/finances/adjustments/",
                "kind": "adjustments",
                "title": ui["adjustments_heading"],
            },
        },
        center=center,
    )


def adjustments_manage(request, slug):
    """The page discounts, waivers, corrections and refunds are recorded on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "finances.adjustment.record")
    action_at = f"/o/{center.slug}/center/finances/adjustments/manage/"
    selects = {"adjustment_action": ADJUSTMENT_ACTION_SELECT}
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "finances_adjustments_manage",
            {
                "fields": rows_from_spec(
                    ui,
                    sent,
                    {},
                    ADJUSTMENT_FIELDS,
                    ADJUSTMENT_FIELD_UI,
                ),
                "form": form_at(
                    action_at,
                    ui,
                    sent,
                    ADJUSTMENT_FIELDS,
                    ADJUSTMENT_FIELD_UI,
                    selects,
                ),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {name: request.POST.get(name, "") for name in ADJUSTMENT_FIELDS}
    problems = {}
    action = sent["adjustment_action"].strip()
    note = ""
    kind = ""
    unchanged = False
    if action == "" or action not in ADJUSTMENT_ACTIONS:
        if action != "":
            problems["adjustment_action"] = ui["field_invalid"].format(action)
        saved, field_problems, codes = None, [], []
    else:
        amount_value = None
        bad_input = False
        amount, ok = parse_decimal(sent["adjustment_amount"])
        if ok:
            amount_value = amount
        else:
            problems["adjustment_amount"] = (
                ui["field_invalid"].format(sent["adjustment_amount"])
                if sent["adjustment_amount"]
                else ui["field_required"]
            )
            bad_input = True
        if bad_input:
            saved, field_problems, codes = None, [], []
        else:
            try:
                saved, field_problems, codes = record_financial_adjustment(
                    request.user,
                    center,
                    sent["adjustment_target"],
                    action,
                    amount_value,
                    reason=sent["adjustment_reason"],
                )
            except PermissionDenied:
                return page(request, "denied", {}, center=center, status=403)
        if not unchanged:
            _field_problem(ui, problems, codes, ADJUSTMENT_PROBLEM_UI)
            _model_problems(
                ui, problems, field_problems, ADJUSTMENT_MODEL_FIELDS
            )
        if not unchanged:
            if saved is not None:
                note, kind = ui["finances_saved"], "saved"
            elif problems:
                note, kind = ui["finances_rejected"], "rejected"
            else:
                note, kind = ui["nothing_changed"], "unchanged"
    return page(
        request,
        "finances_adjustments_manage",
        {
            "fields": rows_from_spec(
                ui,
                sent,
                problems,
                ADJUSTMENT_FIELDS,
                ADJUSTMENT_FIELD_UI,
            ),
            "form": form_at(
                action_at,
                ui,
                sent,
                ADJUSTMENT_FIELDS,
                ADJUSTMENT_FIELD_UI,
                selects,
            ),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


# ------------------------------------------------------------- expenses --


def expenses_page(request, slug):
    """This centre's operating expenses, kept apart from its fee money (FIN-011/012)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.expense.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    rows = _expense_rows(ui, center)
    return page(
        request,
        "finances_expenses",
        {
            "heading": ui["expenses_heading"],
            "hint": ui["expenses_hint"],
            "rows": rows,
            "none": ui["expenses_none"],
        },
        center=center,
    )


def expense_detail(request, slug, number):
    """One operating expense record (FIN-011)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.expense.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    expense = _record_at(Expense, center, number)
    if expense is None:
        return not_found(request, center, ui, "expense")
    rows = [
        {
            "id": "number",
            "title": ui["expense_row_number"],
            "value": str(expense.pk),
        },
        {
            "id": "category",
            "title": ui["expense_row_category"],
            "value": expense.category or ui["not_recorded"],
        },
        {
            "id": "amount",
            "title": ui["expense_row_amount"],
            "value": money_text(ui, expense.amount, expense.currency),
        },
        {
            "id": "date",
            "title": ui["expense_row_date"],
            "value": date_text(expense.date),
        },
        {
            "id": "payee",
            "title": ui["expense_row_payee"],
            "value": expense.payee or ui["not_recorded"],
        },
        {
            "id": "description",
            "title": ui["expense_row_description"],
            "value": expense.description or ui["not_recorded"],
        },
        {
            "id": "attachment",
            "title": ui["expense_row_attachment"],
            "value": expense.attachment_reference or ui["not_recorded"],
        },
        {
            "id": "status",
            "title": ui["expense_row_status"],
            "value": ui["expense_status_" + expense.status],
        },
        {
            "id": "actor",
            "title": ui["expense_row_actor"],
            "value": expense.recorded_by.display_name
            or expense.recorded_by.username,
        },
        {
            "id": "at",
            "title": ui["expense_row_at"],
            "value": moment_text(center, expense.created_at),
        },
    ]
    return page(
        request,
        "finances_expense",
        {
            "heading": ui["expense_heading"].format(str(expense.pk)),
            "number": str(expense.pk),
            "rows": rows,
            "back": {
                "href": f"/o/{center.slug}/center/finances/expenses/",
                "kind": "expenses",
                "title": ui["expenses_heading"],
            },
        },
        center=center,
    )


def expenses_manage(request, slug):
    """The page expenses are recorded and withdrawn on (FIN-011)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(
        member.role, "finances.expense.manage"
    )
    action_at = f"/o/{center.slug}/center/finances/expenses/manage/"
    selects = {
        "expense_action": EXPENSE_ACTION_SELECT,
    }
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "finances_expenses_manage",
            {
                "fields": rows_from_spec(
                    ui, sent, {}, EXPENSE_FIELDS, EXPENSE_FIELD_UI
                ),
                "form": form_at(
                    action_at,
                    ui,
                    sent,
                    EXPENSE_FIELDS,
                    EXPENSE_FIELD_UI,
                    selects,
                ),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {name: request.POST.get(name, "") for name in EXPENSE_FIELDS}
    problems = {}
    action = sent["expense_action"].strip()
    note = ""
    kind = ""
    unchanged = False
    if action == "" or action not in EXPENSE_ACTIONS:
        if action != "":
            problems["expense_action"] = ui["field_invalid"].format(action)
        saved, field_problems, codes = None, [], []
    else:
        amount_value = None
        date_value = None
        bad_input = False
        if action == "record":
            amount, amount_ok = parse_decimal(sent["expense_amount"])
            when, when_ok = parse_date(sent["expense_date"])
            if amount_ok and when_ok:
                amount_value = amount
                date_value = when
            else:
                bad_input = True
                if not amount_ok and sent["expense_amount"]:
                    problems["expense_amount"] = ui["field_invalid"].format(
                        sent["expense_amount"]
                    )
                elif not amount_ok:
                    problems["expense_amount"] = ui["field_required"]
                if not when_ok:
                    problems["expense_date"] = ui["field_invalid"].format(
                        sent["expense_date"]
                    )
        if bad_input:
            saved, field_problems, codes = None, [], []
        else:
            try:
                if action == "record":
                    saved, field_problems, codes = record_financial_expense(
                        request.user,
                        center,
                        sent["expense_category"],
                        amount_value,
                        date_value,
                        payee=sent["expense_payee"],
                        description=sent["expense_description"],
                        attachment_reference=sent["expense_attachment"],
                    )
                else:
                    saved, field_problems, codes = withdraw_financial_expense(
                        request.user,
                        center,
                        sent["expense_target"],
                    )
            except PermissionDenied:
                return page(request, "denied", {}, center=center, status=403)
        for code in codes:
            if code in EXPENSE_UNCHANGED_UI:
                note, kind = ui[EXPENSE_UNCHANGED_UI[code]], "unchanged"
                unchanged = True
                break
        if not unchanged:
            _field_problem(ui, problems, codes, EXPENSE_PROBLEM_UI)
            _model_problems(
                ui, problems, field_problems, EXPENSE_MODEL_FIELDS
            )
        if not unchanged:
            if saved is not None:
                note, kind = ui["finances_saved"], "saved"
            elif problems:
                note, kind = ui["finances_rejected"], "rejected"
            else:
                note, kind = ui["nothing_changed"], "unchanged"
    return page(
        request,
        "finances_expenses_manage",
        {
            "fields": rows_from_spec(
                ui, sent, problems, EXPENSE_FIELDS, EXPENSE_FIELD_UI
            ),
            "form": form_at(
                action_at,
                ui,
                sent,
                EXPENSE_FIELDS,
                EXPENSE_FIELD_UI,
                selects,
            ),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


# ------------------------------------------------------------- receipts --


def receipts_page(request, slug):
    """This centre's receipts, in their own numbering (FIN-007)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.payment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    rows = _receipt_rows(ui, center)
    return page(
        request,
        "finances_receipts",
        {
            "heading": ui["receipts_heading"],
            "hint": ui["receipts_hint"],
            "rows": rows,
            "none": ui["receipts_none"],
        },
        center=center,
    )


def receipt_detail(request, slug, number):
    """One receipt as the centre issued it (FIN-007)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.payment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    receipt = receipt_at(center, number)
    if receipt is None:
        return not_found(request, center, ui, "receipt")
    student = student_shown(receipt.student)
    rows = [
        {
            "id": "center",
            "title": ui["receipt_row_center"],
            "value": center.name,
        },
        {
            "id": "number",
            "title": ui["receipt_row_number"],
            "value": str(receipt.number),
        },
        {
            "id": "payment",
            "title": ui["receipt_row_payment"],
            "value": "#" + str(receipt.payment.pk),
        },
        {
            "id": "enrollment",
            "title": ui["receipt_row_enrollment"],
            "value": "#" + str(receipt.payment.obligation.enrollment.pk),
        },
        {
            "id": "student",
            "title": ui["receipt_row_student"],
            "value": student,
        },
        {
            "id": "amount",
            "title": ui["receipt_row_amount"],
            "value": money_text(ui, receipt.amount, receipt.currency),
        },
        {
            "id": "method",
            "title": ui["receipt_row_method"],
            "value": ui["payment_method_" + receipt.method],
        },
        {
            "id": "date",
            "title": ui["receipt_row_date"],
            "value": moment_text(center, receipt.date),
        },
    ]
    return page(
        request,
        "finances_receipt",
        {
            "heading": ui["receipt_heading"].format(str(receipt.number)),
            "number": str(receipt.number),
            "rows": rows,
            "csv": {
                "href": (
                    f"/o/{center.slug}/center/finances/receipts/"
                    f"{receipt.number}.csv"
                ),
                "title": ui["receipt_csv_title"],
            },
            "back": {
                "href": f"/o/{center.slug}/center/finances/receipts/",
                "kind": "receipts",
                "title": ui["receipts_heading"],
            },
        },
        center=center,
    )


def receipt_csv(request, slug, number):
    """The one receipt as a CSV file: the same rows, the same rules (FIN-007)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.payment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    receipt = receipt_at(center, number)
    if receipt is None:
        return not_found(request, center, ui, "receipt")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="receipt-{}-{}.csv"'.format(
            center.slug, receipt.number
        )
    )
    writer = csv.writer(response)
    writer.writerow(
        [
            ui["receipt_row_center"],
            ui["receipt_row_number"],
            ui["receipt_row_payment"],
            ui["receipt_row_enrollment"],
            ui["receipt_row_student"],
            ui["receipt_row_amount"],
            ui["receipt_row_method"],
            ui["receipt_row_date"],
        ]
    )
    writer.writerow(
        [
            center.name,
            str(receipt.number),
            "#" + str(receipt.payment.pk),
            "#" + str(receipt.payment.obligation.enrollment.pk),
            student_shown(receipt.student),
            money_text(ui, receipt.amount, receipt.currency),
            ui["payment_method_" + receipt.method],
            moment_text(center, receipt.date),
        ]
    )
    return response


# -------------------------------------------------------------- history --


def history_page(request, slug):
    """Payment history and financial adjustments, one after the other (FIN-010)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.history.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    items = []
    for payment in Payment.objects.filter(organization=center):
        figures = payment_figures(payment)
        items.append(
            {
                "at": payment.created_at,
                "kind": ui["history_kind_payment"],
                "agreement": "#" + str(payment.obligation.pk),
                "code": student_code(payment.obligation),
                "amount": money_text(
                    ui, figures["amount"], payment.obligation.currency
                ),
                "reference": (
                    payment.reference or ui["not_recorded"]
                ),
                "what": ui["payment_method_" + payment.method],
                "actor": payment.recorded_by.display_name
                or payment.recorded_by.username,
            }
        )
    for adjustment in FinancialAdjustment.objects.filter(
        organization=center
    ):
        items.append(
            {
                "at": adjustment.created_at,
                "kind": ui["history_kind_adjustment"],
                "agreement": "#" + str(adjustment.obligation.pk),
                "code": student_code(adjustment.obligation),
                "amount": money_text(
                    ui,
                    adjustment.amount,
                    adjustment.obligation.currency,
                ),
                "reference": adjustment.reason or ui["not_recorded"],
                "what": ui["adjustment_kind_" + adjustment.kind],
                "actor": adjustment.actor.display_name
                or adjustment.actor.username,
            }
        )
    items.sort(key=lambda item: item["at"])
    rows = [
        {
            "id": str(index + 1),
            "moment": moment_text(center, item["at"]),
            "kind": item["kind"],
            "agreement": item["agreement"],
            "code": item["code"],
            "amount": item["amount"],
            "reference": item["reference"],
            "what": item["what"],
            "actor": item["actor"],
        }
        for index, item in enumerate(items)
    ]
    return page(
        request,
        "finances_history",
        {
            "heading": ui["history_heading"],
            "hint": ui["history_hint"],
            "rows": rows,
            "none": ui["history_none"],
        },
        center=center,
    )


# ---------------------------------------------------------------- audit --


def audit_page(request, slug):
    """This centre's audited financial actions, in the order they happened."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "finances.audit.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    rows = _audit_rows(ui, center)
    return page(
        request,
        "finances_audit",
        {
            "heading": ui["audit_heading"],
            "hint": ui["audit_hint"],
            "rows": rows,
            "none": ui["audit_none"],
        },
        center=center,
    )
