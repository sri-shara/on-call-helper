"""
GitHub Service for On Call Helper.

Provides read and write access to the Nucleus repository
for fix generation, code analysis, and PR creation.
"""

import base64
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)


@dataclass
class PullRequest:
    """Represents a GitHub pull request."""

    number: int
    url: str
    html_url: str
    title: str
    body: str
    state: str
    head_branch: str
    base_branch: str
    draft: bool
    created_at: datetime
    labels: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "number": self.number,
            "url": self.url,
            "html_url": self.html_url,
            "title": self.title,
            "body": self.body,
            "state": self.state,
            "head_branch": self.head_branch,
            "base_branch": self.base_branch,
            "draft": self.draft,
            "created_at": self.created_at.isoformat(),
            "labels": self.labels,
        }


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

    # ═══════════════════════════════════════════════════════════════════════════
    # Write Operations for PR Creation
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_branch(
        self,
        branch: str,
        repo: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get information about a branch.

        Args:
            branch: Branch name
            repo: Repository override

        Returns:
            Branch info dict with 'name', 'sha', 'protected', or None if not found
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        url = f"/repos/{target_repo}/branches/{branch}"
        client = await self._get_client()

        try:
            response = await client.get(url)
        except httpx.RequestError as e:
            raise GitHubError(f"Request error: {e}")

        if response.status_code == 404:
            return None

        self._handle_response_error(response, f" getting branch {branch}")

        data = response.json()
        return {
            "name": data.get("name"),
            "sha": data.get("commit", {}).get("sha"),
            "protected": data.get("protected", False),
        }

    async def create_branch(
        self,
        branch_name: str,
        from_ref: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new branch from an existing reference.

        Args:
            branch_name: Name for the new branch
            from_ref: Source branch/tag/SHA (defaults to default branch)
            repo: Repository override

        Returns:
            Dict with 'ref' and 'sha' of the new branch

        Raises:
            GitHubError: If branch creation fails
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        # Get the SHA to branch from
        if from_ref is None:
            from_ref = await self.get_default_branch(target_repo)

        source_branch = await self.get_branch(from_ref, target_repo)
        if not source_branch:
            raise GitHubError(f"Source branch '{from_ref}' not found")

        source_sha = source_branch["sha"]

        # Create the new reference
        url = f"/repos/{target_repo}/git/refs"
        client = await self._get_client()

        payload = {
            "ref": f"refs/heads/{branch_name}",
            "sha": source_sha,
        }

        logger.debug(f"Creating branch {branch_name} from {from_ref} ({source_sha[:8]})")

        try:
            response = await client.post(url, json=payload)
        except httpx.RequestError as e:
            raise GitHubError(f"Request error creating branch: {e}")

        # 422 means branch already exists
        if response.status_code == 422:
            raise GitHubError(f"Branch '{branch_name}' already exists")

        self._handle_response_error(response, f" creating branch {branch_name}")

        data = response.json()
        logger.info(f"Created branch: {branch_name}")

        return {
            "ref": data.get("ref"),
            "sha": data.get("object", {}).get("sha"),
        }

    async def update_file(
        self,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create or update a file in the repository.

        Args:
            path: File path relative to repo root
            content: New file content
            message: Commit message
            branch: Branch to commit to
            sha: SHA of the file being replaced (required for updates, not for creates)
            repo: Repository override

        Returns:
            Dict with 'sha' (commit SHA), 'content_sha' (file SHA), 'path'

        Raises:
            GitHubError: If file update fails
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        path = path.lstrip("/")
        url = f"/repos/{target_repo}/contents/{path}"
        client = await self._get_client()

        # If sha not provided, try to get the current file's SHA
        if sha is None:
            file_info = await self.get_file_info(path, ref=branch, repo=target_repo)
            if file_info:
                sha = file_info.get("sha")

        # Encode content as base64
        content_bytes = content.encode("utf-8")
        content_b64 = base64.b64encode(content_bytes).decode("ascii")

        payload = {
            "message": message,
            "content": content_b64,
            "branch": branch,
        }

        if sha:
            payload["sha"] = sha

        logger.debug(f"Updating file {path} on branch {branch}")

        try:
            response = await client.put(url, json=payload)
        except httpx.RequestError as e:
            raise GitHubError(f"Request error updating file: {e}")

        self._handle_response_error(response, f" updating file {path}")

        data = response.json()
        commit = data.get("commit", {})
        content_data = data.get("content", {})

        logger.info(f"Updated file: {path} (commit: {commit.get('sha', '')[:8]})")

        return {
            "sha": commit.get("sha"),
            "content_sha": content_data.get("sha"),
            "path": content_data.get("path"),
        }

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: Optional[str] = None,
        draft: bool = True,
        repo: Optional[str] = None,
    ) -> PullRequest:
        """
        Create a pull request.

        Args:
            title: PR title
            body: PR description (supports markdown)
            head: Branch containing changes
            base: Target branch (defaults to default branch)
            draft: Create as draft PR (default: True for safety)
            repo: Repository override

        Returns:
            PullRequest object

        Raises:
            GitHubError: If PR creation fails
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        if base is None:
            base = await self.get_default_branch(target_repo)

        url = f"/repos/{target_repo}/pulls"
        client = await self._get_client()

        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "draft": draft,
        }

        logger.debug(f"Creating PR: {head} -> {base}")

        try:
            response = await client.post(url, json=payload)
        except httpx.RequestError as e:
            raise GitHubError(f"Request error creating PR: {e}")

        self._handle_response_error(response, " creating pull request")

        data = response.json()
        logger.info(f"Created PR #{data.get('number')}: {title}")

        return self._parse_pull_request(data)

    async def add_labels_to_pr(
        self,
        pr_number: int,
        labels: List[str],
        repo: Optional[str] = None,
    ) -> List[str]:
        """
        Add labels to a pull request.

        Args:
            pr_number: PR number
            labels: List of label names
            repo: Repository override

        Returns:
            List of all labels on the PR

        Raises:
            GitHubError: If adding labels fails
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        url = f"/repos/{target_repo}/issues/{pr_number}/labels"
        client = await self._get_client()

        payload = {"labels": labels}

        logger.debug(f"Adding labels to PR #{pr_number}: {labels}")

        try:
            response = await client.post(url, json=payload)
        except httpx.RequestError as e:
            raise GitHubError(f"Request error adding labels: {e}")

        # 404 means PR doesn't exist
        if response.status_code == 404:
            raise GitHubError(f"Pull request #{pr_number} not found")

        self._handle_response_error(response, f" adding labels to PR #{pr_number}")

        data = response.json()
        return [label.get("name") for label in data]

    async def get_pull_request(
        self,
        pr_number: int,
        repo: Optional[str] = None,
    ) -> Optional[PullRequest]:
        """
        Get a pull request by number.

        Args:
            pr_number: PR number
            repo: Repository override

        Returns:
            PullRequest object or None if not found
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        url = f"/repos/{target_repo}/pulls/{pr_number}"
        client = await self._get_client()

        try:
            response = await client.get(url)
        except httpx.RequestError as e:
            raise GitHubError(f"Request error: {e}")

        if response.status_code == 404:
            return None

        self._handle_response_error(response)

        return self._parse_pull_request(response.json())

    def _parse_pull_request(self, data: Dict[str, Any]) -> PullRequest:
        """Parse GitHub API response into PullRequest object."""
        created_at_str = data.get("created_at", "")
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            created_at = datetime.utcnow()

        return PullRequest(
            number=data.get("number", 0),
            url=data.get("url", ""),
            html_url=data.get("html_url", ""),
            title=data.get("title", ""),
            body=data.get("body", ""),
            state=data.get("state", ""),
            head_branch=data.get("head", {}).get("ref", ""),
            base_branch=data.get("base", {}).get("ref", ""),
            draft=data.get("draft", False),
            created_at=created_at,
            labels=[label.get("name", "") for label in data.get("labels", [])],
        )

    async def create_fix_pr(
        self,
        incident_id: str,
        incident_title: str,
        file_path: str,
        original_code: str,
        fixed_code: str,
        root_cause: str,
        fix_explanation: str,
        service_name: str,
        confidence: float,
        test_results: Optional[Dict[str, Any]] = None,
        repo: Optional[str] = None,
    ) -> PullRequest:
        """
        Create a complete PR for an incident fix.

        This is a convenience method that:
        1. Creates a branch
        2. Updates the file with the fix
        3. Creates a draft PR with full documentation
        4. Adds labels

        Args:
            incident_id: On Call Helper incident ID (e.g., "OCH-12345678")
            incident_title: Brief incident title
            file_path: Path to the file being fixed
            original_code: The buggy code
            fixed_code: The corrected code
            root_cause: Root cause analysis
            fix_explanation: Why the fix works
            service_name: Affected Nucleus service
            confidence: Triage confidence score (0-1)
            test_results: Optional test results dict
            repo: Repository override

        Returns:
            PullRequest object

        Raises:
            GitHubError: If any step fails
        """
        target_repo = repo or self.repo
        if not target_repo:
            raise GitHubError("No repository specified")

        # Generate branch name
        incident_short = incident_id.replace("OCH-", "").lower()[:8]
        branch_name = f"oncall-helper/fix-{incident_short}"

        logger.info(f"Creating fix PR for {incident_id}: {branch_name}")

        # Step 1: Create branch from main
        default_branch = await self.get_default_branch(target_repo)

        # Check if branch already exists
        existing = await self.get_branch(branch_name, target_repo)
        if existing:
            raise GitHubError(
                f"Branch '{branch_name}' already exists. "
                f"A fix may already be in progress for {incident_id}."
            )

        await self.create_branch(branch_name, from_ref=default_branch, repo=target_repo)

        # Step 2: Get current file and apply fix
        current_content = await self.get_file_content(
            file_path, ref=default_branch, repo=target_repo
        )

        if current_content is None:
            raise GitHubError(f"File not found: {file_path}")

        if original_code not in current_content:
            raise GitHubError(
                f"Original code not found in {file_path}. "
                "The file may have changed since triage."
            )

        new_content = current_content.replace(original_code, fixed_code)

        # Step 3: Commit the fix
        commit_message = f"fix: {incident_title[:50]}\n\nAuto-generated fix for {incident_id} by On Call Helper"

        await self.update_file(
            path=file_path,
            content=new_content,
            message=commit_message,
            branch=branch_name,
            repo=target_repo,
        )

        # Step 4: Generate PR body
        pr_body = self._generate_pr_body(
            incident_id=incident_id,
            incident_title=incident_title,
            file_path=file_path,
            original_code=original_code,
            fixed_code=fixed_code,
            root_cause=root_cause,
            fix_explanation=fix_explanation,
            service_name=service_name,
            confidence=confidence,
            test_results=test_results,
        )

        # Step 5: Create PR
        pr_title = f"[On Call Helper] Fix: {incident_title[:50]}"
        pr = await self.create_pull_request(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=default_branch,
            draft=True,
            repo=target_repo,
        )

        # Step 6: Add labels
        labels = ["oncall-helper", "auto-fix"]
        if test_results and test_results.get("passed"):
            labels.append("tests-passed")

        try:
            await self.add_labels_to_pr(pr.number, labels, repo=target_repo)
        except GitHubError as e:
            # Labels are nice-to-have, don't fail the PR
            logger.warning(f"Failed to add labels: {e}")

        logger.info(f"Created PR #{pr.number}: {pr.html_url}")
        return pr

    def _generate_pr_body(
        self,
        incident_id: str,
        incident_title: str,
        file_path: str,
        original_code: str,
        fixed_code: str,
        root_cause: str,
        fix_explanation: str,
        service_name: str,
        confidence: float,
        test_results: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate the PR body with full documentation."""
        # Format test results
        if test_results:
            unit_status = "Passed" if test_results.get("unit_tests_passed") else "Failed"
            smoke_passed = test_results.get("smoke_tests_passed")
            if smoke_passed is None:
                smoke_status = "Skipped"
            else:
                smoke_status = "Passed" if smoke_passed else "Failed"
            tests_run = test_results.get("tests_run", 0)
            tests_passed = test_results.get("tests_passed", 0)
        else:
            unit_status = "Not run"
            smoke_status = "Not run"
            tests_run = 0
            tests_passed = 0

        # Escape code for diff display
        original_escaped = original_code.replace("```", "` ` `")
        fixed_escaped = fixed_code.replace("```", "` ` `")

        return f"""## On Call Helper Auto-Generated Fix

**Status:** {"All tests passed" if test_results and test_results.get("passed") else "Tests pending/failed"} | Draft PR (requires human approval)

### Incident Details
| Field | Value |
|-------|-------|
| ID | `{incident_id}` |
| Title | {incident_title} |
| Service | {service_name} |
| File | `{file_path}` |
| Confidence | {confidence:.0%} |

### Root Cause Analysis
> {root_cause}

### Changes Made
{fix_explanation}

### Code Diff

**Before:**
```go
{original_escaped}
```

**After:**
```go
{fixed_escaped}
```

### Test Results
| Test Suite | Status | Count |
|------------|--------|-------|
| Unit Tests | {unit_status} | {tests_passed}/{tests_run} |
| Smoke Tests | {smoke_status} | - |

### Verification Checklist
- [x] Automated fix generated
- [x] Sandbox tests {"passed" if test_results and test_results.get("passed") else "pending"}
- [ ] Manual code review
- [ ] Verify fix in staging
- [ ] Monitor production after merge

### Production Verification Plan
After merging, On Call Helper will monitor Cloud Logging for 2 hours to verify the error no longer occurs.

---
Generated by On Call Helper | Incident: `{incident_id}`
"""


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
