# GOV-C2-046 — Design Specification

Municipal Evacuation-Shelter Accessibility Triage & Resource-Allocation Planning Agent (Cat 2, GOV).

> **Advisory / decision-support only.** The agent never executes a real resource allocation, dispatches
> supplies/staff, issues an evacuation order, or performs medical triage. It produces a **traceable
> Allocation Plan Draft** for review; the final allocation / evacuation decision and accountability
> remain with the municipality / disaster-response headquarters (災害対策本部) via a mandatory HumanGate.

## Position in AgentCore Architecture

- **Agent Class**: `MunicipalEvacuationShelterAccessibilityNeedsTriageAgent` (module-level alias of `Graph`)
- **L1 Base**: `AgentBaseGraph` (L1-direct). `DocGenerationAgent` is a **pattern reference only** (§5/§10 of
  the proposal) — Level 2 is retired (2026-05-18 platform decision); the Evacuation-Shelter Accessibility
  Triage pattern is encapsulated inside this template.
- **Category**: Cat 2 — multi-step domain workflow (job-to-be-done). GraphNode-in-`main` wrapping an
  inner `BaseGraph` domain workflow.
- **Three-Layer Separation**:
  - State: flat TypedDict composition (`src/schemas/state.py`); complex fields JSON-string encoded (ADR-005).
  - Node: L1 inheritance (Template Method — `execute(self, state: dict) -> dict` override only; no `config` param).
  - Graph: composition (`register_nodes()`); domain complexity in `main` via `EvacuationTriageWorkflowGraphNode.get_subgraph()`.

## Architecture Overview

### Outer graph (fixed 5-slot backbone)

```
START → initialize → pre_process → main(GraphNode) → post_process → finalize → END
                                       ↓ (RETRY, max 3)
                                    pre_process
```

`initialize` / `finalize` are auto-injected by `super().register_nodes()` (never re-registered). The
template owns `pre_process`, `main` (GraphNode), and `post_process`.

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version, session_id, trust_level | user_input | (framework) | InitializeNode (default) |
| pre_process | **IntakeValidate (S-1 + S-2)**: NFKC/control sanitise, My-Number/PII redaction, parse the intake request; prompt-injection / empty / oversize → degraded `SUCCESS + error_code` (body discarded, never processed) | user_input, input_context | validated_input, input_format, enriched_context, (error_code) | `PreProcessNode(FunctionNode)` |
| main | **EvacuationTriageWorkflowGraphNode** — wraps the inner domain workflow | validated_input | result, ingest_count, error_code, status | `GraphNode` (subgraph composition) |
| post_process | **PlanCompose (S-3 + S-4)**: assemble Allocation Plan Draft envelope, PII redaction, injection neutralisation, citation completeness, DRAFT disclaimer, audit | result | formatted_output, disclaimer, audit_logged | `PostProcessNode(FunctionNode)` |
| finalize | response_metadata, total_time_ms | (all) | response envelope | FinalizeNode (default) |

### Inner domain workflow (`src/graph/domain_workflow_graph.py`)

`EvacuationTriageWorkflow(BaseGraph)` — **linear topology with per-node skip guards**. Conditional
edges are *not* used: `add_conditional_edges` does not propagate across the GraphNode subgraph boundary,
so branching is handled by a guard at the head of each inner node.

```
START → intake_ingest → needs_classify → option_evaluate → plan_synthesize → END
```

| Step | Node | Logic | Skip guard |
|------|------|-------|------------|
| 1 | `intake_ingest` (IntakeIngestNode) | Normalise validated intake into structured shelter records; **S-2 access scoping** — drop records whose provenance is not in the caller's authorised allowlist (`access_rejected_count`). Sets `ingest_count`; **0 authorised records → `error_code=NO_DATA`** | — (entry) |
| 2 | `needs_classify` (AccessibilityNeedsClassifyNode) | **Judgment (deterministic taxonomy mapping, no LLM)**: classify occupant support/medical notes into the 要配慮者 accessibility taxonomy (wheelchair / medical-care / sensory / cognitive / language / infant / pregnant / elderly-care) with per-category count + severity | `error_code` or `ingest_count==0` → `{}` |
| 3 | `option_evaluate` (ResourceOptionEvaluateNode) | **Judgment**: score supply / staffing / transfer options against classified needs × facility constraints (capacity, occupancy, barrier-free features) | same |
| 4 | `plan_synthesize` (AllocationPlanSynthesizeNode) | Synthesise **prioritised allocation + escalation plan** (deterministic rule/threshold ranking, no LLM); run the **fail-closed HumanGate review** (low-confidence / data-gap / resource-contention → escalation-only, no allocation emitted); assemble the `result` deliverable, or the out-of-scope safe answer on the degraded branch | `error_code` or `ingest_count==0` → out-of-scope safe answer (`citations=[]`) |

