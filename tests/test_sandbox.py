"""
Tests for Sandbox Service.

Tests the Kind cluster management for ephemeral testing environments.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import subprocess
import tempfile
from datetime import datetime

from backend.services.sandbox import (
    SandboxService,
    Sandbox,
    SandboxError,
    SandboxCreationError,
    SandboxTestError,
    KindNotInstalledError,
    run_sandbox_tests,
)
from backend.models import FixResult, TestResult


# Sample test outputs for mocking
SAMPLE_GO_TEST_PASS = """
=== RUN   TestProcessCase
--- PASS: TestProcessCase (0.00s)
=== RUN   TestHandleError
--- PASS: TestHandleError (0.01s)
PASS
ok  	github.com/nucleus/caseservice	0.015s
"""

SAMPLE_GO_TEST_FAIL = """
=== RUN   TestProcessCase
--- PASS: TestProcessCase (0.00s)
=== RUN   TestHandleError
    handler_test.go:42: expected nil, got error
--- FAIL: TestHandleError (0.01s)
FAIL
FAIL	github.com/nucleus/caseservice	0.015s
"""

SAMPLE_GO_TEST_MULTIPLE_PACKAGES = """
ok  	github.com/nucleus/caseservice	0.015s
ok  	github.com/nucleus/alertservice	0.022s
FAIL	github.com/nucleus/userservice	0.010s
"""


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


@pytest.fixture
def sample_sandbox():
    """Create a sample sandbox for testing."""
    return Sandbox(
        id="sandbox-och-test001-abc1",
        incident_id="OCH-TEST001",
        cluster_name="och-test001-abc1",
        status="created",
    )


class TestSandboxDataclass:
    """Tests for the Sandbox dataclass."""

    def test_sandbox_creation(self):
        """Test creating a sandbox."""
        sandbox = Sandbox(
            id="sandbox-test",
            incident_id="OCH-12345",
            cluster_name="och-12345-abcd",
        )

        assert sandbox.id == "sandbox-test"
        assert sandbox.incident_id == "OCH-12345"
        assert sandbox.cluster_name == "och-12345-abcd"
        assert sandbox.status == "created"
        assert sandbox.work_dir is None
        assert sandbox.created_at is not None

    def test_sandbox_to_dict(self):
        """Test sandbox to_dict method."""
        sandbox = Sandbox(
            id="sandbox-test",
            incident_id="OCH-12345",
            cluster_name="och-12345-abcd",
            work_dir=Path("/tmp/test"),
        )

        result = sandbox.to_dict()

        assert result["id"] == "sandbox-test"
        assert result["incident_id"] == "OCH-12345"
        assert result["cluster_name"] == "och-12345-abcd"
        assert result["work_dir"] == "/tmp/test"
        assert result["status"] == "created"
        assert "created_at" in result

    def test_sandbox_to_dict_no_work_dir(self):
        """Test sandbox to_dict with no work directory."""
        sandbox = Sandbox(
            id="sandbox-test",
            incident_id="OCH-12345",
            cluster_name="och-12345-abcd",
        )

        result = sandbox.to_dict()

        assert result["work_dir"] is None


class TestSandboxServiceInit:
    """Tests for SandboxService initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default settings."""
        with patch("backend.services.sandbox.settings") as mock_settings:
            mock_settings.nucleus_repo_path = Path("/default/nucleus")
            mock_settings.sandbox_timeout_minutes = 30

            service = SandboxService()

            assert service.nucleus_repo_path == Path("/default/nucleus")
            assert service.timeout_minutes == 30

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        service = SandboxService(
            nucleus_repo_path=Path("/custom/nucleus"),
            sandbox_timeout_minutes=60,
        )

        assert service.nucleus_repo_path == Path("/custom/nucleus")
        assert service.timeout_minutes == 60


class TestCheckKindAvailable:
    """Tests for Kind CLI availability checking."""

    def test_kind_found_in_path(self):
        """Test Kind found via shutil.which."""
        service = SandboxService()

        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/kind"

            result = service._check_kind_available()

            assert result is True
            assert service._kind_path == "/usr/local/bin/kind"

    def test_kind_not_found(self):
        """Test Kind not found."""
        service = SandboxService()

        with patch("shutil.which") as mock_which:
            mock_which.return_value = None

            result = service._check_kind_available()

            assert result is False
            assert service._kind_available is False

    def test_kind_check_cached(self):
        """Test that Kind check is cached."""
        service = SandboxService()
        service._kind_available = True
        service._kind_path = "/cached/path"

        # Should return cached value without calling which
        with patch("shutil.which") as mock_which:
            result = service._check_kind_available()

            assert result is True
            mock_which.assert_not_called()


class TestGenerateClusterName:
    """Tests for cluster name generation."""

    def test_generate_cluster_name(self):
        """Test cluster name generation."""
        service = SandboxService()

        name = service._generate_cluster_name("OCH-12345678")

        assert name.startswith("och-")
        assert len(name) <= 20  # Keep names short

    def test_generate_cluster_name_lowercase(self):
        """Test that cluster names are lowercase."""
        service = SandboxService()

        name = service._generate_cluster_name("OCH-ABCDEFGH")

        assert name == name.lower()

    def test_generate_cluster_names_unique(self):
        """Test that cluster names are unique."""
        service = SandboxService()

        names = set()
        for _ in range(10):
            names.add(service._generate_cluster_name("OCH-12345678"))

        assert len(names) == 10


class TestRunCommand:
    """Tests for command execution."""

    def test_run_command_success(self):
        """Test successful command execution."""
        service = SandboxService()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="output",
                stderr="",
                returncode=0,
            )

            result = service._run_command(["echo", "hello"], timeout=10)

            assert result.returncode == 0
            assert result.stdout == "output"

    def test_run_command_with_cwd(self):
        """Test command execution with working directory."""
        service = SandboxService()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            service._run_command(["ls"], timeout=10, cwd=Path("/tmp"))

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["cwd"] == Path("/tmp")

    def test_run_command_with_env(self):
        """Test command execution with environment variables."""
        service = SandboxService()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            service._run_command(["echo"], timeout=10, env={"FOO": "bar"})

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert "FOO" in call_kwargs["env"]

    def test_run_command_timeout(self):
        """Test command timeout handling."""
        service = SandboxService()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["slow_cmd"], timeout=10
            )

            with pytest.raises(SandboxError) as exc_info:
                service._run_command(["slow_cmd"], timeout=10)

            assert "timed out" in str(exc_info.value)


class TestCreateSandbox:
    """Tests for sandbox creation."""

    @pytest.fixture
    def mock_kind_available(self):
        """Mock Kind as available."""
        with patch.object(SandboxService, "_check_kind_available", return_value=True):
            yield

    @pytest.mark.asyncio
    async def test_create_sandbox_success(self, mock_kind_available):
        """Test successful sandbox creation."""
        service = SandboxService()
        service._kind_path = "/usr/local/bin/kind"

        with patch.object(service, "_run_command") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Creating cluster...\nCluster created!",
                stderr="",
                returncode=0,
            )

            sandbox = await service.create_sandbox("OCH-TEST001")

            assert sandbox.incident_id == "OCH-TEST001"
            assert sandbox.cluster_name.startswith("och-")
            assert sandbox.status == "created"

    @pytest.mark.asyncio
    async def test_create_sandbox_kind_not_installed(self):
        """Test error when Kind is not installed."""
        service = SandboxService()

        with patch.object(service, "_check_kind_available", return_value=False):
            with pytest.raises(KindNotInstalledError) as exc_info:
                await service.create_sandbox("OCH-TEST001")

            assert "not installed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_sandbox_cluster_creation_fails(self, mock_kind_available):
        """Test handling of cluster creation failure."""
        service = SandboxService()
        service._kind_path = "/usr/local/bin/kind"

        with patch.object(service, "_run_command") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="",
                stderr="Error: failed to create cluster",
                returncode=1,
            )

            with pytest.raises(SandboxCreationError) as exc_info:
                await service.create_sandbox("OCH-TEST001")

            assert "Failed to create" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_sandbox_uses_kind_config(self, mock_kind_available):
        """Test that kind-config.yaml is used when present."""
        service = SandboxService()
        service._kind_path = "/usr/local/bin/kind"

        with patch.object(service, "_run_command") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            with patch.object(Path, "exists", return_value=True):
                await service.create_sandbox("OCH-TEST001")

            call_args = mock_run.call_args[0][0]
            assert "--config" in call_args


class TestApplyFix:
    """Tests for applying fixes to sandbox."""

    @pytest.mark.asyncio
    async def test_apply_fix_success(self, sample_sandbox, sample_fix):
        """Test successful fix application."""
        service = SandboxService()
        service.nucleus_repo_path = Path("/nucleus")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the file to be fixed
            fix_dir = Path(tmpdir) / "backend" / "services" / "caseservice"
            fix_dir.mkdir(parents=True)
            fix_file = fix_dir / "handler.go"
            fix_file.write_text(sample_fix.original_code)

            with patch.object(service, "_run_command") as mock_run:
                # Mock git clone
                def mock_clone(*args, **kwargs):
                    # Simulate git clone by creating file
                    return MagicMock(returncode=0)

                mock_run.return_value = MagicMock(returncode=0)

                with patch("tempfile.mkdtemp", return_value=tmpdir):
                    await service.apply_fix(sample_sandbox, sample_fix)

            assert sample_sandbox.work_dir == Path(tmpdir)
            assert sample_sandbox.status in ["fix_applied", "deployed"]

    @pytest.mark.asyncio
    async def test_apply_fix_clone_fails(self, sample_sandbox, sample_fix):
        """Test handling of git clone failure."""
        service = SandboxService()
        service.nucleus_repo_path = Path("/nucleus")

        with patch.object(service, "_run_command") as mock_run:
            mock_run.return_value = MagicMock(
                stderr="Error: repository not found",
                returncode=1,
            )

            with patch("tempfile.mkdtemp", return_value="/tmp/test"):
                with pytest.raises(SandboxError) as exc_info:
                    await service.apply_fix(sample_sandbox, sample_fix)

            assert "Failed to clone" in str(exc_info.value)
            assert sample_sandbox.status == "failed"

    @pytest.mark.asyncio
    async def test_apply_fix_creates_missing_file(self, sample_sandbox, sample_fix):
        """Test that missing files are created."""
        service = SandboxService()
        service.nucleus_repo_path = Path("/nucleus")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(service, "_run_command") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                with patch("tempfile.mkdtemp", return_value=tmpdir):
                    await service.apply_fix(sample_sandbox, sample_fix)

            # Check file was created
            fix_path = Path(tmpdir) / sample_fix.file_path
            assert fix_path.exists()
            assert sample_fix.fixed_code in fix_path.read_text()


class TestRunTests:
    """Tests for running tests in sandbox."""

    @pytest.mark.asyncio
    async def test_run_tests_all_pass(self, sample_sandbox):
        """Test running tests that all pass."""
        service = SandboxService()

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_sandbox.work_dir = Path(tmpdir)

            with patch.object(service, "_run_command") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout=SAMPLE_GO_TEST_PASS,
                    stderr="",
                    returncode=0,
                )

                with patch.object(Path, "exists", return_value=True):
                    result = await service.run_tests(sample_sandbox)

            assert result.passed is True
            assert result.unit_tests_passed is True
            assert result.tests_passed >= 2

    @pytest.mark.asyncio
    async def test_run_tests_unit_fail(self, sample_sandbox):
        """Test running tests where unit tests fail."""
        service = SandboxService()

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_sandbox.work_dir = Path(tmpdir)

            with patch.object(service, "_run_command") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout=SAMPLE_GO_TEST_FAIL,
                    stderr="",
                    returncode=1,
                )

                with patch.object(Path, "exists", return_value=True):
                    result = await service.run_tests(sample_sandbox)

            assert result.passed is False
            assert result.unit_tests_passed is False
            assert result.tests_failed >= 1

    @pytest.mark.asyncio
    async def test_run_tests_no_work_dir(self, sample_sandbox):
        """Test error when work directory is missing."""
        service = SandboxService()
        sample_sandbox.work_dir = None

        with pytest.raises(SandboxTestError) as exc_info:
            await service.run_tests(sample_sandbox)

        assert "work directory not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_tests_smoke_after_unit(self, sample_sandbox):
        """Test that smoke tests run after unit tests pass."""
        service = SandboxService()

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_sandbox.work_dir = Path(tmpdir)

            call_count = [0]

            def mock_run_command(cmd, *args, **kwargs):
                call_count[0] += 1
                return MagicMock(
                    stdout=SAMPLE_GO_TEST_PASS,
                    stderr="",
                    returncode=0,
                )

            with patch.object(service, "_run_command", side_effect=mock_run_command):
                with patch.object(Path, "exists", return_value=True):
                    result = await service.run_tests(sample_sandbox)

            # Should have run both unit and smoke tests
            assert call_count[0] >= 2
            assert result.smoke_tests_passed is True

    @pytest.mark.asyncio
    async def test_run_tests_skip_smoke_on_unit_fail(self, sample_sandbox):
        """Test that smoke tests are skipped when unit tests fail."""
        service = SandboxService()

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_sandbox.work_dir = Path(tmpdir)

            call_count = [0]

            def mock_run_command(cmd, *args, **kwargs):
                call_count[0] += 1
                return MagicMock(
                    stdout=SAMPLE_GO_TEST_FAIL,
                    stderr="",
                    returncode=1,
                )

            with patch.object(service, "_run_command", side_effect=mock_run_command):
                with patch.object(Path, "exists", return_value=True):
                    result = await service.run_tests(sample_sandbox)

            # Should only have run unit tests (not smoke)
            assert call_count[0] == 1
            assert result.smoke_tests_passed is None


class TestParseTestCounts:
    """Tests for parsing test output."""

    def test_parse_passing_tests(self):
        """Test parsing output with passing tests."""
        service = SandboxService()

        run, passed, failed = service._parse_test_counts(SAMPLE_GO_TEST_PASS)

        assert passed == 2
        assert failed == 0
        assert run == 2

    def test_parse_failing_tests(self):
        """Test parsing output with failing tests."""
        service = SandboxService()

        run, passed, failed = service._parse_test_counts(SAMPLE_GO_TEST_FAIL)

        assert passed == 1
        assert failed == 1
        assert run == 2

    def test_parse_package_level_results(self):
        """Test parsing package-level results."""
        service = SandboxService()

        run, passed, failed = service._parse_test_counts(SAMPLE_GO_TEST_MULTIPLE_PACKAGES)

        # Should count from package-level results
        assert passed >= 2
        assert failed >= 1

    def test_parse_empty_output(self):
        """Test parsing empty output."""
        service = SandboxService()

        run, passed, failed = service._parse_test_counts("")

        assert run == 0
        assert passed == 0
        assert failed == 0


class TestCleanup:
    """Tests for sandbox cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_success(self, sample_sandbox):
        """Test successful cleanup."""
        service = SandboxService()
        service._kind_path = "/usr/local/bin/kind"

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_sandbox.work_dir = Path(tmpdir)

            with patch.object(service, "_check_kind_available", return_value=True):
                with patch.object(service, "_run_command") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)

                    with patch.object(Path, "exists", return_value=False):
                        await service.cleanup(sample_sandbox)

            assert sample_sandbox.status == "cleaned_up"

    @pytest.mark.asyncio
    async def test_cleanup_with_errors_logs_warning(self, sample_sandbox):
        """Test that cleanup errors are logged but don't raise."""
        service = SandboxService()
        service._kind_path = "/usr/local/bin/kind"

        with patch.object(service, "_check_kind_available", return_value=True):
            with patch.object(service, "_run_command") as mock_run:
                mock_run.return_value = MagicMock(
                    stderr="Error deleting cluster",
                    returncode=1,
                )

                # Should not raise
                await service.cleanup(sample_sandbox)

        assert sample_sandbox.status == "cleaned_up"

    @pytest.mark.asyncio
    async def test_cleanup_removes_work_dir(self, sample_sandbox):
        """Test that work directory is removed."""
        service = SandboxService()

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_sandbox.work_dir = Path(tmpdir)
            # Create a file in the directory
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("test")

            with patch.object(service, "_check_kind_available", return_value=False):
                await service.cleanup(sample_sandbox)

            assert not test_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_uses_cleanup_script(self, sample_sandbox):
        """Test that cleanup script is used when available."""
        service = SandboxService()
        service._kind_path = "/usr/local/bin/kind"

        with patch.object(service, "_check_kind_available", return_value=True):
            with patch.object(service, "_run_command") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                with patch.object(Path, "exists", return_value=True):
                    await service.cleanup(sample_sandbox)

                # Check cleanup script was called
                call_args = mock_run.call_args_list[0][0][0]
                assert "cleanup" in str(call_args[0]).lower()


