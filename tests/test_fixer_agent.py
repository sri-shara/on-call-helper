"""
Tests for Fixer Agent.

Tests the Claude-based code fix generator with mocked API responses.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.fixer import FixerAgent, FixerError, generate_fix
from backend.models import TriageResult, FixResult, TriageClassification

from tests.fixtures.sample_triage_results import (
    create_nil_pointer_triage,
    create_index_out_of_bounds_triage,
    create_json_unmarshal_triage,
    create_missing_error_handling_triage,
    create_infra_issue_triage,
    create_needs_human_triage,
    SAMPLE_HANDLER_GO,
    SAMPLE_PROCESSOR_GO,
    SAMPLE_REPOSITORY_GO,
    SAMPLE_NIL_CHECK_FIX_RESPONSE,
    SAMPLE_BOUNDS_CHECK_FIX_RESPONSE,
    SAMPLE_ERROR_HANDLING_FIX_RESPONSE,
    SAMPLE_FIX_WITH_RETRY_RESPONSE,
)


class TestFixerAgentInit:
    """Tests for FixerAgent initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default settings."""
        with patch("backend.agents.fixer.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.fixer_model = "claude-sonnet-4-20250514"

            agent = FixerAgent()

            assert agent.api_key == "test-key"
            assert agent.model == "claude-sonnet-4-20250514"

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        mock_github = MagicMock()

        agent = FixerAgent(
            github_service=mock_github,
            api_key="custom-key",
            model="claude-3-opus-20240229",
        )

        assert agent.api_key == "custom-key"
        assert agent.model == "claude-3-opus-20240229"
        assert agent.github == mock_github

    def test_init_without_api_key(self):
        """Test initialization without API key."""
        with patch("backend.agents.fixer.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.fixer_model = "claude-sonnet-4-20250514"

            agent = FixerAgent()

            assert agent.client is None


class TestFormatTriage:
    """Tests for triage formatting."""

    def test_format_basic_triage(self):
        """Test formatting a basic triage result."""
        agent = FixerAgent(api_key="test")
        triage = create_nil_pointer_triage()

        formatted = agent._format_triage(triage, SAMPLE_HANDLER_GO)

        assert "Nil pointer dereference" in formatted
        assert "backend/services/caseservice/handler.go" in formatted
        assert "processCase" in formatted
        assert "0.85" in formatted or "85%" in formatted
        assert SAMPLE_HANDLER_GO in formatted

    def test_format_triage_with_code_snippet(self):
        """Test formatting includes code snippet."""
        agent = FixerAgent(api_key="test")
        triage = create_nil_pointer_triage()

        formatted = agent._format_triage(triage, SAMPLE_HANDLER_GO)

        assert "Problematic Code Snippet" in formatted
        assert "nil check missing" in formatted

    def test_format_triage_with_suggested_fix(self):
        """Test formatting includes suggested fix."""
        agent = FixerAgent(api_key="test")
        triage = create_nil_pointer_triage()

        formatted = agent._format_triage(triage, SAMPLE_HANDLER_GO)

        assert "Suggested Fix Approach" in formatted
        assert "nil check" in formatted.lower()


class TestFormatRetryPrompt:
    """Tests for retry prompt formatting."""

    def test_format_retry_with_feedback(self):
        """Test formatting retry prompt with CodeRabbit feedback."""
        agent = FixerAgent(api_key="test")
        triage = create_nil_pointer_triage()
        previous_fix = FixResult(
            incident_id="OCH-TEST001",
            file_path="backend/services/caseservice/handler.go",
            original_code="func processCase(c *Case) error {",
            fixed_code="func processCase(c *Case) error { if c == nil { return nil }",
            explanation="Added nil check",
            diff_summary="Added nil check",
            iteration=1,
        )
        feedback = "Error message should include context about which case was nil"

        formatted = agent._format_retry_prompt(
            triage, SAMPLE_HANDLER_GO, previous_fix, feedback
        )

        assert "Previous Attempt" in formatted
        assert "Iteration 1" in formatted
        assert "CodeRabbit Feedback" in formatted
        assert "context about which case" in formatted


class TestExtractJson:
    """Tests for JSON extraction."""

    def test_extract_json_from_code_block(self):
        """Test extracting JSON from markdown code block."""
        agent = FixerAgent(api_key="test")
        response = """Here's the fix:

