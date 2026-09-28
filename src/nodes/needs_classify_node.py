"""GOV-C2-046 — inner workflow step 2: needs_classify (AccessibilityNeedsClassify).

Classifies occupant support / medical notes into the 要配慮者 accessibility taxonomy (medical-care /
wheelchair / elderly-care / pregnant / infant / sensory / cognitive / language), aggregating per-category
counts + severity. Classification is **deterministic** (no LLM): notes are mapped over the seeded taxonomy
markers (auditable, no PII in the output — aggregate counts only). No-ops on the rejected / 0-data
branch.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import ShelterKB
from src.utils.audit import emit_trace_event


class AccessibilityNeedsClassifyNode(FunctionNode):
    """Classify accessibility / vulnerable-resident needs per shelter."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("ingest_count", 0) == 0:
            emit_trace_event("needs_classify.skip", {"reason": state.get("error_code") or "no_data"}, state)
            return {}
        records = json.loads(state.get("shelter_records") or "[]")
        classified = [ShelterKB.classify_needs(r) for r in records]
        total_needs = sum(len(c["needs"]) for c in classified)
        emit_trace_event(
            "needs_classify.complete", {"shelters": len(classified), "need_categories": total_needs}, state
        )
        return {"classified_needs": json.dumps(classified, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}
