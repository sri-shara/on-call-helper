# On Call Helper - Feature Documentation

Complete documentation of all features and functionality in the On Call Helper application.

## Table of Contents

1. [Overview](#overview)
2. [Incident Pipeline](#incident-pipeline)
3. [AI Agents](#ai-agents)
4. [Knowledge Modules](#knowledge-modules)
5. [Filters & Pre-processing](#filters--pre-processing)
6. [Pattern Learning](#pattern-learning)
7. [Services](#services)
8. [Storage Backends](#storage-backends)
9. [WebSocket Real-time Updates](#websocket-real-time-updates)
10. [Frontend Dashboard](#frontend-dashboard)
11. [API Endpoints](#api-endpoints)
12. [Configuration Options](#configuration-options)

---

## Overview

On Call Helper is an autonomous incident response system that:

1. **Monitors** GCP Cloud Logging and Google Chat alert channels for production errors
2. **Filters** noise (K8s infrastructure, demo tenants, transient errors)
3. **Triages** incidents using Claude AI (via Vertex AI or Anthropic API) with embedded SRE knowledge
4. **Learns** from historical patterns to improve classification
5. **Generates** targeted code fixes for fixable bugs
6. **Reviews** fixes using CodeRabbit (optional)
7. **Tests** fixes in sandbox environment (optional)
8. **Creates** pull requests with generated fixes
9. **Verifies** fixes in production after merge

---

## Incident Pipeline

### Pipeline Stages

| Stage | Description |
|-------|-------------|
| `RECEIVED` | Incident ingested from GCP or webhook |
| `TRIAGING` | Claude AI analyzing the error |
| `FIXING` | Generating code fix |
| `REVIEWING` | CodeRabbit code review |
| `TESTING` | Sandbox testing in Kind cluster |
| `CREATING_PR` | Creating GitHub pull request |
| `VERIFYING` | Monitoring production for recurrence |
| `COMPLETED` | Pipeline finished successfully |
| `ESCALATED` | Escalated to human |
| `FAILED` | Pipeline failed |

### Incident Statuses

| Status | Description |
|--------|-------------|
| `new` | Just created |
| `processing` | Pipeline is running |
| `triaged` | Triage complete |
| `fixing` | Fix being generated |
| `reviewing` | Under code review |
| `testing` | In sandbox testing |
| `pr_created` | PR opened |
| `verifying` | Monitoring production |
| `resolved` | Successfully fixed |
| `fixed` | Fix applied (alias for resolved) |
| `escalated` | Needs human attention |
| `auto_resolved` | Transient error self-healed |

### Escalation Reasons

When an incident cannot be automatically resolved:

| Reason | Description |
|--------|-------------|
| `TRIAGE_FAILED` | Claude couldn't analyze the error |
| `NOT_FIXABLE` | Classified as INFRA_ISSUE or NEEDS_HUMAN |
| `FIX_GENERATION_FAILED` | Couldn't generate valid fix |
| `REVIEW_FAILED_MAX_RETRIES` | CodeRabbit rejected fix 3 times |
| `SANDBOX_FAILED` | Tests failed in sandbox |
| `PR_CREATION_FAILED` | Couldn't create GitHub PR |
| `VERIFICATION_FAILED` | Errors continued after fix |
| `UNKNOWN_ERROR` | Unexpected pipeline error |

---

## AI Agents

### Triage Agent

**Purpose:** Analyze production incidents and classify them for appropriate action.

**Location:** `backend/agents/triage.py`

**Capabilities:**
- Claude AI-powered analysis (claude-sonnet-4-20250514 by default)
- Embedded SRE knowledge for Nucleus platform
- GCP log context fetching for richer analysis
- Error pattern matching (47+ known patterns)
- Tenant classification (production vs demo)
- Infrastructure health checks
- Runbook suggestions

**Classifications:**

| Classification | Description | Action |
|----------------|-------------|--------|
| `FIXABLE` | Code bug that can be auto-fixed | Generate fix, create PR |
| `TRANSIENT` | Self-healing error with retry | Auto-resolve, no action |
| `INFRA_ISSUE` | Infrastructure problem | Escalate with runbook |
| `NEEDS_HUMAN` | Too complex for automation | Escalate for review |

**Pre-analysis Steps:**
1. Error pattern matching (47+ patterns)
2. Transient error detection
3. Tenant type classification
4. Infrastructure health checks
5. Runbook suggestions
6. Historical pattern lookup

**Output (TriageResult):**
- `classification` - One of the four classifications
- `root_cause` - Explanation of the error cause
- `confidence` - 0.0 to 1.0 confidence score
- `service_name`, `file_path`, `function_name` - Location info
- `suggested_fix` - Initial fix suggestion
- `runbook_reference` - Relevant runbook if applicable
- `manual_steps` - Steps for human intervention
- `pattern_suggestion` - Historical pattern match (if any)
- `override_reason` - Explanation if classification was overridden

### Fixer Agent

**Purpose:** Generate minimal code fixes based on triage analysis.

**Location:** `backend/agents/fixer.py`

**Capabilities:**
- Reads actual source code from local repository
- Generates targeted fixes for identified bugs
- Produces fixes that compile and pass `go vet`
- Supports iterative refinement (max 3 iterations with CodeRabbit)
- Minimal scope changes

**Output (FixResult):**
- `file_path` - File to modify
- `original_code` - Code being replaced
- `fixed_code` - New code
- `explanation` - What the fix does
- `diff_summary` - Summary of changes
- `iteration` - Which iteration (1-3)

### Orchestrator

**Purpose:** Coordinate the entire incident response pipeline.

**Location:** `backend/agents/orchestrator.py`

**Responsibilities:**
- Run triage, fix, review, test stages
- Handle errors and retries
- Manage escalations
- Broadcast status updates via WebSocket
- Record patterns for learning
- Track verification results

---

## Knowledge Modules

### Error Patterns

**Location:** `backend/knowledge/error_patterns.py`

**Purpose:** Recognize known error patterns for faster triage.

**Pattern Categories:**

| Category | Count | Examples |
|----------|-------|----------|
| Critical | 2 | Unicode escape errors, data loss |
| Self-Resolving | 40+ | Network timeouts, race conditions, rate limits |

**Critical Patterns:**
- Unicode escape sequence errors (data loss risk)
- Silent drops/data loss indicators

**Self-Resolving Patterns:**
- Network timeouts and connection resets
- AlloyDB routing issues with auto-retry
- Race conditions with idempotency
- Cloud Run routing failures
- Context deadline exceeded
- Duplicate key errors (already processed)
- Agent JSON parsing failures (temporary)
- Lock contention in entity processing

### Infrastructure Health Checker

**Location:** `backend/knowledge/infrastructure.py`

**Purpose:** Run GCP monitoring queries during triage.

**Monitored Components:**

| Component | Warning Threshold | Critical Threshold |
|-----------|-------------------|-------------------|
| AlloyDB connections | 70% capacity | 90% capacity |
| AlloyDB wait time | 2 seconds | 5 seconds |
| AlloyDB wait count | 500 | 2000 |
| Pub/Sub backlog age | 5 minutes | 15 minutes |

**Health Status Levels:**
- `HEALTHY` - All metrics within normal range
- `WARNING` - Some metrics elevated
- `CRITICAL` - Metrics at dangerous levels
- `UNKNOWN` - Could not retrieve metrics

### Tenant Classification

**Location:** `backend/knowledge/tenants.py`

**Purpose:** Identify demo vs production tenants.

**Demo Tenants (filtered out):**
- TENEX POC Demo
- Tenex Demo
- TENEX POC MSSP
- TENEX Internal
- Tenex Sandbox

**Production Customers:**
- Whitney, Horizontal, Bowtie, and others

**Classification Results:**
- `PRODUCTION` - Real customers, errors investigated
- `DEMO` - Demo/internal, usually noise
- `UNKNOWN` - Not in known lists

### Runbook Suggestions

**Location:** `backend/knowledge/runbooks.py`

**Purpose:** Map errors to relevant runbooks for manual remediation.

**Runbook Categories:**

| Category | Examples |
|----------|----------|
| AlloyDB | Lock contention, connection issues, slow queries |
| Pub/Sub | Backlog monitoring, redelivery storms, stuck messages |
| Cloud Run | Timeouts, memory/CPU limits, scaling issues |
| Integration Services | Cisco AMP, Gemini, SOAR, Chronicle |

**Suggestion Fields:**
- `runbook_path` - Path to runbook file
- `relevance_score` - 0.0 to 1.0
- `section_hint` - Specific section to reference
- `manual_steps` - Quick steps for intervention

---

## Filters & Pre-processing

### Smart Pre-processing Pipeline

When incidents arrive, they go through this pipeline:

1. **Service Filter** - Skip Kubernetes infrastructure noise
2. **Error Signature Generation** - MD5 hash for deduplication
3. **Aggregation Check** - Increment existing or create new
4. **Tenant Filter** - Skip demo tenants
5. **Transient Check** - Auto-resolve if transient pattern
6. **Save & Broadcast** - If not aggregated
7. **Run Pipeline** - If not auto-resolved

### Service Filter

**Location:** `backend/filters/service_filter.py`

**Purpose:** Prioritize Nucleus services, filter K8s noise.

**Nucleus Services (always processed):**
- Core: caseservice, alertservice, casemaker
- Writers: batchwriter, eventwriter
- Entity: entityenricher, entityextractor
- Integrations: chronicle-fetcher, secops-sync
- Others: api-gateway, graphql, tier-processor

**K8s Infrastructure (filtered unless CRITICAL):**
- Monitoring: prometheus, grafana, alertmanager
- Logging: fluentd, loki
- Networking: ingress-nginx, coredns, calico
- Storage: csi-driver
- Cert management: cert-manager

### Transient Error Filter

**Location:** `backend/filters/transient.py`

**Purpose:** Identify self-healing errors.

**Pattern Categories:**

| Category | Count | Examples |
|----------|-------|----------|
| Network/Connection | 5 | Connection reset, timeout, DNS |
| Race Conditions | 8 | Duplicate key, optimistic locking |
| Rate Limiting | 6 | Rate limit, retry, backoff |
| Data Handling | 5 | EOF, invalid JSON |
| Agent/LLM | 4 | Token limit, parse failure |

### Tenant Filter

**Location:** `backend/filters/tenant.py`

**Purpose:** Skip demo tenants, process production only.

**Logic:**
- Match tenant_id or tenant_name against known lists
- Return (should_process, reason)
- Demo tenants filtered unless explicitly enabled

---

## Pattern Learning

### Overview

Pattern Learning improves triage accuracy by learning from historical incidents.

**Location:** `backend/knowledge/pattern_learner.py`

### How It Works

1. **Error Signature Generation**
   - MD5 hash of (service_name + normalized_error_message)
   - Normalization removes timestamps, UUIDs, line numbers, memory addresses

2. **Pattern Recording**
   - After each triage, record: pattern_id, classification, outcome
   - Track success/failure for each classification

3. **Pattern Matching**
   - For new incidents, generate signature
   - Look up historical pattern in Firestore
   - Return suggestion if pattern exists

4. **Confidence Boosting**
   - If Claude's classification matches pattern: +0.10 to +0.15 boost
   - Higher boost for patterns with 70%+ success rate

5. **Classification Override**
   - If pattern has high confidence, can override Claude
   - Requires: 3+ occurrences, 70%+ success rate, 80%+ confidence

### Pattern Record Fields

| Field | Description |
|-------|-------------|
| `pattern_id` | MD5 signature |
| `error_template` | First 200 chars of normalized error |
| `service_name` | Originating service |
| `classifications` | Count by classification type |
| `success_count` | Successful resolutions |
| `failure_count` | Failed resolutions |
| `first_seen` | First occurrence timestamp |
| `last_seen` | Most recent occurrence |
| `successful_fixes` | Recent successful fix details (max 10) |

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `pattern_learning_enabled` | true | Enable/disable feature |
| `pattern_min_occurrences` | 3 | Min occurrences for override |
| `pattern_override_success_rate` | 0.70 | Min success rate |
| `pattern_override_confidence` | 0.80 | Min confidence |

### Integration Points

**In Triage Agent:**
- Pattern lookup in `_pre_analyze()` step 6
- Historical context added to Claude prompt
- Confidence boosting after Claude response
- Override logic if high-confidence pattern disagrees

**In Orchestrator:**
- Record pattern after triage completes
- Record outcome after verification success
- Record TRANSIENT success when auto-resolved

---

## Google Chat Integration

### Overview

On Call Helper can monitor a Google Chat space for alert messages forwarded by GCP alerting policies.

**Location:** `backend/services/gchat.py`, `backend/services/gchat_poller.py`

### Architecture

```
Google Chat Space → Apps Script Relay → Cloudflare Tunnel → Backend Webhook
                                                              (/webhook/gchat)
```

### Message Parsing (`gchat.py`)

The `ParsedAlert` model extracts structured data from alert text:
- `title` - Alert title
- `service_name` - Extracted via pattern matching (configuration_name, revision_name, alert title mapping)
- `error_message` - Alert body text
- `severity` - Mapped from alert content
- `logging_url` - Cloud Logging URL if present in message text

### Service Name Extraction

The `_extract_service_from_alert()` function uses multiple strategies:
1. `configuration_name` field extraction (Cloud Run services)
2. `revision_name` field extraction
3. Alert name mapping (e.g., "secops integration errors" → secops-integration)
4. Generic `SERVICE-prod` pattern matching
5. Fallback enrichment at API response time

### GChat Polling (`gchat_poller.py`)

Alternative to the Apps Script relay - polls the Google Chat API directly:
- Configurable polling interval
- Tracks processed message IDs for deduplication
- Start/stop via API endpoints

### Apps Script Relay (`scripts/gchat-relay.gs`)

Google Apps Script that:
1. Polls the Google Chat API for new messages in a configured space
2. Forwards messages to the backend webhook via HTTP POST
3. Requires a public URL (Cloudflare tunnel) to reach the local backend

---

## Vertex AI Support

### Overview

On Call Helper supports two AI backends for Claude access:

**Location:** `backend/ai_client.py`

### AI Client Factory

The `create_ai_client()` function creates the appropriate client:

| Setting | Client | Authentication |
|---------|--------|---------------|
| `USE_VERTEX=true` | `AnthropicVertex` | GCP Application Default Credentials |
| `USE_VERTEX=false` | `Anthropic` | `ANTHROPIC_API_KEY` environment variable |

Both clients have identical `messages.create()` interfaces.

### Model Names

| Backend | Triage Model | Fixer Model |
|---------|-------------|-------------|
| Vertex AI | `claude-sonnet-4-5@20250929` | `claude-sonnet-4-5@20250929` |
| Anthropic | `claude-sonnet-4-20250514` | `claude-sonnet-4-20250514` |

### Helper Functions

- `get_triage_model()` - Returns appropriate model name for current backend
- `get_fixer_model()` - Returns appropriate model name for current backend
- `get_backend_name()` - Returns human-readable backend description

---

## Cloud Logging Links

### Overview

Each incident displays a clickable link icon that opens GCP Cloud Logging in the browser.

### URL Construction Priority

1. **Direct URL** - `logging_url` extracted from GChat message text
2. **GCP Log Name** - Built from `gcp_log_name` field (format: `projects/PROJECT/logs/LOG_NAME`)
3. **Service Name Search** - Constructs a search URL using the service name (for GChat incidents)

### Implementation

- `buildCloudLoggingUrl(gcpLogName, gcpResourceType)` - Builds URL from GCP log metadata
- `buildServiceLoggingUrl(serviceName)` - Builds search URL from service name
- `CloudLoggingLink` component - Blue link icon with fallback chain

---

## Analyzing State

### Overview

New incidents show an "Analyzing..." state with a spinner while triage is in progress.

### Behavior

- When `classification` is not yet set and status is `active` or `triaging`:
  - Service name shows "Analyzing..." with a spinning indicator
  - Error title shows "Waiting for triage..."
- When `triage_complete` WebSocket event arrives, the card updates to show real data

---

## Feedback Persistence

### Overview

Operators can provide feedback on escalated incidents (e.g., "Does not need human review"). This feedback is persisted in Firestore.

### Implementation

- `feedback_given` field on the Incident model
- `POST /incidents/{id}/feedback` endpoint saves feedback
- Frontend initializes button state from persisted `feedback_given` value on load

---

## Services

### Error Aggregator

**Location:** `backend/services/error_aggregator.py`

**Purpose:** Reduce noise by aggregating similar errors.

**Features:**
- MD5-based error signatures
- Normalization removes variable content
- Time-window based aggregation (10 minutes)
- Active signature tracking (1000-item limit)

### GCP Logging Service

**Location:** `backend/services/gcp_logging.py`

**Purpose:** Ingest errors from GCP Cloud Logging.

**Modes:**
- **Polling**: Query Cloud Logging API on interval
- **Pub/Sub Push**: Receive via HTTP webhook

**Features:**
- Parse Cloud Logging entries
- Map GCP severity to incident severity
- Extract error messages, stack traces, file paths
- Deduplication via GCP insertId
- Auto-generate incident IDs (OCH-{8chars})

### GitHub Service

**Location:** `backend/services/github.py`

**Purpose:** Read/write access to source code repositories.

**Capabilities:**
- Read source files from local clone
- Create branches for fixes
- Commit changes with fix description
- Open draft pull requests
- Uses `gh` CLI for GitHub operations

### CodeRabbit Service

**Location:** `backend/services/coderabbit.py`

**Purpose:** Automated code review.

**Features:**
- Local CLI integration
- Issue severity: critical, high, medium, low
- Blocking issues prevent merge
- Retry loop (max 3 iterations)
- Language-specific file extensions

### Sandbox Service

**Location:** `backend/services/sandbox.py`

**Purpose:** Test fixes in isolated environment.

**Features:**
- Kind cluster for Kubernetes testing
- Unit test execution
- Smoke test execution
- Coverage measurement
- Configurable timeout (default 15 minutes)

### Production Monitor

**Location:** `backend/services/production_monitor.py`

**Purpose:** Verify fixes in production.

**Features:**
- Monitor Cloud Logging after PR merge
- Check for error recurrence
- Configurable duration (default 2 hours)
- Check interval (default 5 minutes)

**Verification Status:**
- `SUCCESS` - No errors for full duration
- `PARTIAL` - Errors reduced but not eliminated
- `FAILED` - Errors continue at same rate

### PagerDuty Service

**Location:** `backend/services/pagerduty.py`

**Purpose:** Send escalations to on-call.

**Features:**
- Events API integration
- Routing key configuration
- Escalate unresolvable incidents

---

## Storage Backends

### In-Memory Storage

**Location:** `backend/storage.py`

**Purpose:** Fast development storage.

**Limitations:**
- Data lost on restart
- No persistence
- Single instance only

### Firestore Storage

**Location:** `backend/storage_firestore.py`

**Purpose:** Production-grade persistent storage.

**Collections:**

| Collection | Purpose |
|------------|---------|
| `incidents` | Incident documents |
| `triage_results` | Triage analysis results |
| `fix_results` | Generated code fixes |
| `test_results` | Sandbox test results |
| `verification_results` | Production verification |
| `seen_gcp_ids` | Deduplication cache |
| `metrics` | Aggregated metrics |
| `incident_patterns` | Learned error patterns |

**Features:**
- Query by service name
- Query by classification
- Pattern storage and retrieval
- Recent errors summary
- Cross-tenant support
- Configurable database ID

---

## WebSocket Real-time Updates

### Event Types

**Connection Events:**
- `WELCOME` - Initial connection with metrics
- `PING`/`PONG` - Heartbeat (30s interval)

**Incident Lifecycle:**
- `INCIDENT_CREATED` - New incident detected
- `INCIDENT_UPDATED` - Status changed
- `INCIDENT_RESOLVED` - Successfully fixed
- `INCIDENT_ESCALATED` - Needs human attention

**Pipeline Stages:**
- `TRIAGE_STARTED`/`TRIAGE_COMPLETE`
- `FIX_STARTED`/`FIX_GENERATED`
- `REVIEW_STARTED`/`REVIEW_COMPLETE`
- `SANDBOX_STARTED`/`SANDBOX_COMPLETE`
- `PR_CREATED`
- `VERIFICATION_STARTED`/`VERIFICATION_COMPLETE`

**Agent Events:**
- `AGENT_THINKING` - Real-time Claude reasoning
- `CODE_DIFF` - Generated fix preview

**System:**
- `METRICS_UPDATE` - Broadcast updated metrics

### WebSocket Manager

**Location:** `backend/websocket_manager.py`

**Features:**
- Client connection tracking
- Broadcast to all clients
- Per-incident subscriptions
- Ping/pong heartbeat
- JSON serialization with timestamps

### Frontend WebSocket Hook

**Location:** `frontend/src/hooks/useWebSocket.js`

**Features:**
- Auto-reconnect with exponential backoff
- Infinite retry by default
- 30-second ping interval
- Subscribe/unsubscribe to incidents
- Graceful cleanup on unmount

---

## Frontend Dashboard

### Main Components

**App Component** (`frontend/src/App.jsx`)
- Status badge display with animations
- Severity indicators (critical/high/medium/low)
- Time-relative formatting ("5m ago")
- Service name extraction
- Incident title cleanup

**IncidentContext** (`frontend/src/context/IncidentContext.jsx`)
- Redux-like state management
- Incident tracking and updates
- Event feed (last 100 events)
- WebSocket integration
- Metrics synchronization
- Connection status tracking

### UI Components

| Component | Purpose |
|-----------|---------|
| `IncidentTable.jsx` | All incidents with expandable details |
| `MetricsPanel.jsx` | Total, fixed, escalated, MTTR |
| `AgentThinking.jsx` | Real-time Claude reasoning |
| `CodeDiff.jsx` | Before/after code preview |
| `IncidentFeed.jsx` | Event stream visualization |
| `SandboxStatus.jsx` | Test execution status |
| `VerificationStatus.jsx` | Production verification progress |

### Filter Tabs

| Tab | Shows |
|-----|-------|
| All | All incidents |
| Active | processing, triaged, fixing, reviewing |
| Fixed | resolved, fixed, pr_created |
| Review | escalated, needs review |

---

## API Endpoints

### Health & Info

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Basic health check |
| `/health/ready` | GET | Readiness (verifies repo paths) |
| `/health/live` | GET | Liveness check |
| `/info` | GET | App name, version, environment |
| `/metrics` | GET | Incident processing metrics |

### Incidents

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/incidents` | GET | List with optional filters |
| `/incidents/{id}` | GET | Full incident with all results |
| `/incidents/all/details` | GET | All incidents with complete data |
| `/incidents/{id}/resolve` | POST | Manually mark as resolved |
| `/incidents/{id}/feedback` | POST | Submit feedback on incident |

### History & Analytics

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/history/by-service/{name}` | GET | Incidents by service |
| `/history/by-classification/{class}` | GET | Incidents by classification |
| `/history/summary` | GET | Error summary with counts |
| `/history/triage-decisions` | GET | Recent triage decisions |

### Pattern Learning

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/patterns/stats` | GET | Pattern statistics |
| `/patterns/similar` | GET | Find similar patterns |
| `/patterns/config` | GET | Current configuration |

### Webhooks

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/gcp-logs` | POST | GCP Pub/Sub push endpoint |
| `/webhook/gchat` | POST | Google Chat alert webhook |
| `/webhook/test` | POST | Test incident simulation |

### GCP Polling

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/gcp/polling/start` | POST | Start polling |
| `/gcp/polling/stop` | POST | Stop polling |
| `/gcp/polling/status` | GET | Current status |

### Google Chat Polling

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/gchat/polling/start` | POST | Start GChat polling |
| `/gchat/polling/stop` | POST | Stop GChat polling |
| `/gchat/polling/status` | GET | Current GChat polling status |

### WebSocket

| Endpoint | Description |
|----------|-------------|
| `/ws` | Real-time updates |
| `/ws/connections` | Active connection info |

---

## Configuration Options

### Application

| Setting | Default | Description |
|---------|---------|-------------|
| `app_name` | "on-call-helper" | Application name |
| `app_version` | "1.0.0" | Version string |
| `debug` | false | Debug mode |
| `log_level` | "INFO" | Logging verbosity |

### Server

| Setting | Default | Description |
|---------|---------|-------------|
| `host` | "0.0.0.0" | Bind address |
| `port` | 8080 | Listen port |

### AI Models - Anthropic API (default)

| Setting | Default | Description |
|---------|---------|-------------|
| `anthropic_api_key` | - | Anthropic API key |
| `triage_model` | claude-sonnet-4-20250514 | Triage model |
| `fixer_model` | claude-sonnet-4-20250514 | Fixer model |

### AI Models - Vertex AI (alternative)

| Setting | Default | Description |
|---------|---------|-------------|
| `use_vertex` | false | Use Vertex AI instead of Anthropic API |
| `vertex_project_id` | - | GCP project for Vertex AI |
| `vertex_region` | us-east5 | Vertex AI region |
| `vertex_triage_model` | claude-sonnet-4-5@20250929 | Vertex triage model |
| `vertex_fixer_model` | claude-sonnet-4-5@20250929 | Vertex fixer model |

### GCP

| Setting | Default | Description |
|---------|---------|-------------|
| `gcp_project_id` | - | Cloud Logging project |
| `gcp_credentials_path` | - | Service account key path |
| `gcp_log_filter` | "severity>=ERROR" | Log query filter |
| `gcp_auto_poll` | true | Auto-start polling |
| `gcp_poll_interval` | 30 | Polling interval (seconds) |

### Google Chat

| Setting | Default | Description |
|---------|---------|-------------|
| `gchat_space_id` | - | Google Chat space ID to monitor |
| `gchat_auto_poll` | false | Auto-start GChat polling |
| `gchat_poll_interval` | 30 | Polling interval (seconds) |
| `gchat_credentials_path` | - | Service account JSON path (empty = ADC) |
| `gchat_verify_token` | false | Verify Google Chat JWT tokens |

### GitHub

| Setting | Default | Description |
|---------|---------|-------------|
| `github_token` | - | Personal access token |
| `github_repo` | - | Target repository (owner/repo) |
| `github_base_branch` | "main" | Base branch for PRs |

### Repository Paths

| Setting | Description |
|---------|-------------|
| `nucleus_repo_path` | Local clone of target codebase |
| `oncall_repo_path` | SRE knowledge/runbooks repo |

### Storage

| Setting | Default | Description |
|---------|---------|-------------|
| `storage_backend` | "memory" | "memory" or "firestore" |
| `firestore_project_id` | - | Firestore GCP project |
| `firestore_database_id` | "(default)" | Firestore database ID |

### Pattern Learning

| Setting | Default | Description |
|---------|---------|-------------|
| `pattern_learning_enabled` | true | Enable feature |
| `pattern_min_occurrences` | 3 | Min for override |
| `pattern_override_success_rate` | 0.70 | Min success rate |
| `pattern_override_confidence` | 0.80 | Min confidence |

### Verification

| Setting | Default | Description |
|---------|---------|-------------|
| `verification_duration_hours` | 2 | Monitoring duration |
| `verification_check_interval_minutes` | 5 | Check frequency |

### Optional Services

| Setting | Default | Description |
|---------|---------|-------------|
| `coderabbit_max_retries` | 3 | Max review iterations |
| `sandbox_timeout_minutes` | 15 | Sandbox timeout |
| `pagerduty_routing_key` | - | PagerDuty integration |
| `dashboard_url` | - | Dashboard URL for links |

---

## Data Models

### Incident

```python
class Incident:
    id: str                      # OCH-{8chars}
    title: str                   # Error summary
    error_message: str           # Full error message
    stack_trace: Optional[str]   # Stack trace if available
    file_path: Optional[str]     # Affected file
    service_name: str            # Originating service
    severity: Severity           # critical/high/medium/low
    tenant_name: Optional[str]   # Tenant identifier
    environment: str             # production/staging/dev
    status: IncidentStatus       # Current status
    created_at: datetime         # When created
    resolved_at: Optional[datetime]  # When resolved
    pr_url: Optional[str]        # GitHub PR URL

    # GCP metadata
    gcp_insert_id: Optional[str]
    gcp_resource_type: Optional[str]
    gcp_log_name: Optional[str]

    # Aggregation
    occurrence_count: int        # How many times seen
    error_signature: Optional[str]  # MD5 signature

    # Auto-resolution
    auto_resolved: bool
    auto_resolve_reason: Optional[str]
```

### TriageResult

```python
class TriageResult:
    classification: TriageClassification  # fixable/transient/infra_issue/needs_human
    root_cause: str              # Explanation
    confidence: float            # 0.0-1.0
    service_name: str
    file_path: Optional[str]
    function_name: Optional[str]
    code_snippet: Optional[str]
    line_numbers: Optional[str]
    suggested_fix: Optional[str]
    runbook_reference: Optional[str]
    manual_steps: Optional[List[str]]
    related_context: Optional[str]
    gcp_context: Optional[str]
    gcp_queries: Optional[List[str]]
    pre_analysis: Optional[Dict]
    tenant_type: Optional[str]
    pattern_suggestion: Optional[PatternSuggestion]
    override_reason: Optional[str]
```

### PatternRecord

```python
class PatternRecord:
    pattern_id: str              # MD5 signature
    error_template: str          # Normalized error (first 200 chars)
    service_name: str
    classifications: Dict[str, int]  # {"fixable": 5, "transient": 2}
    success_count: int
    failure_count: int
    first_seen: datetime
    last_seen: datetime
    successful_fixes: List[FixRecord]  # Max 10
```

### PatternSuggestion

```python
class PatternSuggestion:
    pattern_id: str
    classification: str
    confidence: float
    occurrence_count: int
    success_rate: float
    suggested_fix: Optional[FixRecord]
```
