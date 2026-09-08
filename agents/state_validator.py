"""
state_validator.py — Validate MigrationState before each node execution.

Prevents invalid state from flowing through the graph.
"""
from typing import List, Tuple, Optional, Any
from agents.pipeline_state import MigrationState


class StateValidationError(ValueError):
    """Raised when state validation fails."""
    pass


class MigrationStateValidator:
    """Validator for MigrationState with typed checks."""

    # Valid cloud providers — "multi" is accepted for mixed-provider source architectures
    VALID_CLOUDS = {"aws", "gcp", "azure", "multi"}

    # Required fields at each stage
    REQUIRED_BY_STAGE = {
        "analyze": ["repo_url"],
        "plan": ["repo_url", "source_cloud", "target_cloud"],
        "generate": ["migration_plan", "target_cloud"],
        "validate": ["artifacts"],
        "deploy": ["deployment_status"],
    }

    @staticmethod
    def validate_repo_url(url: str) -> Tuple[bool, Optional[str]]:
        """Validate GitHub repo URL format.

        Returns:
            (is_valid, error_message)
        """
        if not url:
            return False, "repo_url cannot be empty"
        if not url.startswith("https://github.com/"):
            return False, f"Invalid GitHub URL: {url}"
        parts = url.split("/")
        if len(parts) < 5:
            return False, f"GitHub URL must be: https://github.com/owner/repo, got {url}"
        return True, None

    @staticmethod
    def validate_cloud(cloud: str) -> Tuple[bool, Optional[str]]:
        """Validate cloud provider."""
        if cloud not in MigrationStateValidator.VALID_CLOUDS:
            return False, f"Invalid cloud: {cloud}. Must be aws, gcp, or azure"
        return True, None

    @staticmethod
    def validate_stage(
        state: MigrationState,
        stage: str
    ) -> Tuple[bool, List[str]]:
        """Validate state has required fields for a stage.

        Args:
            state: MigrationState to validate
            stage: Stage name ("analyze", "plan", "generate", "validate", "deploy")

        Returns:
            (is_valid, list_of_errors)
        """
        required_fields = MigrationStateValidator.REQUIRED_BY_STAGE.get(stage, [])
        errors = []

        for field in required_fields:
            value = state.get(field)
            if not value:
                errors.append(f"Missing required field '{field}' for stage '{stage}'")

        # Stage-specific validations
        if stage == "analyze":
            if state.get("repo_url"):
                valid, err = MigrationStateValidator.validate_repo_url(state["repo_url"])
                if not valid:
                    errors.append(err)

        if stage == "plan":
            if state.get("source_cloud"):
                valid, err = MigrationStateValidator.validate_cloud(state["source_cloud"])
                if not valid:
                    errors.append(err)
            if state.get("target_cloud"):
                valid, err = MigrationStateValidator.validate_cloud(state["target_cloud"])
                if not valid:
                    errors.append(err)

        if stage == "generate":
            migration_plan = state.get("migration_plan") or {}
            if not isinstance(migration_plan, dict):
                errors.append("migration_plan must be a dict")
            if not migration_plan.get("resources"):
                errors.append("migration_plan must have 'resources' list")

        return len(errors) == 0, errors

    @staticmethod
    def validate_and_raise(
        state: MigrationState,
        stage: str
    ):
        """Validate state and raise StateValidationError if invalid.

        Args:
            state: MigrationState to validate
            stage: Stage name

        Raises:
            StateValidationError: If validation fails
        """
        is_valid, errors = MigrationStateValidator.validate_stage(state, stage)
        if not is_valid:
            error_msg = f"State validation failed for stage '{stage}':\n" + "\n".join(
                f"  - {err}" for err in errors
            )
            raise StateValidationError(error_msg)

    @staticmethod
    def get_validation_errors(
        state: MigrationState,
        stage: str
    ) -> List[str]:
        """Get list of validation errors without raising.

        Args:
            state: MigrationState to validate
            stage: Stage name

        Returns:
            List of error messages (empty if valid)
        """
        _, errors = MigrationStateValidator.validate_stage(state, stage)
        return errors
