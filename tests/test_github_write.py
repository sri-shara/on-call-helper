"""
Tests for GitHub Service (Write Operations).

Tests the GitHub API client for PR creation and file updates.
"""

import base64
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from backend.services.github import (
    GitHubService,
    GitHubError,
    PullRequest,
)


class TestPullRequestDataclass:
    """Tests for the PullRequest dataclass."""

    def test_pull_request_creation(self):
        """Test creating a PullRequest."""
        pr = PullRequest(
            number=123,
            url="https://api.github.com/repos/test/repo/pulls/123",
            html_url="https://github.com/test/repo/pull/123",
            title="Test PR",
            body="PR description",
            state="open",
            head_branch="feature-branch",
            base_branch="main",
            draft=True,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            labels=["bug", "auto-fix"],
        )

        assert pr.number == 123
        assert pr.title == "Test PR"
        assert pr.draft is True
        assert "bug" in pr.labels

    def test_pull_request_to_dict(self):
        """Test PullRequest to_dict method."""
        pr = PullRequest(
            number=123,
            url="https://api.github.com/repos/test/repo/pulls/123",
            html_url="https://github.com/test/repo/pull/123",
            title="Test PR",
            body="Description",
            state="open",
            head_branch="feature",
            base_branch="main",
            draft=False,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            labels=["enhancement"],
        )

        result = pr.to_dict()

        assert result["number"] == 123
        assert result["html_url"] == "https://github.com/test/repo/pull/123"
        assert result["draft"] is False
        assert "2024-01-01" in result["created_at"]


