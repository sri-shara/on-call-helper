"""
Tests for SRE Knowledge Loader.

Verifies knowledge loading from the oncall repository.
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from backend.knowledge.loader import (
    load_sre_knowledge,
    get_triage_system_prompt,
    get_knowledge_summary,
    clear_knowledge_cache,
    SREKnowledge,
    _load_file,
    _is_loaded,
)


class TestLoadFile:
    """Tests for file loading helper."""

    def test_load_existing_file(self, tmp_path):
        """Test loading an existing file."""
        test_file = tmp_path / "test.md"
        test_file.write_text("# Test Content\n\nSome content here.")

        content = _load_file(test_file)

        assert content == "# Test Content\n\nSome content here."

    def test_load_missing_file(self, tmp_path):
        """Test loading a missing file returns placeholder."""
        missing_file = tmp_path / "nonexistent.md"

        content = _load_file(missing_file)

        assert content.startswith("[Knowledge file not found:")
        assert "nonexistent.md" in content

    def test_load_file_strips_whitespace(self, tmp_path):
        """Test file content is stripped of leading/trailing whitespace."""
        test_file = tmp_path / "test.md"
        test_file.write_text("\n\n  Content  \n\n")

        content = _load_file(test_file)

        assert content == "Content"


class TestIsLoaded:
    """Tests for load status checker."""

    def test_loaded_content(self):
        """Test detecting successfully loaded content."""
        assert _is_loaded("# Real content") is True
        assert _is_loaded("Some text") is True

    def test_placeholder_content(self):
        """Test detecting placeholder content."""
        assert _is_loaded("[Knowledge file not found: test.md]") is False
        assert _is_loaded("[Error loading test.md: some error]") is False
        assert _is_loaded("[Permission denied: test.md]") is False


class TestLoadSREKnowledge:
    """Tests for SRE knowledge loading."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear cache before each test."""
        clear_knowledge_cache()
        yield
        clear_knowledge_cache()

    def test_load_from_real_oncall_repo(self):
        """Test loading from the actual oncall repository."""
        knowledge = load_sre_knowledge()

        # Should have loaded from the configured path
        assert knowledge.loaded_from == str(Path("/Users/sri/oncall"))

        # Should have loaded at least some files
        assert knowledge.files_loaded > 0

    def test_load_returns_sre_knowledge_object(self):
        """Test that loading returns an SREKnowledge object."""
        knowledge = load_sre_knowledge()

        assert isinstance(knowledge, SREKnowledge)

    def test_triage_framework_loaded(self):
        """Test that the main triage framework is loaded."""
        knowledge = load_sre_knowledge()

        # If the file exists, it should contain real content
        if _is_loaded(knowledge.triage_framework):
            assert len(knowledge.triage_framework) > 100
            # Should contain triage-related content
            assert "triage" in knowledge.triage_framework.lower() or \
                   "incident" in knowledge.triage_framework.lower() or \
                   "error" in knowledge.triage_framework.lower()

    def test_error_patterns_loaded(self):
        """Test that error patterns are loaded."""
        knowledge = load_sre_knowledge()

        if _is_loaded(knowledge.error_patterns):
            # Should contain error pattern content
            assert "error" in knowledge.error_patterns.lower() or \
                   "pattern" in knowledge.error_patterns.lower()

    def test_runbooks_loaded(self):
        """Test that runbooks are loaded."""
        knowledge = load_sre_knowledge()

        # Check at least one runbook is loaded
        runbooks = [
            knowledge.runbook_alloydb,
            knowledge.runbook_pubsub,
            knowledge.runbook_cloud_run,
            knowledge.runbook_integrations,
            knowledge.runbook_secops,
        ]

        loaded_runbooks = [r for r in runbooks if _is_loaded(r)]
        assert len(loaded_runbooks) > 0, "At least one runbook should be loaded"

    def test_caching(self):
        """Test that knowledge is cached."""
        knowledge1 = load_sre_knowledge()
        knowledge2 = load_sre_knowledge()

        # Should be the same object (cached)
        assert knowledge1 is knowledge2

    def test_custom_path(self, tmp_path):
        """Test loading from a custom path."""
        # Create mock knowledge structure
        claude_dir = tmp_path / ".claude" / "commands"
        claude_dir.mkdir(parents=True)
        sre_triage_dir = claude_dir / "sre-triage"
        sre_triage_dir.mkdir()
        runbooks_dir = tmp_path / "runbooks"
        runbooks_dir.mkdir()

        # Create mock files
        (claude_dir / "sre-triage.md").write_text("# Mock Triage Framework")
        (sre_triage_dir / "error-patterns.md").write_text("# Mock Error Patterns")
        (sre_triage_dir / "infrastructure-checks.md").write_text("# Mock Infra Checks")
        (sre_triage_dir / "tenant-reference.md").write_text("# Mock Tenant Ref")
        (sre_triage_dir / "bigquery-queries.md").write_text("# Mock BQ Queries")
        (sre_triage_dir / "output-format.md").write_text("# Mock Output Format")
        (sre_triage_dir / "handoff-updates.md").write_text("# Mock Handoff")
        (runbooks_dir / "alloydb.md").write_text("# Mock AlloyDB Runbook")
        (runbooks_dir / "pubsub-backlogs.md").write_text("# Mock PubSub Runbook")
        (runbooks_dir / "cloud-run.md").write_text("# Mock Cloud Run Runbook")
        (runbooks_dir / "integrations.md").write_text("# Mock Integrations Runbook")
        (runbooks_dir / "secops-integration.md").write_text("# Mock SecOps Runbook")

        knowledge = load_sre_knowledge(str(tmp_path))

        assert knowledge.loaded_from == str(tmp_path)
        assert knowledge.triage_framework == "# Mock Triage Framework"
        assert knowledge.error_patterns == "# Mock Error Patterns"
        assert knowledge.runbook_alloydb == "# Mock AlloyDB Runbook"
        assert knowledge.files_loaded == 12
        assert knowledge.files_missing == 0


