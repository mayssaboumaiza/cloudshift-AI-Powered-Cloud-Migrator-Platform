"""
agents — LangGraph AI pipeline for cloud migration.

Public entry points (imported on demand to avoid circular imports at startup):
  pipeline_graph.app                 — compiled LangGraph workflow (invoke / stream)
  migration_planner.run_agent_01     — migration plan generation (ReAct + 7R taxonomy)
  iac_generator.run_agent_02         — Terraform IaC generation (Graph RAG + fix loop)
  deployer.run_agent_03              — deployment script + CI/CD YAML generation
  stack_analyzer.run_stack_analyzer  — GitHub repo parsing (HCL/ARM/Helm/CFN/AST)
  multirepo_merger.merge_graphs      — multi-repo dependency graph deduplication
"""

__all__ = [
    "migration_planner",
    "iac_generator",
    "deployer",
    "pipeline_graph",
    "pipeline_state",
    "stack_analyzer",
    "multirepo_merger",
    "base_agent",
]
