"""Unit tests for Terraform variable resolution and jsonencode handling.

Tests cover:
  - _parse_tf_variables()   : extract default values from variables.tf
  - _parse_tfvars()         : extract actual values from *.tfvars
  - _resolve_var()          : resolve ${var.name} and var.name references
  - _extract_iam_trust_service(): jsonencode() + dict + variable cases
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from services.stack_analyzer.iac_parsers import (
    _parse_tf_variables,
    _parse_tfvars,
    _resolve_var,
    _extract_iam_trust_service,
    _parse_tf_hcl2,
)


# ── _parse_tf_variables ───────────────────────────────────────────────────────

VARIABLES_TF = """
variable "db_instance_class" {
  description = "RDS instance class"
  default     = "db.t3.micro"
}

variable "environment" {
  default = "staging"
}

variable "no_default" {
  description = "No default value here"
}
"""

def test_parse_tf_variables_extracts_defaults():
    result = _parse_tf_variables(VARIABLES_TF)
    assert result["db_instance_class"] == "db.t3.micro"
    assert result["environment"] == "staging"

def test_parse_tf_variables_ignores_no_default():
    result = _parse_tf_variables(VARIABLES_TF)
    assert "no_default" not in result

def test_parse_tf_variables_empty_content():
    assert _parse_tf_variables("") == {}

def test_parse_tf_variables_no_variables_block():
    assert _parse_tf_variables('resource "aws_s3_bucket" "b" {}') == {}


# ── _parse_tfvars ─────────────────────────────────────────────────────────────

TFVARS_CONTENT = """
db_instance_class = "db.t3.large"
environment       = "production"
"""

def test_parse_tfvars_extracts_values():
    result = _parse_tfvars(TFVARS_CONTENT)
    assert result["db_instance_class"] == "db.t3.large"
    assert result["environment"] == "production"

def test_parse_tfvars_overrides_variables_tf():
    """tfvars values should override variables.tf defaults."""
    defaults = _parse_tf_variables(VARIABLES_TF)
    overrides = _parse_tfvars(TFVARS_CONTENT)
    merged = {**defaults, **overrides}
    assert merged["db_instance_class"] == "db.t3.large"  # tfvars wins

def test_parse_tfvars_empty():
    assert _parse_tfvars("") == {}


# ── _resolve_var ──────────────────────────────────────────────────────────────

_VARS = {"db_instance_class": "db.t3.micro", "env": "prod"}

def test_resolve_var_interpolation_syntax():
    assert _resolve_var("${var.db_instance_class}", _VARS) == "db.t3.micro"

def test_resolve_var_plain_syntax():
    assert _resolve_var("var.env", _VARS) == "prod"

def test_resolve_var_unknown_returns_original():
    assert _resolve_var("${var.unknown_var}", _VARS) == "${var.unknown_var}"

def test_resolve_var_plain_string_unchanged():
    assert _resolve_var("db.t3.large", _VARS) == "db.t3.large"

def test_resolve_var_none_returns_none():
    assert _resolve_var(None, _VARS) is None

def test_resolve_var_empty_var_values():
    assert _resolve_var("${var.db_instance_class}", {}) == "${var.db_instance_class}"


# ── _extract_iam_trust_service ────────────────────────────────────────────────

def _make_trust_policy(service: str) -> str:
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": service},
            "Action": "sts:AssumeRole",
        }]
    })


def test_extract_iam_trust_plain_json_ec2(monkeypatch):
    """Plain JSON policy for EC2 → LLM resolves to azurerm_user_assigned_identity."""
    monkeypatch.setattr(
        "services.stack_analyzer.iac_parsers._resolve_iam_trust_llm",
        lambda policy_json, principals: {
            "azure_target": "azurerm_user_assigned_identity",
            "hint": "EC2 role → Managed Identity",
            "trust_principals": principals,
        }
    )
    config = {"assume_role_policy": _make_trust_policy("ec2.amazonaws.com")}
    result = _extract_iam_trust_service(config)
    assert result is not None
    assert result["azure_target"] == "azurerm_user_assigned_identity"
    assert "ec2.amazonaws.com" in result["trust_principals"]


def test_extract_iam_trust_dict_value(monkeypatch):
    """python-hcl2 may return assume_role_policy as a Python dict (jsonencode evaluated)."""
    monkeypatch.setattr(
        "services.stack_analyzer.iac_parsers._resolve_iam_trust_llm",
        lambda policy_json, principals: {
            "azure_target": "azurerm_user_assigned_identity",
            "hint": "Lambda → Managed Identity",
            "trust_principals": principals,
        }
    )
    policy_dict = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }]
    }
    config = {"assume_role_policy": policy_dict}
    result = _extract_iam_trust_service(config)
    assert result is not None
    assert "lambda.amazonaws.com" in result["trust_principals"]


def test_extract_iam_trust_jsonencode_string(monkeypatch):
    """Terraform jsonencode() wrapper string — principals extracted via regex."""
    monkeypatch.setattr(
        "services.stack_analyzer.iac_parsers._resolve_iam_trust_llm",
        lambda policy_json, principals: {
            "azure_target": "azurerm_user_assigned_identity",
            "hint": "ECS task role → Managed Identity",
            "trust_principals": principals,
        }
    )
    raw = 'jsonencode({"Version":"2012-10-17","Statement":[{"Principal":{"Service":"ecs-tasks.amazonaws.com"}}]})'
    config = {"assume_role_policy": raw}
    result = _extract_iam_trust_service(config)
    assert result is not None
    assert "ecs-tasks.amazonaws.com" in result["trust_principals"]


def test_extract_iam_trust_variable_resolution(monkeypatch):
    """Variable reference ${var.trust_policy} resolved from var_values."""
    monkeypatch.setattr(
        "services.stack_analyzer.iac_parsers._resolve_iam_trust_llm",
        lambda policy_json, principals: {
            "azure_target": "azurerm_user_assigned_identity",
            "hint": "eks → AKS identity",
            "trust_principals": principals,
        }
    )
    var_values = {
        "trust_policy": _make_trust_policy("eks.amazonaws.com")
    }
    config = {"assume_role_policy": "${var.trust_policy}"}
    result = _extract_iam_trust_service(config, var_values)
    assert result is not None
    assert "eks.amazonaws.com" in result["trust_principals"]


def test_extract_iam_trust_no_policy_returns_none():
    config = {"description": "no assume_role_policy key"}
    assert _extract_iam_trust_service(config) is None


def test_extract_iam_trust_empty_principals_returns_none():
    """Policy with no Service principals → None."""
    policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": "*", "Action": "sts:AssumeRole"}]
    })
    config = {"assume_role_policy": policy}
    result = _extract_iam_trust_service(config)
    # "*" is filtered out — no principals found
    assert result is None


# ── _parse_tf_hcl2 with var_values ───────────────────────────────────────────

TF_WITH_VAR = """
resource "aws_db_instance" "main" {
  instance_class = "${var.db_instance_class}"
  engine         = "postgres"
}
"""

def test_parse_tf_hcl2_resolves_instance_class_var():
    var_values = {"db_instance_class": "db.t3.large"}
    resources, _ = _parse_tf_hcl2(TF_WITH_VAR, "main.tf", var_values)
    assert len(resources) == 1
    hints = resources[0].get("contextual_hints", {})
    assert hints.get("instance_class") == "db.t3.large"


def test_parse_tf_hcl2_unresolvable_var_excluded():
    """A var reference with no matching var_values → contextual_hints NOT set."""
    var_values = {}  # no resolution available
    resources, _ = _parse_tf_hcl2(TF_WITH_VAR, "main.tf", var_values)
    assert len(resources) == 1
    # instance_class starts with "${" → excluded from contextual_hints
    hints = resources[0].get("contextual_hints", {})
    assert "instance_class" not in hints


TF_INLINE_CLASS = """
resource "aws_db_instance" "db" {
  instance_class = "db.r5.xlarge"
  engine         = "mysql"
}
"""

def test_parse_tf_hcl2_inline_instance_class():
    """Inline literal instance_class extracted without var_values."""
    resources, _ = _parse_tf_hcl2(TF_INLINE_CLASS, "main.tf")
    assert resources[0]["contextual_hints"]["instance_class"] == "db.r5.xlarge"
