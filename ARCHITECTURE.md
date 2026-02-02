# On Call Helper - Architecture & Implementation Guide

A standalone AI-powered incident response agent that monitors the Nucleus MDR platform for production errors, automatically triages issues, generates fixes, validates them thoroughly, and creates PRs.

---

## 🎯 Current Implementation Status

**Last Updated**: February 2026

### ✅ Fully Implemented
- **Error Ingestion**: GCP Cloud Logging integration (webhook + polling modes)
- **Filtering**: Transient error filter (20+ patterns) and tenant filter (explicit lists)
- **Triage Agent**: Claude AI with embedded SRE knowledge + GCP context fetching
- **Fixer Agent**: Code fix generation with fuzzy matching and local file reading
- **GitHub Integration**: PR creation via `git` + `gh` CLI
- **WebSocket Manager**: Real-time event broadcasting to dashboard
- **Storage**: In-memory storage (with optional Firestore backend)
- **Frontend**: React dashboard with real-time updates
- **Orchestrator**: Full pipeline coordination with error handling

### ⚠️ Optional Features (Can Be Skipped)
- **CodeRabbit Review**: Code review integration (skips if not installed)
- **Sandbox Testing**: Kind cluster testing (skips if Kind unavailable)
- **Production Verification**: Post-deploy monitoring (can be disabled)
- **PagerDuty**: Team notifications (optional)

### 📊 Key Metrics
- **Storage Backend**: In-memory (default) or Firestore (optional)
- **GCP Integration**: Webhook (Pub/Sub push) or Polling (API queries)
- **Source Code**: Reads from local Nucleus repository filesystem
- **PR Creation**: Uses `git` commands + GitHub CLI (`gh`)

---

## Repository References

> **IMPORTANT**: When implementing this system, always reference these repositories for context:

### 1. Nucleus Repository
**Path**: `/Users/sri/nucleus`

**What it is**: The main MDR (Managed Detection & Response) platform being monitored.

**Key areas to reference**:
- `backend/services/` - All Go microservices (42+ services)
- `backend/processors/` - Async Pub/Sub workers (16+ processors)
- `backend/db/nucleus/schema/` - Database schemas
- `test/smoke/` - Smoke test suite
- `Taskfile.yaml` - Build/test commands (`task test`, `task lint`)
- `Tiltfile` - Local K8s development setup
- `k8s/` - Kubernetes configurations
- `localdev/` - Local development utilities

**Tech stack**: Go 1.24+, PostgreSQL 15+, Google Cloud Pub/Sub, Kubernetes, Temporal

### 2. On-Call Repository
**Path**: `/Users/sri/oncall`

**What it is**: The on-call engineering handbook with runbooks, SRE knowledge, and triage procedures.

**Key areas to reference**:
- `.claude/commands/sre-triage.md` - Main triage framework
- `.claude/commands/sre-triage/` - Triage sub-components:
  - `infrastructure-checks.md` - AlloyDB, Pub/Sub diagnostics
  - `bigquery-queries.md` - Data validation queries
  - `tenant-reference.md` - Production vs demo tenant list
  - `error-patterns.md` - Known patterns and resolutions
  - `output-format.md` - Structured triage output
- `runbooks/` - Alert-specific playbooks:
  - `alloydb.md` - Database issues
  - `pubsub-backlogs.md` - Message queue issues
  - `cloud-run.md` - Service errors
  - `integrations.md` - Third-party integration errors
- `scripts/oncall-checkout.sh` - Health check automation
- `handoffs/` - Shift documentation structure
- `postmortems/` - Incident review templates

---

## System Overview

### What On Call Helper Does

1. **Detects** production errors from GCP Cloud Logging (Nucleus infrastructure)
2. **Filters** out transient/self-healing errors and demo tenant noise
3. **Triages** using Claude AI embedded with SRE knowledge from the oncall repo
4. **Generates** minimal code fixes using Claude AI
5. **Validates** fixes through multi-stage pipeline:
   - Static analysis (Go build, vet, lint)
   - CodeRabbit code review (with retry loop)
   - Sandbox testing in ephemeral Kind clusters
   - Optional staging deployment
6. **Creates** GitHub PRs for approved fixes
7. **Verifies** fixes resolved the issue in production post-deployment
8. **Notifies** team via PagerDuty throughout the process

### Key Value Proposition

- **Automated triage**: Uses embedded SRE runbook knowledge
- **Safe testing**: Fixes tested in isolated Kind clusters matching Nucleus's architecture
- **Production verification**: Confirms fixes actually work after deployment
- **Tenant awareness**: Ignores demo tenant errors, focuses on production
- **Transient filtering**: Skips known self-healing error patterns
- **Full audit trail**: Every step logged and visible in dashboard

---