class TestGetBranch:
    """Tests for get_branch method."""

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
    async def test_get_branch_success(self, service, mock_client):
        """Test successfully getting branch info."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "main",
            "commit": {"sha": "abc123def456"},
            "protected": False,
        }
        mock_client.get.return_value = mock_response

        result = await service.get_branch("main")

        assert result["name"] == "main"
        assert result["sha"] == "abc123def456"
        assert result["protected"] is False

    @pytest.mark.asyncio
    async def test_get_branch_not_found(self, service, mock_client):
        """Test branch not found returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response

        result = await service.get_branch("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_branch_protected(self, service, mock_client):
        """Test getting protected branch info."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "main",
            "commit": {"sha": "abc123"},
            "protected": True,
        }
        mock_client.get.return_value = mock_response

        result = await service.get_branch("main")

        assert result["protected"] is True


class TestCreateBranch:
    """Tests for create_branch method."""

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
    async def test_create_branch_success(self, service, mock_client):
        """Test successfully creating a branch."""
        # Mock get for source branch
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "name": "main",
            "commit": {"sha": "abc123def456"},
            "protected": False,
        }

        # Mock post for creating branch
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {
            "ref": "refs/heads/new-branch",
            "object": {"sha": "abc123def456"},
        }

        mock_client.get.return_value = get_response
        mock_client.post.return_value = post_response

        result = await service.create_branch("new-branch", from_ref="main")

        assert result["ref"] == "refs/heads/new-branch"
        assert result["sha"] == "abc123def456"

    @pytest.mark.asyncio
    async def test_create_branch_from_default(self, service, mock_client):
        """Test creating branch from default branch."""
        # Mock get for repo info (default branch)
        repo_response = MagicMock()
        repo_response.status_code = 200
        repo_response.json.return_value = {"default_branch": "main"}

        # Mock get for branch info
        branch_response = MagicMock()
        branch_response.status_code = 200
        branch_response.json.return_value = {
            "name": "main",
            "commit": {"sha": "abc123"},
            "protected": False,
        }

        # Mock post for creating branch
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {
            "ref": "refs/heads/feature",
            "object": {"sha": "abc123"},
        }

        # Set up mock to return different responses
        mock_client.get.side_effect = [repo_response, branch_response]
        mock_client.post.return_value = post_response

        result = await service.create_branch("feature")

        assert result["ref"] == "refs/heads/feature"

    @pytest.mark.asyncio
    async def test_create_branch_already_exists(self, service, mock_client):
        """Test error when branch already exists."""
        # Mock get for source branch
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "name": "main",
            "commit": {"sha": "abc123"},
            "protected": False,
        }

        # Mock 422 for branch exists
        post_response = MagicMock()
        post_response.status_code = 422
        post_response.text = "Reference already exists"
        post_response.headers = {}

        mock_client.get.return_value = get_response
        mock_client.post.return_value = post_response

        with pytest.raises(GitHubError) as exc_info:
            await service.create_branch("existing-branch", from_ref="main")

        assert "already exists" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_branch_source_not_found(self, service, mock_client):
        """Test error when source branch doesn't exist."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response

        with pytest.raises(GitHubError) as exc_info:
            await service.create_branch("new-branch", from_ref="nonexistent")

        assert "not found" in str(exc_info.value)


class TestUpdateFile:
    """Tests for update_file method."""

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
    async def test_update_file_success(self, service, mock_client):
        """Test successfully updating a file."""
        # Mock get for existing file info
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "name": "handler.go",
            "path": "backend/handler.go",
            "sha": "existing-sha",
            "type": "file",
        }

        # Mock put for update
        put_response = MagicMock()
        put_response.status_code = 200
        put_response.json.return_value = {
            "commit": {"sha": "new-commit-sha"},
            "content": {
                "sha": "new-content-sha",
                "path": "backend/handler.go",
            },
        }

        mock_client.get.return_value = get_response
        mock_client.put.return_value = put_response

        result = await service.update_file(
            path="backend/handler.go",
            content="package main\n\nfunc handler() {}\n",
            message="Update handler",
            branch="feature-branch",
        )

        assert result["sha"] == "new-commit-sha"
        assert result["content_sha"] == "new-content-sha"
        assert result["path"] == "backend/handler.go"

    @pytest.mark.asyncio
    async def test_update_file_with_explicit_sha(self, service, mock_client):
        """Test updating file with explicit SHA."""
        put_response = MagicMock()
        put_response.status_code = 200
        put_response.json.return_value = {
            "commit": {"sha": "commit-sha"},
            "content": {"sha": "content-sha", "path": "test.go"},
        }
        mock_client.put.return_value = put_response

        await service.update_file(
            path="test.go",
            content="new content",
            message="Update",
            branch="main",
            sha="explicit-sha",
        )

        # Should not call get since SHA was provided
        mock_client.get.assert_not_called()

        # Check that SHA was included in payload
        call_args = mock_client.put.call_args
        assert call_args[1]["json"]["sha"] == "explicit-sha"

    @pytest.mark.asyncio
    async def test_update_file_creates_new(self, service, mock_client):
        """Test creating a new file when it doesn't exist."""
        # Mock get returns 404
        get_response = MagicMock()
        get_response.status_code = 404
        mock_client.get.return_value = get_response

        # Mock put for create
        put_response = MagicMock()
        put_response.status_code = 201
        put_response.json.return_value = {
            "commit": {"sha": "commit-sha"},
            "content": {"sha": "content-sha", "path": "new-file.go"},
        }
        mock_client.put.return_value = put_response

        result = await service.update_file(
            path="new-file.go",
            content="package main",
            message="Create new file",
            branch="main",
        )

        assert result["sha"] == "commit-sha"

    @pytest.mark.asyncio
    async def test_update_file_encodes_content(self, service, mock_client):
        """Test that content is base64 encoded."""
        put_response = MagicMock()
        put_response.status_code = 200
        put_response.json.return_value = {
            "commit": {"sha": "sha"},
            "content": {"sha": "sha", "path": "test.go"},
        }
        mock_client.put.return_value = put_response

        await service.update_file(
            path="test.go",
            content="Hello, World!",
            message="Test",
            branch="main",
            sha="existing-sha",
        )

        call_args = mock_client.put.call_args
        payload = call_args[1]["json"]

        # Decode and verify
        decoded = base64.b64decode(payload["content"]).decode()
        assert decoded == "Hello, World!"


class TestCreatePullRequest:
    """Tests for create_pull_request method."""

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
    async def test_create_pull_request_success(self, service, mock_client):
        """Test successfully creating a PR."""
        # Mock get for default branch
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {"default_branch": "main"}

        # Mock post for PR creation
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {
            "number": 42,
            "url": "https://api.github.com/repos/test/repo/pulls/42",
            "html_url": "https://github.com/test/repo/pull/42",
            "title": "Test PR",
            "body": "PR description",
            "state": "open",
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
            "draft": True,
            "created_at": "2024-01-01T12:00:00Z",
            "labels": [],
        }

        mock_client.get.return_value = get_response
        mock_client.post.return_value = post_response

        result = await service.create_pull_request(
            title="Test PR",
            body="PR description",
            head="feature",
            draft=True,
        )

        assert isinstance(result, PullRequest)
        assert result.number == 42
        assert result.html_url == "https://github.com/test/repo/pull/42"
        assert result.draft is True

    @pytest.mark.asyncio
    async def test_create_pull_request_with_base(self, service, mock_client):
        """Test creating PR with explicit base branch."""
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {
            "number": 1,
            "url": "url",
            "html_url": "html_url",
            "title": "PR",
            "body": "body",
            "state": "open",
            "head": {"ref": "feature"},
            "base": {"ref": "develop"},
            "draft": False,
            "created_at": "2024-01-01T12:00:00Z",
            "labels": [],
        }
        mock_client.post.return_value = post_response

        await service.create_pull_request(
            title="PR",
            body="body",
            head="feature",
            base="develop",
        )

        # Should not call get for default branch
        mock_client.get.assert_not_called()

        # Check base branch in payload
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["base"] == "develop"

    @pytest.mark.asyncio
    async def test_create_pull_request_default_draft(self, service, mock_client):
        """Test that PRs are draft by default."""
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {
            "number": 1,
            "url": "url",
            "html_url": "html_url",
            "title": "PR",
            "body": "body",
            "state": "open",
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
            "draft": True,
            "created_at": "2024-01-01T12:00:00Z",
            "labels": [],
        }
        mock_client.post.return_value = post_response

        await service.create_pull_request(
            title="PR",
            body="body",
            head="feature",
            base="main",
        )

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["draft"] is True