class TestListClusters:
    """Tests for listing sandbox clusters."""

    @pytest.mark.asyncio
    async def test_list_clusters_success(self):
        """Test listing clusters."""
        service = SandboxService()
        service._kind_path = "/usr/local/bin/kind"

        with patch.object(service, "_check_kind_available", return_value=True):
            with patch.object(service, "_run_command") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout="och-test1-abc1\noch-test2-def2\nother-cluster\n",
                    returncode=0,
                )

                result = await service.list_clusters()

        # Should only return clusters starting with "och-"
        assert len(result) == 2
        assert "och-test1-abc1" in result
        assert "och-test2-def2" in result
        assert "other-cluster" not in result

    @pytest.mark.asyncio
    async def test_list_clusters_kind_not_available(self):
        """Test listing clusters when Kind is not available."""
        service = SandboxService()

        with patch.object(service, "_check_kind_available", return_value=False):
            result = await service.list_clusters()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_clusters_empty(self):
        """Test listing clusters when none exist."""
        service = SandboxService()
        service._kind_path = "/usr/local/bin/kind"

        with patch.object(service, "_check_kind_available", return_value=True):
            with patch.object(service, "_run_command") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout="",
                    returncode=0,
                )

                result = await service.list_clusters()

        assert result == []


class TestCheckHealth:
    """Tests for health check."""

    @pytest.mark.asyncio
    async def test_health_when_available(self):
        """Test health check when Kind is available."""
        service = SandboxService()

        with patch.object(service, "_check_kind_available", return_value=True):
            service._kind_path = "/usr/local/bin/kind"

            with patch.object(service, "_run_command") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout="kind v0.20.0 go1.21.1 darwin/arm64",
                    returncode=0,
                )

                with patch.object(service, "list_clusters", return_value=["och-test1"]):
                    result = await service.check_health()

        assert result["kind_available"] is True
        assert result["kind_path"] == "/usr/local/bin/kind"
        assert "0.20.0" in result["kind_version"]
        assert "och-test1" in result["active_clusters"]

    @pytest.mark.asyncio
    async def test_health_when_not_available(self):
        """Test health check when Kind is not available."""
        service = SandboxService()

        with patch.object(service, "_check_kind_available", return_value=False):
            result = await service.check_health()

        assert result["kind_available"] is False
        assert result["kind_version"] is None


