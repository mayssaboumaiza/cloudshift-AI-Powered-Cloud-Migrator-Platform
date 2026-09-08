"""
deployer — Agent 03: deployment script generator + CI/CD pipeline builder.

Module structure:
  deployment_agent.py  — main agent (run_agent_03): terraform apply + deploy.sh + CI/CD YAML
  templates/
    cicd/   — github_actions.yml.j2
    deploy/ — aws/azure/gcp deploy.sh Jinja2 templates
"""
from agents.deployer.deployment_agent import run_agent_03

__all__ = ["run_agent_03"]