## Architecture

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         GCP CLOUD LOGGING (Nucleus Infra)                        │
│  Errors from: Cloud Run services, AlloyDB, Pub/Sub processors, API-proxy        │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     ON CALL HELPER (Standalone FastAPI App)                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                         ERROR INGESTION LAYER                             │   │
│  │  • GCP Log Sink → Pub/Sub → Push webhook                                  │   │
│  │  • Filter: severity >= ERROR, exclude demo tenants                        │   │
│  │  • Deduplication: track insertIds, group related errors                   │   │
│  │  • Transient Filter: skip known self-healing patterns                     │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                            │
│                                     ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                    TRIAGE AGENT (Claude + SRE Knowledge)                  │   │
│  │  • Embedded: /sre-triage runbooks, error-patterns, tenant-reference       │   │
│  │  • Infrastructure checks: AlloyDB health, Pub/Sub backlogs                │   │
│  │  • BigQuery validation queries                                            │   │
│  │  • Output: root cause, affected service, file path, confidence            │   │
│  │  • Decision: FIXABLE vs INFRA_ISSUE vs TRANSIENT vs NEEDS_HUMAN           │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                            │
│                          ┌──────────┴──────────┐                                │
│                          │ Is it FIXABLE?       │                                │
│                          └──────────┬──────────┘                                │
│                    NO ──────────────┼────────────── YES                         │
│                    │                │                │                          │
│                    ▼                │                ▼                          │
│           Log & Alert               │    ┌───────────────────────────────────┐  │
│           (PagerDuty)               │    │         FIXER AGENT (Claude)      │  │
│                                     │    │  • Read actual source from GitHub │  │
│                                     │    │  • Generate minimal fix           │  │
│                                     │    │  • Include fix explanation        │  │
│                                     │    └───────────────────────┬───────────┘  │
│                                     │                            │              │
│                                     │                            ▼              │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                      VALIDATION PIPELINE (Multi-Stage)                    │   │
│  │                                                                           │   │
│  │  Stage 1: STATIC ANALYSIS                                                 │   │
│  │  ├── Go build check (syntax, imports)                                     │   │
│  │  ├── Go vet (common mistakes)                                             │   │
│  │  └── golangci-lint (comprehensive linting)                                │   │
│  │                                                                           │   │
│  │  Stage 2: CODE REVIEW                                                     │   │
│  │  ├── CodeRabbit CLI review                                                │   │
│  │  └── If issues → feedback to Fixer Agent (max 3 iterations)               │   │
│  │                                                                           │   │
│  │  Stage 3: SANDBOX TESTING (Ephemeral Kind Cluster)                        │   │
│  │  ├── Spin up isolated Kind cluster                                        │   │
│  │  ├── Deploy via Tilt/Helm with fix applied                                │   │
│  │  ├── Run: task test (Go unit tests)                                       │   │
│  │  ├── Run: smoke tests (test/smoke/)                                       │   │
│  │  └── Tear down cluster                                                    │   │
│  │                                                                           │   │
│  │  Stage 4: STAGING DEPLOYMENT (Optional but Recommended)                   │   │
│  │  ├── Deploy to staging environment                                        │   │
│  │  ├── Replay error-triggering scenario                                     │   │
│  │  └── Verify error doesn't recur                                           │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                            │
│                          All stages passed?                                      │
│                                     │                                            │
│                                     ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                         PR CREATION (GitHub)                              │   │
│  │  • Branch: oncall-helper/fix-{incident_id}                                │   │
│  │  • PR body: root cause, fix explanation, test results, verification plan │   │
│  │  • Labels: oncall-helper, auto-fix, tests-passed                          │   │
│  │  • Status: Draft PR (requires human approval to merge)                    │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                            │
│                                     ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                    PRODUCTION VERIFICATION (Post-Merge)                   │   │
│  │  • Monitor: Same error signature in Cloud Logging                         │   │
│  │  • Window: 30 min - 2 hours after deployment                              │   │
│  │  • Success: Error rate drops to 0 or acceptable threshold                 │   │
│  │  • Failure: Alert on-call, consider rollback                              │   │
│  │  • Update: Handoff doc with resolution status                             │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                         WEBSOCKET MANAGER                                 │   │
│  │  Broadcasts real-time events to dashboard clients                         │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              REACT DASHBOARD                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│  • Incident Feed: List of incidents with status                                 │
│  • Agent Thinking: Real-time agent activity visualization                       │
│  • Code Diff: Before/after code comparison                                      │
│  • Sandbox Status: Test execution progress                                      │
│  • Production Verification: Post-deploy monitoring                              │
│  • Metrics Panel: MTTR, success rate, counts                                    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Directory Structure

**Current Implementation**:

```
on-call-helper/
├── backend/
│   ├── main.py                     # ✅ FastAPI app, routes, WebSocket endpoint
│   ├── config.py                   # ✅ Environment configuration (Pydantic Settings)
│   ├── websocket_manager.py        # ✅ Connection management, event broadcasting
│   ├── storage.py                  # ✅ In-memory storage (with Firestore option)
│   ├── storage_firestore.py        # ✅ Optional Firestore backend
│   ├── models/
│   │   └── incident.py             # ✅ Pydantic models for all entities
│   ├── agents/
│   │   ├── orchestrator.py         # ✅ Pipeline coordinator (full implementation)
│   │   ├── triage.py               # ✅ Claude + embedded SRE knowledge + GCP context
│   │   └── fixer.py                # ✅ Claude fix generation (local file reading)
│   ├── services/
│   │   ├── gcp_logging.py          # ✅ GCP log ingestion (webhook + polling)
│   │   ├── coderabbit.py           # ✅ Code review via CLI (optional)
│   │   ├── sandbox.py              # ⚠️ Kind cluster management (can be skipped)
│   │   ├── github.py               # ✅ PR creation (git + gh CLI)
│   │   ├── pagerduty.py            # ✅ Team notifications (optional)
│   │   └── production_monitor.py   # ✅ Post-deploy verification (can be skipped)
│   ├── filters/
│   │   ├── transient.py            # ✅ Self-healing error filter (20+ patterns)
│   │   └── tenant.py               # ✅ Demo tenant filter (explicit tenant list)
│   └── knowledge/
│       ├── __init__.py
│       └── loader.py               # ✅ Load knowledge from oncall repo (cached)
├── frontend/
│   └── src/
│       ├── hooks/useWebSocket.js   # ✅ WebSocket with auto-reconnect
│       ├── context/IncidentContext.jsx  # ✅ Global state management
│       └── components/             # ✅ Dashboard UI components
├── sandbox/
│   ├── kind-config.yaml            # Kind cluster configuration
│   ├── deploy.sh                   # Deploy Nucleus to Kind
│   ├── run-tests.sh                # Execute test suite
│   └── cleanup.sh                  # Teardown cluster
├── tests/
│   ├── test_triage.py              # ✅ Tests
│   ├── test_fixer.py               # ✅ Tests
│   ├── test_sandbox.py             # ✅ Tests
│   └── ...                         # Additional test files
├── requirements.txt               # ✅ Python dependencies
├── package.json                   # ✅ Frontend dependencies
├── Dockerfile                      # ✅ Backend container
├── docker-compose.yaml             # ✅ Docker Compose setup
└── ARCHITECTURE.md                 # This file
```

**Legend**:
- ✅ Fully implemented
- ⚠️ Implemented but can be skipped (optional features)

---

## Component Specifications

### 1. Error Ingestion Layer

**Purpose**: Receive and filter production errors from Nucleus infrastructure

**Implementation Status**: ✅ **IMPLEMENTED**

**Two Modes of Operation**:

1. **Pub/Sub Push Webhook** (Production recommended)
2. **GCP Polling Mode** (Development/testing, read-only access)

