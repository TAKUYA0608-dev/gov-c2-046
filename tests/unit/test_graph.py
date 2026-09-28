# GOV-C2-046 — Unit Tests: Cat 2 graph wiring (outer GraphNode + inner workflow)

import pytest

from src.graph.domain_workflow_graph import EvacuationTriageWorkflow
from src.graph.graph import (
    EvacuationTriageWorkflowGraphNode,
    Graph,
    MunicipalEvacuationShelterAccessibilityNeedsTriageAgent,
)
from src.schemas.state import State


class TestOuterGraph:
    def test_registry_alias(self):
        assert MunicipalEvacuationShelterAccessibilityNeedsTriageAgent is Graph

    def test_name_and_state_schema(self):
        g = Graph()
        assert g.name == "MunicipalEvacuationShelterAccessibilityNeedsTriageAgent"
        assert g.state_schema is State

    def test_main_slot_is_graphnode(self):
        g = Graph()
        g.register_nodes()
        assert isinstance(g._nodes["main"], EvacuationTriageWorkflowGraphNode)
        for slot in ("pre_process", "main", "post_process"):
            assert slot in g._nodes

    def test_error_strategy_propagate(self):
        assert EvacuationTriageWorkflowGraphNode.error_strategy == "propagate"

    def test_get_subgraph_is_cached(self):
        node = EvacuationTriageWorkflowGraphNode()
        assert node.get_subgraph() is node.get_subgraph()

    def test_extract_input_prefers_validated(self):
        node = EvacuationTriageWorkflowGraphNode()
        assert node.extract_input({"validated_input": "V", "user_input": "U"}) == "V"
        assert node.extract_input({"user_input": "U"}) == "U"

    def test_merge_output_maps_fields(self):
        node = EvacuationTriageWorkflowGraphNode()
        merged = node.merge_output({}, {"output": '{"x":1}', "ingest_count": 2, "status": "success",
                                        "error_code": None})
        assert merged["result"] == '{"x":1}' and merged["ingest_count"] == 2 and merged["status"] == "success"


class TestInnerWorkflow:
    def test_inner_registers_four_nodes(self):
        wf = EvacuationTriageWorkflow(config={})
        wf.register_nodes()
        for slot in ("intake_ingest", "needs_classify", "option_evaluate", "plan_synthesize"):
            assert slot in wf._nodes

    def test_route_zero_data_to_synthesize(self):
        wf = EvacuationTriageWorkflow(config={})
        assert wf.route({"ingest_count": 0}) == "plan_synthesize"
        assert wf.route({"error_code": "NO_DATA"}) == "plan_synthesize"

    def test_route_normal_to_classify(self):
        wf = EvacuationTriageWorkflow(config={})
        assert wf.route({"ingest_count": 3}) == "needs_classify"

    def test_get_output_surfaces_result(self):
        wf = EvacuationTriageWorkflow(config={})
        out = wf.get_output({"result": "R", "status": "success", "ingest_count": 1})
        assert out["output"] == "R" and out["ingest_count"] == 1


class TestServerModule:
    def test_server_imports(self):
        try:
            import src.api.server as server
        except ModuleNotFoundError as exc:
            pytest.skip(f"platform module unavailable in the local stub env: {exc}")
        assert server.app is not None and server.agent is not None
