"""
Tests for CodeRabbit Service.

Tests the CodeRabbit CLI integration for automated code review.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch, mock_open
import subprocess

from backend.services.coderabbit import (
    CodeRabbitService,
    CodeRabbitError,
    CodeRabbitNotInstalledError,
    review_fix,
)
from backend.models import FixResult, ReviewResult, ReviewIssue


# Sample CodeRabbit CLI outputs for mocking

SAMPLE_CLEAN_REVIEW = json.dumps({
    "issues": [],
    "suggestions": [
        "Consider adding a comment explaining the nil check"
    ],
})

SAMPLE_REVIEW_WITH_ISSUES = json.dumps({
    "issues": [
        {
            "severity": "high",
            "message": "Error not wrapped with context",
            "line": 15,
            "suggestion": "Use fmt.Errorf to wrap the error with context"
        },
        {
            "severity": "medium",
            "message": "Consider using named return values",
            "line": 10,
            "suggestion": None
        }
    ],
    "suggestions": [
        "Add unit tests for the new code path"
    ],
})

SAMPLE_REVIEW_WITH_CRITICAL = json.dumps({
    "issues": [
        {
            "severity": "critical",
            "message": "Potential nil pointer dereference",
            "line": 25,
            "suggestion": "Add nil check before dereferencing"
        },
        {
            "severity": "low",
            "message": "Magic number should be a constant",
            "line": 30,
            "suggestion": None
        }
    ],
    "suggestions": [],
})

SAMPLE_REVIEW_WITH_SECURITY = json.dumps({
    "issues": [
        {
            "severity": "security",
            "message": "SQL injection vulnerability detected",
            "line": 42,
            "suggestion": "Use parameterized queries"
        }
    ],
    "suggestions": [],
})


@pytest.fixture
def sample_fix():
    """Create a sample fix result for testing."""
    return FixResult(
        incident_id="OCH-TEST001",
        file_path="backend/services/caseservice/handler.go",
        original_code="func processCase(c *Case) error {\n    result := c.GetStatus()\n    return nil\n}",
        fixed_code="func processCase(c *Case) error {\n    if c == nil {\n        return ErrNilCase\n    }\n    result := c.GetStatus()\n    return nil\n}",
        explanation="Added nil check",
        diff_summary="Added nil check for case parameter",
        iteration=1,
    )


class TestCodeRabbitServiceInit:
    """Tests for CodeRabbitService initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default settings."""
        with patch("backend.services.coderabbit.settings") as mock_settings:
            mock_settings.coderabbit_max_retries = 3

            service = CodeRabbitService()

            assert service.max_retries == 3
            assert service.timeout == 120

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        service = CodeRabbitService(max_retries=5, timeout=60)

        assert service.max_retries == 5
        assert service.timeout == 60


class TestCheckCliAvailable:
    """Tests for CLI availability checking."""

    def test_cli_found_in_path(self):
        """Test CLI found via shutil.which."""
        service = CodeRabbitService()

        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/coderabbit"

            result = service._check_cli_available()

            assert result is True
            assert service._cli_path == "/usr/local/bin/coderabbit"

    def test_cli_found_in_common_path(self):
        """Test CLI found in common installation path."""
        service = CodeRabbitService()

        with patch("shutil.which") as mock_which:
            mock_which.return_value = None

            with patch("os.path.isfile") as mock_isfile:
                with patch("os.access") as mock_access:
                    mock_isfile.return_value = True
                    mock_access.return_value = True

                    result = service._check_cli_available()

                    assert result is True

    def test_cli_not_found(self):
        """Test CLI not found."""
        service = CodeRabbitService()

        with patch("shutil.which") as mock_which:
            mock_which.return_value = None

            with patch("os.path.isfile") as mock_isfile:
                mock_isfile.return_value = False

                result = service._check_cli_available()

                assert result is False
                assert service._cli_available is False

    def test_cli_check_cached(self):
        """Test that CLI check is cached."""
        service = CodeRabbitService()
        service._cli_available = True
        service._cli_path = "/cached/path"

        # Should return cached value without calling which
        with patch("shutil.which") as mock_which:
            result = service._check_cli_available()

            assert result is True
            mock_which.assert_not_called()


class TestGetFileExtension:
    """Tests for file extension detection."""

    def test_go_extension(self):
        """Test Go file extension."""
        service = CodeRabbitService()

        assert service._get_file_extension("backend/handler.go") == ".go"

    def test_python_extension(self):
        """Test Python file extension."""
        service = CodeRabbitService()

        assert service._get_file_extension("backend/service.py") == ".py"

    def test_no_extension_defaults_to_go(self):
        """Test file without extension defaults to Go."""
        service = CodeRabbitService()

        assert service._get_file_extension("Makefile") == ".go"

    def test_typescript_extension(self):
        """Test TypeScript file extension."""
        service = CodeRabbitService()

        assert service._get_file_extension("frontend/app.tsx") == ".tsx"


class TestWriteTempFile:
    """Tests for temp file creation."""

    def test_write_temp_file_success(self):
        """Test successful temp file creation."""
        service = CodeRabbitService()

        temp_path = service._write_temp_file("package main\n\nfunc main() {}", ".go")

        try:
            assert os.path.exists(temp_path)
            assert temp_path.endswith(".go")

            with open(temp_path) as f:
                content = f.read()
            assert "package main" in content
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_write_temp_file_with_unicode(self):
        """Test temp file with unicode content."""
        service = CodeRabbitService()

        temp_path = service._write_temp_file("// Unicode: 你好 🌍", ".go")

        try:
            assert os.path.exists(temp_path)

            with open(temp_path, encoding="utf-8") as f:
                content = f.read()
            assert "你好" in content
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestParseReviewOutput:
    """Tests for parsing CLI output."""

    def test_parse_clean_json(self):
        """Test parsing clean JSON output."""
        service = CodeRabbitService()

        result = service._parse_review_output(SAMPLE_CLEAN_REVIEW)

        assert result["issues"] == []
        assert len(result["suggestions"]) == 1

    def test_parse_json_with_issues(self):
        """Test parsing JSON with issues."""
        service = CodeRabbitService()

        result = service._parse_review_output(SAMPLE_REVIEW_WITH_ISSUES)

        assert len(result["issues"]) == 2
        assert result["issues"][0]["severity"] == "high"

    def test_parse_invalid_json_returns_empty(self):
        """Test parsing invalid JSON returns empty structure."""
        service = CodeRabbitService()

        result = service._parse_review_output("not json at all")

        assert result["issues"] == []
        assert result["suggestions"] == []

    def test_parse_json_with_extra_output(self):
        """Test parsing JSON embedded in other output."""
        service = CodeRabbitService()

        output = f"Starting review...\n{SAMPLE_CLEAN_REVIEW}\nDone!"

        result = service._parse_review_output(output)

        assert result["issues"] == []


class TestFormatFeedback:
    """Tests for feedback formatting."""

    def test_format_empty_issues(self):
        """Test formatting empty issue list."""
        service = CodeRabbitService()

        result = service._format_feedback([])

        assert result == ""

    def test_format_single_issue(self):
        """Test formatting single issue."""
        service = CodeRabbitService()
        issues = [
            ReviewIssue(
                severity="high",
                message="Error not wrapped",
                line=15,
                suggestion="Use fmt.Errorf",
            )
        ]

        result = service._format_feedback(issues)

        assert "[HIGH]" in result
        assert "Line 15" in result
        assert "Error not wrapped" in result
        assert "Suggestion: Use fmt.Errorf" in result

    def test_format_multiple_issues(self):
        """Test formatting multiple issues."""
        service = CodeRabbitService()
        issues = [
            ReviewIssue(severity="critical", message="Nil dereference", line=10, suggestion=None),
            ReviewIssue(severity="high", message="Missing error check", line=20, suggestion="Add check"),
        ]

        result = service._format_feedback(issues)

        assert "[CRITICAL]" in result
        assert "[HIGH]" in result
        assert "Line 10" in result
        assert "Line 20" in result

    def test_format_issue_without_line(self):
        """Test formatting issue without line number."""
        service = CodeRabbitService()
        issues = [
            ReviewIssue(severity="medium", message="Style issue", line=None, suggestion=None)
        ]

        result = service._format_feedback(issues)

        assert "[MEDIUM]" in result
        assert "Style issue" in result
        assert "Line" not in result.split("[MEDIUM]")[1].split(":")[0]


class TestReview:
    """Tests for the review method."""

    @pytest.fixture
    def mock_cli_available(self):
        """Mock CLI as available."""
        with patch.object(CodeRabbitService, "_check_cli_available", return_value=True):
            yield

    @pytest.mark.asyncio
    async def test_review_clean_passes(self, sample_fix, mock_cli_available):
        """Test review passes with no issues."""
        service = CodeRabbitService()
        service._cli_path = "/usr/local/bin/coderabbit"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=SAMPLE_CLEAN_REVIEW,
                stderr="",
                returncode=0,
            )

            with patch.object(service, "_write_temp_file", return_value="/tmp/test.go"):
                with patch("os.path.exists", return_value=True):
                    with patch("os.unlink"):
                        result = await service.review(sample_fix)

        assert result.passed is True
        assert len(result.issues) == 0
        assert len(result.suggestions) == 1
        assert result.summary == ""

    @pytest.mark.asyncio
    async def test_review_with_blocking_issues_fails(self, sample_fix, mock_cli_available):
        """Test review fails with high severity issues."""
        service = CodeRabbitService()
        service._cli_path = "/usr/local/bin/coderabbit"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=SAMPLE_REVIEW_WITH_ISSUES,
                stderr="",
                returncode=0,
            )

            with patch.object(service, "_write_temp_file", return_value="/tmp/test.go"):
                with patch("os.path.exists", return_value=True):
                    with patch("os.unlink"):
                        result = await service.review(sample_fix)

        assert result.passed is False
        assert len(result.issues) == 2
        assert "HIGH" in result.summary

    @pytest.mark.asyncio
    async def test_review_with_critical_fails(self, sample_fix, mock_cli_available):
        """Test review fails with critical issues."""
        service = CodeRabbitService()
        service._cli_path = "/usr/local/bin/coderabbit"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=SAMPLE_REVIEW_WITH_CRITICAL,
                stderr="",
                returncode=0,
            )

            with patch.object(service, "_write_temp_file", return_value="/tmp/test.go"):
                with patch("os.path.exists", return_value=True):
                    with patch("os.unlink"):
                        result = await service.review(sample_fix)

        assert result.passed is False
        # Low severity doesn't block
        blocking = [i for i in result.issues if i.severity.lower() in ["critical", "high"]]
        assert len(blocking) == 1

    @pytest.mark.asyncio
    async def test_review_with_security_fails(self, sample_fix, mock_cli_available):
        """Test review fails with security issues."""
        service = CodeRabbitService()
        service._cli_path = "/usr/local/bin/coderabbit"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=SAMPLE_REVIEW_WITH_SECURITY,
                stderr="",
                returncode=0,
            )

            with patch.object(service, "_write_temp_file", return_value="/tmp/test.go"):
                with patch("os.path.exists", return_value=True):
                    with patch("os.unlink"):
                        result = await service.review(sample_fix)

        assert result.passed is False
        assert any(i.severity == "security" for i in result.issues)

    @pytest.mark.asyncio
    async def test_review_cli_not_installed(self, sample_fix):
        """Test review raises error when CLI not installed."""
        service = CodeRabbitService()

        with patch.object(service, "_check_cli_available", return_value=False):
            with pytest.raises(CodeRabbitNotInstalledError) as exc_info:
                await service.review(sample_fix)

            assert "not installed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_review_cli_timeout(self, sample_fix, mock_cli_available):
        """Test review handles CLI timeout."""
        service = CodeRabbitService(timeout=1)
        service._cli_path = "/usr/local/bin/coderabbit"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="coderabbit", timeout=1)

            with patch.object(service, "_write_temp_file", return_value="/tmp/test.go"):
                with patch("os.path.exists", return_value=True):
                    with patch("os.unlink"):
                        with pytest.raises(CodeRabbitError) as exc_info:
                            await service.review(sample_fix)

                        assert "timed out" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_review_cleans_up_temp_file(self, sample_fix, mock_cli_available):
        """Test that temp file is cleaned up after review."""
        service = CodeRabbitService()
        service._cli_path = "/usr/local/bin/coderabbit"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=SAMPLE_CLEAN_REVIEW,
                stderr="",
                returncode=0,
            )

            with patch.object(service, "_write_temp_file", return_value="/tmp/test.go"):
                with patch("os.path.exists", return_value=True) as mock_exists:
                    with patch("os.unlink") as mock_unlink:
                        await service.review(sample_fix)

                        mock_unlink.assert_called_once_with("/tmp/test.go")


class TestCheckHealth:
    """Tests for health check."""

    @pytest.mark.asyncio
    async def test_health_when_available(self):
        """Test health check when CLI is available."""
        service = CodeRabbitService()

        with patch.object(service, "_check_cli_available", return_value=True):
            service._cli_path = "/usr/local/bin/coderabbit"

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout="coderabbit version 1.2.3",
                    returncode=0,
                )

                result = await service.check_health()

        assert result["available"] is True
        assert result["path"] == "/usr/local/bin/coderabbit"
        assert "1.2.3" in result["version"]

    @pytest.mark.asyncio
    async def test_health_when_not_available(self):
        """Test health check when CLI is not available."""
        service = CodeRabbitService()

        with patch.object(service, "_check_cli_available", return_value=False):
            result = await service.check_health()

        assert result["available"] is False
        assert result["version"] is None


class TestReviewFixFunction:
    """Tests for the module-level review_fix function."""

    @pytest.mark.asyncio
    async def test_review_fix_function(self, sample_fix):
        """Test the convenience function."""
        with patch("backend.services.coderabbit.CodeRabbitService") as mock_class:
            mock_service = MagicMock()
            mock_result = ReviewResult(
                passed=True,
                issues=[],
                suggestions=[],
                summary="",
            )
            mock_service.review = MagicMock(return_value=mock_result)
            mock_class.return_value = mock_service

            # Make the mock async
            async def mock_review(fix):
                return mock_result

            mock_service.review = mock_review

            result = await review_fix(sample_fix)

            assert result.passed is True
            mock_class.assert_called_once()


class TestBlockingSeverities:
    """Tests for blocking severity logic."""

    def test_blocking_severities_defined(self):
        """Test that blocking severities are defined."""
        service = CodeRabbitService()

        assert "critical" in service.BLOCKING_SEVERITIES
        assert "high" in service.BLOCKING_SEVERITIES
        assert "security" in service.BLOCKING_SEVERITIES
        assert "error" in service.BLOCKING_SEVERITIES

    def test_low_severity_not_blocking(self):
        """Test that low severity is not blocking."""
        service = CodeRabbitService()

        assert "low" not in service.BLOCKING_SEVERITIES
        assert "medium" not in service.BLOCKING_SEVERITIES
        assert "info" not in service.BLOCKING_SEVERITIES