**GCP Setup (Pub/Sub Push)**:
```bash
# 1. Create Pub/Sub topic for error logs
gcloud pubsub topics create oncall-helper-errors

# 2. Create push subscription pointing to On Call Helper
gcloud pubsub subscriptions create oncall-helper-errors-sub \
  --topic=oncall-helper-errors \
  --push-endpoint=https://your-oncall-helper-domain/webhook/gcp-logs \
  --ack-deadline=60

# 3. Create log sink to route errors to the topic
gcloud logging sinks create oncall-helper-sink \
  pubsub.googleapis.com/projects/PROJECT_ID/topics/oncall-helper-errors \
  --log-filter='severity>=ERROR AND resource.type="cloud_run_revision"'
```

**GCP Polling Mode** (Alternative):
```bash
# Authenticate with GCP
gcloud auth application-default login

# Start polling via API
curl -X POST "http://localhost:8001/gcp/polling/start?interval_seconds=30"
```

**Webhook Endpoint** (`/webhook/gcp-logs`):
- ✅ Parses Pub/Sub push messages
- ✅ Extracts error message, stack trace, service name, tenant info
- ✅ Deduplication via `insertId` tracking
- ✅ Applies transient and tenant filters
- ✅ Creates incidents and triggers pipeline

**Polling Endpoint** (`/gcp/polling/start`):
- ✅ Queries Cloud Logging API directly
- ✅ Configurable interval (default 30s)
- ✅ Tracks processed logs to avoid duplicates
- ✅ Can be started/stopped via API

**Key Features**:
- Automatic deduplication using GCP `insertId`
- Service name extraction from resource labels
- Tenant ID/name extraction from log payload
- File path extraction from stack traces
- Severity mapping (GCP → Incident severity)

---

### 2. Transient Error Filter

**Purpose**: Skip known self-healing errors that don't need fixes

**Reference**: `/Users/sri/oncall/.claude/commands/sre-triage/error-patterns.md`

```python
# filters/transient.py

TRANSIENT_PATTERNS = [
    {
        "pattern": r"Routing deadline expired",
        "reason": "Transient Cloud Run → AlloyDB connection, auto-retries",
        "action": "IGNORE"
    },
    {
        "pattern": r"context deadline exceeded",
        "reason": "External API timeout, has automatic retry",
        "action": "IGNORE"
    },
    {
        "pattern": r"case number already exists",
        "reason": "Race condition in casemaker, retries successfully",
        "action": "IGNORE"
    },
    {
        "pattern": r"RESOURCE_EXHAUSTED.*Quota exceeded",
        "reason": "Rate limiting, backs off automatically",
        "action": "MONITOR"  # Alert if persists > 10 min
    },
    {
        "pattern": r"connection reset by peer",
        "reason": "Network blip, auto-reconnects",
        "action": "IGNORE"
    },
    {
        "pattern": r"deadline exceeded.*retry",
        "reason": "Transient timeout with retry mechanism",
        "action": "IGNORE"
    },
]

def is_transient_error(error_message: str) -> tuple[bool, str]:
    """Check if error matches known transient patterns."""
    for pattern_info in TRANSIENT_PATTERNS:
        if re.search(pattern_info["pattern"], error_message, re.IGNORECASE):
            return True, pattern_info["reason"]
    return False, ""
```

---

### 3. Tenant Filter

**Purpose**: Ignore errors from demo/test tenants, focus on production

**Reference**: `/Users/sri/oncall/.claude/commands/sre-triage/tenant-reference.md`

```python
# filters/tenant.py

# Demo/Test tenants - errors are usually noise
DEMO_TENANTS = [
    "TENEX POC Demo",
    "Tenex Demo",
    "TENEX Internal",
    "Tenex Sandbox",
    "Test Customer",
    "Demo Organization",
]

# Production tenants - investigate errors
PRODUCTION_TENANTS = [
    "Whitney",
    "Horizontal",
    "Bowtie",
    "Sycamore",
    "Haven",
    "RedRock",
    "Warehouse",
    "IPA",
    "Royals",
    "Kingpins",
    "QuantumLeap",
]

def should_process_tenant(tenant_name: str) -> tuple[bool, str]:
    """Determine if errors from this tenant should be processed."""
    if not tenant_name:
        return True, "No tenant context - processing"

    # Normalize tenant name for comparison
    normalized = tenant_name.strip().lower()

    for demo in DEMO_TENANTS:
        if demo.lower() in normalized or normalized in demo.lower():
            return False, f"Demo tenant ({tenant_name}) - ignoring"

    return True, "Production tenant - processing"
```

---

### 4. Triage Agent (Claude + SRE Knowledge)

**Purpose**: Analyze errors using Claude with embedded SRE runbook knowledge

**Implementation Status**: ✅ **IMPLEMENTED** (Enhanced with GCP context fetching)

**Knowledge Loading** (`backend/knowledge/loader.py`):
- ✅ Loads SRE knowledge from `/Users/sri/oncall` repository
- ✅ Cached with `@lru_cache` for performance
- ✅ Handles missing files gracefully
- ✅ Loads triage framework, runbooks, error patterns, tenant reference

**Key Features**:
- **GCP Context Fetching**: Actively queries GCP logs for additional context:
  - Related errors from same service (past hour)
  - Error frequency and patterns
  - Similar errors across other services
  - Recent service activity logs
- **Enhanced Classification**: Uses GCP context to make better decisions:
  - Widespread errors → INFRA_ISSUE
  - High frequency → TRANSIENT or INFRA
  - Isolated errors → FIXABLE
- **System Prompt**: Includes all SRE knowledge in Claude's system prompt
- **JSON Output**: Structured classification with confidence scores

**Triage Classifications**:
- `FIXABLE`: Code bug that can be auto-fixed
- `INFRA_ISSUE`: Infrastructure problem (AlloyDB, Pub/Sub, networking)
- `TRANSIENT`: Self-healing error that will resolve
- `NEEDS_HUMAN`: Too complex for automated handling