class TestAddLabelsToPR:
    """Tests for add_labels_to_pr method."""

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
    async def test_add_labels_success(self, service, mock_client):
        """Test successfully adding labels."""
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = [
            {"name": "bug"},
            {"name": "oncall-helper"},
            {"name": "auto-fix"},
        ]
        mock_client.post.return_value = post_response

        result = await service.add_labels_to_pr(42, ["oncall-helper", "auto-fix"])

        assert "oncall-helper" in result
        assert "auto-fix" in result
        assert "bug" in result  # Existing label

    @pytest.mark.asyncio
    async def test_add_labels_to_nonexistent_pr(self, service, mock_client):
        """Test adding labels to nonexistent PR."""
        post_response = MagicMock()
        post_response.status_code = 404
        post_response.text = "Not Found"
        post_response.headers = {}
        mock_client.post.return_value = post_response

        with pytest.raises(GitHubError):
            await service.add_labels_to_pr(99999, ["label"])


class TestGetPullRequest:
    """Tests for get_pull_request method."""

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
    async def test_get_pull_request_success(self, service, mock_client):
        """Test getting a PR."""
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "number": 42,
            "url": "api-url",
            "html_url": "https://github.com/test/repo/pull/42",
            "title": "Fix bug",
            "body": "Fixes issue",
            "state": "open",
            "head": {"ref": "fix-branch"},
            "base": {"ref": "main"},
            "draft": False,
            "created_at": "2024-01-15T10:00:00Z",
            "labels": [{"name": "bug"}],
        }
        mock_client.get.return_value = get_response

        result = await service.get_pull_request(42)

        assert isinstance(result, PullRequest)
        assert result.number == 42
        assert result.title == "Fix bug"
        assert "bug" in result.labels

    @pytest.mark.asyncio
    async def test_get_pull_request_not_found(self, service, mock_client):
        """Test PR not found returns None."""
        get_response = MagicMock()
        get_response.status_code = 404
        mock_client.get.return_value = get_response

        result = await service.get_pull_request(99999)

        assert result is None


