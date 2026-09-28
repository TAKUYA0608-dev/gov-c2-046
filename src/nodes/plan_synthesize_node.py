"""GOV-C2-046 — inner workflow step 4: plan_synthesize (AllocationPlanSynthesize + HumanGate).

Synthesises a prioritised allocation + escalation plan from the evaluated options (rule/threshold
ranking — partially deterministic), then runs the **fail-closed HumanGate review**: on low confidence,
data gaps, or critical resource contention the allocation recommendation is *withheld* and only an
escalation-to-incident-command draft is emitted. This is the deliverable-producing node — on
the rejected / 0-data branch it emits the out-of-scope safe answer (no fabricated plan). The agent never
executes an allocation, dispatch, or evacuation order; a human approver is always required.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import TAXONOMY_SOURCE, AllocationEngine
from src.utils.audit import emit_trace_event

_OUT_OF_SCOPE = (
    "認可済みの避難所 intake（shelter_id・capacity・occupancy・facility_features・occupants を含む構造化データ）"
    "が得られなかったため、accessibility triage と配分 plan を作成できません。認可済みの避難所 intake / "
    "occupancy / facility データを構造化して指定するか、災害対策本部にご確認ください。"
)


class AllocationPlanSynthesizeNode(FunctionNode):
    """Synthesise the allocation plan draft + fail-closed HumanGate review (or safe answer on 0-data)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("ingest_count", 0) == 0:
            emit_trace_event("plan_synthesize.safe", {"reason": state.get("error_code") or "no_data"}, state)
            report: dict[str, Any] = {
                "status_kind": "out_of_scope",
                "message": _OUT_OF_SCOPE,
                "priorities": [],
                "escalations": [],
                "citations": [],
            }
            return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

        records = json.loads(state.get("shelter_records") or "[]")
        classified = json.loads(state.get("classified_needs") or "[]")
        evaluations = json.loads(state.get("option_evaluations") or "[]")

        plan = AllocationEngine.synthesize(evaluations)
        review = AllocationEngine.human_gate_review(records, classified, plan)
        citations = self._citations(records)

        if review["fail_closed"]:
            report = {
                "status_kind": "escalation_only",
                "message": (
                    "HumanGate（fail-closed）: 低信頼 / データ欠落 / 資源競合を検出したため、"
                    "配分 plan は提示せず、認可済み災害対策本部レビューへエスカレーションします。"
                ),
                "priorities": [],  # allocation recommendation withheld
                "escalations": plan["escalations"],
                "human_review": review,
                "citations": citations,
            }
        else:
            report = {
                "status_kind": "allocation_plan",
                "message": (
                    "配分 plan draft（要 human approver）。実配分・物資/要員移送・避難指示は行いません。"
                    if plan["priorities"] or plan["escalations"]
                    else "要配慮ニーズは検出されませんでした（intake は正常に取り込み済み）。"
                ),
                "priorities": plan["priorities"],
                "escalations": plan["escalations"],
                "human_review": review,
                "citations": citations,
            }

        emit_trace_event(
            "plan_synthesize.complete",
            {
                "status_kind": report["status_kind"],
                "priorities": len(report["priorities"]),
                "escalations": len(report["escalations"]),
                "fail_closed": review["fail_closed"],
            },
            state,
        )
        return {
            "result": json.dumps(report, ensure_ascii=False),
            "allocation_plan": json.dumps(plan, ensure_ascii=False),
            "human_review": json.dumps(review, ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _citations(records: list[dict[str, Any]]) -> list[dict[str, str]]:
        cites = [{"shelter_id": r["shelter_id"], "source": f"{r.get('provenance', 'unknown')} intake"} for r in records]
        cites.append({"ref": TAXONOMY_SOURCE})
        return cites
