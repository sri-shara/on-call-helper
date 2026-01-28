"""
CodeRabbit Service for On Call Helper.

Provides automated code review via the CodeRabbit CLI.
Used in the fix validation pipeline to ensure code quality.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.models import FixResult, ReviewResult, ReviewIssue

logger = logging.getLogger(__name__)


class CodeRabbitError(Exception):
    """Error during CodeRabbit review."""
    pass


class CodeRabbitNotInstalledError(CodeRabbitError):
    """CodeRabbit CLI is not installed."""
    pass


class CodeRabbitService:
    """
    CodeRabbit CLI integration for automated code review.

    Reviews code fixes and identifies issues before sandbox testing.
    Supports a retry loop with the fixer agent (max 3 iterations).
    """

    # Issue severities that block the fix
    BLOCKING_SEVERITIES = ["critical", "high", "error", "security"]

    # File extension mapping for different languages
    LANGUAGE_EXTENSIONS = {
        "go": ".go",
        "python": ".py",
        "javascript": ".js",
        "typescript": ".ts",
        "java": ".java",
        "rust": ".rs",
    }

    def __init__(
        self,
        max_retries: Optional[int] = None,
        timeout: int = 120,
    ):
        """
        Initialize the CodeRabbit service.

        Args:
            max_retries: Maximum review iterations (defaults to settings)
            timeout: CLI timeout in seconds
        """
        self.max_retries = max_retries or settings.coderabbit_max_retries
        self.timeout = timeout
        self._cli_path: Optional[str] = None
        self._cli_available: Optional[bool] = None

    def _check_cli_available(self) -> bool:
        """Check if CodeRabbit CLI is installed and available."""
        if self._cli_available is not None:
            return self._cli_available

        # Try to find coderabbit in PATH
        self._cli_path = shutil.which("coderabbit")

        if self._cli_path:
            self._cli_available = True
            logger.debug(f"CodeRabbit CLI found at: {self._cli_path}")
            return True

        # Try common installation paths
        common_paths = [
            "/usr/local/bin/coderabbit",
            os.path.expanduser("~/.local/bin/coderabbit"),
            os.path.expanduser("~/go/bin/coderabbit"),
        ]

        for path in common_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                self._cli_path = path
                self._cli_available = True
                logger.debug(f"CodeRabbit CLI found at: {path}")
                return True

        self._cli_available = False
        logger.warning("CodeRabbit CLI not found")
        return False

    def _get_file_extension(self, file_path: str) -> str:
        """Get the appropriate file extension based on the file path."""
        # Extract extension from the original file path
        path = Path(file_path)
        if path.suffix:
            return path.suffix

        # Default to Go for Nucleus codebase
        return ".go"

    def _write_temp_file(self, content: str, extension: str) -> str:
        """
        Write content to a temporary file.

        Args:
            content: File content to write
            extension: File extension (e.g., ".go")

        Returns:
            Path to the temporary file
        """
        # Create temp file with correct extension
        fd, temp_path = tempfile.mkstemp(suffix=extension, prefix="coderabbit_")

        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            return temp_path
        except Exception as e:
            # Clean up on error
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise CodeRabbitError(f"Failed to write temp file: {e}")

    def _parse_review_output(self, output: str) -> Dict[str, Any]:
        """
        Parse CodeRabbit CLI JSON output.

        Args:
            output: Raw CLI output

        Returns:
            Parsed JSON data
        """
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            # Try to find JSON in the output (CLI might have other output)
            import re
            json_match = re.search(r'\{[\s\S]*\}', output)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass

            # Return empty structure if parsing fails
            logger.warning(f"Failed to parse CodeRabbit output: {output[:200]}")
            return {"issues": [], "suggestions": []}

    def _format_feedback(self, issues: List[ReviewIssue]) -> str:
        """
        Format issues as feedback for the Claude fixer agent.

        Args:
            issues: List of review issues

        Returns:
            Formatted feedback string
        """
        if not issues:
            return ""

        lines = ["CodeRabbit found the following issues that need to be addressed:", ""]

        for issue in issues:
            severity_badge = f"[{issue.severity.upper()}]"
            line_info = f" Line {issue.line}:" if issue.line else ":"

            lines.append(f"- {severity_badge}{line_info} {issue.message}")

            if issue.suggestion:
                lines.append(f"  Suggestion: {issue.suggestion}")

        lines.append("")
        lines.append("Please update the fix to address these issues.")

        return "\n".join(lines)

    def _run_cli(self, file_path: str) -> subprocess.CompletedProcess:
        """
        Run the CodeRabbit CLI on a file.

        Args:
            file_path: Path to the file to review

        Returns:
            CompletedProcess with stdout/stderr

        Raises:
            CodeRabbitNotInstalledError: If CLI is not available
            CodeRabbitError: If CLI execution fails
        """
        if not self._check_cli_available():
            raise CodeRabbitNotInstalledError(
                "CodeRabbit CLI is not installed. "
                "Install it from: https://docs.coderabbit.ai/cli"
            )

        cmd = [
            self._cli_path,
            "review",
            file_path,
            "--format", "json",
        ]

        logger.debug(f"Running CodeRabbit CLI: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return result

        except subprocess.TimeoutExpired:
            raise CodeRabbitError(
                f"CodeRabbit review timed out after {self.timeout} seconds"
            )
        except FileNotFoundError:
            raise CodeRabbitNotInstalledError(
                "CodeRabbit CLI not found at expected path"
            )
        except Exception as e:
            raise CodeRabbitError(f"Failed to run CodeRabbit CLI: {e}")

    async def review(self, fix: FixResult) -> ReviewResult:
        """
        Review a code fix using CodeRabbit CLI.

        Args:
            fix: The fix result to review

        Returns:
            ReviewResult with pass/fail status and issues

        Raises:
            CodeRabbitError: If review fails
        """
        temp_path = None

        try:
            # Determine file extension
            extension = self._get_file_extension(fix.file_path)

            # Write fixed code to temp file
            temp_path = self._write_temp_file(fix.fixed_code, extension)
            logger.info(f"Reviewing fix for {fix.incident_id} ({extension} file)")

            # Run CodeRabbit CLI
            result = self._run_cli(temp_path)

            # Parse output
            review_data = self._parse_review_output(result.stdout)

            # Extract issues
            raw_issues = review_data.get("issues", [])
            issues = []

            for raw_issue in raw_issues:
                issue = ReviewIssue(
                    severity=raw_issue.get("severity", "medium"),
                    message=raw_issue.get("message", "Unknown issue"),
                    line=raw_issue.get("line"),
                    suggestion=raw_issue.get("suggestion"),
                )
                issues.append(issue)

            # Check for blocking issues
            blocking_issues = [
                issue for issue in issues
                if issue.severity.lower() in self.BLOCKING_SEVERITIES
            ]

            # Extract suggestions
            suggestions = review_data.get("suggestions", [])

            # Build result
            passed = len(blocking_issues) == 0
            feedback = self._format_feedback(blocking_issues) if not passed else ""

            logger.info(
                f"CodeRabbit review for {fix.incident_id}: "
                f"{'PASSED' if passed else 'FAILED'} "
                f"({len(issues)} issues, {len(blocking_issues)} blocking)"
            )

            return ReviewResult(
                passed=passed,
                issues=issues,
                suggestions=suggestions,
                summary=feedback,
            )

        finally:
            # Clean up temp file
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                    logger.debug(f"Cleaned up temp file: {temp_path}")
                except OSError as e:
                    logger.warning(f"Failed to clean up temp file {temp_path}: {e}")

    def review_sync(self, fix: FixResult) -> ReviewResult:
        """Synchronous version of review."""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self.review(fix))

    async def check_health(self) -> Dict[str, Any]:
        """
        Check CodeRabbit CLI health status.

        Returns:
            Dict with 'available', 'version', and 'path'
        """
        available = self._check_cli_available()

        result = {
            "available": available,
            "path": self._cli_path,
            "version": None,
        }

        if available:
            try:
                version_result = subprocess.run(
                    [self._cli_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                result["version"] = version_result.stdout.strip()
            except Exception as e:
                logger.warning(f"Failed to get CodeRabbit version: {e}")

        return result


# Module-level convenience function
async def review_fix(fix: FixResult) -> ReviewResult:
    """
    Review a fix using the default CodeRabbit service.

    Args:
        fix: The fix to review

    Returns:
        ReviewResult with pass/fail status and issues
    """
    service = CodeRabbitService()
    return await service.review(fix)
