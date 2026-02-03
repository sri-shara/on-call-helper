"""
Tests for AI Client Factory.

Tests the create_ai_client factory and model name getters with mocked settings.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock


class TestCreateAiClient:
    """Tests for create_ai_client factory function."""

    def test_creates_anthropic_client_by_default(self):
        """Test that Anthropic client is created when USE_VERTEX=false."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = False
            mock_settings.anthropic_api_key = "sk-ant-test-key"

            with patch("backend.ai_client.Anthropic") as mock_anthropic:
                from backend.ai_client import create_ai_client

                client = create_ai_client()

                mock_anthropic.assert_called_once_with(api_key="sk-ant-test-key")
                assert client == mock_anthropic.return_value

    def test_creates_vertex_client_when_enabled(self):
        """Test that AnthropicVertex client is created when USE_VERTEX=true."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = True
            mock_settings.vertex_project_id = "anthropic-vertex"
            mock_settings.vertex_region = "us-east5"

            with patch("backend.ai_client.AnthropicVertex") as mock_vertex:
                from backend.ai_client import create_ai_client

                client = create_ai_client()

                mock_vertex.assert_called_once_with(
                    project_id="anthropic-vertex",
                    region="us-east5",
                )
                assert client == mock_vertex.return_value

    def test_returns_none_when_anthropic_key_missing(self):
        """Test that None is returned when Anthropic API key is not set."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = False
            mock_settings.anthropic_api_key = ""

            from backend.ai_client import create_ai_client

            client = create_ai_client()

            assert client is None

    def test_returns_none_when_vertex_project_missing(self):
        """Test that None is returned when Vertex project ID is not set."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = True
            mock_settings.vertex_project_id = ""

            from backend.ai_client import create_ai_client

            client = create_ai_client()

            assert client is None

    def test_api_key_override_for_anthropic(self):
        """Test that api_key parameter overrides settings for Anthropic."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = False
            mock_settings.anthropic_api_key = "default-key"

            with patch("backend.ai_client.Anthropic") as mock_anthropic:
                from backend.ai_client import create_ai_client

                client = create_ai_client(api_key="override-key")

                mock_anthropic.assert_called_once_with(api_key="override-key")

    def test_api_key_override_ignored_for_vertex(self):
        """Test that api_key parameter is ignored when using Vertex AI."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = True
            mock_settings.vertex_project_id = "test-project"
            mock_settings.vertex_region = "us-east5"

            with patch("backend.ai_client.AnthropicVertex") as mock_vertex:
                from backend.ai_client import create_ai_client

                # api_key should be ignored for Vertex
                client = create_ai_client(api_key="ignored-key")

                mock_vertex.assert_called_once_with(
                    project_id="test-project",
                    region="us-east5",
                )


class TestGetTriageModel:
    """Tests for get_triage_model function."""

    def test_returns_anthropic_model_by_default(self):
        """Test that Anthropic model is returned when USE_VERTEX=false."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = False
            mock_settings.triage_model = "claude-sonnet-4-20250514"

            from backend.ai_client import get_triage_model

            assert get_triage_model() == "claude-sonnet-4-20250514"

    def test_returns_vertex_model_when_enabled(self):
        """Test that Vertex model is returned when USE_VERTEX=true."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = True
            mock_settings.vertex_triage_model = "claude-sonnet-4-5@20250929"

            from backend.ai_client import get_triage_model

            assert get_triage_model() == "claude-sonnet-4-5@20250929"


class TestGetFixerModel:
    """Tests for get_fixer_model function."""

    def test_returns_anthropic_model_by_default(self):
        """Test that Anthropic model is returned when USE_VERTEX=false."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = False
            mock_settings.fixer_model = "claude-sonnet-4-20250514"

            from backend.ai_client import get_fixer_model

            assert get_fixer_model() == "claude-sonnet-4-20250514"

    def test_returns_vertex_model_when_enabled(self):
        """Test that Vertex model is returned when USE_VERTEX=true."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = True
            mock_settings.vertex_fixer_model = "claude-sonnet-4-5@20250929"

            from backend.ai_client import get_fixer_model

            assert get_fixer_model() == "claude-sonnet-4-5@20250929"


class TestGetBackendName:
    """Tests for get_backend_name function."""

    def test_returns_anthropic_api_by_default(self):
        """Test that 'Anthropic API' is returned when USE_VERTEX=false."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = False

            from backend.ai_client import get_backend_name

            assert get_backend_name() == "Anthropic API"

    def test_returns_vertex_info_when_enabled(self):
        """Test that Vertex AI info is returned when USE_VERTEX=true."""
        with patch("backend.ai_client.settings") as mock_settings:
            mock_settings.use_vertex = True
            mock_settings.vertex_project_id = "my-project"
            mock_settings.vertex_region = "us-east5"

            from backend.ai_client import get_backend_name

            assert get_backend_name() == "Vertex AI (my-project/us-east5)"