**Triage Agent System Prompt**:
```python
# agents/triage.py

from knowledge.loader import load_sre_knowledge

SRE_KNOWLEDGE = load_sre_knowledge()

TRIAGE_SYSTEM_PROMPT = f"""
You are an SRE triage agent for Nucleus, a Security Operations Center (SOC) platform
for Managed Detection & Response (MDR) services.

## Your Knowledge Base

### Triage Framework
{SRE_KNOWLEDGE["triage_framework"]}

### Infrastructure Checks
{SRE_KNOWLEDGE["infrastructure_checks"]}

### Known Error Patterns
{SRE_KNOWLEDGE["error_patterns"]}

### Tenant Reference
{SRE_KNOWLEDGE["tenant_reference"]}

### Runbooks

#### AlloyDB Issues
{SRE_KNOWLEDGE["runbooks"]["alloydb"]}

#### Pub/Sub Issues
{SRE_KNOWLEDGE["runbooks"]["pubsub"]}

#### Cloud Run Issues
{SRE_KNOWLEDGE["runbooks"]["cloud_run"]}

#### Integration Issues
{SRE_KNOWLEDGE["runbooks"]["integrations"]}

---

## Your Task

Analyze the production error and determine:

1. **Classification**:
   - FIXABLE: Code bug that can be fixed programmatically
   - INFRA_ISSUE: Infrastructure problem (AlloyDB, Pub/Sub, networking)
   - TRANSIENT: Self-healing error that will resolve
   - NEEDS_HUMAN: Complex issue requiring human judgment

2. **If FIXABLE, provide**:
   - `root_cause`: Detailed explanation of what went wrong
   - `service_name`: Which Nucleus service is affected
   - `file_path`: Specific file in the nucleus repo containing the bug
   - `function_name`: The function with the bug
   - `code_snippet`: The problematic code
   - `suggested_fix`: High-level description of the fix needed
   - `confidence`: 0.0-1.0 score

3. **If INFRA_ISSUE, provide**:
   - `root_cause`: What infrastructure component is failing
   - `runbook_reference`: Which runbook to follow
   - `manual_steps`: Recommended manual intervention

Output your analysis as JSON.
"""
```

**Triage Agent Implementation**:
```python
# agents/triage.py

from anthropic import Anthropic

class TriageAgent:
    def __init__(self):
        self.client = Anthropic()
        self.model = "claude-sonnet-4-20250514"  # Fast, cost-effective

    async def analyze(self, incident: Incident) -> TriageResult:
        """Analyze incident and determine classification."""

        user_prompt = f"""
        Analyze this production error from Nucleus:

        ## Error Details
        - **Service**: {incident.service_name}
        - **Error Message**: {incident.error_message}
        - **Stack Trace**:
        ```
        {incident.stack_trace}
        ```
        - **File Path** (if known): {incident.file_path}
        - **Tenant**: {incident.tenant_name}
        - **Environment**: {incident.environment}
        - **Timestamp**: {incident.created_at}

        Provide your triage analysis as JSON.
        """

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=TRIAGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )

        return self._parse_response(response, incident.id)
```

---

### 5. Fixer Agent (Claude)

**Purpose**: Generate minimal code fixes based on triage analysis

**Implementation Status**: ✅ **IMPLEMENTED**

**Reference for Nucleus code patterns**: `/Users/sri/nucleus/backend/`

**Key Features**:
- ✅ Reads source code from **local filesystem** (not GitHub API)
- ✅ Fuzzy code matching handles whitespace differences
- ✅ Validates fixes before returning (balanced braces, code found in source)
- ✅ Supports retry loop with CodeRabbit feedback
- ✅ Normalizes whitespace for accurate code replacement

**Source Code Reading**:
```python
# Reads from local Nucleus repository
local_path = settings.nucleus_repo_path / file_path
content = local_path.read_text(encoding="utf-8")
```

**Code Matching**:
- First tries exact match
- Falls back to normalized whitespace matching
- Uses `difflib` for fuzzy matching (85%+ similarity threshold)
- Returns actual source code for accurate replacement

**Fix Validation**:
- Ensures `original_code` exists in source file
- Validates `fixed_code` is different from original
- Checks balanced braces/parentheses
- Returns actual matching code from source (handles formatting differences)

---

### 6. Sandbox Service (Kind Clusters)

**Purpose**: Test fixes in isolated Kubernetes environments matching Nucleus architecture

**Why Kind?**
- Nucleus uses Kind + Tilt for local development
- Matches production K8s environment
- Can run actual Go tests and smoke tests
- Uses existing Helm charts and configurations

**Reference**: `/Users/sri/nucleus/localdev/`, `/Users/sri/nucleus/Tiltfile`

```python
# services/sandbox.py

import subprocess
import uuid
from pathlib import Path

NUCLEUS_REPO_PATH = Path("/Users/sri/nucleus")

class SandboxService:
    """Manage ephemeral Kind clusters for testing fixes."""

    async def create_sandbox(self, incident_id: str) -> Sandbox:
        """Create isolated Kind cluster for testing."""

        cluster_name = f"oncall-test-{incident_id[:8]}-{uuid.uuid4().hex[:4]}"

        # Create Kind cluster using Nucleus config
        result = subprocess.run(
            ["kind", "create", "cluster",
             "--name", cluster_name,
             "--config", str(NUCLEUS_REPO_PATH / "localdev/kind-config.yaml")],
            capture_output=True,
            text=True,
            timeout=300  # 5 min timeout
        )

        if result.returncode != 0:
            raise SandboxCreationError(f"Failed to create cluster: {result.stderr}")

        return Sandbox(
            id=cluster_name,
            incident_id=incident_id,
            status="created"
        )

    async def apply_fix(self, sandbox: Sandbox, fix: FixResult) -> None:
        """Apply the code fix to the sandbox environment."""

        # Clone Nucleus repo to temp directory
        work_dir = Path(f"/tmp/oncall-sandbox-{sandbox.id}")
        subprocess.run(
            ["git", "clone", "--depth=1", str(NUCLEUS_REPO_PATH), str(work_dir)],
            check=True
        )

        # Apply the fix
        fix_path = work_dir / fix.file_path
        original_content = fix_path.read_text()
        fixed_content = original_content.replace(fix.original_code, fix.fixed_code)
        fix_path.write_text(fixed_content)

        # Build Docker images with fix
        subprocess.run(
            ["task", "build:docker"],
            cwd=work_dir,
            check=True
        )

        # Load images into Kind cluster
        subprocess.run(
            ["kind", "load", "docker-image", "nucleus:local",
             "--name", sandbox.id],
            check=True
        )

        sandbox.work_dir = work_dir
        sandbox.status = "fix_applied"

    async def run_tests(self, sandbox: Sandbox) -> TestResult:
        """Run test suite in the sandbox."""

        results = {"unit_tests": None, "smoke_tests": None}

        # Run Go unit tests
        unit_result = subprocess.run(
            ["task", "test"],
            cwd=sandbox.work_dir,
            capture_output=True,
            text=True,
            timeout=600  # 10 min timeout
        )
        results["unit_tests"] = {
            "passed": unit_result.returncode == 0,
            "output": unit_result.stdout + unit_result.stderr
        }

        # Run smoke tests if unit tests passed
        if unit_result.returncode == 0:
            # Deploy to Kind cluster first
            subprocess.run(
                ["tilt", "up", "--port=0"],
                cwd=sandbox.work_dir,
                timeout=300
            )

            smoke_result = subprocess.run(
                ["task", "test:smoke"],
                cwd=sandbox.work_dir,
                capture_output=True,
                text=True,
                timeout=600
            )
            results["smoke_tests"] = {
                "passed": smoke_result.returncode == 0,
                "output": smoke_result.stdout + smoke_result.stderr
            }

        return TestResult(
            incident_id=sandbox.incident_id,
            passed=all(r["passed"] for r in results.values() if r),
            unit_tests=results["unit_tests"],
            smoke_tests=results["smoke_tests"]
        )

    async def cleanup(self, sandbox: Sandbox) -> None:
        """Delete Kind cluster and cleanup resources."""

        subprocess.run(
            ["kind", "delete", "cluster", "--name", sandbox.id],
            capture_output=True
        )

        if sandbox.work_dir and sandbox.work_dir.exists():
            subprocess.run(["rm", "-rf", str(sandbox.work_dir)])
```

