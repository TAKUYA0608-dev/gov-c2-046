"""GOV-C2-046 — Agent state (Municipal Evacuation-Shelter Accessibility Triage & Resource-Allocation
Planning, Cat 2).

ADR-005: State is a flat TypedDict — never a validation/BaseModel instance. Complex fields are stored
as JSON strings (``NotRequired[str]`` + ``# JSON:``); nodes ``json.dumps`` on write / ``json.loads`` on
read. This keeps the state msgpack-serialisable across the LangGraph checkpoint boundary.

Security posture (advisory / decision-support only): the agent NEVER executes a real resource
allocation, dispatches supplies/staff, or issues an evacuation order. Vulnerable-resident intake is
access-scoped (S-2) on ingest and PII-redacted (S-3) on output; the audit trail carries aggregate
counts only (no PII). Material decisions / low-confidence / resource-contention paths are routed to an
authorised incident-command HumanGate (fail-closed) — the agent emits no allocation recommendation.

All agent-specific fields are NotRequired (populated progressively; absent at empty-start invoke).
"""

from __future__ import annotations


from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for the evacuation-shelter accessibility triage + allocation-plan workflow."""

    # ── pre_process (IntakeValidate, S-1 sanitised + S-2 access-scoped request) ──────────────
    validated_input: str  # JSON: {intake, authorized_provenances, incident_id, taxonomy_hint}
    input_format: str  # "json" | "text" | "empty"
    enriched_context: str  # JSON: {source, channel} (read-only caller context)

    # ── inner workflow: ingest → needs_classify → option_evaluate → plan_synthesize ──────────
    shelter_records: str  # JSON: [{shelter_id, name, capacity, occupancy, facility_features, occupants[], provenance}]
    ingest_count: int  # authorised shelter records ingested (0 → out-of-scope safe answer)
    access_rejected_count: int  # records dropped by S-2 provenance/access scoping
    classified_needs: str  # JSON: [{shelter_id, needs:[{category, count, severity, evidence}]}]
    option_evaluations: str  # JSON: [{shelter_id, options:[{option_type, target_category, score, feasible}]}]
    allocation_plan: str  # JSON: {priorities:[...], escalations:[...]}
    human_review: str  # JSON: {decision, fail_closed, exceptions[], escalations[], required_human_approver}
    result: str  # JSON: assembled allocation plan draft (deliverable)

    # ── post_process (PlanCompose, S-3 gate + S-4 audit) ─────────────────────────────────────
    formatted_output: str  # JSON: final response envelope (plan draft + DRAFT disclaimer)
    disclaimer: str  # mandatory DRAFT / decision-support-only disclaimer
    audit_logged: bool  # True once the terminal audit event is emitted

    # ── degraded-path signalling (SUCCESS + error_code, never status=ERROR) ──────────────────
    error_code: str  # INJECTION_REJECTED | INPUT_REJECTED | INPUT_TOO_LONG | NO_DATA | NO_ALLOCATION
    error_message: str  # operator-facing detail
