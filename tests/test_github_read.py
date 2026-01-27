"""
Tests for GitHub Service (Read Operations).

Tests the GitHub API client for reading repository files.
"""

import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from backend.services.github import (
    GitHubService,
    GitHubError,
    GitHubRateLimitError,
    GitHubAuthError,
    get_nucleus_file,
)


class TestGitHubServiceInit:
    """Tests for GitHubService initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default settings."""
        with patch("backend.services.github.settings") as mock_settings:
            mock_settings.github_token = "default-token"
            mock_settings.github_repo = "owner/repo"

            service = GitHubService()

            assert service.token == "default-token"
            assert service.repo == "owner/repo"

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        service = GitHubService(
            token="custom-token",
            repo="custom/repo",
            timeout=60.0,
        )

        assert service.token == "custom-token"
        assert service.repo == "custom/repo"
        assert service.timeout == 60.0

    def test_headers_with_token(self):
        """Test headers include authorization when token is set."""
        service = GitHubService(token="test-token")

        headers = service.headers

        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-token"
        assert "Accept" in headers

    def test_headers_without_token(self):
        """Test headers without authorization when no token."""
        service = GitHubService(token="")

        headers = service.headers

        assert "Authorization" not in headers
        assert "Accept" in headers


class TestGetFileContent:
    """Tests for get_file_content method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = GitHubService(token="test-token", repo="test/repo")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_get_file_content_success(self, service, mock_client):
        """Test successfully fetching file content."""
        content = "package main\n\nfunc main() {}\n"
        encoded = base64.b64encode(content.encode()).decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": encoded,
        }
        mock_client.get.return_value = mock_response

        result = await service.get_file_content("backend/main.go")

        assert result == content
        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert "test/repo" in call_args[0][0]
        assert "backend/main.go" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_file_content_with_ref(self, service, mock_client):
        """Test fetching file content with specific ref."""
        content = "// v1.0 code"
        encoded = base64.b64encode(content.encode()).decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": encoded,
        }
        mock_client.get.return_value = mock_response

        result = await service.get_file_content("main.go", ref="v1.0.0")

        assert result == content
        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["ref"] == "v1.0.0"

    @pytest.mark.asyncio
    async def test_get_file_content_not_found(self, service, mock_client):
        """Test file not found returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response

        result = await service.get_file_content("nonexistent.go")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_file_content_directory(self, service, mock_client):
        """Test that directories return None."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "type": "dir",
        }
        mock_client.get.return_value = mock_response

        result = await service.get_file_content("backend/")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_file_content_strips_leading_slash(self, service, mock_client):
        """Test that leading slashes are stripped from path."""
        content = "test"
        encoded = base64.b64encode(content.encode()).decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": encoded,
        }
        mock_client.get.return_value = mock_response

        await service.get_file_content("/backend/main.go")

        call_args = mock_client.get.call_args
        # Should not have double slashes
        assert "//" not in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_file_content_no_repo_raises_error(self):
        """Test that missing repo raises error."""
        service = GitHubService(token="test", repo="")

        with pytest.raises(GitHubError) as exc_info:
            await service.get_file_content("main.go")

        assert "No repository specified" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_file_content_repo_override(self, service, mock_client):
        """Test fetching from a different repo."""
        content = "other repo content"
        encoded = base64.b64encode(content.encode()).decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": encoded,
        }
        mock_client.get.return_value = mock_response

        result = await service.get_file_content(
            "README.md",
            repo="other/repo"
        )

        assert result == content
        call_args = mock_client.get.call_args
        assert "other/repo" in call_args[0][0]


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = GitHubService(token="test-token", repo="test/repo")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_auth_error_401(self, service, mock_client):
        """Test 401 raises GitHubAuthError."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client.get.return_value = mock_response

        with pytest.raises(GitHubAuthError) as exc_info:
            await service.get_file_content("main.go")

        assert "Invalid or missing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_rate_limit_403(self, service, mock_client):
        """Test rate limit 403 raises GitHubRateLimitError."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1234567890",
        }
        mock_client.get.return_value = mock_response

        with pytest.raises(GitHubRateLimitError) as exc_info:
            await service.get_file_content("main.go")

        assert exc_info.value.reset_at == 1234567890

    @pytest.mark.asyncio
    async def test_forbidden_403_not_rate_limit(self, service, mock_client):
        """Test 403 without rate limit raises GitHubAuthError."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {}
        mock_client.get.return_value = mock_response

        with pytest.raises(GitHubAuthError) as exc_info:
            await service.get_file_content("main.go")

        assert "Access forbidden" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_server_error_500(self, service, mock_client):
        """Test 500 raises GitHubError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.headers = {}
        mock_client.get.return_value = mock_response

        with pytest.raises(GitHubError) as exc_info:
            await service.get_file_content("main.go")

        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_timeout_error(self, service, mock_client):
        """Test timeout raises GitHubError."""
        mock_client.get.side_effect = httpx.TimeoutException("Timeout")

        with pytest.raises(GitHubError) as exc_info:
            await service.get_file_content("main.go")

        assert "Timeout" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_error(self, service, mock_client):
        """Test connection error raises GitHubError."""
        mock_client.get.side_effect = httpx.RequestError("Connection failed")

        with pytest.raises(GitHubError) as exc_info:
            await service.get_file_content("main.go")

        assert "Request error" in str(exc_info.value)


class TestGetFileInfo:
    """Tests for get_file_info method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = GitHubService(token="test-token", repo="test/repo")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_get_file_info_success(self, service, mock_client):
        """Test getting file info."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "main.go",
            "path": "backend/main.go",
            "sha": "abc123",
            "size": 1234,
            "type": "file",
            "html_url": "https://github.com/test/repo/blob/main/backend/main.go",
        }
        mock_client.get.return_value = mock_response

        result = await service.get_file_info("backend/main.go")

        assert result["name"] == "main.go"
        assert result["path"] == "backend/main.go"
        assert result["sha"] == "abc123"
        assert result["size"] == 1234
        assert result["type"] == "file"

    @pytest.mark.asyncio
    async def test_get_file_info_not_found(self, service, mock_client):
        """Test file info not found returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response

        result = await service.get_file_info("nonexistent.go")

        assert result is None