**Sandbox Runner Infrastructure**:
```yaml
# Required specs for sandbox runner VM/container
compute:
  cpu: 8 cores (minimum)
  memory: 32GB RAM
  disk: 100GB SSD

# Can run on:
- GCE instance: n2-standard-8
- Self-hosted runner with Docker + Kind
- Dedicated build server
```

---

### 7. CodeRabbit Service

**Purpose**: Automated code review before sandbox testing

```python
# services/coderabbit.py

import subprocess
import tempfile
import json

class CodeRabbitService:
    """Code review via CodeRabbit CLI."""

    MAX_ITERATIONS = 3
    BLOCKING_SEVERITIES = ["critical", "high", "error", "security"]

    async def review(self, fix: FixResult) -> ReviewResult:
        """Run CodeRabbit review on the fix."""

        # Write fixed code to temp file with correct extension
        suffix = ".go"  # Nucleus is Go
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=suffix,
            delete=False
        ) as f:
            f.write(fix.fixed_code)
            temp_path = f.name

        try:
            result = subprocess.run(
                ["coderabbit", "review", temp_path, "--format", "json"],
                capture_output=True,
                text=True,
                timeout=120
            )

            review_data = json.loads(result.stdout)

            # Check for blocking issues
            blocking_issues = [
                issue for issue in review_data.get("issues", [])
                if issue.get("severity") in self.BLOCKING_SEVERITIES
            ]

            return ReviewResult(
                passed=len(blocking_issues) == 0,
                issues=review_data.get("issues", []),
                suggestions=review_data.get("suggestions", []),
                summary=self._format_feedback(blocking_issues)
            )

        finally:
            os.unlink(temp_path)

    def _format_feedback(self, issues: list) -> str:
        """Format issues as feedback for Claude fixer agent."""
        if not issues:
            return ""

        feedback = "CodeRabbit found the following issues:\n\n"
        for issue in issues:
            feedback += f"- [{issue['severity']}] Line {issue.get('line', '?')}: {issue['message']}\n"
            if issue.get('suggestion'):
                feedback += f"  Suggestion: {issue['suggestion']}\n"

        return feedback
```

---

### 8. GitHub Service

**Purpose**: Create PRs for validated fixes

**Implementation Status**: ✅ **IMPLEMENTED** (Uses `git` + `gh` CLI)

**Approach**: Uses local `git` commands and GitHub CLI (`gh`) instead of GitHub API

**Key Features**:
- ✅ Works with local Nucleus repository
- ✅ Creates branch, commits fix, pushes to remote
- ✅ Uses `gh pr create` for PR creation
- ✅ Generates comprehensive PR body with:
  - Incident details and root cause
  - Code diff
  - Test results
  - Verification plan
- ✅ Creates draft PRs (requires human approval)

**Workflow**:
1. Checkout new branch: `oncall-helper/fix-{incident_id}`
2. Apply fix to local file (string replacement)
3. Commit with descriptive message
4. Push branch to remote
5. Create PR via `gh pr create --draft`
6. Add labels: `oncall-helper`, `auto-fix`, `tests-passed`

**Requirements**:
- GitHub CLI (`gh`) installed and authenticated
- Local Nucleus repository with remote configured
- Write access to repository

    def _generate_pr_body(
        self,
        incident: Incident,
        triage: TriageResult,
        fix: FixResult,
        test_results: TestResult
    ) -> str:
        return f"""## On Call Helper Auto-Generated Fix

**Status:** All tests passed | Draft PR (requires human approval)

### Incident Details
| Field | Value |
|-------|-------|
| ID | `{incident.id}` |
| Title | {incident.title} |
| Service | {incident.service_name} |
| File | `{fix.file_path}` |
| Severity | {incident.severity} |
| Tenant | {incident.tenant_name or "N/A"} |

### Root Cause Analysis
> {triage.root_cause}

**Confidence:** {triage.confidence:.0%}

### Changes Made
{fix.explanation}

### Code Diff
```diff
- {fix.original_code}
+ {fix.fixed_code}
```

### Test Results
- Unit Tests: {"Passed" if test_results.unit_tests["passed"] else "Failed"}
- Smoke Tests: {"Passed" if test_results.smoke_tests and test_results.smoke_tests["passed"] else "Skipped/Failed"}

### Verification Checklist
- [x] Sandbox tests passed
- [ ] Manual code review
- [ ] Verify fix in staging
- [ ] Monitor production after merge

### Production Verification Plan
After merging, On Call Helper will monitor Cloud Logging for 2 hours to verify the error no longer occurs.

---
Generated by On Call Helper | [View Incident Dashboard](your-dashboard-url)
"""
```

---

### 9. Production Verification Service

**Purpose**: Confirm fixes actually resolve issues in production

```python
# services/production_monitor.py

