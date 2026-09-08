"""
iac_generator — Agent 02: Terraform IaC generator (Graph RAG + fix loop).

Module structure:
  generator.py            — main agent entry points (run_agent_02, run_agent_02_fix)
  orchestrator.py         — deterministic IaC validator (terraform + checkov + infracost)
  tools_terraform.py      — IaC file tools: write/read .tf files, Jinja2, deploy, CI/CD
  iac_guidance.py         — generation guidance: provider versions, variables, ordering
  security_policy_engine.py — Checkov classification + security context injection
  checkov_fixer.py        — automated Checkov finding remediation
  iac_validator.py        — local terraform validate + plan wrapper

Note: execution_graph.py is a compatibility shim -> re-exports from iac_guidance.py.
"""
from agents.iac_generator.generator import run_agent_02, run_agent_02_fix, SERVICE_TO_FILE
from agents.iac_generator.orchestrator import Agent02IaCValidator

__all__ = ["run_agent_02", "run_agent_02_fix", "Agent02IaCValidator", "SERVICE_TO_FILE"]
