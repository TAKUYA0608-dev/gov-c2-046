"""GOV-C2-046 — pre_process node: IntakeValidate (S-1 input sanitisation + S-2 domain hook).

S-1 (execute): NFKC-normalise, strip control chars, and **redact secrets / vulnerable-resident PII on
every supplied field before it is persisted to State** — credentials (sk-*/AKIA*/JWT), email, phone, and
My-Number are masked, and occupant records are minimised to the accessibility-need note fields (all
direct identifiers — name / phone / email / address / guardian ... — are dropped). Then parse the
authorised evacuation-shelter intake request into ``{intake, authorized_provenances, incident_id,
taxonomy_hint}`` (``authorized_provenances`` is sanitised element-by-element, not copied verbatim).

All rejects — prompt-injection markers, empty, and oversize — are handled in ``execute()`` as a degraded
``status=SUCCESS + error_code`` path: the untrusted / rejected body is discarded (``validated_input="{}"``)
and never processed, so the inner workflow resolves to the out-of-scope safe answer and post_process
(S-3 disclaimer / redaction + S-4 audit) always runs. A ``status=ERROR`` here would short-circuit
``__call__`` and route straight to ``finalize``, skipping main / post_process.

S-2 (`_extra_security_gate_input`): the domain hook is a **no-op** (SDK 1.0.0: MUST NOT raise, MUST NOT
set ERROR). The framework default S-2 PII masking still applies. Record-level provenance access scoping
is applied downstream in `intake_ingest`; injection markers are additionally neutralised on egress
(S-3, defense-in-depth).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import OCCUPANT_NEED_FIELDS, redact_pii
from src.utils.audit import emit_trace_event

_MAX_INPUT = 200_000  # evacuation-shelter batch intake can be large
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard the above",
    "system prompt",
    "you are now",
    "###system",
    "<|im_start|>",
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _sanitize(text: str) -> str:
    """S-1 field hygiene: NFKC-normalise then strip control chars + redact secrets / direct-identifier
    patterns (My-Number / credentials / email / phone) before the value is persisted to State."""
    return redact_pii(_nfkc(text))


def _sanitize_tree(obj: Any) -> Any:
    """Recursively sanitise string leaves of a parsed object (no PII/credentials persisted)."""
    if isinstance(obj, str):
        return _sanitize(obj)
    if isinstance(obj, list):
        return [_sanitize_tree(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_tree(v) for k, v in obj.items()}
    return obj


def _scope_occupant(occ: Any) -> dict[str, Any]:
    """S-1 minimisation: keep only the accessibility-need note fields the workflow consumes; drop every
    occupant direct identifier (name / phone / email / address / My-Number / guardian ...). Preserves the
    occupant *entry* (so occupant_total / low-confidence counting is unchanged) but not its identifiers.
    """
    if not isinstance(occ, dict):
        return {}
    return {k: _sanitize(str(occ[k])) for k in OCCUPANT_NEED_FIELDS if occ.get(k) is not None}


def _scope_intake(intake: Any) -> list[Any]:
    """Sanitise the intake list and minimise occupant records to need-fields only (no resident PII)."""
    if not isinstance(intake, list):
        return []
    scoped: list[Any] = []
    for shelter in intake:
        if isinstance(shelter, dict):
            rec: dict[str, Any] = {}
            for k, v in shelter.items():
                if k == "occupants" and isinstance(v, list):
                    rec[k] = [_scope_occupant(o) for o in v]
                else:
                    rec[k] = _sanitize_tree(v)
            scoped.append(rec)
        else:
            scoped.append(_sanitize_tree(shelter))
    return scoped


class PreProcessNode(FunctionNode):
    """Validate + access-scope the intake request and extract the triage slots."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2 domain hook — no-op (SDK 1.0.0: MUST NOT raise, MUST NOT set ERROR).

        Prompt-injection / empty / oversize are handled as a degraded ``status=SUCCESS + error_code``
        path in ``execute()`` (the untrusted body is discarded and never processed) so main /
        post_process S-3/S-4 always run. A ``status=ERROR`` here would short-circuit ``__call__`` and
        skip main / post_process. The framework default S-2 PII masking still applies;
        record-level access scoping is applied in `intake_ingest`. Returns the state unchanged.
        """
        return dict(state)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = state.get("user_input", "") or ""
        input_context = state.get("input_context", {})  # read-only [C1]
        enriched = json.dumps(
            {
                "source": "MunicipalEvacuationShelterAccessibilityNeedsTriageAgent",
                "channel": input_context.get("channel", "unknown"),
            },
            ensure_ascii=False,
        )
        normalized = _nfkc(raw)

        # Prompt-injection -> degraded SUCCESS + error_code. The body is discarded (never processed);
        # NOT status=ERROR: ERROR short-circuits __call__ so main / post_process (disclaimer / redaction /
        # audit) would be skipped. Egress S-3 neutralisation remains defense-in-depth.
        if any(marker in normalized.lower() for marker in _INJECTION_MARKERS):
            emit_trace_event("intake_validate.rejected", {"reason": "prompt_injection"}, state)
            return {
                "validated_input": "{}",
                "input_format": "rejected",
                "enriched_context": enriched,
                "error_code": "INJECTION_REJECTED",
                "error_message": "prompt-injection marker detected; intake not processed",
                "status": AgentStatus.SUCCESS.value,
            }

        if not raw.strip():
            emit_trace_event("intake_validate.rejected", {"reason": "empty_input"}, state)
            return {
                "validated_input": "{}",
                "input_format": "empty",
                "enriched_context": enriched,
                "error_code": "INPUT_REJECTED",
                "status": AgentStatus.SUCCESS.value,
            }

        # Oversize -> degraded SUCCESS + INPUT_TOO_LONG. The body is discarded so the zero-data safe
        # branch runs and post_process still delivers the disclaimer / redaction / audit.
        if len(normalized) > _MAX_INPUT:
            emit_trace_event("intake_validate.rejected", {"reason": "oversize"}, state)
            return {
                "validated_input": "{}",
                "input_format": "oversize",
                "enriched_context": enriched,
                "error_code": "INPUT_TOO_LONG",
                "error_message": f"intake exceeds size cap ({_MAX_INPUT} chars)",
                "status": AgentStatus.SUCCESS.value,
            }

        scope, fmt = self._parse(_CONTROL.sub("", raw))
        emit_trace_event(
            "intake_validate.validated",
            {
                "input_format": fmt,
                "shelter_count": len(scope.get("intake", [])),
                "scoped": bool(scope.get("authorized_provenances")),
            },
            state,
        )
        return {
            "validated_input": json.dumps(scope, ensure_ascii=False),
            "input_format": fmt,
            "enriched_context": enriched,
            "status": AgentStatus.SUCCESS.value,
        }

    def _parse(self, text: str) -> tuple[dict[str, Any], str]:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                intake = obj.get("intake") or obj.get("shelters") or obj.get("records") or []
                scope = {
                    "intake": _scope_intake(intake if isinstance(intake, list) else []),
                    "authorized_provenances": [_sanitize(str(p)) for p in (obj.get("authorized_provenances") or [])],
                    "incident_id": _sanitize(str(obj.get("incident_id") or "")),
                    "taxonomy_hint": _sanitize(str(obj.get("taxonomy_hint") or "")),
                }
                return scope, "json"
            if isinstance(obj, list):
                return {
                    "intake": _scope_intake(obj),
                    "authorized_provenances": [],
                    "incident_id": "",
                    "taxonomy_hint": "",
                }, "json"
        except (ValueError, TypeError):
            pass
        # Non-structured text is out of scope for this agent (structured intake required) → 0 records.
        return {
            "intake": [],
            "authorized_provenances": [],
            "incident_id": "",
            "taxonomy_hint": "",
            "raw_text": _sanitize(text),
        }, "text"
