"""The vocabularies an assessment, a result and an audit event may have.

Like every other statuses module, one module owns the words so the models
(field choices) and the services (action names) read the same source:

- GRD-001..GRD-007's audit vocabulary: the three actions a grade change goes
  through - an assessment defined, a result recorded, and a recorded score
  changed (RULE 6.5: grade changes are audited, and a new audited action only
  adds to this table).

The assessment type is a bounded, required text field: the SRS names no
vocabulary for it, so none is invented here (docs/OPEN_QUESTIONS.md).
Scales, bands, pass marks and final grades are deliberately absent from this
phase (docs/STATUS.md's deferred list).
"""

GRADE_CREATED = "assessment_created"
GRADE_RECORDED = "result_recorded"
GRADE_UPDATED = "result_updated"

GRADE_AUDIT_ACTIONS = (GRADE_CREATED, GRADE_RECORDED, GRADE_UPDATED)

GRADE_AUDIT_ACTION_CHOICES = [(value, value) for value in GRADE_AUDIT_ACTIONS]