class TestRunSandboxTestsFunction:
    """Tests for the module-level run_sandbox_tests function."""

    @pytest.mark.asyncio
    async def test_run_sandbox_tests_full_pipeline(self, sample_fix):
        """Test the full sandbox testing pipeline."""
        with patch("backend.services.sandbox.SandboxService") as mock_class:
            mock_service = MagicMock()
            mock_sandbox = Sandbox(
                id="sandbox-test",
                incident_id="OCH-TEST001",
                cluster_name="och-test-1234",
            )
            mock_result = TestResult(
                incident_id="OCH-TEST001",
                passed=True,
                unit_tests_passed=True,
                tests_run=5,
                tests_passed=5,
                tests_failed=0,
                duration_ms=1000,
            )

            async def mock_create(incident_id):
                return mock_sandbox

            async def mock_apply(sandbox, fix):
                pass

            async def mock_run_tests(sandbox):
                return mock_result

            async def mock_cleanup(sandbox):
                pass

            mock_service.create_sandbox = mock_create
            mock_service.apply_fix = mock_apply
            mock_service.run_tests = mock_run_tests
            mock_service.cleanup = mock_cleanup
            mock_class.return_value = mock_service

            result = await run_sandbox_tests("OCH-TEST001", sample_fix)

            assert result.passed is True
            assert result.incident_id == "OCH-TEST001"

    @pytest.mark.asyncio
    async def test_run_sandbox_tests_cleanup_on_error(self, sample_fix):
        """Test that cleanup runs even on error."""
        with patch("backend.services.sandbox.SandboxService") as mock_class:
            mock_service = MagicMock()
            mock_sandbox = Sandbox(
                id="sandbox-test",
                incident_id="OCH-TEST001",
                cluster_name="och-test-1234",
            )

            cleanup_called = [False]

            async def mock_create(incident_id):
                return mock_sandbox

            async def mock_apply(sandbox, fix):
                raise SandboxError("Apply failed")

            async def mock_cleanup(sandbox):
                cleanup_called[0] = True

            mock_service.create_sandbox = mock_create
            mock_service.apply_fix = mock_apply
            mock_service.cleanup = mock_cleanup
            mock_class.return_value = mock_service

            with pytest.raises(SandboxError):
                await run_sandbox_tests("OCH-TEST001", sample_fix)

            assert cleanup_called[0] is True


class TestTimeouts:
    """Tests for timeout configurations."""

    def test_default_timeouts(self):
        """Test default timeout values."""
        assert SandboxService.CLUSTER_CREATE_TIMEOUT == 180
        assert SandboxService.DEPLOY_TIMEOUT == 300
        assert SandboxService.TEST_TIMEOUT == 600

    def test_script_dir_path(self):
        """Test script directory path is set."""
        assert SandboxService.SCRIPT_DIR is not None
        assert "sandbox" in str(SandboxService.SCRIPT_DIR)
