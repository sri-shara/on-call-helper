"""
Tests for Triage Agent.

Tests the Claude-based triage agent with mocked API responses.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from backend.agents.triage import TriageAgent, TriageError, triage_incident
from backend.models import Incident, TriageResult, TriageClassification, Severity

from tests.fixtures.sample_incidents import (
    create_null_pointer_incident,
    create_database_connection_incident,
    create_pubsub_backlog_incident,
    create_timeout_incident,
    create_json_parsing_incident,
    create_index_out_of_bounds_incident,
    SAMPLE_FIXABLE_RESPONSE,
    SAMPLE_INFRA_RESPONSE,
    SAMPLE_TRANSIENT_RESPONSE,
    SAMPLE_NEEDS_HUMAN_RESPONSE,
)


class TestTriageAgentInit:
    """Tests for TriageAgent initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default settings."""
        with patch("backend.agents.triage.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.triage_model = "claude-sonnet-4-20250514"

            agent = TriageAgent()

            assert agent.api_key == "test-key"
            assert agent.model == "claude-sonnet-4-20250514"

    def test_init_with_custom_values(self):
        """Test initialization with custom API key and model."""
        agent = TriageAgent(api_key="custom-key", model="claude-3-opus-20240229")

        assert agent.api_key == "custom-key"
        assert agent.model == "claude-3-opus-20240229"

    def test_init_without_api_key(self):
        """Test initialization without API key."""
        with patch("backend.agents.triage.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.triage_model = "claude-sonnet-4-20250514"

            agent = TriageAgent()

            assert agent.client is None


class TestFormatIncident:
    """Tests for incident formatting."""

    def test_format_basic_incident(self):
        """Test formatting a basic incident."""
        agent = TriageAgent(api_key="test")
        incident = Incident(
            id="OCH-TEST001",
            title="Test Error",
            error_message="Something went wrong",
            service_name="testservice",
            severity=Severity.HIGH,
        )

        formatted = agent._format_incident(incident)

        assert "OCH-TEST001" in formatted
        assert "Test Error" in formatted
        assert "Something went wrong" in formatted
        assert "testservice" in formatted
        assert "high" in formatted

    def test_format_incident_with_stack_trace(self):
        """Test formatting incident with stack trace."""
        agent = TriageAgent(api_key="test")
        incident = create_null_pointer_incident()

        formatted = agent._format_incident(incident)

        assert "Stack Trace" in formatted
        assert "handler.go:142" in formatted

    def test_format_incident_with_file_path(self):
        """Test formatting incident with file path."""
        agent = TriageAgent(api_key="test")
        incident = create_null_pointer_incident()

        formatted = agent._format_incident(incident)

        assert "File Path" in formatted
        assert "/backend/services/caseservice/handler.go" in formatted

    def test_format_incident_with_tenant(self):
        """Test formatting incident with tenant name."""
        agent = TriageAgent(api_key="test")
        incident = Incident(
            id="OCH-TEST001",
            title="Test Error",
            error_message="Error",
            service_name="testservice",
            severity=Severity.HIGH,
            tenant_name="horizontal-dc",
        )

        formatted = agent._format_incident(incident)

        assert "Tenant" in formatted
        assert "horizontal-dc" in formatted


class TestExtractJson:
    """Tests for JSON extraction from Claude responses."""

    def test_extract_json_from_code_block(self):
        """Test extracting JSON from markdown code block."""
        agent = TriageAgent(api_key="test")
        response = """Here's my analysis:

```json
{"classification": "FIXABLE", "confidence": 0.9}
```

Let me know if you need more details."""

        result = agent._extract_json(response)

        assert result["classification"] == "FIXABLE"
        assert result["confidence"] == 0.9

    def test_extract_json_from_code_block_no_lang(self):
        """Test extracting JSON from code block without language."""
        agent = TriageAgent(api_key="test")
        response = """Analysis:

```
{"classification": "INFRA_ISSUE", "confidence": 0.85}
```"""

        result = agent._extract_json(response)

        assert result["classification"] == "INFRA_ISSUE"

    def test_extract_raw_json(self):
        """Test extracting raw JSON without code block."""
        agent = TriageAgent(api_key="test")
        response = """{"classification": "TRANSIENT", "confidence": 0.95}"""

        result = agent._extract_json(response)

        assert result["classification"] == "TRANSIENT"

    def test_extract_json_with_text_around(self):
        """Test extracting JSON with surrounding text."""
        agent = TriageAgent(api_key="test")
        response = """Based on my analysis, here is the result:
{"classification": "NEEDS_HUMAN", "confidence": 0.6}
This requires further investigation."""

        result = agent._extract_json(response)

        assert result["classification"] == "NEEDS_HUMAN"

    def test_extract_json_invalid_raises_error(self):
        """Test that invalid JSON raises TriageError."""
        agent = TriageAgent(api_key="test")
        response = "No JSON here, just plain text analysis."

        with pytest.raises(TriageError) as exc_info:
            agent._extract_json(response)

        assert "No valid JSON found" in str(exc_info.value)


class TestParseClassification:
    """Tests for classification parsing."""

    def test_parse_fixable(self):
        """Test parsing FIXABLE classification."""
        agent = TriageAgent(api_key="test")

        assert agent._parse_classification("FIXABLE") == TriageClassification.FIXABLE
        assert agent._parse_classification("fixable") == TriageClassification.FIXABLE

    def test_parse_infra_issue_variants(self):
        """Test parsing INFRA_ISSUE classification variants."""
        agent = TriageAgent(api_key="test")

        assert agent._parse_classification("INFRA_ISSUE") == TriageClassification.INFRA_ISSUE
        assert agent._parse_classification("INFRA-ISSUE") == TriageClassification.INFRA_ISSUE
        assert agent._parse_classification("INFRA") == TriageClassification.INFRA_ISSUE
        assert agent._parse_classification("INFRASTRUCTURE") == TriageClassification.INFRA_ISSUE

    def test_parse_transient(self):
        """Test parsing TRANSIENT classification."""
        agent = TriageAgent(api_key="test")

        assert agent._parse_classification("TRANSIENT") == TriageClassification.TRANSIENT
        assert agent._parse_classification("transient") == TriageClassification.TRANSIENT

    def test_parse_needs_human_variants(self):
        """Test parsing NEEDS_HUMAN classification variants."""
        agent = TriageAgent(api_key="test")

        assert agent._parse_classification("NEEDS_HUMAN") == TriageClassification.NEEDS_HUMAN
        assert agent._parse_classification("NEEDS-HUMAN") == TriageClassification.NEEDS_HUMAN
        assert agent._parse_classification("HUMAN") == TriageClassification.NEEDS_HUMAN

    def test_parse_unknown_raises_error(self):
        """Test that unknown classification raises error."""
        agent = TriageAgent(api_key="test")

        with pytest.raises(TriageError) as exc_info:
            agent._parse_classification("UNKNOWN")

        assert "Unknown classification" in str(exc_info.value)


class TestParseLineNumbers:
    """Tests for line number parsing."""

    def test_parse_list_format(self):
        """Test parsing line numbers as list."""
        agent = TriageAgent(api_key="test")
        data = {"line_numbers": [142, 145]}

        result = agent._parse_line_numbers(data)

        assert result == (142, 145)

    def test_parse_tuple_format(self):
        """Test parsing line numbers as tuple."""
        agent = TriageAgent(api_key="test")
        data = {"line_numbers": (100, 110)}

        result = agent._parse_line_numbers(data)

        assert result == (100, 110)

    def test_parse_dict_format_start_end(self):
        """Test parsing line numbers as dict with start/end."""
        agent = TriageAgent(api_key="test")
        data = {"line_numbers": {"start": 50, "end": 60}}

        result = agent._parse_line_numbers(data)

        assert result == (50, 60)

    def test_parse_dict_format_from_to(self):
        """Test parsing line numbers as dict with from/to."""
        agent = TriageAgent(api_key="test")
        data = {"line_numbers": {"from": 20, "to": 30}}

        result = agent._parse_line_numbers(data)

        assert result == (20, 30)

    def test_parse_missing_returns_none(self):
        """Test missing line numbers returns None."""
        agent = TriageAgent(api_key="test")

        assert agent._parse_line_numbers({}) is None
        assert agent._parse_line_numbers({"line_numbers": None}) is None


class TestParseResponse:
    """Tests for full response parsing."""

    def test_parse_fixable_response(self):
        """Test parsing a FIXABLE classification response."""
        agent = TriageAgent(api_key="test")

        result = agent._parse_response(SAMPLE_FIXABLE_RESPONSE, "OCH-TEST001")

        assert result.incident_id == "OCH-TEST001"
        assert result.classification == TriageClassification.FIXABLE
        assert result.confidence == 0.85
        assert "Null pointer" in result.root_cause
        assert result.file_path == "/backend/services/caseservice/handler.go"
        assert result.function_name == "processCase"
        assert result.line_numbers == (142, 145)
        assert "nil check" in result.suggested_fix

    def test_parse_infra_response(self):
        """Test parsing an INFRA_ISSUE classification response."""
        agent = TriageAgent(api_key="test")

        result = agent._parse_response(SAMPLE_INFRA_RESPONSE, "OCH-TEST002")

        assert result.classification == TriageClassification.INFRA_ISSUE
        assert result.confidence == 0.92
        assert "AlloyDB" in result.root_cause
        assert result.runbook_reference == "runbooks/alloydb.md"
        assert len(result.manual_steps) == 4
        assert "pg_stat_activity" in result.manual_steps[0]

    def test_parse_transient_response(self):
        """Test parsing a TRANSIENT classification response."""
        agent = TriageAgent(api_key="test")

        result = agent._parse_response(SAMPLE_TRANSIENT_RESPONSE, "OCH-TEST003")

        assert result.classification == TriageClassification.TRANSIENT
        assert result.confidence == 0.95
        assert "transient" in result.root_cause.lower()

    def test_parse_needs_human_response(self):
        """Test parsing a NEEDS_HUMAN classification response."""
        agent = TriageAgent(api_key="test")

        result = agent._parse_response(SAMPLE_NEEDS_HUMAN_RESPONSE, "OCH-TEST004")

        assert result.classification == TriageClassification.NEEDS_HUMAN
        assert result.confidence == 0.6
        assert "inconsistency" in result.root_cause.lower()

    def test_parse_clamps_confidence(self):
        """Test that confidence is clamped to valid range."""
        agent = TriageAgent(api_key="test")

        # Test over 1.0
        response_high = '{"classification": "FIXABLE", "confidence": 1.5, "root_cause": "test"}'
        result = agent._parse_response(response_high, "OCH-TEST")
        assert result.confidence == 1.0

        # Test under 0.0
        response_low = '{"classification": "FIXABLE", "confidence": -0.5, "root_cause": "test"}'
        result = agent._parse_response(response_low, "OCH-TEST")
        assert result.confidence == 0.0


class TestAnalyze:
    """Tests for the analyze method with mocked API."""

    @pytest.fixture
    def mock_anthropic(self):
        """Create a mock Anthropic client."""
        with patch("backend.agents.triage.Anthropic") as mock:
            yield mock

    @pytest.mark.asyncio
    async def test_analyze_fixable_incident(self, mock_anthropic):
        """Test analyzing a fixable incident."""
        # Setup mock
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=SAMPLE_FIXABLE_RESPONSE)]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        agent = TriageAgent(api_key="test-key")
        agent.client = mock_client
        incident = create_null_pointer_incident()

        result = await agent.analyze(incident)

        assert result.classification == TriageClassification.FIXABLE
        assert result.confidence == 0.85
        mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_infra_incident(self, mock_anthropic):
        """Test analyzing an infrastructure incident."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=SAMPLE_INFRA_RESPONSE)]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        agent = TriageAgent(api_key="test-key")
        agent.client = mock_client
        incident = create_database_connection_incident()

        result = await agent.analyze(incident)

        assert result.classification == TriageClassification.INFRA_ISSUE
        assert result.runbook_reference == "runbooks/alloydb.md"

    @pytest.mark.asyncio
    async def test_analyze_without_client_raises_error(self):
        """Test that analyzing without client raises error."""
        with patch("backend.agents.triage.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.triage_model = "claude-sonnet-4-20250514"

            agent = TriageAgent()
            incident = create_null_pointer_incident()

            with pytest.raises(TriageError) as exc_info:
                await agent.analyze(incident)

            assert "not initialized" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_analyze_handles_api_error(self, mock_anthropic):
        """Test that API errors are handled gracefully."""
        from anthropic import APIError

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = APIError(
            message="Internal server error",
            request=MagicMock(),
            body=None,
        )
        mock_anthropic.return_value = mock_client

        agent = TriageAgent(api_key="test-key")
        agent.client = mock_client
        incident = create_null_pointer_incident()

        with pytest.raises(TriageError) as exc_info:
            await agent.analyze(incident)

        assert "API error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_analyze_handles_rate_limit(self, mock_anthropic):
        """Test that rate limits are handled gracefully."""
        from anthropic import RateLimitError

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RateLimitError(
            message="Rate limited",
            response=MagicMock(),
            body=None,
        )
        mock_anthropic.return_value = mock_client

        agent = TriageAgent(api_key="test-key")
        agent.client = mock_client
        incident = create_null_pointer_incident()

        with pytest.raises(TriageError) as exc_info:
            await agent.analyze(incident)

        assert "Rate limited" in str(exc_info.value)


class TestSystemPrompt:
    """Tests for system prompt generation."""

    def test_system_prompt_includes_knowledge(self):
        """Test that system prompt includes SRE knowledge."""
        with patch("backend.agents.triage.get_triage_system_prompt") as mock_prompt:
            mock_prompt.return_value = "Test prompt with SRE knowledge"

            agent = TriageAgent(api_key="test")

            assert agent.system_prompt == "Test prompt with SRE knowledge"
            mock_prompt.assert_called_once()

    def test_system_prompt_is_cached(self):
        """Test that system prompt is cached after first access."""
        with patch("backend.agents.triage.get_triage_system_prompt") as mock_prompt:
            mock_prompt.return_value = "Test prompt"

            agent = TriageAgent(api_key="test")

            # Access twice
            _ = agent.system_prompt
            _ = agent.system_prompt

            # Should only be called once due to caching
            mock_prompt.assert_called_once()


class TestTriageIncidentFunction:
    """Tests for the module-level triage_incident function."""

    @pytest.mark.asyncio
    async def test_triage_incident_creates_agent(self):
        """Test that triage_incident creates and uses an agent."""
        with patch("backend.agents.triage.TriageAgent") as mock_agent_class:
            mock_agent = MagicMock()
            mock_result = TriageResult(
                incident_id="OCH-TEST",
                classification=TriageClassification.FIXABLE,
                root_cause="Test",
                confidence=0.9,
            )
            mock_agent.analyze = MagicMock(return_value=mock_result)
            mock_agent_class.return_value = mock_agent

            incident = create_null_pointer_incident()

            # Need to make the mock async
            async def mock_analyze(inc):
                return mock_result

            mock_agent.analyze = mock_analyze

            result = await triage_incident(incident)

            assert result.classification == TriageClassification.FIXABLE
            mock_agent_class.assert_called_once()


class TestSampleIncidents:
    """Tests to verify sample incidents are properly formed."""

    def test_null_pointer_incident(self):
        """Test null pointer incident structure."""
        incident = create_null_pointer_incident()

        assert incident.id == "OCH-TEST001"
        assert "NullPointer" in incident.title
        assert incident.stack_trace is not None
        assert incident.file_path is not None

    def test_database_incident(self):
        """Test database incident structure."""
        incident = create_database_connection_incident()

        assert "AlloyDB" in incident.title
        assert incident.severity == Severity.CRITICAL

    def test_timeout_incident(self):
        """Test timeout incident structure."""
        incident = create_timeout_incident()

        assert "deadline" in incident.error_message.lower()
        assert incident.severity == Severity.MEDIUM

    def test_json_parsing_incident(self):
        """Test JSON parsing incident structure."""
        incident = create_json_parsing_incident()

        assert "unmarshal" in incident.error_message
        assert incident.file_path is not None

    def test_index_out_of_bounds_incident(self):
        """Test index out of bounds incident structure."""
        incident = create_index_out_of_bounds_incident()

        assert "index out of range" in incident.error_message