from google.cloud import logging_v2
import asyncio

class ProductionMonitorService:
    """Monitor production after fix deployment to verify resolution."""

    def __init__(self, project_id: str):
        self.client = logging_v2.Client(project=project_id)
        self.check_interval_seconds = 300  # 5 minutes
        self.monitoring_duration_hours = 2

    async def verify_fix(
        self,
        incident: Incident,
        pr_merged_at: datetime
    ) -> VerificationResult:
        """Monitor production to verify fix resolved the issue."""

        # Build query for the same error signature
        error_filter = self._build_error_filter(incident)

        # Monitor for 2 hours
        end_time = pr_merged_at + timedelta(hours=self.monitoring_duration_hours)
        error_counts = []

        while datetime.utcnow() < end_time:
            await asyncio.sleep(self.check_interval_seconds)

            # Count errors since deployment
            count = await self._count_errors(
                error_filter,
                start_time=pr_merged_at,
                end_time=datetime.utcnow()
            )
            error_counts.append(count)

            # Broadcast status update
            await self._broadcast_verification_status(
                incident.id,
                count,
                datetime.utcnow()
            )

        # Analyze results
        total_errors_after = sum(error_counts)

        # Get baseline (errors before fix in same time window)
        baseline_count = await self._count_errors(
            error_filter,
            start_time=pr_merged_at - timedelta(hours=self.monitoring_duration_hours),
            end_time=pr_merged_at
        )

        # Determine success
        if total_errors_after == 0:
            status = "SUCCESS"
            message = "Error completely resolved - no occurrences since deployment"
        elif total_errors_after < baseline_count * 0.1:
            status = "SUCCESS"
            message = f"Error reduced by >90% ({baseline_count} → {total_errors_after})"
        elif total_errors_after < baseline_count:
            status = "PARTIAL"
            message = f"Error reduced but not eliminated ({baseline_count} → {total_errors_after})"
        else:
            status = "FAILED"
            message = f"Error persists or increased ({baseline_count} → {total_errors_after})"

        return VerificationResult(
            incident_id=incident.id,
            status=status,
            message=message,
            errors_before=baseline_count,
            errors_after=total_errors_after,
            monitoring_duration_hours=self.monitoring_duration_hours
        )

    def _build_error_filter(self, incident: Incident) -> str:
        """Build Cloud Logging filter for this specific error."""
        # Match on error message pattern and service
        escaped_msg = incident.error_message[:100].replace('"', '\\"')
        return f'''
            severity>=ERROR
            AND resource.labels.service_name="{incident.service_name}"
            AND textPayload:"{escaped_msg}"
        '''
```

---

### 10. PagerDuty Service

**Purpose**: Notify team about incidents and resolution status

**Reference**: Nucleus already uses PagerDuty for on-call (see `/Users/sri/oncall/README.md`)

```python
# services/pagerduty.py

import httpx

