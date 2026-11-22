"""The vocabularies a fee obligation, a payment, an adjustment or an expense may have.

One module is the single source for every fixed set this slice's models
(their choices) and services (their transition and per-kind rules) read,
exactly like the other app's statuses.py modules: the transitions themselves
are business rules and live in services.py, which is the only place a status
is ever changed.

- FIN-005/6.2: an obligation is opened, paid once every installment of every
  plan under it is fully allocated, and then closed. Closing is agreement
  state, never the money: the derived figures never read it (a closed
  obligation with an outstanding amount still shows it).
- FIN-006: the payment methods the SRS names ("cash, bank transfer and other
  manually recorded methods") plus the receipt the payment writes. This is
  transaction recording, never payment processing (FIN-006's own note).
- FIN-008/FIN-009 and the §7 FinancialAdjustment row name the five kinds a
  financial adjustment may be: discount, waiver, correction, refund, reversal.
  The three whose amount the obligation requires, and the two that take back
  money already received, are split here so services.py reads one source for
  the two accounting roles (§6.2).
- FIN-011: an expense is a normal operational record; withdrawn expenses stop
  counting in the expense totals, and nothing is ever silently deleted
  (RULE 6.5: financial history is traceable).
- RULE 6.5 and §13's audit-trail row name the audited financial actions. The
  action name is fixed here so the FinanceAuditEvent choices and the services
  agree, as the attendance corrections do in the academic app.
"""

# FIN-005 (opened, paid once every installment of every plan under it is fully
# allocated, then closed); §6.2's lifecycle.
OBLIGATION_OPEN = "open"
OBLIGATION_PAID = "paid"
OBLIGATION_CLOSED = "closed"

OBLIGATION_STATUSES = (
    OBLIGATION_OPEN,
    OBLIGATION_PAID,
    OBLIGATION_CLOSED,
)

OBLIGATION_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in OBLIGATION_STATUSES
]

# FIN-005: an obligation is paid when every installment of every plan under it
# is fully allocated, and it is closed by explicit choice. paid -> closed is
# allowed; a paid obligation reopened is not modelled (open in
# docs/OPEN_QUESTIONS.md).
OBLIGATION_TRANSITIONS = {
    OBLIGATION_OPEN: (OBLIGATION_PAID, OBLIGATION_CLOSED),
    OBLIGATION_PAID: (OBLIGATION_CLOSED,),
    OBLIGATION_CLOSED: (),
}

# What an opened obligation's agreement snapshot keeps, in the fixed form the
# FIN-001 anchor names: its enrollment's agreed fee and its centre's
# center-primary currency at opening (convention D81, open item 50).
OBLIGATION_OPENING_FIELDS = ("fee_snapshot", "currency")

# FIN-006: cash, bank transfer and other manually recorded methods. A payment
# is a transaction record, never payment processing (FIN-006's own note).
METHOD_CASH = "cash"
METHOD_BANK = "bank_transfer"
METHOD_OTHER = "other"

PAYMENT_METHODS = (
    METHOD_CASH,
    METHOD_BANK,
    METHOD_OTHER,
)

PAYMENT_METHOD_CHOICES = [
    (value, value.replace("_", " ").title()) for value in PAYMENT_METHODS
]

# The payment's own state: posted payments are recorded, and a payment whose
# whole amount has been reversed is marked reversed (FIN-008: the posted
# payment stays in the history, marked, never deleted). The reversal rule
# (6.2) is the only thing that moves a payment between these two states.
PAYMENT_RECORDED = "recorded"
PAYMENT_REVERSED = "reversed"

PAYMENT_STATUSES = (
    PAYMENT_RECORDED,
    PAYMENT_REVERSED,
)

PAYMENT_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in PAYMENT_STATUSES
]

# §7's FinancialAdjustment row and FIN-008/FIN-009 name the five kinds one
# financial transaction may be.
ADJ_DISCOUNT = "discount"
ADJ_WAIVER = "waiver"
ADJ_CORRECTION = "correction"
ADJ_REFUND = "refund"
ADJ_REVERSAL = "reversal"