class TestListDirectory:
    """Tests for list_directory method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = GitHubService(token="test-token", repo="test/repo")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_list_directory_success(self, service, mock_client):
        """Test listing directory contents."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"name": "main.go", "path": "backend/main.go", "type": "file", "size": 100, "sha": "abc"},
            {"name": "config", "path": "backend/config", "type": "dir", "size": 0, "sha": "def"},
        ]
        mock_client.get.return_value = mock_response

        result = await service.list_directory("backend")

        assert len(result) == 2
        assert result[0]["name"] == "main.go"
        assert result[0]["type"] == "file"
        assert result[1]["name"] == "config"
        assert result[1]["type"] == "dir"

    @pytest.mark.asyncio
    async def test_list_directory_not_found(self, service, mock_client):
        """Test directory not found returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response

        result = await service.list_directory("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_list_root_directory(self, service, mock_client):
        """Test listing root directory."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"name": "README.md", "path": "README.md", "type": "file", "size": 500, "sha": "abc"},
        ]
        mock_client.get.return_value = mock_response

        result = await service.list_directory("")

        assert len(result) == 1
        assert result[0]["name"] == "README.md"


class TestGetDefaultBranch:
    """Tests for get_default_branch method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = GitHubService(token="test-token", repo="test/repo")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_get_default_branch(self, service, mock_client):
        """Test getting default branch."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "default_branch": "main",
        }
        mock_client.get.return_value = mock_response

        result = await service.get_default_branch()

        assert result == "main"

    @pytest.mark.asyncio
    async def test_get_default_branch_master(self, service, mock_client):
        """Test getting master as default branch."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "default_branch": "master",
        }
        mock_client.get.return_value = mock_response

        result = await service.get_default_branch()

        assert result == "master"


class TestCheckRateLimit:
    """Tests for check_rate_limit method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = GitHubService(token="test-token", repo="test/repo")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_check_rate_limit(self, service, mock_client):
        """Test checking rate limit status."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "resources": {
                "core": {
                    "limit": 5000,
                    "remaining": 4999,
                    "reset": 1234567890,
                    "used": 1,
                }
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.check_rate_limit()

        assert result["limit"] == 5000
        assert result["remaining"] == 4999
        assert result["reset"] == 1234567890
        assert result["used"] == 1


class TestClientManagement:
    """Tests for HTTP client management."""

    @pytest.mark.asyncio
    async def test_client_created_lazily(self):
        """Test that client is created on first request."""
        service = GitHubService(token="test", repo="test/repo")

        assert service._client is None

        # Mock the actual HTTP call
        with patch.object(httpx.AsyncClient, "get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            await service.get_file_content("test.go")

        assert service._client is not None
        await service.close()

    @pytest.mark.asyncio
    async def test_close_client(self):
        """Test closing the client."""
        service = GitHubService(token="test", repo="test/repo")

        # Create a mock client
        mock_client = AsyncMock()
        mock_client.is_closed = False
        service._client = mock_client

        await service.close()

        mock_client.aclose.assert_called_once()
        assert service._client is None


class TestGetNucleusFile:
    """Tests for the convenience function."""

    @pytest.mark.asyncio
    async def test_get_nucleus_file(self):
        """Test get_nucleus_file convenience function."""
        with patch("backend.services.github.GitHubService") as mock_class:
            mock_service = AsyncMock()
            mock_service.get_file_content.return_value = "file content"
            mock_class.return_value = mock_service

            result = await get_nucleus_file("backend/main.go")

            assert result == "file content"
            mock_service.get_file_content.assert_called_once_with(
                "backend/main.go", ref=None
            )
            mock_service.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_nucleus_file_with_ref(self):
        """Test get_nucleus_file with specific ref."""
        with patch("backend.services.github.GitHubService") as mock_class:
            mock_service = AsyncMock()
            mock_service.get_file_content.return_value = "old content"
            mock_class.return_value = mock_service

            result = await get_nucleus_file("main.go", ref="v1.0.0")

            assert result == "old content"
            mock_service.get_file_content.assert_called_once_with(
                "main.go", ref="v1.0.0"
            )


class TestBase64Decoding:
    """Tests for base64 content decoding."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = GitHubService(token="test-token", repo="test/repo")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_decode_with_newlines(self, service, mock_client):
        """Test decoding base64 content that has newlines."""
        content = "Hello, World!"
        # GitHub returns base64 with newlines every 60 chars
        encoded = base64.b64encode(content.encode()).decode()
        encoded_with_newlines = "\n".join(
            encoded[i:i+10] for i in range(0, len(encoded), 10)
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": encoded_with_newlines,
        }
        mock_client.get.return_value = mock_response

        result = await service.get_file_content("test.txt")

        assert result == content

    @pytest.mark.asyncio
    async def test_decode_unicode_content(self, service, mock_client):
        """Test decoding unicode content."""
        content = "Hello, 世界! 🌍"
        encoded = base64.b64encode(content.encode()).decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": encoded,
        }
        mock_client.get.return_value = mock_response

        result = await service.get_file_content("unicode.txt")

        assert result == content

    @pytest.mark.asyncio
    async def test_unsupported_encoding_raises_error(self, service, mock_client):
        """Test that unsupported encoding raises error."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "type": "file",
            "encoding": "utf-16",
            "content": "some content",
        }
        mock_client.get.return_value = mock_response

        with pytest.raises(GitHubError) as exc_info:
            await service.get_file_content("test.txt")

        assert "Unsupported encoding" in str(exc_info.value)