```json
{"file_path": "test.go", "original_code": "a", "fixed_code": "b", "explanation": "c"}
```
"""
        result = agent._extract_json(response)

        assert result["file_path"] == "test.go"
        assert result["fixed_code"] == "b"

    def test_extract_raw_json(self):
        """Test extracting raw JSON."""
        agent = FixerAgent(api_key="test")
        response = '{"file_path": "test.go", "original_code": "a", "fixed_code": "b", "explanation": "c"}'

        result = agent._extract_json(response)

        assert result["file_path"] == "test.go"

    def test_extract_json_invalid_raises_error(self):
        """Test invalid JSON raises FixerError."""
        agent = FixerAgent(api_key="test")
        response = "No JSON here, just text."

        with pytest.raises(FixerError) as exc_info:
            agent._extract_json(response)

        assert "No valid JSON found" in str(exc_info.value)


class TestParseResponse:
    """Tests for response parsing."""

    def test_parse_nil_check_fix(self):
        """Test parsing a nil check fix response."""
        agent = FixerAgent(api_key="test")

        result = agent._parse_response(
            SAMPLE_NIL_CHECK_FIX_RESPONSE, "OCH-TEST001", 1
        )

        assert result.incident_id == "OCH-TEST001"
        assert result.file_path == "backend/services/caseservice/handler.go"
        assert "nil" in result.fixed_code.lower()
        assert "nil check" in result.explanation.lower()
        assert result.iteration == 1

    def test_parse_bounds_check_fix(self):
        """Test parsing a bounds check fix response."""
        agent = FixerAgent(api_key="test")

        result = agent._parse_response(
            SAMPLE_BOUNDS_CHECK_FIX_RESPONSE, "OCH-TEST002", 1
        )

        assert "len(alerts)" in result.fixed_code
        assert "ErrNoAlerts" in result.fixed_code

    def test_parse_error_handling_fix(self):
        """Test parsing an error handling fix response."""
        agent = FixerAgent(api_key="test")

        result = agent._parse_response(
            SAMPLE_ERROR_HANDLING_FIX_RESPONSE, "OCH-TEST003", 1
        )

        assert "err :=" in result.fixed_code
        assert "return nil, err" in result.fixed_code

    def test_parse_missing_field_raises_error(self):
        """Test missing required field raises error."""
        agent = FixerAgent(api_key="test")
        response = '{"file_path": "test.go", "original_code": "a"}'

        with pytest.raises(FixerError) as exc_info:
            agent._parse_response(response, "OCH-TEST", 1)

        assert "Missing required field" in str(exc_info.value)

    def test_parse_tracks_iteration(self):
        """Test that iteration number is tracked."""
        agent = FixerAgent(api_key="test")

        result = agent._parse_response(
            SAMPLE_NIL_CHECK_FIX_RESPONSE, "OCH-TEST001", 2
        )

        assert result.iteration == 2


class TestValidateFix:
    """Tests for fix validation."""

    def test_validate_fix_success(self):
        """Test validation passes for valid fix."""
        agent = FixerAgent(api_key="test")
        fix = FixResult(
            incident_id="test",
            file_path="test.go",
            original_code="func processCase(c *Case) error {\n    result := c.GetStatus()\n    if result == \"\" {\n        return errors.New(\"empty status\")\n    }\n    return nil\n}",
            fixed_code="func processCase(c *Case) error {\n    if c == nil {\n        return ErrNilCase\n    }\n    result := c.GetStatus()\n    if result == \"\" {\n        return errors.New(\"empty status\")\n    }\n    return nil\n}",
            explanation="Added nil check",
            diff_summary="Added nil check",
            iteration=1,
        )

        # Should not raise
        agent._validate_fix(fix, SAMPLE_HANDLER_GO)

    def test_validate_fix_original_not_found(self):
        """Test validation fails if original code not in source."""
        agent = FixerAgent(api_key="test")
        fix = FixResult(
            incident_id="test",
            file_path="test.go",
            original_code="this code does not exist",
            fixed_code="fixed code",
            explanation="explanation",
            diff_summary="summary",
            iteration=1,
        )

        with pytest.raises(FixerError) as exc_info:
            agent._validate_fix(fix, SAMPLE_HANDLER_GO)

        assert "not found in source" in str(exc_info.value)

    def test_validate_fix_identical_code(self):
        """Test validation fails if fixed code is same as original."""
        agent = FixerAgent(api_key="test")
        fix = FixResult(
            incident_id="test",
            file_path="test.go",
            original_code="func test() {}",
            fixed_code="func test() {}",
            explanation="explanation",
            diff_summary="summary",
            iteration=1,
        )

        with pytest.raises(FixerError) as exc_info:
            agent._validate_fix(fix, "func test() {}")

        assert "identical" in str(exc_info.value)

    def test_validate_fix_unbalanced_braces(self):
        """Test validation fails for unbalanced braces."""
        agent = FixerAgent(api_key="test")
        fix = FixResult(
            incident_id="test",
            file_path="test.go",
            original_code="func test()",
            fixed_code="func test() { if true {",  # Missing closing braces
            explanation="explanation",
            diff_summary="summary",
            iteration=1,
        )

        with pytest.raises(FixerError) as exc_info:
            agent._validate_fix(fix, "func test()")

        assert "Unbalanced braces" in str(exc_info.value)


class TestGenerateFix:
    """Tests for the generate_fix method."""

    @pytest.fixture
    def mock_github(self):
        """Create a mock GitHub service."""
        mock = AsyncMock()
        mock.get_file_content.return_value = SAMPLE_HANDLER_GO
        mock.close = AsyncMock()
        return mock

    @pytest.fixture
    def mock_anthropic(self):
        """Create a mock Anthropic client."""
        with patch("backend.agents.fixer.Anthropic") as mock:
            yield mock

    @pytest.mark.asyncio
    async def test_generate_fix_success(self, mock_github, mock_anthropic):
        """Test successful fix generation."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=SAMPLE_NIL_CHECK_FIX_RESPONSE)]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        agent = FixerAgent(github_service=mock_github, api_key="test-key")
        agent.client = mock_client
        triage = create_nil_pointer_triage()

        result = await agent.generate_fix(triage)

        assert result.incident_id == "OCH-TEST001"
        assert "nil" in result.fixed_code.lower()
        assert result.iteration == 1
        mock_github.get_file_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_fix_with_retry(self, mock_github, mock_anthropic):
        """Test fix generation with retry and feedback."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=SAMPLE_FIX_WITH_RETRY_RESPONSE)]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        agent = FixerAgent(github_service=mock_github, api_key="test-key")
        agent.client = mock_client
        triage = create_nil_pointer_triage()
        previous_fix = FixResult(
            incident_id="OCH-TEST001",
            file_path="backend/services/caseservice/handler.go",
            original_code="old",
            fixed_code="func processCase(c *Case) error { if c == nil { return nil }",
            explanation="First attempt",
            diff_summary="Added nil check",
            iteration=1,
        )

        result = await agent.generate_fix(
            triage,
            coderabbit_feedback="Add context to error message",
            previous_fix=previous_fix,
        )

        assert result.iteration == 2
        # Check that prompt includes feedback
        call_args = mock_client.messages.create.call_args
        user_content = call_args[1]["messages"][0]["content"]
        assert "Previous Attempt" in user_content or "CodeRabbit" in user_content

    @pytest.mark.asyncio
    async def test_generate_fix_non_fixable_raises_error(self, mock_github):
        """Test that non-fixable triage raises error."""
        agent = FixerAgent(github_service=mock_github, api_key="test-key")
        agent.client = MagicMock()
        triage = create_infra_issue_triage()

        with pytest.raises(FixerError) as exc_info:
            await agent.generate_fix(triage)

        assert "non-fixable classification" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_fix_missing_file_path_raises_error(self, mock_github):
        """Test that missing file_path raises error."""
        agent = FixerAgent(github_service=mock_github, api_key="test-key")
        agent.client = MagicMock()
        triage = TriageResult(
            incident_id="OCH-TEST",
            classification=TriageClassification.FIXABLE,
            root_cause="Test bug",
            confidence=0.8,
            file_path=None,  # Missing
        )

        with pytest.raises(FixerError) as exc_info:
            await agent.generate_fix(triage)

        assert "missing file_path" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_fix_without_client_raises_error(self, mock_github):
        """Test that missing client raises error."""
        with patch("backend.agents.fixer.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.fixer_model = "claude-sonnet-4-20250514"

            agent = FixerAgent(github_service=mock_github)
            triage = create_nil_pointer_triage()

            with pytest.raises(FixerError) as exc_info:
                await agent.generate_fix(triage)

            assert "not initialized" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_fix_github_error(self, mock_anthropic):
        """Test handling of GitHub errors."""
        from backend.services.github import GitHubError

        mock_github = AsyncMock()
        mock_github.get_file_content.side_effect = GitHubError("GitHub error")
        mock_github.close = AsyncMock()

        agent = FixerAgent(github_service=mock_github, api_key="test-key")
        agent.client = MagicMock()
        triage = create_nil_pointer_triage()

        with pytest.raises(FixerError) as exc_info:
            await agent.generate_fix(triage)

        assert "fetch source code" in str(exc_info.value) or "GitHub error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_fix_file_not_found(self, mock_anthropic):
        """Test handling of file not found."""
        mock_github = AsyncMock()
        mock_github.get_file_content.return_value = None
        mock_github.close = AsyncMock()

        agent = FixerAgent(github_service=mock_github, api_key="test-key")
        agent.client = MagicMock()
        triage = create_nil_pointer_triage()

        with pytest.raises(FixerError) as exc_info:
            await agent.generate_fix(triage)

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_iteration_caps_at_max(self, mock_github, mock_anthropic):
        """Test that iteration doesn't exceed MAX_ITERATIONS."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=SAMPLE_NIL_CHECK_FIX_RESPONSE)]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        agent = FixerAgent(github_service=mock_github, api_key="test-key")
        agent.client = mock_client
        triage = create_nil_pointer_triage()
        previous_fix = FixResult(
            incident_id="OCH-TEST001",
            file_path="test.go",
            original_code="a",
            fixed_code="b",
            explanation="c",
            diff_summary="d",
            iteration=3,  # Already at max
        )

        result = await agent.generate_fix(
            triage,
            coderabbit_feedback="feedback",
            previous_fix=previous_fix,
        )

        # Should stay at max (3)
        assert result.iteration == 3


