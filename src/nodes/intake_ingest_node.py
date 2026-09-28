"""GOV-C2-046 — inner workflow step 1: intake_ingest.

Normalises the validated intake into structured shelter records and applies **S-2 record-level access
scoping** — records whose provenance is not in the caller's authorised allowlist are dropped and counted
(`access_rejected_count`). Sets `ingest_count`; **0 authorised records (rejected / empty / non-structured
input) routes to the out-of-scope safe answer** — the agent never fabricates a plan that is not grounded
in authorised intake.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import ShelterKB
from src.utils.audit import emit_trace_event


class IntakeIngestNode(FunctionNode):
    """Ingest + access-scope authorised shelter records for the incident."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        scope = json.loads(state.get("validated_input") or state.get("user_input") or "{}")
        if state.get("error_code") or not scope.get("intake"):
            emit_trace_event("intake_ingest.skip", {"reason": state.get("error_code") or "no_intake"}, state)
            return {
                "shelter_records": "[]",
                "ingest_count": 0,
                "access_rejected_count": 0,
                "error_code": state.get("error_code") or "NO_DATA",
                "status": AgentStatus.SUCCESS.value,
            }

        records = ShelterKB.normalize_records(scope.get("intake"))
        kept, rejected = ShelterKB.access_scope(records, scope.get("authorized_provenances"))
        emit_trace_event("intake_ingest.complete", {"ingested": len(kept), "access_rejected": rejected}, state)
        out = {
            "shelter_records": json.dumps(kept, ensure_ascii=False),
            "ingest_count": len(kept),
            "access_rejected_count": rejected,
            "status": AgentStatus.SUCCESS.value,
        }
        if not kept:
            out["error_code"] = "NO_DATA"
        return out
