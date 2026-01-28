"""
Sandbox Service for On Call Helper.

Manages ephemeral Kind clusters for testing code fixes in isolation.
Matches the Nucleus development environment for accurate testing.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.models import FixResult, TestResult

logger = logging.getLogger(__name__)


class SandboxError(Exception):
    """Base error for sandbox operations."""
    pass


class SandboxCreationError(SandboxError):
    """Error creating sandbox environment."""
    pass


class SandboxTestError(SandboxError):
    """Error running tests in sandbox."""
    pass


class KindNotInstalledError(SandboxError):
    """Kind CLI is not installed."""
    pass


@dataclass
class Sandbox:
    """Represents an ephemeral sandbox environment."""

    id: str
    incident_id: str
    cluster_name: str
    work_dir: Optional[Path] = None
    status: str = "created"
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "cluster_name": self.cluster_name,
            "work_dir": str(self.work_dir) if self.work_dir else None,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class SandboxService:
    """
    Manages ephemeral Kind clusters for testing fixes.

    Creates isolated Kubernetes environments that mirror the Nucleus
    production setup for safe testing of code changes.
    """

    # Paths to sandbox scripts
    SCRIPT_DIR = Path(__file__).parent.parent.parent / "sandbox"

    # Timeouts in seconds
    CLUSTER_CREATE_TIMEOUT = 180  # 3 minutes
    DEPLOY_TIMEOUT = 300  # 5 minutes
    TEST_TIMEOUT = 600  # 10 minutes

    def __init__(
        self,
        nucleus_repo_path: Optional[Path] = None,
        sandbox_timeout_minutes: Optional[int] = None,
    ):
        """
        Initialize the sandbox service.

        Args:
            nucleus_repo_path: Path to Nucleus repository
            sandbox_timeout_minutes: Overall sandbox timeout
        """
        self.nucleus_repo_path = nucleus_repo_path or settings.nucleus_repo_path
        self.timeout_minutes = sandbox_timeout_minutes or settings.sandbox_timeout_minutes
        self._kind_available: Optional[bool] = None
        self._kind_path: Optional[str] = None

    def _check_kind_available(self) -> bool:
        """Check if Kind CLI is installed."""
        if self._kind_available is not None:
            return self._kind_available

        self._kind_path = shutil.which("kind")

        if self._kind_path:
            self._kind_available = True
            logger.debug(f"Kind CLI found at: {self._kind_path}")
            return True

        self._kind_available = False
        logger.warning("Kind CLI not found")
        return False

    def _generate_cluster_name(self, incident_id: str) -> str:
        """Generate a unique cluster name."""
        short_id = incident_id.replace("OCH-", "")[:8].lower()
        unique = uuid.uuid4().hex[:4]
        return f"och-{short_id}-{unique}"

    def _run_command(
        self,
        cmd: List[str],
        timeout: int,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        """
        Run a shell command with timeout.

        Args:
            cmd: Command and arguments
            timeout: Timeout in seconds
            cwd: Working directory
            env: Environment variables

        Returns:
            CompletedProcess result
        """
        full_env = os.environ.copy()
        if env:
            full_env.update(env)

        logger.debug(f"Running command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=full_env,
            )
            return result
        except subprocess.TimeoutExpired as e:
            raise SandboxError(f"Command timed out after {timeout}s: {' '.join(cmd)}")

    async def create_sandbox(self, incident_id: str) -> Sandbox:
        """
        Create a new sandbox environment with Kind cluster.

        Args:
            incident_id: ID of the incident being tested

        Returns:
            Sandbox object with cluster details

        Raises:
            KindNotInstalledError: If Kind is not installed
            SandboxCreationError: If cluster creation fails
        """
        if not self._check_kind_available():
            raise KindNotInstalledError(
                "Kind CLI is not installed. Install from: https://kind.sigs.k8s.io/"
            )

        cluster_name = self._generate_cluster_name(incident_id)
        sandbox_id = f"sandbox-{cluster_name}"

        logger.info(f"Creating sandbox for {incident_id}: {cluster_name}")

        # Get Kind config path
        kind_config = self.SCRIPT_DIR / "kind-config.yaml"
        if not kind_config.exists():
            kind_config = None

        # Build Kind create command
        cmd = [self._kind_path, "create", "cluster", "--name", cluster_name]
        if kind_config:
            cmd.extend(["--config", str(kind_config)])

        try:
            result = self._run_command(cmd, self.CLUSTER_CREATE_TIMEOUT)

            if result.returncode != 0:
                raise SandboxCreationError(
                    f"Failed to create Kind cluster: {result.stderr}"
                )

            logger.info(f"Created Kind cluster: {cluster_name}")

            return Sandbox(
                id=sandbox_id,
                incident_id=incident_id,
                cluster_name=cluster_name,
                status="created",
            )

        except SandboxError:
            raise
        except Exception as e:
            raise SandboxCreationError(f"Failed to create sandbox: {e}")

    async def apply_fix(self, sandbox: Sandbox, fix: FixResult) -> None:
        """
        Apply a code fix to the sandbox environment.

        Clones the Nucleus repo, applies the fix, and builds images.

        Args:
            sandbox: The sandbox environment
            fix: The fix to apply

        Raises:
            SandboxError: If applying fix fails
        """
        logger.info(f"Applying fix for {fix.incident_id} to sandbox {sandbox.id}")

        # Create work directory
        work_dir = Path(tempfile.mkdtemp(prefix=f"och-sandbox-{sandbox.cluster_name}-"))
        sandbox.work_dir = work_dir
        sandbox.status = "preparing"

        try:
            # Clone Nucleus repository
            logger.debug(f"Cloning Nucleus repo to {work_dir}")
            result = self._run_command(
                ["git", "clone", "--depth=1", str(self.nucleus_repo_path), str(work_dir)],
                timeout=60,
            )

            if result.returncode != 0:
                raise SandboxError(f"Failed to clone repository: {result.stderr}")

            # Apply the fix
            fix_path = work_dir / fix.file_path
            if not fix_path.parent.exists():
                fix_path.parent.mkdir(parents=True)

            if fix_path.exists():
                original_content = fix_path.read_text()
                if fix.original_code in original_content:
                    fixed_content = original_content.replace(
                        fix.original_code, fix.fixed_code
                    )
                    fix_path.write_text(fixed_content)
                    logger.debug(f"Applied fix to {fix.file_path}")
                else:
                    logger.warning(
                        f"Original code not found in {fix.file_path}, "
                        "writing full fixed code"
                    )
                    fix_path.write_text(fix.fixed_code)
            else:
                logger.warning(f"File {fix.file_path} not found, creating it")
                fix_path.write_text(fix.fixed_code)

            sandbox.status = "fix_applied"

            # Deploy to cluster (optional, depends on test requirements)
            deploy_script = self.SCRIPT_DIR / "deploy.sh"
            if deploy_script.exists():
                logger.debug("Running deploy script")
                result = self._run_command(
                    [str(deploy_script), sandbox.cluster_name, str(work_dir)],
                    self.DEPLOY_TIMEOUT,
                )
                if result.returncode != 0:
                    logger.warning(f"Deploy script failed: {result.stderr}")
                    # Don't fail - deployment is optional for unit tests

            sandbox.status = "deployed"
            logger.info(f"Fix applied to sandbox {sandbox.id}")

        except SandboxError:
            sandbox.status = "failed"
            raise
        except Exception as e:
            sandbox.status = "failed"
            raise SandboxError(f"Failed to apply fix: {e}")

    async def run_tests(self, sandbox: Sandbox) -> TestResult:
        """
        Run tests in the sandbox environment.

        Executes unit tests and smoke tests if unit tests pass.

        Args:
            sandbox: The sandbox environment

        Returns:
            TestResult with test outcomes

        Raises:
            SandboxTestError: If test execution fails
        """
        if not sandbox.work_dir or not sandbox.work_dir.exists():
            raise SandboxTestError("Sandbox work directory not found")

        logger.info(f"Running tests in sandbox {sandbox.id}")
        sandbox.status = "testing"

        start_time = datetime.utcnow()
        unit_output = ""
        smoke_output = ""
        unit_passed = False
        smoke_passed = None

        try:
            # Run unit tests
            test_script = self.SCRIPT_DIR / "run-tests.sh"
            if test_script.exists():
                result = self._run_command(
                    [str(test_script), str(sandbox.work_dir), "unit"],
                    self.TEST_TIMEOUT,
                )
                unit_output = result.stdout + result.stderr
                unit_passed = result.returncode == 0
            else:
                # Fallback to direct task/go test
                result = self._run_command(
                    ["go", "test", "./...", "-v"],
                    self.TEST_TIMEOUT,
                    cwd=sandbox.work_dir,
                )
                unit_output = result.stdout + result.stderr
                unit_passed = result.returncode == 0

            # Run smoke tests if unit tests passed
            if unit_passed:
                if test_script.exists():
                    result = self._run_command(
                        [str(test_script), str(sandbox.work_dir), "smoke"],
                        self.TEST_TIMEOUT,
                    )
                    smoke_output = result.stdout + result.stderr
                    smoke_passed = result.returncode == 0
                else:
                    # Try to run smoke tests directly
                    smoke_dir = sandbox.work_dir / "test" / "smoke"
                    if smoke_dir.exists():
                        result = self._run_command(
                            ["go", "test", "./test/smoke/...", "-v"],
                            self.TEST_TIMEOUT,
                            cwd=sandbox.work_dir,
                        )
                        smoke_output = result.stdout + result.stderr
                        smoke_passed = result.returncode == 0

            end_time = datetime.utcnow()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            # Parse test counts from output
            tests_run, tests_passed, tests_failed = self._parse_test_counts(
                unit_output + smoke_output
            )

            # Determine overall pass status
            passed = unit_passed and (smoke_passed is None or smoke_passed)

            sandbox.status = "tested"

            return TestResult(
                incident_id=sandbox.incident_id,
                passed=passed,
                unit_tests_passed=unit_passed,
                unit_tests_output=unit_output[:10000],  # Truncate long output
                smoke_tests_passed=smoke_passed,
                smoke_tests_output=smoke_output[:10000] if smoke_output else None,
                tests_run=tests_run,
                tests_passed=tests_passed,
                tests_failed=tests_failed,
                duration_ms=duration_ms,
            )

        except SandboxError:
            sandbox.status = "test_failed"
            raise
        except Exception as e:
            sandbox.status = "test_failed"
            raise SandboxTestError(f"Failed to run tests: {e}")

    def _parse_test_counts(self, output: str) -> tuple:
        """Parse test counts from test output."""
        import re

        tests_run = 0
        tests_passed = 0
        tests_failed = 0

        # Go test output patterns
        # "ok  	package	0.123s"
        # "FAIL	package	0.123s"
        # "--- PASS: TestName (0.00s)"
        # "--- FAIL: TestName (0.00s)"

        pass_pattern = r"--- PASS:"
        fail_pattern = r"--- FAIL:"

        tests_passed = len(re.findall(pass_pattern, output))
        tests_failed = len(re.findall(fail_pattern, output))
        tests_run = tests_passed + tests_failed

        # Also check for package-level results
        ok_packages = len(re.findall(r"^ok\s+", output, re.MULTILINE))
        fail_packages = len(re.findall(r"^FAIL\s+", output, re.MULTILINE))

        # Use package counts if individual test counts are 0
        if tests_run == 0:
            tests_passed = ok_packages
            tests_failed = fail_packages
            tests_run = ok_packages + fail_packages

        return tests_run, tests_passed, tests_failed

    async def cleanup(self, sandbox: Sandbox) -> None:
        """
        Clean up sandbox environment.

        Deletes the Kind cluster and work directory.

        Args:
            sandbox: The sandbox to clean up
        """
        logger.info(f"Cleaning up sandbox {sandbox.id}")

        errors = []

        # Delete Kind cluster
        if self._check_kind_available():
            cleanup_script = self.SCRIPT_DIR / "cleanup.sh"

            if cleanup_script.exists():
                try:
                    result = self._run_command(
                        [
                            str(cleanup_script),
                            sandbox.cluster_name,
                            str(sandbox.work_dir) if sandbox.work_dir else "",
                        ],
                        timeout=60,
                    )
                    if result.returncode != 0:
                        errors.append(f"Cleanup script failed: {result.stderr}")
                except Exception as e:
                    errors.append(f"Cleanup script error: {e}")
            else:
                # Direct Kind delete
                try:
                    result = self._run_command(
                        [self._kind_path, "delete", "cluster", "--name", sandbox.cluster_name],
                        timeout=60,
                    )
                    if result.returncode != 0:
                        errors.append(f"Kind delete failed: {result.stderr}")
                except Exception as e:
                    errors.append(f"Kind delete error: {e}")

        # Clean up work directory
        if sandbox.work_dir and sandbox.work_dir.exists():
            try:
                shutil.rmtree(sandbox.work_dir)
                logger.debug(f"Removed work directory: {sandbox.work_dir}")
            except Exception as e:
                errors.append(f"Failed to remove work directory: {e}")

        sandbox.status = "cleaned_up"

        if errors:
            logger.warning(f"Cleanup completed with errors: {errors}")
        else:
            logger.info(f"Sandbox {sandbox.id} cleaned up successfully")

    async def list_clusters(self) -> List[str]:
        """List all On Call Helper sandbox clusters."""
        if not self._check_kind_available():
            return []

        result = self._run_command(
            [self._kind_path, "get", "clusters"],
            timeout=10,
        )

        if result.returncode != 0:
            return []

        clusters = result.stdout.strip().split("\n")
        return [c for c in clusters if c.startswith("och-")]

    async def check_health(self) -> Dict[str, Any]:
        """Check sandbox service health."""
        kind_available = self._check_kind_available()

        result = {
            "kind_available": kind_available,
            "kind_path": self._kind_path,
            "kind_version": None,
            "active_clusters": [],
        }

        if kind_available:
            # Get Kind version
            try:
                version_result = self._run_command(
                    [self._kind_path, "version"],
                    timeout=10,
                )
                result["kind_version"] = version_result.stdout.strip()
            except Exception:
                pass

            # List active clusters
            try:
                result["active_clusters"] = await self.list_clusters()
            except Exception:
                pass

        return result


# Module-level convenience function
async def run_sandbox_tests(
    incident_id: str,
    fix: FixResult,
) -> TestResult:
    """
    Run a fix through the sandbox testing pipeline.

    Creates sandbox, applies fix, runs tests, and cleans up.

    Args:
        incident_id: Incident ID
        fix: The fix to test

    Returns:
        TestResult with outcomes
    """
    service = SandboxService()
    sandbox = None

    try:
        sandbox = await service.create_sandbox(incident_id)
        await service.apply_fix(sandbox, fix)
        return await service.run_tests(sandbox)
    finally:
        if sandbox:
            await service.cleanup(sandbox)