`EvacuationTriageWorkflowGraphNode.get_subgraph()` caches the inner workflow instance in `self._subgraph`
(built/compiled once; `BaseGraph.invoke()`'s `_ensure_compiled` is idempotent). `merge_output` surfaces
`result / ingest_count / error_code / status` so the outer route can branch to `post_process` on status.

### Data Flow

```
START → initialize → pre_process(IntakeValidate) → main(EvacuationTriageWorkflow) → post_process(PlanCompose) → finalize → END
                                            ↓ (retry)
                                        pre_process
```

### State Definition

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| validated_input | str (JSON) | sanitised + access-scoped intake request | pre_process |
| input_format | str | "json" / "text" / "empty" | pre_process |
| enriched_context | str (JSON) | read-only caller context {source, channel} | pre_process |
| shelter_records | str (JSON) | ingested structured shelter records | ingest |
| ingest_count | int | authorised records (0 → safe answer) | ingest |
| access_rejected_count | int | records dropped by S-2 access scoping | ingest |
| classified_needs | str (JSON) | accessibility-taxonomy needs per shelter | classify |
| option_evaluations | str (JSON) | scored supply/staffing/transfer options | evaluate |
| allocation_plan | str (JSON) | prioritised allocation + escalations | synthesize |
| human_review | str (JSON) | HumanGate decision / exceptions / required approver | synthesize |
| result | str (JSON) | assembled Allocation Plan Draft (deliverable) | synthesize |
| formatted_output | str (JSON) | final response envelope | post_process |
| disclaimer | str | mandatory DRAFT / decision-support disclaimer | post_process |
| audit_logged | bool | terminal audit event emitted | post_process |
| error_code | str | INJECTION_REJECTED / INPUT_REJECTED / INPUT_TOO_LONG / NO_DATA / NO_ALLOCATION | degraded |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serialisable types); complex fields stored as JSON strings (ADR-005).
- No JWT / API keys / credentials in State (checkpoint DB leakage).
- InvocationContext via `config["configurable"]` only (not in State).
- No Pydantic models / dataclass / arbitrary Python objects (msgpack incompatible).
- **No status=ERROR on degraded paths** — rejects / 0-data return `status=SUCCESS + error_code` so the real
  SDK still runs `post_process` (S-3/S-4). ERROR would skip the output gate and audit.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, trust_level)
- [x] S-1 input sanitisation — NFKC + control-char strip + My-Number/PII redaction in `pre_process.execute()`
- [x] S-2: `_extra_security_gate_input()` — **no-op domain hook** (returns state unchanged; **never raises**, **never sets ERROR**). Prompt-injection / empty / oversize are handled in `pre_process.execute()` as a degraded `status=SUCCESS + error_code` path (the untrusted body is discarded → `validated_input="{}"`, never processed) so the inner workflow reaches the out-of-scope safe answer and `post_process` (S-3/S-4) always runs; `status=ERROR` would short-circuit `__call__` and skip main / post_process. Record-level provenance access scoping is applied in `intake_ingest`.
- [x] S-3: `_extra_security_gate_output()` — verify the DRAFT/advisory disclaimer is present in the output envelope (**may raise** to block a non-compliant output). Injection-marker neutralisation + residual-PII redaction of free-text fields is applied in `post_process.execute()`.
- [x] S-4: `emit_trace_event()` — at least one domain event inside every `execute()` (aggregate counts only — no PII). Via the `src.utils.audit` shim (platform logger + stderr fallback).
- [x] S-5: no credentials in `src/`; no persistent PII; vulnerable-resident intake minimised + redacted.

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass (pre_process, post_process, all four inner nodes) → framework `@final` gate runs
>   automatically; extended via `_extra_security_gate_input()` / `_extra_security_gate_output()` only.
> - `GraphNode` (`EvacuationTriageWorkflowGraphNode`) → deliberate no-op (inner FunctionNode gates already applied).

### Composition Pattern

- **Pattern**: GraphNode (subgraph) in the `main` slot wrapping an inner `BaseGraph` (Cat 2 canonical).
- **Composition target**: `EvacuationTriageWorkflow` (inner 4-node linear workflow).
- **Error propagation strategy**: `error_strategy = "propagate"`; degraded branches carry `error_code` and
  resolve to the out-of-scope safe answer inside the inner workflow (no exceptions across the boundary).

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0) — PB-4.
- [x] Import targets: `framework/` and `shared/` only; agent-local imports use the `src.` prefix.

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Deterministic multi-step workflow, not an autonomous think→act loop. |
| Composition pattern | FunctionNode-in-main (Cat 1) | GraphNode-in-main (Cat 2) | **GraphNode-in-main** | Domain complexity (ingest → classify → evaluate → synthesize) is an encapsulated workflow. |
| Inner branching | conditional edges | linear + per-node skip guard | **linear + skip guard** | Conditional edges do not propagate across the subgraph boundary. |
| Degraded signalling | status=ERROR | status=SUCCESS + error_code | **SUCCESS + error_code** | Preserve S-3/S-4 (post_process) on rejects; ERROR short-circuits the output gate. |
| HumanGate | optional | mandatory + fail-closed | **mandatory, fail-closed** | Life-safety-adjacent; low-confidence / data-gap / resource-contention ⇒ escalation-only. |
| Injection defence placement | ingress reject only | ingress degraded + egress neutralise | **ingress degraded (execute) + egress S-3** | Injection markers never appear in legitimate structured intake → reject at ingress as degraded `SUCCESS + INJECTION_REJECTED` (untrusted body never processed; safe answer + disclaimer + audit still delivered, no ERROR short-circuit); S-3 egress neutralisation kept as defense-in-depth. |

## Open Items (Stage ③ implementation plan)

The composite `main` GraphNode and the four inner nodes are **implemented in the Stage ③ impl MR**
(this design MR ships `docs/02_design.md` + `src/schemas/state.py` only). Stage ③ scope:
- `src/nodes/{pre_process,intake_ingest,needs_classify,option_evaluate,plan_synthesize,post_process}_node.py`
- `src/graph/graph.py` (outer + `EvacuationTriageWorkflowGraphNode` + registry alias) and
  `src/graph/domain_workflow_graph.py` (inner `EvacuationTriageWorkflow`).
- `src/services/service.py` (deterministic `ShelterKB` + `AllocationEngine`, incl. fail-closed HumanGate review),
  `src/utils/audit.py` (S-4 shim).
- `tests/unit/*`, `tests/integration/test_end_to_end.py`, `docs/03_test_spec.md`, `docs/07_operation_guide.md`.