class TestGenerateFixFunction:
    """Tests for the module-level generate_fix function."""

    @pytest.mark.asyncio
    async def test_generate_fix_function(self):
        """Test the convenience function creates agent."""
        with patch("backend.agents.fixer.FixerAgent") as mock_agent_class:
            mock_agent = AsyncMock()
            mock_result = FixResult(
                incident_id="OCH-TEST",
                file_path="test.go",
                original_code="a",
                fixed_code="b",
                explanation="c",
                diff_summary="d",
                iteration=1,
            )
            mock_agent.generate_fix.return_value = mock_result
            mock_agent_class.return_value = mock_agent

            triage = create_nil_pointer_triage()
            result = await generate_fix(triage)

            assert result.file_path == "test.go"
            mock_agent_class.assert_called_once()


class TestSampleTriageResults:
    """Tests to verify sample triage results are properly formed."""

    def test_nil_pointer_triage(self):
        """Test nil pointer triage structure."""
        triage = create_nil_pointer_triage()

        assert triage.classification == TriageClassification.FIXABLE
        assert triage.file_path is not None
        assert triage.function_name == "processCase"
        assert triage.confidence > 0

    def test_index_out_of_bounds_triage(self):
        """Test index out of bounds triage structure."""
        triage = create_index_out_of_bounds_triage()

        assert triage.classification == TriageClassification.FIXABLE
        assert "index" in triage.root_cause.lower()

    def test_infra_issue_triage(self):
        """Test infrastructure issue triage structure."""
        triage = create_infra_issue_triage()

        assert triage.classification == TriageClassification.INFRA_ISSUE
        assert triage.runbook_reference is not None
        assert len(triage.manual_steps) > 0

    def test_needs_human_triage(self):
        """Test needs human triage structure."""
        triage = create_needs_human_triage()

        assert triage.classification == TriageClassification.NEEDS_HUMAN
        assert triage.confidence < 0.7  # Lower confidence for unclear issues
