"""GOV-C2-046 — inner domain workflow graph (Cat 2).

Instantiated by EvacuationTriageWorkflowGraphNode.get_subgraph() in graph.py. Linear topology with
per-node skip guards (the portable Cat 2 form; conditional edges don't propagate across the subgraph
boundary):

    START → intake_ingest → needs_classify → option_evaluate → plan_synthesize → END

On rejected / empty / non-structured input, intake_ingest sets ingest_count=0 (+error_code=NO_DATA);
needs_classify and option_evaluate no-op and plan_synthesize emits the out-of-scope safe answer — no
fabricated allocation plan.
"""

from __future__ import annotations
from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState

from src.nodes.intake_ingest_node import IntakeIngestNode
from src.nodes.needs_classify_node import AccessibilityNeedsClassifyNode
from src.nodes.option_evaluate_node import ResourceOptionEvaluateNode
from src.nodes.plan_synthesize_node import AllocationPlanSynthesizeNode
from src.schemas.state import State


class EvacuationTriageWorkflow(BaseGraph):
    """Inner graph: intake_ingest → needs_classify → option_evaluate → plan_synthesize."""

    @property
    def name(self) -> str:
        return "EvacuationTriageWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        # No super() — BaseGraph.register_nodes() is abstract.
        self._nodes["intake_ingest"] = IntakeIngestNode()
        self._nodes["needs_classify"] = AccessibilityNeedsClassifyNode()
        self._nodes["option_evaluate"] = ResourceOptionEvaluateNode()
        self._nodes["plan_synthesize"] = AllocationPlanSynthesizeNode()

    def add_edges(self) -> None:
        # Static linear backbone; the 0-data / rejected skip is handled by per-node guards.
        self._sg.add_edge(START, "intake_ingest")
        self._sg.add_edge("intake_ingest", "needs_classify")
        self._sg.add_edge("needs_classify", "option_evaluate")
        self._sg.add_edge("option_evaluate", "plan_synthesize")
        self._sg.add_edge("plan_synthesize", END)

    def route(self, state: AgentState) -> str:
        """Required by the BaseGraph ABC. Linear topology → not wired to a conditional edge."""
        if state.get("error_code") or state.get("ingest_count", 0) == 0:
            return "plan_synthesize"
        return "needs_classify"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        return {
            "output": state.get("result"),
            "status": state.get("status"),
            "ingest_count": state.get("ingest_count", 0),
            "error_code": state.get("error_code"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
