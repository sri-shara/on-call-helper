"""
GitHub Service for On Call Helper.

Provides read access to source files in the Nucleus repository
for fix generation and code analysis.
"""

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    """Base exception for GitHub service errors."""
    pass


class GitHubRateLimitError(GitHubError):
    """Raised when GitHub API rate limit is exceeded."""

    def __init__(self, reset_at: Optional[int] = None):
        self.reset_at = reset_at
        super().__init__(f"GitHub API rate limit exceeded. Resets at: {reset_at}")


class GitHubAuthError(GitHubError):
    """Raised when GitHub authentication fails."""
    pass


class GitHubService:
    """
    GitHub API client for reading repository files.

    Supports both public and private repositories using personal access tokens.
    """

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: Optional[str] = None,
        repo: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """
        Initialize the GitHub service.

        Args:
            token: GitHub personal access token (defaults to settings)
            repo: Repository in format "owner/repo" (defaults to settings)
            timeout: Request timeout in seconds
        """
        self.token = token or settings.github_token
        self.repo = repo or settings.github_repo
        self.timeout = timeout

        self._client: Optional[httpx.AsyncClient] = None

    @property
    def headers(self) -> Dict[str, str]:
        """Get headers for GitHub API requests."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers=self.headers,
                timeout=self.timeout,
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _handle_response_error(self, response: httpx.Response, context: str = ""):
        """Handle error responses from GitHub API."""
        if response.status_code == 401:
            raise GitHubAuthError("Invalid or missing GitHub token")

        if response.status_code == 403:
            # Check for rate limiting
            if "X-RateLimit-Remaining" in response.headers:
                remaining = int(response.headers.get("X-RateLimit-Remaining", 0))
                if remaining == 0:
                    reset_at = response.headers.get("X-RateLimit-Reset")
                    raise GitHubRateLimitError(
                        reset_at=int(reset_at) if reset_at else None
                    )
            raise GitHubAuthError("Access forbidden - check token permissions")

        if response.status_code == 404:
            # Not found is handled by returning None
            return None

        if response.status_code >= 400:
            raise GitHubError(
                f"GitHub API error ({response.status_code}){context}: {response.text}"
            )

        return response

    async def get_file_content(
        self,
        path: str,
        ref: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> Optional[str]:
        """
        Get the content of a file from the repository.

        Args:
            path: Path to the file relative to repo root
            ref: Git reference (branch, tag, or commit SHA). Defaults to default branch.
            repo: Repository override in format "owner/repo"

        Returns:
            File content as string, or None if file doesn't exist

        Raises:
            GitHubError: If API request fails (other than 404)
            GitHubRateLimitError: If rate limit is exceeded
            GitHubAuthError: If authentication fails
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        # Normalize path (remove leading slash if present)
        path = path.lstrip("/")

        url = f"/repos/{target_repo}/contents/{path}"
        params = {}
        if ref:
            params["ref"] = ref

        logger.debug(f"Fetching file: {target_repo}/{path} (ref: {ref or 'default'})")

        client = await self._get_client()

        try:
            response = await client.get(url, params=params)
        except httpx.TimeoutException:
            raise GitHubError(f"Timeout fetching {path}")
        except httpx.RequestError as e:
            raise GitHubError(f"Request error fetching {path}: {e}")

        # Handle errors
        if response.status_code == 404:
            logger.debug(f"File not found: {target_repo}/{path}")
            return None

        self._handle_response_error(response, f" fetching {path}")

        # Parse response
        data = response.json()

        # Check if it's a file (not a directory)
        if data.get("type") != "file":
            logger.warning(f"Path is not a file: {path} (type: {data.get('type')})")
            return None

        # Decode content
        encoding = data.get("encoding", "base64")
        content = data.get("content", "")

        if encoding == "base64":
            try:
                # GitHub returns base64 with newlines, need to remove them
                content_clean = content.replace("\n", "")
                decoded = base64.b64decode(content_clean).decode("utf-8")
                logger.debug(f"Successfully fetched {path} ({len(decoded)} chars)")
                return decoded
            except Exception as e:
                raise GitHubError(f"Failed to decode file content: {e}")
        else:
            raise GitHubError(f"Unsupported encoding: {encoding}")

    async def get_file_info(
        self,
        path: str,
        ref: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get metadata about a file without fetching content.

        Args:
            path: Path to the file
            ref: Git reference
            repo: Repository override

        Returns:
            File metadata dict or None if not found
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        path = path.lstrip("/")
        url = f"/repos/{target_repo}/contents/{path}"
        params = {}
        if ref:
            params["ref"] = ref

        client = await self._get_client()

        try:
            response = await client.get(url, params=params)
        except httpx.RequestError as e:
            raise GitHubError(f"Request error: {e}")

        if response.status_code == 404:
            return None

        self._handle_response_error(response)

        data = response.json()

        return {
            "name": data.get("name"),
            "path": data.get("path"),
            "sha": data.get("sha"),
            "size": data.get("size"),
            "type": data.get("type"),
            "url": data.get("html_url"),
        }

    async def list_directory(
        self,
        path: str = "",
        ref: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        List contents of a directory.

        Args:
            path: Path to directory (empty string for root)
            ref: Git reference
            repo: Repository override

        Returns:
            List of file/directory info dicts, or None if path doesn't exist
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        path = path.lstrip("/")
        url = f"/repos/{target_repo}/contents/{path}" if path else f"/repos/{target_repo}/contents"
        params = {}
        if ref:
            params["ref"] = ref

        client = await self._get_client()

        try:
            response = await client.get(url, params=params)
        except httpx.RequestError as e:
            raise GitHubError(f"Request error: {e}")

        if response.status_code == 404:
            return None

        self._handle_response_error(response)

        data = response.json()

        # If it's a single file, wrap in list
        if isinstance(data, dict):
            data = [data]

        return [
            {
                "name": item.get("name"),
                "path": item.get("path"),
                "type": item.get("type"),
                "size": item.get("size"),
                "sha": item.get("sha"),
            }
            for item in data
        ]

    async def get_default_branch(self, repo: Optional[str] = None) -> str:
        """
        Get the default branch of the repository.

        Args:
            repo: Repository override

        Returns:
            Default branch name (e.g., "main" or "master")
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        url = f"/repos/{target_repo}"
        client = await self._get_client()

        try:
            response = await client.get(url)
        except httpx.RequestError as e:
            raise GitHubError(f"Request error: {e}")

        self._handle_response_error(response)

        data = response.json()
        return data.get("default_branch", "main")

    async def check_rate_limit(self) -> Dict[str, Any]:
        """
        Check current rate limit status.

        Returns:
            Dict with 'limit', 'remaining', 'reset' (timestamp), 'used'
        """
        client = await self._get_client()

        try:
            response = await client.get("/rate_limit")
        except httpx.RequestError as e:
            raise GitHubError(f"Request error: {e}")

        self._handle_response_error(response)

        data = response.json()
        core = data.get("resources", {}).get("core", {})

        return {
            "limit": core.get("limit", 0),
            "remaining": core.get("remaining", 0),
            "reset": core.get("reset", 0),
            "used": core.get("used", 0),
        }


# Module-level convenience functions


async def get_nucleus_file(path: str, ref: Optional[str] = None) -> Optional[str]:
    """
    Get a file from the Nucleus repository.

    Args:
        path: Path to file relative to repo root
        ref: Git reference (optional)

    Returns:
        File content or None if not found
    """
    service = GitHubService()
    try:
        return await service.get_file_content(path, ref=ref)
    finally:
        await service.close()