class PagerDutyService:
    """PagerDuty Events API v2 integration."""

    EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"

    def __init__(self, routing_key: str):
        self.routing_key = routing_key

    async def trigger(self, incident: Incident) -> str:
        """Trigger new PagerDuty incident."""
        payload = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "dedup_key": f"oncall-helper-{incident.id}",
            "payload": {
                "summary": f"[On Call Helper] {incident.title}",
                "severity": self._map_severity(incident.severity),
                "source": "on-call-helper",
                "custom_details": {
                    "incident_id": incident.id,
                    "service": incident.service_name,
                    "error_message": incident.error_message[:500],
                    "tenant": incident.tenant_name,
                    "dashboard_url": f"https://your-dashboard/{incident.id}"
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.EVENTS_URL, json=payload)
            return response.json().get("dedup_key")

    async def acknowledge(self, incident_id: str) -> None:
        """Acknowledge - pipeline is processing."""
        await self._send_event(incident_id, "acknowledge")

    async def resolve(self, incident_id: str, pr_url: str) -> None:
        """Resolve - fix successful."""
        await self._send_event(
            incident_id,
            "resolve",
            custom_details={"pr_url": pr_url, "status": "Fix PR created"}
        )

    async def escalate(self, incident_id: str, reason: str) -> None:
        """Escalate - automated fix failed."""
        # Trigger new high-severity incident for human attention
        await self._send_event(
            f"{incident_id}-escalation",
            "trigger",
            severity="high",
            summary=f"[On Call Helper] ESCALATION: {reason}"
        )

    def _map_severity(self, severity: str) -> str:
        mapping = {
            "critical": "critical",
            "high": "error",
            "medium": "warning",
            "low": "info"
        }
        return mapping.get(severity, "warning")
```

---

## Data Models

**Implementation Status**: ✅ **IMPLEMENTED** (`backend/models/incident.py`)

All models use Pydantic for validation and serialization:

```python
# models/incident.py

class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class IncidentStatus(str, Enum):
    ACTIVE = "active"
    TRIAGING = "triaging"
    FIXING = "fixing"
    REVIEWING = "reviewing"  # Added
    TESTING = "testing"
    PR_CREATED = "pr_created"
    VERIFYING = "verifying"
    FIXED = "fixed"
    ESCALATED = "escalated"
    FILTERED = "filtered"

class TriageClassification(str, Enum):
    FIXABLE = "fixable"
    INFRA_ISSUE = "infra_issue"
    TRANSIENT = "transient"
    NEEDS_HUMAN = "needs_human"

class Incident(BaseModel):
    id: str  # Format: "OCH-{8chars}"
    title: str
    error_message: str
    stack_trace: Optional[str]
    file_path: Optional[str]
    service_name: str
    severity: Severity
    tenant_name: Optional[str]
    environment: str  # production, staging
    status: IncidentStatus
    created_at: datetime
    resolved_at: Optional[datetime] = None
    # GCP metadata
    gcp_insert_id: Optional[str]  # For deduplication
    gcp_resource_type: Optional[str]
    gcp_log_name: Optional[str]

class TriageResult(BaseModel):
    incident_id: str
    classification: TriageClassification
    root_cause: str
    confidence: float  # 0.0-1.0
    # FIXABLE fields
    service_name: Optional[str]
    file_path: Optional[str]
    function_name: Optional[str]
    code_snippet: Optional[str]
    line_numbers: Optional[Tuple[int, int]]  # Added
    suggested_fix: Optional[str]
    # INFRA_ISSUE fields
    runbook_reference: Optional[str]
    manual_steps: Optional[List[str]]
    # Metadata
    related_context: List[str]  # Added
    gcp_context: Optional[Dict[str, Any]]  # Added - GCP log context
    created_at: datetime

class FixResult(BaseModel):
    incident_id: str
    file_path: str
    original_code: str
    fixed_code: str
    explanation: str
    diff_summary: str
    iteration: int  # 1-3 (CodeRabbit iteration)
    created_at: datetime

class ReviewIssue(BaseModel):  # Added structured issue model
    severity: str
    message: str
    line: Optional[int]
    suggestion: Optional[str]

class ReviewResult(BaseModel):
    passed: bool
    issues: List[ReviewIssue]  # Structured
    suggestions: List[str]
    summary: str

class TestResult(BaseModel):
    incident_id: str
    passed: bool
    unit_tests_passed: Optional[bool]
    unit_tests_output: Optional[str]
    smoke_tests_passed: Optional[bool]
    smoke_tests_output: Optional[str]
    tests_run: int
    tests_passed: int
    tests_failed: int
    duration_ms: int
    coverage_percent: Optional[float]
    created_at: datetime

class VerificationStatus(str, Enum):  # Added enum
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"

class VerificationResult(BaseModel):
    incident_id: str
    status: VerificationStatus  # Enum instead of string
    message: str
    errors_before: int
    errors_after: int
    monitoring_duration_hours: int
    pr_url: Optional[str]  # Added
    created_at: datetime

class Metrics(BaseModel):  # Added
    total_incidents: int
    auto_fixed: int
    escalated: int
    filtered: int
    processing: int
    mttr_seconds: Optional[float]
    success_rate: Optional[float]
```

---

## Pipeline Orchestration

```python
# agents/orchestrator.py

class PipelineOrchestrator:
    """Coordinates the full incident response pipeline."""

    def __init__(
        self,
        triage_agent: TriageAgent,
        fixer_agent: FixerAgent,
        coderabbit: CodeRabbitService,
        sandbox: SandboxService,
        github: GitHubService,
        pagerduty: PagerDutyService,
        production_monitor: ProductionMonitorService,
        websocket: WebSocketManager
    ):
        self.triage = triage_agent
        self.fixer = fixer_agent
        self.coderabbit = coderabbit
        self.sandbox = sandbox
        self.github = github
        self.pagerduty = pagerduty
        self.monitor = production_monitor
        self.ws = websocket

    async def process_incident(self, incident: Incident) -> None:
        """Run the full pipeline for an incident."""

        try:
            # Acknowledge in PagerDuty
            await self.pagerduty.acknowledge(incident.id)
            await self.ws.broadcast("incident_created", incident)

            # Step 1: Triage
            await self.ws.broadcast("agent_thinking", {
                "step": "triage",
                "message": "Analyzing error with SRE knowledge..."
            })

            triage_result = await self.triage.analyze(incident)
            await self.ws.broadcast("triage_complete", triage_result)

            # Check if fixable
            if triage_result.classification != TriageClassification.FIXABLE:
                await self._handle_non_fixable(incident, triage_result)
                return

            # Step 2: Generate fix with CodeRabbit loop
            fix_result = await self._fix_with_review_loop(incident, triage_result)
            if not fix_result:
                await self._escalate(incident, "Failed to generate valid fix after 3 attempts")
                return

            # Step 3: Sandbox testing
            await self.ws.broadcast("sandbox_status", {"status": "creating"})
            sandbox = await self.sandbox.create_sandbox(incident.id)

            await self.ws.broadcast("sandbox_status", {"status": "applying_fix"})
            await self.sandbox.apply_fix(sandbox, fix_result)

            await self.ws.broadcast("sandbox_status", {"status": "testing"})
            test_result = await self.sandbox.run_tests(sandbox)

            await self.sandbox.cleanup(sandbox)

            if not test_result.passed:
                await self.ws.broadcast("sandbox_status", {"status": "failed"})
                await self._escalate(incident, f"Tests failed: {test_result}")
                return

            await self.ws.broadcast("sandbox_status", {"status": "passed"})

            # Step 4: Create PR
            pr_url = await self.github.create_fix_pr(
                incident, triage_result, fix_result, test_result
            )

            await self.ws.broadcast("incident_resolved", {
                "incident_id": incident.id,
                "pr_url": pr_url
            })

            # Resolve in PagerDuty
            await self.pagerduty.resolve(incident.id, pr_url)

            # Step 5: Monitor production (runs in background after PR merge)
            # This would be triggered by a GitHub webhook when PR is merged

        except Exception as e:
            await self._escalate(incident, f"Pipeline error: {str(e)}")

    async def _fix_with_review_loop(
        self,
        incident: Incident,
        triage: TriageResult
    ) -> FixResult | None:
        """Generate fix with CodeRabbit review loop (max 3 iterations)."""

        coderabbit_feedback = None

        for iteration in range(1, 4):
            await self.ws.broadcast("agent_thinking", {
                "step": "fixing",
                "message": f"Generating fix (attempt {iteration}/3)..."
            })

            fix_result = await self.fixer.generate_fix(triage, coderabbit_feedback)
            fix_result.iteration = iteration

            await self.ws.broadcast("code_diff", fix_result)

            # Run CodeRabbit review
            await self.ws.broadcast("agent_thinking", {
                "step": "review",
                "message": "Running CodeRabbit review..."
            })

            review = await self.coderabbit.review(fix_result)

            if review.passed:
                return fix_result

            # Prepare feedback for next iteration
            coderabbit_feedback = review.summary

            await self.ws.broadcast("review_feedback", {
                "iteration": iteration,
                "issues": review.issues,
                "retrying": iteration < 3
            })

        return None  # Failed after 3 attempts

    async def _handle_non_fixable(
        self,
        incident: Incident,
        triage: TriageResult
    ) -> None:
        """Handle non-fixable issues (infra, transient, needs human)."""

        if triage.classification == TriageClassification.TRANSIENT:
            await self.ws.broadcast("incident_filtered", {
                "incident_id": incident.id,
                "reason": "Transient error - will self-resolve"
            })
            incident.status = IncidentStatus.FILTERED

        elif triage.classification == TriageClassification.INFRA_ISSUE:
            await self.ws.broadcast("incident_escalated", {
                "incident_id": incident.id,
                "reason": f"Infrastructure issue: {triage.root_cause}",
                "runbook": triage.runbook_reference,
                "manual_steps": triage.manual_steps
            })
            await self.pagerduty.escalate(
                incident.id,
                f"Infrastructure issue - see runbook: {triage.runbook_reference}"
            )

        else:  # NEEDS_HUMAN
            await self._escalate(incident, triage.root_cause)

    async def _escalate(self, incident: Incident, reason: str) -> None:
        """Escalate to human on-call."""
        incident.status = IncidentStatus.ESCALATED

        await self.ws.broadcast("incident_escalated", {
            "incident_id": incident.id,
            "reason": reason
        })

        await self.pagerduty.escalate(incident.id, reason)
```

---

## Environment Variables

**Implementation Status**: ✅ **IMPLEMENTED** (`backend/config.py`)

All settings use Pydantic Settings with environment variable loading:

```bash
# .env

# ═══════════════ AI ═══════════════
ANTHROPIC_API_KEY=sk-ant-...
TRIAGE_MODEL=claude-sonnet-4-20250514
FIXER_MODEL=claude-sonnet-4-20250514

# ═══════════════ GCP ═══════════════
GCP_PROJECT_ID=your-nucleus-project
GCP_CREDENTIALS_PATH=./credentials.json  # Optional (uses ADC if not set)
GCP_LOG_FILTER=severity>=ERROR

# ═══════════════ GitHub ═══════════════
GITHUB_TOKEN=ghp_...  # For gh CLI authentication
GITHUB_REPO=your-org/nucleus
GITHUB_BASE_BRANCH=main

# ═══════════════ PagerDuty ═══════════════
PAGERDUTY_ROUTING_KEY=...  # Optional

# ═══════════════ CodeRabbit ═══════════════
CODERABBIT_MAX_RETRIES=3  # Optional (skips if not installed)

# ═══════════════ Sandbox ═══════════════
SANDBOX_TIMEOUT_MINUTES=15
NUCLEUS_REPO_PATH=/Users/sri/nucleus
ONCALL_REPO_PATH=/Users/sri/oncall

# ═══════════════ Storage ═══════════════
STORAGE_BACKEND=memory  # or "firestore"

# ═══════════════ Production Monitoring ═══════════════
VERIFICATION_DURATION_HOURS=2
VERIFICATION_CHECK_INTERVAL_MINUTES=5

# ═══════════════ App ═══════════════
APP_NAME=On Call Helper
APP_VERSION=0.1.0
DEBUG=false
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8000
DASHBOARD_URL=http://localhost:3000
```

**Configuration Features**:
- ✅ Pydantic Settings with validation
- ✅ Environment variable loading from `.env` file
- ✅ Sensible defaults for development
- ✅ `validate_required_for_production()` method
- ✅ Case-insensitive environment variable names

---

## Implementation Status

### ✅ Phase 1: Foundation (COMPLETE)
1. ✅ FastAPI scaffolding with health endpoints
2. ✅ Pydantic models for all entities
3. ✅ WebSocket manager for real-time updates
4. ✅ Configuration and environment loading

### ✅ Phase 2: Filters (COMPLETE)
5. ✅ Transient error filter (20+ patterns from oncall repo)
6. ✅ Tenant filter (explicit demo/production tenant lists)
7. ✅ Error ingestion webhook endpoint
8. ✅ GCP polling mode (alternative to webhook)

### ✅ Phase 3: Triage (COMPLETE)
9. ✅ SRE knowledge loader (from oncall repo, cached)
10. ✅ Triage agent with embedded knowledge
11. ✅ GCP context fetching (enhanced triage)
12. ✅ GCP Cloud Logging integration (webhook + polling)

### ✅ Phase 4: Fix Generation (COMPLETE)
13. ✅ Local file reading (from Nucleus repo)
14. ✅ Fixer agent with fuzzy code matching
15. ✅ CodeRabbit service with retry loop (optional)

### ⚠️ Phase 5: Validation (PARTIAL)
16. ⚠️ Sandbox service (Kind cluster management) - Implemented but can be skipped
17. ⚠️ Test runner integration - Implemented but optional

### ✅ Phase 6: PR & Notification (COMPLETE)
18. ✅ GitHub PR creation (git + gh CLI)
19. ✅ PagerDuty integration (optional)
20. ✅ Production verification service (can be skipped)

### ✅ Phase 7: Frontend (COMPLETE)
21. ✅ WebSocket hook with auto-reconnect
22. ✅ Incident context and state management
23. ✅ Dashboard components (IncidentTable, AgentThinking, CodeDiff, etc.)

## Current Capabilities

**Fully Working**:
- ✅ GCP error ingestion (webhook + polling)
- ✅ Transient and tenant filtering
- ✅ AI-powered triage with SRE knowledge
- ✅ Code fix generation
- ✅ GitHub PR creation
- ✅ Real-time dashboard with WebSocket updates
- ✅ Metrics tracking (MTTR, success rate)

**Optional Features** (can be skipped):
- ⚠️ CodeRabbit review (skips if not installed)
- ⚠️ Sandbox testing (skips if Kind not available)
- ⚠️ Production verification (can be disabled)
- ⚠️ PagerDuty notifications (optional)

**Storage Options**:
- ✅ In-memory storage (default, for development)
- ✅ Firestore storage (optional, for production)

---

## Key Reminders

1. **Always reference the repos**: When implementing any component, check the actual code and patterns in `/Users/sri/nucleus` and `/Users/sri/oncall`

2. **Nucleus is Go**: All code fixes target Go code, tests use `go test` and `task test`

3. **Multi-tenant**: Always consider tenant context - demo tenant errors are noise

4. **SRE knowledge**: The oncall repo has valuable runbooks and error patterns - embed them

5. **Production verification**: Don't just create PRs - verify fixes actually work after deployment

6. **Kind clusters**: Use ephemeral Kind clusters for testing - they match Nucleus's local dev environment

7. **Draft PRs**: Always create draft PRs requiring human approval before merge

8. **Local File Reading**: The fixer agent reads from local filesystem, not GitHub API - ensure Nucleus repo is cloned locally

9. **GitHub CLI Required**: PR creation uses `gh` CLI - must be installed and authenticated

10. **GCP Context**: Triage agent actively fetches GCP log context for better classification decisions
