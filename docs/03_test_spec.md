# GOV-C2-046 — Test Specification

## Test Strategy
- Coverage target: **≥ 89%** (achieved: 94% over `src/`, unit + integration).
- Test types: Unit (`tests/unit/`) / Integration (`tests/integration/`) / Proof-of-Boundary (`tests/proof_of_boundary/`).
- Determinism: classification, option scoring, allocation ranking, and the HumanGate review are deterministic
  (seeded taxonomy + rule/threshold, no LLM), so tests assert exact outcomes.

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict, complex fields JSON-string (ADR-005) | Type check pass, no Pydantic/dataclass | ✅ |
| TC-02 | Degraded path uses SUCCESS + error_code (never status=ERROR) | Injection/empty/oversize/0-data return SUCCESS + error_code; post_process still runs | ✅ `test_empty_degrades*`, `test_injection_degrades`, `test_oversize_degrades`, `test_out_of_scope_safe*`, `TestGraphInvoke::*` |
| TC-03 | No JWT/Credential in State/src | CI `gate-credential-scan`: 0 violations | ✅ (no literals; secrets/PII redacted on every supplied field at S-1) |
| TC-04 | InvocationContext via configurable only | Not stored in State | ✅ |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start/complete/error` absent from bodies | ✅ (domain events only) |
| TC-06 | S-2: `_security_gate_input()` not overridden | only `_extra_security_gate_input()` used | ✅ |
| TC-07 | S-3: `_security_gate_output()` not overridden | only `_extra_security_gate_output()` used | ✅ |
| TC-08 | `required_trust_level` declared on every FunctionNode | `scripts/check_trust_level.py` PASS | ✅ VERIFIED_EXTERNAL on 6 nodes |
| TC-09 | S-2 `_extra_security_gate_input()` raise-free no-op (no ERROR); injection/oversize degraded in `execute()` | hook returns state unchanged; `execute()` → SUCCESS + error_code | ✅ `test_s2_hook_is_noop`, `test_injection_degrades`, `test_oversize_degrades` |
| TC-10 | S-3 `_extra_security_gate_output()` non-trivial | disclaimer preservation (may raise) | ✅ `test_gate_raises_when_disclaimer_missing` |
| TC-11 | S-4: ≥1 domain `emit_trace_event()` per `execute()` | event on every path | ✅ (all 6 nodes) |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Expected Result | Result |
|-------|----------|----------------|--------|
| PB-1 | BaseNode → EventEmitter (`emit_trace_event` fires) | No silent failures | ✅ |
| PB-2 | State serialization (primitives only) | No Pydantic/dataclass | ✅ `test_state_safety` |
| PB-3 | main → inner workflow (GraphNode subgraph) | Inner workflow invoked, output surfaced | ✅ `test_inner_chain_produces_evaluations` |
| PB-4 | Import isolation (no Level 0 imports) | AST scan: 0 violations | ✅ `test_import_isolation` |
| PB-5 | Checkpoint safety (no JWT/Pydantic) | Inspection pass | ✅ |
| PB-6 | Invoke execution order | S-1 → node_start → S-2 → execute → S-3 → node_complete | ✅ (real-SDK CI) |
| PB-7 | HITL interrupt propagation *(conditional)* | **Auto-waived — non-HITL** (`hitl.enabled` unset) | ✅ 2 SKIPPED |

> PB-1..PB-6 mandatory. PB-7 auto-waived (non-HITL) — the conditional stub is retained but does not block the gate.
> Note: the HumanGate in this template is a **deterministic advisory gate** (produces review flags for a human
> approver); it is not a LangGraph `GraphInterrupt`/HITL, so `hitl.enabled` is not set.

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Structured intake → allocation plan draft | barrier-free shelter, classifiable needs | `status_kind=allocation_plan`, priorities ranked, citations present, `fail_closed=False` | ✅ |
| BL-02 | Accessibility needs classification | occupant notes (wheelchair/infant/elderly) | multi-category taxonomy hits, unclassified counted | ✅ |
| BL-03 | Resource option evaluation | needs × facility features | in-place vs escalation (critical for medical transfer) | ✅ |
| BL-04 | Fail-closed HumanGate — resource contention | medical need, no medical facility | `escalation_only`, allocation **withheld** | ✅ |
| BL-05 | Fail-closed HumanGate — data gap | missing capacity | `escalation_only` | ✅ |
| BL-06 | Fail-closed HumanGate — low confidence | >50% unclassifiable notes | `escalation_only` | ✅ |
| BL-07 | S-2 access scoping | unauthorised provenance | record dropped, `access_rejected_count` incremented | ✅ |
| BL-08 | 0-data / non-structured input | text / empty | out-of-scope safe answer, `citations=[]` | ✅ |
| BL-09 | S-1/S-3 PII + injection defence | My-Number + injection markers | ingress: injection → degraded `SUCCESS+INJECTION_REJECTED` (body discarded); egress: My-Number/secrets redacted / residual markers neutralised | ✅ |
| BL-10 | Citation completeness | grounded plan/escalation | `citation_complete=True` | ✅ |
| BL-11 | **Real `Graph().invoke()` degraded acceptance** | injection / oversize input | `status=SUCCESS.value`, `PostProcessNode` in `node_history`, envelope `status_kind=out_of_scope`, DRAFT/HumanGate disclaimer, no rejected body/canary in output | ✅ `TestGraphInvoke::test_injection_reaches_post_and_audits`, `::test_oversize_reaches_post_and_audits` |
| BL-12 | **S-1 field-level redaction** — every supplied field scrubbed before State | occupant `{name/phone/email/mynumber}` + credential/email in `authorized_provenances` | resident identifiers / secrets (credential / email / phone / My-Number) absent from `validated_input` **and** final report; occupant minimised to need-fields; need signal + legit provenance retained | ✅ `TestPreProcess::test_field_level_pii_redacted_before_state`, `::test_authorized_provenances_are_sanitised`, `TestRedactPii::*`, `TestEndToEnd::test_no_resident_pii_in_final_report` |

## Test Execution Summary
- Execution: local local stub env (CI runs the real SDK, wheel-era).
- Unit + integration: **63 passed / 1 skipped** (server import skip under a local SDK stub) · Coverage **94%**.
- Full `tests/` incl. proof_of_boundary: `test_pb_invoke_order` + PB-7 pass/behave under the real-SDK CI arm
  (documented the local SDK stub local diff — the local SDK stub `base_node` lacks `emit_trace_event`).
- Gates: `check_trust_level.py` PASS · ruff clean (src + tests) · no credential literals.
