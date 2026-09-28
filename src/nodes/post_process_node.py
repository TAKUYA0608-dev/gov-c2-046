"""GOV-C2-046 — post_process node: PlanCompose (S-3 output gate + S-4 audit).

S-3: neutralise prompt-injection markers and redact residual PII (My-Number) in the assembled draft's
free-text fields (the intake is authorised operational data, so injection defence is applied on egress),
verify citation completeness (a grounded plan / escalation must cite its intake provenance), and append
the mandatory DRAFT / decision-support disclaimer. S-4: emit an audit event (status kind + aggregate
counts only — never raw intake / vulnerable-resident PII). Runs on the full plan, the fail-closed
escalation-only draft, and the out-of-scope safe branch.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import redact_pii
from src.utils.audit import emit_trace_event

_DISCLAIMER = (
    "本案内は認可済みの避難所 intake / occupancy / facility データに基づく参考用の DRAFT allocation plan "
    "であり、助言専用（decision-support only）です。本エージェントは実際の resource 配分・物資/要員の移送・"
    "避難指示の発令・医療トリアージを一切行いません。最終的な配分・避難判断および accountability は認可済みの"
    "自治体 / 災害対策本部の人間（HumanGate）が保持します。owner / 承認者は candidate/placeholder です。"
)

_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard the above",
    "system prompt",
    "you are now",
    "###system",
    "<|im_start|>",
)
_INJECTION_MASK = "[REDACTED-DIRECTIVE]"


def _neutralize(text: str) -> str:
    """S-3: strip control chars, redact residual secrets / PII (credential / email / phone / My-Number),
    and neutralise injection markers (defense-in-depth, shares the S-1 redaction patterns)."""
    out = redact_pii(text)
    low = out.lower()
    for marker in _INJECTION_MARKERS:
        idx = low.find(marker)
        while idx != -1:
            out = out[:idx] + _INJECTION_MASK + out[idx + len(marker) :]
            low = out.lower()
            idx = low.find(marker)
    return out


def _neutralize_tree(obj: Any) -> Any:
    if isinstance(obj, str):
        return _neutralize(obj)
    if isinstance(obj, list):
        return [_neutralize_tree(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _neutralize_tree(v) for k, v in obj.items()}
    return obj


class PostProcessNode(FunctionNode):
    """Neutralise output, verify citations, append DRAFT disclaimer, emit audit."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3 preservation check: the DRAFT / decision-support disclaimer must be present.

        SDK 1.0.0 contract: receives the **result dict from `execute()`**; returns the (possibly
        filtered) result. MAY raise to block an output missing the mandatory disclaimer.
        """
        out = result.get("formatted_output", "")
        if out and ("DRAFT" not in out or "災害対策本部" not in out):
            raise ValueError("S-3: DRAFT / decision-support disclaimer missing from output")
        return dict(result)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report: dict[str, Any] = json.loads(state.get("result", "{}") or "{}")
        report = _neutralize_tree(report)

        status_kind = report.get("status_kind")
        grounded = status_kind in ("allocation_plan", "escalation_only")
        citations = report.get("citations", [])
        citation_complete = (not grounded) or bool(citations)

        formatted = {
            "status_kind": status_kind,
            "message": report.get("message"),
            "priorities": report.get("priorities", []),
            "escalations": report.get("escalations", []),
            "human_review": report.get("human_review"),
            "citations": citations,
            "citation_complete": citation_complete,
            "disclaimer": _DISCLAIMER,
        }
        review = report.get("human_review") or {}
        emit_trace_event(
            "plan_compose.complete",
            {
                "status_kind": status_kind,
                "priorities": len(report.get("priorities", [])),
                "escalations": len(report.get("escalations", [])),
                "fail_closed": bool(review.get("fail_closed")),
                "citation_complete": citation_complete,
                "error_code": state.get("error_code"),
            },
            state,
        )
        return {
            "formatted_output": json.dumps(formatted, ensure_ascii=False),
            "disclaimer": _DISCLAIMER,
            "audit_logged": True,
            "status": AgentStatus.SUCCESS.value,
        }