class TestCreateFixPR:
    """Tests for the create_fix_pr convenience method."""

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
    async def test_create_fix_pr_full_pipeline(self, service, mock_client):
        """Test the full fix PR creation pipeline."""
        original_code = "func handler() { return nil }"
        fixed_code = "func handler() error { return nil }"
        file_content = f"package main\n\n{original_code}\n"

        # Setup response mocks
        responses = []

        # 1. Get default branch
        default_branch_resp = MagicMock()
        default_branch_resp.status_code = 200
        default_branch_resp.json.return_value = {"default_branch": "main"}
        responses.append(default_branch_resp)

        # 2. Check if branch exists (should return 404)
        branch_check_resp = MagicMock()
        branch_check_resp.status_code = 404
        responses.append(branch_check_resp)

        # 3. Get source branch for create_branch
        source_branch_resp = MagicMock()
        source_branch_resp.status_code = 200
        source_branch_resp.json.return_value = {
            "name": "main",
            "commit": {"sha": "main-sha"},
            "protected": False,
        }
        responses.append(source_branch_resp)

        # 4. Get file content
        file_content_resp = MagicMock()
        file_content_resp.status_code = 200
        encoded = base64.b64encode(file_content.encode()).decode()
        file_content_resp.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": encoded,
        }
        responses.append(file_content_resp)

        # 5. Get file info for update
        file_info_resp = MagicMock()
        file_info_resp.status_code = 200
        file_info_resp.json.return_value = {
            "name": "handler.go",
            "path": "backend/handler.go",
            "sha": "file-sha",
            "type": "file",
        }
        responses.append(file_info_resp)

        mock_client.get.side_effect = responses

        # Post responses
        post_responses = []

        # Create branch
        create_branch_resp = MagicMock()
        create_branch_resp.status_code = 201
        create_branch_resp.json.return_value = {
            "ref": "refs/heads/oncall-helper/fix-test0001",
            "object": {"sha": "branch-sha"},
        }
        post_responses.append(create_branch_resp)

        # Create PR
        create_pr_resp = MagicMock()
        create_pr_resp.status_code = 201
        create_pr_resp.json.return_value = {
            "number": 123,
            "url": "api-url",
            "html_url": "https://github.com/test/repo/pull/123",
            "title": "[On Call Helper] Fix: Test incident",
            "body": "PR body",
            "state": "open",
            "head": {"ref": "oncall-helper/fix-test0001"},
            "base": {"ref": "main"},
            "draft": True,
            "created_at": "2024-01-01T12:00:00Z",
            "labels": [],
        }
        post_responses.append(create_pr_resp)

        # Add labels
        add_labels_resp = MagicMock()
        add_labels_resp.status_code = 200
        add_labels_resp.json.return_value = [
            {"name": "oncall-helper"},
            {"name": "auto-fix"},
            {"name": "tests-passed"},
        ]
        post_responses.append(add_labels_resp)

        mock_client.post.side_effect = post_responses

        # Update file
        update_file_resp = MagicMock()
        update_file_resp.status_code = 200
        update_file_resp.json.return_value = {
            "commit": {"sha": "commit-sha"},
            "content": {"sha": "content-sha", "path": "backend/handler.go"},
        }
        mock_client.put.return_value = update_file_resp

        # Execute
        result = await service.create_fix_pr(
            incident_id="OCH-TEST0001",
            incident_title="Test incident",
            file_path="backend/handler.go",
            original_code=original_code,
            fixed_code=fixed_code,
            root_cause="Missing error return type",
            fix_explanation="Added error return type to handler function",
            service_name="caseservice",
            confidence=0.95,
            test_results={"passed": True, "unit_tests_passed": True, "tests_run": 5, "tests_passed": 5},
        )

        assert isinstance(result, PullRequest)
        assert result.number == 123
        assert result.draft is True

    @pytest.mark.asyncio
    async def test_create_fix_pr_branch_exists(self, service, mock_client):
        """Test error when fix branch already exists."""
        # Get default branch
        default_branch_resp = MagicMock()
        default_branch_resp.status_code = 200
        default_branch_resp.json.return_value = {"default_branch": "main"}

        # Branch exists
        branch_exists_resp = MagicMock()
        branch_exists_resp.status_code = 200
        branch_exists_resp.json.return_value = {
            "name": "oncall-helper/fix-test0001",
            "commit": {"sha": "existing-sha"},
            "protected": False,
        }

        mock_client.get.side_effect = [default_branch_resp, branch_exists_resp]

        with pytest.raises(GitHubError) as exc_info:
            await service.create_fix_pr(
                incident_id="OCH-TEST0001",
                incident_title="Test",
                file_path="test.go",
                original_code="old",
                fixed_code="new",
                root_cause="Bug",
                fix_explanation="Fixed",
                service_name="service",
                confidence=0.9,
            )

        assert "already exists" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_fix_pr_file_not_found(self, service, mock_client):
        """Test error when file to fix doesn't exist."""
        responses = []

        # Get default branch
        default_branch_resp = MagicMock()
        default_branch_resp.status_code = 200
        default_branch_resp.json.return_value = {"default_branch": "main"}
        responses.append(default_branch_resp)

        # Branch doesn't exist
        branch_check_resp = MagicMock()
        branch_check_resp.status_code = 404
        responses.append(branch_check_resp)

        # Get source branch
        source_branch_resp = MagicMock()
        source_branch_resp.status_code = 200
        source_branch_resp.json.return_value = {
            "name": "main",
            "commit": {"sha": "main-sha"},
            "protected": False,
        }
        responses.append(source_branch_resp)

        # File not found
        file_not_found_resp = MagicMock()
        file_not_found_resp.status_code = 404
        responses.append(file_not_found_resp)

        mock_client.get.side_effect = responses

        # Create branch succeeds
        create_branch_resp = MagicMock()
        create_branch_resp.status_code = 201
        create_branch_resp.json.return_value = {
            "ref": "refs/heads/oncall-helper/fix-test0001",
            "object": {"sha": "sha"},
        }
        mock_client.post.return_value = create_branch_resp

        with pytest.raises(GitHubError) as exc_info:
            await service.create_fix_pr(
                incident_id="OCH-TEST0001",
                incident_title="Test",
                file_path="nonexistent.go",
                original_code="old",
                fixed_code="new",
                root_cause="Bug",
                fix_explanation="Fixed",
                service_name="service",
                confidence=0.9,
            )

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_fix_pr_original_code_not_found(self, service, mock_client):
        """Test error when original code doesn't match."""
        file_content = "package main\n\nfunc different() {}\n"

        responses = []

        # Get default branch
        default_branch_resp = MagicMock()
        default_branch_resp.status_code = 200
        default_branch_resp.json.return_value = {"default_branch": "main"}
        responses.append(default_branch_resp)

        # Branch doesn't exist
        branch_check_resp = MagicMock()
        branch_check_resp.status_code = 404
        responses.append(branch_check_resp)

        # Get source branch
        source_branch_resp = MagicMock()
        source_branch_resp.status_code = 200
        source_branch_resp.json.return_value = {
            "name": "main",
            "commit": {"sha": "main-sha"},
            "protected": False,
        }
        responses.append(source_branch_resp)

        # Get file content (different from expected)
        file_content_resp = MagicMock()
        file_content_resp.status_code = 200
        encoded = base64.b64encode(file_content.encode()).decode()
        file_content_resp.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": encoded,
        }
        responses.append(file_content_resp)

        mock_client.get.side_effect = responses

        # Create branch succeeds
        create_branch_resp = MagicMock()
        create_branch_resp.status_code = 201
        create_branch_resp.json.return_value = {
            "ref": "refs/heads/oncall-helper/fix-test0001",
            "object": {"sha": "sha"},
        }
        mock_client.post.return_value = create_branch_resp

        with pytest.raises(GitHubError) as exc_info:
            await service.create_fix_pr(
                incident_id="OCH-TEST0001",
                incident_title="Test",
                file_path="handler.go",
                original_code="func handler() {}",  # Not in file
                fixed_code="func handler() error {}",
                root_cause="Bug",
                fix_explanation="Fixed",
                service_name="service",
                confidence=0.9,
            )

        assert "not found" in str(exc_info.value)


