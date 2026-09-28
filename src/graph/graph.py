"""GOV-C2-046 — outer graph (Cat 2).

AgentBaseGraph 5-node backbone; domain complexity in the `main` slot via
EvacuationTriageWorkflowGraphNode (a GraphNode wrapping the inner EvacuationTriageWorkflow).

    START → initialize → pre_process → main(GraphNode) → post_process → finalize → END
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState

from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

if TYPE_CHECKING:
    from src.graph.domain_workflow_graph import EvacuationTriageWorkflow


class EvacuationTriageWorkflowGraphNode(GraphNode):
    """`main` slot — wraps the inner EvacuationTriageWorkflow (composition criterion #9)."""

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def __init__(self) -> None:
        super().__init__()
        self._subgraph: EvacuationTriageWorkflow | None = None  # cache: build/compile the inner workflow once

    def get_subgraph(self) -> EvacuationTriageWorkflow:
        # Cache the inner-workflow instance (BaseGraph.invoke() _ensure_compiled is idempotent →
        # skips per-request DAG compile).
        if self._subgraph is None:
            from src.graph.domain_workflow_graph import EvacuationTriageWorkflow

            self._subgraph = EvacuationTriageWorkflow(config=self._parent_config())
        return self._subgraph

    def extract_input(self, state: AgentState) -> str:
        return cast(str, state.get("validated_input", state.get("user_input", "")))

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "result": sub_result.get("output"),
            "ingest_count": sub_result.get("ingest_count", state.get("ingest_count", 0)),
            "error_code": state.get("error_code") or sub_result.get("error_code"),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> dict[str, Any]:
        return {}


class Graph(AgentBaseGraph):
    """Outer Cat 2 graph for GOV-C2-046."""

    @property
    def name(self) -> str:
        return "MunicipalEvacuationShelterAccessibilityNeedsTriageAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = EvacuationTriageWorkflowGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        """Framework default, plus the guarantee that a success is never empty.

        The Marketplace runner rejects a successful invocation whose output is
        missing — verified on a deployed Pod — and a degraded run
        (SUCCESS + error_code) produces no artefact for the framework default
        to surface. Report the degradation instead: this states what happened,
        it does not invent an answer.

        Only on SUCCESS. A request refused by the framework's S-2 gate (status
        ERROR) must keep publishing nothing — answering a hostile input with a
        notice would undo the refusal, and the runner treats a non-success
        invocation as a failure regardless, so there is nothing to rescue.
        """
        out: dict[str, Any] = super().get_output(state)
        if not out.get("output") and str(state.get("status", "")).lower().endswith("success"):
            code = state.get("error_code") or "NO_CONTENT"
            out["output"] = (
                "This request could not be completed "
                f"(error_code={code}). No content was produced; "
                "see error_code and error_log for the degradation cause."
            )
        return out


# server.py / AgentRegistry expect a module-level alias for the agent class.
MunicipalEvacuationShelterAccessibilityNeedsTriageAgent = Graph
