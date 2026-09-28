"""GOV-C2-046 — inner workflow step 3: option_evaluate (ResourceOptionEvaluate).

Scores supply / staffing / transfer options for each classified need against the shelter's facility
constraints (capacity headroom, barrier-free / medical / welfare-shelter features). Scoring is
**deterministic** (no LLM): severity × count, feasibility derived from features.
Options that cannot be met in-place are flagged as escalations (life-critical ones as `critical`). No-ops
on the rejected / 0-data branch.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import ShelterKB
from src.utils.audit import emit_trace_event


class ResourceOptionEvaluateNode(FunctionNode):
    """Evaluate resource options (supply / staffing / transfer) per shelter × need."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("ingest_count", 0) == 0:
            emit_trace_event("option_evaluate.skip", {"reason": state.get("error_code") or "no_data"}, state)
            return {}
        records = {r["shelter_id"]: r for r in json.loads(state.get("shelter_records") or "[]")}
        classified = json.loads(state.get("classified_needs") or "[]")
        evaluations = [
            ShelterKB.evaluate_options(records[c["shelter_id"]], c) for c in classified if c["shelter_id"] in records
        ]
        escalation_count = sum(1 for e in evaluations for o in e["options"] if o.get("escalation"))
        emit_trace_event(
            "option_evaluate.complete", {"shelters": len(evaluations), "escalation_options": escalation_count}, state
        )
        return {"option_evaluations": json.dumps(evaluations, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}