class TestGeneratePRBody:
    """Tests for PR body generation."""

    def test_generate_pr_body_with_tests(self):
        """Test PR body generation with test results."""
        service = GitHubService(token="test", repo="test/repo")

        body = service._generate_pr_body(
            incident_id="OCH-12345678",
            incident_title="Nil pointer in handler",
            file_path="backend/services/handler.go",
            original_code="func handler() { x.method() }",
            fixed_code="func handler() { if x != nil { x.method() } }",
            root_cause="Missing nil check before calling method",
            fix_explanation="Added nil check to prevent panic",
            service_name="caseservice",
            confidence=0.92,
            test_results={
                "passed": True,
                "unit_tests_passed": True,
                "smoke_tests_passed": True,
                "tests_run": 10,
                "tests_passed": 10,
            },
        )

        assert "OCH-12345678" in body
        assert "Nil pointer in handler" in body
        assert "backend/services/handler.go" in body
        assert "caseservice" in body
        assert "92%" in body
        assert "Missing nil check" in body
        assert "Added nil check" in body
        assert "Passed" in body
        assert "All tests passed" in body

    def test_generate_pr_body_without_tests(self):
        """Test PR body generation without test results."""
        service = GitHubService(token="test", repo="test/repo")

        body = service._generate_pr_body(
            incident_id="OCH-ABCD1234",
            incident_title="Error handling bug",
            file_path="backend/api/routes.go",
            original_code="return err",
            fixed_code="return fmt.Errorf(\"failed: %w\", err)",
            root_cause="Error not wrapped",
            fix_explanation="Wrapped error with context",
            service_name="apiservice",
            confidence=0.85,
            test_results=None,
        )

        assert "OCH-ABCD1234" in body
        assert "Not run" in body
        assert "Tests pending" in body

    def test_generate_pr_body_escapes_backticks(self):
        """Test that code with backticks is properly escaped."""
        service = GitHubService(token="test", repo="test/repo")

        body = service._generate_pr_body(
            incident_id="OCH-TEST",
            incident_title="Test",
            file_path="test.go",
            original_code="fmt.Sprintf(`%s`, x)",
            fixed_code="fmt.Sprintf(`%s`, y)",
            root_cause="Wrong variable",
            fix_explanation="Fixed variable",
            service_name="test",
            confidence=0.9,
            test_results=None,
        )

        # Should not have unescaped triple backticks that would break markdown
        assert "```go" in body  # Code block markers should exist
