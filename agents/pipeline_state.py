from typing import TypedDict, List, Dict, Any, Optional


# ── Phase-scoped sub-TypedDicts ───────────────────────────────────────────────

class WorkflowMeta(TypedDict, total=False):
    thread_id: str
    current_step: str
    source_cloud: str
    target_cloud: str
    errors: List
    warnings: List


class AnalysisState(TypedDict, total=False):
    repo_url: str
    repo_urls: Optional[List[str]]
    dependency_graph: Dict[str, Any]
    source_services_inventory: List[Dict[str, Any]]
    migration_priority: List[Dict[str, Any]]
    needs_service_selection: Optional[bool]
    user_selected_services: List[Dict[str, Any]]
    excluded_services: List[str]
    # Multi-cloud source: populated when the infrastructure spans several providers.
    # Maps provider name to resource count, e.g. {"aws": 5, "azure": 2}.
    cloud_distribution: Optional[Dict[str, int]]


class UserInputState(TypedDict, total=False):
    monthly_budget_usd: Optional[float]
    timeline: str
    data_residency_requirement: str
    # Structured compliance — replaces free-text regulatory_constraints in the pipeline
    compliance_standards: List[str]        # e.g. ['GDPR', 'HIPAA', 'PCI-DSS']
    high_availability_required: bool       # controls HA block generation
    network_isolation_required: bool       # controls public_network_access_enabled
    # Repos for which the user explicitly confirms database ownership.
    # Nodes whose source_file matches one of these URLs get owns_resource=True,
    # bypassing the external-connection filter in Agent 01.
    owned_repos: Optional[List[str]]
    user_accepted: Optional[bool]
    rejection_count: int
    rejection_feedback: str
    partial_rejection: bool
    rejected_services: List[Dict]


class PlanningState(TypedDict, total=False):
    migration_plan: Dict[str, Any]
    architecture_specs: Dict[str, Any]


class IaCState(TypedDict, total=False):
    generated_code_files: List[str]
    artifacts: Dict[str, Any]
    iac_validation_success: bool
    correction_counts: Dict[str, int]
    modules_to_fix: List[str]
    iac_regen_count: int
    needs_security_regen: Optional[bool]
    security_violations: Optional[List[str]]
    security_regen_count: int


class RunnerState(TypedDict, total=False):
    deployment_manifest: Optional[Dict[str, Any]]
    runner_job_id: Optional[str]
    runner_retry_count: int
    runner_regen_count: int
    runner_total_attempts: int
    runner_failure_class: Optional[str]
    runner_failure_signature: Optional[str]
    runner_failure_history: List[Dict[str, Any]]
    runner_failure_detail: Optional[Dict[str, Any]]
    runner_last_counted_retry_job_id: Optional[str]
    runner_last_counted_regen_job_id: Optional[str]


class GitHubState(TypedDict, total=False):
    github_pr_url: Optional[str]
    github_repo_url: Optional[str]


class IncrementalState(TypedDict, total=False):
    # ── Path 2 — Incremental migration support ────────────────────────────────
    # "full" | "incremental" | "up_to_date"
    migration_mode: str

    # Structured diff returned by ChangeDetector
    detected_changes: dict  # {added_files, modified_files, removed_files, new_resources, ...}

    # ID of the previous successful migration for the same (repo, target_cloud)
    previous_migration_id: str

    # Scoped context for Agent 02 in incremental mode:
    # only files/resources that changed need to be re-generated
    incremental_resources: List[str]   # terraform resource types to (re)generate
    incremental_files: List[str]       # Python source files to re-migrate


class OtherState(TypedDict, total=False):
    # Service inventory detected by stack_analyzer before pipeline phase assignment
    detected_services: List
    # User-supplied constraints not yet promoted to a phase-scoped bucket
    target_region: str
    regulatory_constraints: str
    performance_requirements: str
    # AI stack auto-detected from repo (not user input)
    ai_stack: Dict[str, str]
    # Source Python files for Agent 02 code migration (filename → content)
    source_files: Dict[str, str]
    # Per-service rejection reasons keyed by source service name
    rejection_reasons: Dict[str, str]
    # IaC fix-loop overflow — set True when correction_counts exceeds limit
    needs_human_escalation: bool
    # True when Checkov warnings were accepted after max fix attempts
    iac_quality_warning: Optional[bool]
    # Deterministic intent-validation issues fed back into Agent 02
    intent_issues: List
    # DB primary key (distinct from thread_id which is the LangGraph checkpoint key)
    migration_id: str
    # True when cloud credentials were pre-validated at form submission
    credentials_pre_validated: bool
    # Deployment outcome status string written by Agent 03
    deployment_status: str

class MigrationState(
    WorkflowMeta,
    AnalysisState,
    UserInputState,
    PlanningState,
    IaCState,
    RunnerState,
    GitHubState,
    IncrementalState,
    OtherState,
    total=False,
):
    pass
