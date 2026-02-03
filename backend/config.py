"""
Configuration management for On Call Helper.

Loads configuration from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ═══════════════ App ═══════════════
    app_name: str = Field("On Call Helper", description="Application name")
    app_version: str = Field("0.1.0", description="Application version")
    debug: bool = Field(False, description="Debug mode")
    log_level: str = Field("INFO", description="Logging level")

    # ═══════════════ Server ═══════════════
    host: str = Field("0.0.0.0", description="Server host")
    port: int = Field(8000, description="Server port")

    # ═══════════════ AI ═══════════════
    anthropic_api_key: str = Field("", description="Anthropic API key")
    triage_model: str = Field("claude-sonnet-4-20250514", description="Model for triage")
    fixer_model: str = Field("claude-sonnet-4-20250514", description="Model for fix generation")

    # ═══════════════ GCP ═══════════════
    gcp_project_id: str = Field("", description="GCP project ID")
    gcp_credentials_path: str = Field("", description="Path to GCP credentials JSON")
    gcp_log_filter: str = Field("severity>=ERROR", description="Cloud Logging filter")
    gcp_auto_poll: bool = Field(True, description="Auto-start GCP polling on backend startup")
    gcp_poll_interval: int = Field(30, description="GCP polling interval in seconds")

    # ═══════════════ GitHub ═══════════════
    github_token: str = Field("", description="GitHub personal access token")
    github_repo: str = Field("", description="Target repo (owner/repo)")
    github_base_branch: str = Field("main", description="Base branch for PRs")

    # ═══════════════ PagerDuty ═══════════════
    pagerduty_routing_key: str = Field("", description="PagerDuty Events API routing key")

    # ═══════════════ CodeRabbit ═══════════════
    coderabbit_max_retries: int = Field(3, description="Max CodeRabbit review iterations")

    # ═══════════════ Sandbox ═══════════════
    sandbox_timeout_minutes: int = Field(15, description="Sandbox test timeout")

    # ═══════════════ Repository Paths ═══════════════
    nucleus_repo_path: Path = Field(
        Path("/Users/sri/nucleus"),
        description="Path to Nucleus repository"
    )
    oncall_repo_path: Path = Field(
        Path("/Users/sri/oncall"),
        description="Path to On-Call repository"
    )

    # ═══════════════ Production Monitoring ═══════════════
    verification_duration_hours: int = Field(2, description="Hours to monitor after deploy")
    verification_check_interval_minutes: int = Field(5, description="Check interval")

    # ═══════════════ Storage ═══════════════
    storage_backend: str = Field("memory", description="Storage backend: 'memory' or 'firestore'")
    firestore_project_id: str = Field(
        "",
        description="GCP project ID for Firestore (if different from gcp_project_id). "
                    "Useful when Cloud Logging is in one project and Firestore in another."
    )
    firestore_database_id: str = Field(
        "",
        description="Firestore database ID. Leave empty for '(default)' database."
    )

    # ═══════════════ Dashboard ═══════════════
    dashboard_url: str = Field("http://localhost:3000", description="Dashboard URL")

    # ═══════════════ Pattern Learning ═══════════════
    pattern_learning_enabled: bool = Field(
        True,
        description="Enable pattern learning from historical incidents"
    )
    pattern_min_occurrences: int = Field(
        3,
        description="Minimum occurrences before pattern can override classification"
    )
    pattern_override_success_rate: float = Field(
        0.70,
        description="Minimum success rate (0.0-1.0) for pattern to override classification"
    )
    pattern_override_confidence: float = Field(
        0.80,
        description="Minimum pattern confidence (0.0-1.0) for override"
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    def validate_required_for_production(self) -> list[str]:
        """Check required settings for production mode."""
        missing = []

        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.gcp_project_id:
            missing.append("GCP_PROJECT_ID")
        if not self.github_token:
            missing.append("GITHUB_TOKEN")
        if not self.github_repo:
            missing.append("GITHUB_REPO")

        return missing

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.debug or os.getenv("ENVIRONMENT", "development") == "development"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience function
settings = get_settings()