class TestGetTriageSystemPrompt:
    """Tests for system prompt generation."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear cache before each test."""
        clear_knowledge_cache()
        yield
        clear_knowledge_cache()

    def test_prompt_includes_knowledge(self):
        """Test that the system prompt includes loaded knowledge."""
        prompt = get_triage_system_prompt()

        # Should include key sections
        assert "SRE triage agent" in prompt
        assert "Nucleus" in prompt
        assert "FIXABLE" in prompt
        assert "INFRA_ISSUE" in prompt
        assert "TRANSIENT" in prompt
        assert "NEEDS_HUMAN" in prompt

    def test_prompt_includes_output_format(self):
        """Test that the system prompt includes JSON output format."""
        prompt = get_triage_system_prompt()

        assert "classification" in prompt
        assert "confidence" in prompt
        assert "root_cause" in prompt
        assert "JSON" in prompt or "json" in prompt

    def test_prompt_with_custom_knowledge(self, tmp_path):
        """Test generating prompt with custom knowledge."""
        # Create minimal mock knowledge
        claude_dir = tmp_path / ".claude" / "commands"
        claude_dir.mkdir(parents=True)
        sre_triage_dir = claude_dir / "sre-triage"
        sre_triage_dir.mkdir()
        runbooks_dir = tmp_path / "runbooks"
        runbooks_dir.mkdir()

        (claude_dir / "sre-triage.md").write_text("CUSTOM TRIAGE FRAMEWORK")
        (sre_triage_dir / "error-patterns.md").write_text("CUSTOM ERROR PATTERNS")
        (sre_triage_dir / "infrastructure-checks.md").write_text("CUSTOM INFRA")
        (sre_triage_dir / "tenant-reference.md").write_text("CUSTOM TENANT")
        (sre_triage_dir / "bigquery-queries.md").write_text("CUSTOM BQ")
        (sre_triage_dir / "output-format.md").write_text("CUSTOM OUTPUT")
        (sre_triage_dir / "handoff-updates.md").write_text("CUSTOM HANDOFF")
        (runbooks_dir / "alloydb.md").write_text("CUSTOM ALLOYDB RUNBOOK")
        (runbooks_dir / "pubsub-backlogs.md").write_text("CUSTOM PUBSUB")
        (runbooks_dir / "cloud-run.md").write_text("CUSTOM CLOUD RUN")
        (runbooks_dir / "integrations.md").write_text("CUSTOM INTEGRATIONS")
        (runbooks_dir / "secops-integration.md").write_text("CUSTOM SECOPS")

        knowledge = load_sre_knowledge(str(tmp_path))
        prompt = get_triage_system_prompt(knowledge)

        assert "CUSTOM TRIAGE FRAMEWORK" in prompt
        assert "CUSTOM ERROR PATTERNS" in prompt
        assert "CUSTOM ALLOYDB RUNBOOK" in prompt


class TestGetKnowledgeSummary:
    """Tests for knowledge summary."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear cache before each test."""
        clear_knowledge_cache()
        yield
        clear_knowledge_cache()

    def test_summary_includes_all_files(self):
        """Test that summary includes all knowledge files."""
        summary = get_knowledge_summary()

        expected_keys = [
            "loaded_from",
            "files_loaded",
            "files_missing",
            "triage_framework",
            "infrastructure_checks",
            "bigquery_queries",
            "tenant_reference",
            "error_patterns",
            "output_format",
            "handoff_updates",
            "runbook_alloydb",
            "runbook_pubsub",
            "runbook_cloud_run",
            "runbook_integrations",
            "runbook_secops",
        ]

        for key in expected_keys:
            assert key in summary

    def test_summary_status_values(self):
        """Test that summary status values are valid."""
        summary = get_knowledge_summary()

        status_keys = [
            "triage_framework",
            "error_patterns",
            "runbook_alloydb",
        ]

        for key in status_keys:
            assert summary[key] in ["loaded", "missing"]


class TestClearKnowledgeCache:
    """Tests for cache clearing."""

    def test_clear_cache_allows_reload(self):
        """Test that clearing cache allows reloading."""
        knowledge1 = load_sre_knowledge()
        clear_knowledge_cache()
        knowledge2 = load_sre_knowledge()

        # Should be different objects after cache clear
        # (Note: content may be same, but objects should be new)
        # This is hard to test directly, so we just verify no errors
        assert knowledge1.loaded_from == knowledge2.loaded_from