ADJUSTMENT_KINDS = (
    ADJ_DISCOUNT,
    ADJ_WAIVER,
    ADJ_CORRECTION,
    ADJ_REFUND,
    ADJ_REVERSAL,
)

ADJUSTMENT_KIND_CHOICES = [
    (value, value.replace("_", " ").title()) for value in ADJUSTMENT_KINDS
]

# §6.2's two accounting roles: the kinds whose amount the obligation requires,
# and the kinds that take back money already received.
REQUIRED_ADJUSTMENT_KINDS = (
    ADJ_DISCOUNT,
    ADJ_WAIVER,
    ADJ_CORRECTION,
)

RECEIVED_ADJUSTMENT_KINDS = (
    ADJ_REFUND,
    ADJ_REVERSAL,
)

# The kinds whose sign is fixed: discounts and waivers lower what the
# obligation requires, so their amount is positive; a correction is a signed
# repair of the agreement and may go either way (FIN-009 and §6.2).
ADJUSTMENT_AMOUNT_SIGNED = {
    ADJ_DISCOUNT: "positive",
    ADJ_WAIVER: "positive",
    ADJ_CORRECTION: "any",
}

# FIN-008's reversals reference a posted payment; the refund's payment
# reference is not modelled and the transaction refuses it there (open item
# 51).
ADJUSTMENT_PAYMENT_REFERENCE = (
    ADJ_REVERSAL,
)

# FIN-011's expense records; withdrawn expenses stop counting in the expense
# totals, and nothing is ever silently deleted.
EXPENSE_RECORDED = "recorded"
EXPENSE_WITHDRAWN = "withdrawn"

EXPENSE_STATUSES = (
    EXPENSE_RECORDED,
    EXPENSE_WITHDRAWN,
)

EXPENSE_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in EXPENSE_STATUSES
]

# FIN-011's expense records; withdrawn expenses stop counting in the expense
# totals, and nothing is ever silently deleted (RULE 6.5: financial history
# stays traceable). Withdrawing is the only move, and a withdrawn record
# does not come back: recording a new expense is the way to correct one.
EXPENSE_TRANSITIONS = {
    EXPENSE_RECORDED: (EXPENSE_WITHDRAWN,),
    EXPENSE_WITHDRAWN: (),
}

# RULE 6.5 and §13's audit-trail row: the audited financial actions. What
# happened, where and who did it (FIN-009 and §13 name discount/waiver
# records, corrections, refunds, reversals, expense records, plus the
# conventions that open an obligation, save a plan, reverse a payment or
# close an obligation).
AUDIT_OBLIGATION_OPENED = "obligation_opened"
AUDIT_PLAN_SAVED = "plan_saved"
AUDIT_PAYMENT_POSTED = "payment_posted"
AUDIT_PAYMENT_REVERSED = "payment_reversed"
AUDIT_ADJUSTMENT_RECORDED = "adjustment_recorded"
AUDIT_EXPENSE_RECORDED = "expense_recorded"
AUDIT_EXPENSE_WITHDRAWN = "expense_withdrawn"
AUDIT_OBLIGATION_CLOSED = "obligation_closed"

FINANCE_AUDIT_ACTIONS = (
    AUDIT_OBLIGATION_OPENED,
    AUDIT_PLAN_SAVED,
    AUDIT_PAYMENT_POSTED,
    AUDIT_PAYMENT_REVERSED,
    AUDIT_ADJUSTMENT_RECORDED,
    AUDIT_EXPENSE_RECORDED,
    AUDIT_EXPENSE_WITHDRAWN,
    AUDIT_OBLIGATION_CLOSED,
)

FINANCE_AUDIT_ACTION_CHOICES = [
    (value, value.replace("_", " ").title()) for value in FINANCE_AUDIT_ACTIONS
]

# The one receipt-numbering counter's name (convention D81: a per-centre
# counter record keeps the numbering safe under concurrency, instead of an
# unprotected MAX() plus one).
RECEIPT_COUNTER_NAME = "receipt-counter"
