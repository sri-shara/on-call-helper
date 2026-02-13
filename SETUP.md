# On Call Helper - Setup & Usage Guide

Complete guide for setting up and using On Call Helper to monitor your Nucleus production errors.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Running the Application](#running-the-application)
5. [GCP Cloud Logging Setup](#gcp-cloud-logging-setup)
6. [Google Chat Setup](#google-chat-setup)
7. [Vertex AI Setup](#vertex-ai-setup)
8. [Firestore Setup](#firestore-setup)
9. [Pattern Learning](#pattern-learning)
10. [Using the Dashboard](#using-the-dashboard)
11. [API Reference](#api-reference)
12. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Software

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.9+ | Backend server |
| Node.js | 18+ | Frontend dashboard |
| Git | 2.x | Version control |
| GitHub CLI | 2.x | Creating PRs (`gh`) |
| gcloud CLI | Latest | GCP authentication |

### Required Accounts & Access

- **GCP Project Access** - Read access to Cloud Logging (Logging Viewer role)
- **Claude AI Access** - Via Vertex AI (recommended) or Anthropic API key
- **GitHub Access** - Push access to the Nucleus repository
- **Local Nucleus Clone** - The repository being monitored

### Install GitHub CLI

```bash
# macOS
brew install gh

# Authenticate
gh auth login
```

### Install Google Cloud CLI

```bash
# macOS
brew install google-cloud-sdk

# Authenticate for Application Default Credentials
gcloud auth application-default login
```

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/sri-shara/on-call-helper.git
cd on-call-helper
```

### 2. Set Up Python Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Install Frontend Dependencies

```bash
cd frontend
npm install
cd ..
```

### 4. Verify Installation

```bash
# Check Python packages
pip list | grep -E "fastapi|anthropic|google-cloud"

# Check Node packages
cd frontend && npm list --depth=0
```

---

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```bash
# ═══════════════════════════════════════════════════════════════
# AI Backend - Choose Vertex AI (recommended) or Anthropic API
# ═══════════════════════════════════════════════════════════════
# Option A: Vertex AI (uses GCP credentials, no separate API key needed)
USE_VERTEX=true
VERTEX_PROJECT_ID=your-vertex-project
VERTEX_REGION=us-east5
VERTEX_TRIAGE_MODEL=claude-sonnet-4-5@20250929
VERTEX_FIXER_MODEL=claude-sonnet-4-5@20250929

# Option B: Anthropic API (set USE_VERTEX=false)
# ANTHROPIC_API_KEY=sk-ant-api03-xxxxx

# ═══════════════════════════════════════════════════════════════
# REQUIRED - GCP Cloud Logging
# ═══════════════════════════════════════════════════════════════
# Your GCP project ID
GCP_PROJECT_ID=your-gcp-project

# Log filter (default catches all errors)
GCP_LOG_FILTER=severity>=ERROR

# Auto-start polling on startup
GCP_AUTO_POLL=true

# Polling interval in seconds
GCP_POLL_INTERVAL=30

# ═══════════════════════════════════════════════════════════════
# OPTIONAL - Google Chat Integration
# ═══════════════════════════════════════════════════════════════
# Google Chat space ID to monitor for alerts
GCHAT_SPACE_ID=spaces/YOUR_SPACE_ID

# Auto-start GChat polling on startup
GCHAT_AUTO_POLL=false

# Polling interval in seconds
GCHAT_POLL_INTERVAL=30

# Path to service account JSON for Chat API (empty = ADC)
# GCHAT_CREDENTIALS_PATH=./gchat-credentials.json

# ═══════════════════════════════════════════════════════════════
# REQUIRED - Local Repository Paths
# ═══════════════════════════════════════════════════════════════
# Path to your local Nucleus repository clone
NUCLEUS_REPO_PATH=/Users/yourname/nucleus

# Path to your oncall/runbooks repository (for SRE knowledge)
ONCALL_REPO_PATH=/Users/yourname/oncall

# ═══════════════════════════════════════════════════════════════
# REQUIRED - GitHub PR Creation
# ═══════════════════════════════════════════════════════════════
# Repository where PRs will be created (owner/repo format)
GITHUB_REPO=your-org/nucleus

# Base branch for PRs
GITHUB_BASE_BRANCH=main

# GitHub token (can also use gh CLI auth)
GITHUB_TOKEN=ghp_xxxxx

# ═══════════════════════════════════════════════════════════════
# STORAGE - Persistence Backend
# ═══════════════════════════════════════════════════════════════
# Options: 'memory' (default) or 'firestore' (persistent)
STORAGE_BACKEND=firestore

# Firestore project (can differ from GCP_PROJECT_ID)
FIRESTORE_PROJECT_ID=your-firestore-project

# Firestore database ID (default: (default))
FIRESTORE_DATABASE_ID=oncall-helper-db

# ═══════════════════════════════════════════════════════════════
# PATTERN LEARNING - Historical Pattern Matching
# ═══════════════════════════════════════════════════════════════
# Enable pattern learning from incident history
PATTERN_LEARNING_ENABLED=true

# Minimum occurrences before pattern can override classification
PATTERN_MIN_OCCURRENCES=3

# Minimum success rate required for override (0.0-1.0)
PATTERN_OVERRIDE_SUCCESS_RATE=0.70

# Minimum confidence required for override (0.0-1.0)
PATTERN_OVERRIDE_CONFIDENCE=0.80

# ═══════════════════════════════════════════════════════════════
# VERIFICATION - Production Monitoring
# ═══════════════════════════════════════════════════════════════
# How long to monitor production after PR merge
VERIFICATION_DURATION_HOURS=2

# How often to check for error recurrence
VERIFICATION_CHECK_INTERVAL_MINUTES=5

# ═══════════════════════════════════════════════════════════════
# OPTIONAL - Additional Settings
# ═══════════════════════════════════════════════════════════════
DEBUG=true
LOG_LEVEL=INFO

# Dashboard URL (for links in notifications)
DASHBOARD_URL=http://localhost:5173

# CodeRabbit review retries
CODERABBIT_MAX_RETRIES=3

# Sandbox testing timeout (minutes)
SANDBOX_TIMEOUT_MINUTES=15

# PagerDuty routing key (for escalations)
# PAGERDUTY_ROUTING_KEY=xxxxx
```

### Verify Configuration

```bash
# Test that the backend can read the config
source venv/bin/activate
python -c "from backend.config import settings; print(f'Project: {settings.gcp_project_id}')"
```

---

## Running the Application

### Start Backend Server

```bash
source venv/bin/activate

# Run with uvicorn for auto-reload during development (port 8080)
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080

# Or specify a different port
uvicorn backend.main:app --reload --host 0.0.0.0 --port 9000
```

The backend will be available at:
- API: http://localhost:8080
- API Docs: http://localhost:8080/docs
- Health Check: http://localhost:8080/health

### Start Frontend Dashboard

In a separate terminal:

```bash
cd frontend
npm run dev
```

The dashboard will be available at:
- Dashboard: http://localhost:5173

The Vite dev server proxies `/api` and `/ws` requests to the backend on port 8080.

### Verify Everything is Running

```bash
# Check backend health
curl http://localhost:8080/health

# Check incidents API
curl http://localhost:8080/incidents

# Check frontend is serving
curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/
```

---

## GCP Cloud Logging Setup

On Call Helper supports two modes for receiving GCP logs:

### Option 1: Polling Mode (Recommended)

If you have read access to GCP Cloud Logging:

```bash
# 1. Authenticate with GCP
gcloud auth application-default login

# 2. Polling starts automatically if GCP_AUTO_POLL=true
# Or start manually via API:
curl -X POST "http://localhost:8080/gcp/polling/start?interval_seconds=30"

# 3. Check polling status
curl http://localhost:8080/gcp/polling/status

# 4. Stop polling when done
curl -X POST http://localhost:8080/gcp/polling/stop
```

### Option 2: Pub/Sub Push (Requires Admin Access)

If you have admin access to create Pub/Sub resources:

```bash
# 1. Create Pub/Sub topic
gcloud pubsub topics create oncall-helper-errors --project=YOUR_PROJECT

# 2. Create Log Sink
gcloud logging sinks create oncall-helper-sink \
  pubsub.googleapis.com/projects/YOUR_PROJECT/topics/oncall-helper-errors \
  --project=YOUR_PROJECT \
  --log-filter='severity>=ERROR'

# 3. Grant sink permission to publish
SINK_SA=$(gcloud logging sinks describe oncall-helper-sink \
  --project=YOUR_PROJECT --format='value(writerIdentity)')
gcloud pubsub topics add-iam-policy-binding oncall-helper-errors \
  --project=YOUR_PROJECT \
  --member="$SINK_SA" \
  --role="roles/pubsub.publisher"

# 4. Create push subscription (requires public URL)
gcloud pubsub subscriptions create oncall-helper-push \
  --project=YOUR_PROJECT \
  --topic=oncall-helper-errors \
  --push-endpoint=https://YOUR_PUBLIC_URL/webhook/gcp-logs
```

---

## Google Chat Setup

On Call Helper can monitor a Google Chat space for alert messages (e.g., from GCP alerting policies that post to Chat).

### Architecture

```
Google Chat Space → Apps Script Relay → Cloudflare Tunnel → On Call Helper Backend
```

The GChat integration uses a Google Apps Script relay (`scripts/gchat-relay.gs`) that:
1. Polls the Google Chat API for new messages in the configured space
2. Forwards alert messages to the On Call Helper backend webhook

### Setup Steps

#### 1. Configure Google Apps Script

1. Go to [script.google.com](https://script.google.com) and create a new project
2. Copy the contents of `scripts/gchat-relay.gs` into the script editor
3. Update the `WEBHOOK_URL` constant to point to your backend (or Cloudflare tunnel URL)
4. Enable the Google Chat API in your Apps Script project:
   - Go to Services > Add a service > Google Chat API
5. Deploy the script and set up a time-driven trigger to run periodically

#### 2. Expose Backend via Cloudflare Tunnel (for remote access)

For the Apps Script to reach your local backend, you need a public URL:

```bash
# Quick tunnel (ephemeral URL, changes on restart)
cloudflared tunnel --url http://localhost:8080

# Or create a named tunnel (permanent URL, requires Cloudflare account)
cloudflared tunnel create oncall-helper
cloudflared tunnel route dns oncall-helper oncall.yourdomain.com
cloudflared tunnel run --url http://localhost:8080 oncall-helper
```

Update the `WEBHOOK_URL` in your Apps Script with the tunnel URL.

#### 3. Configure Environment

```bash
# Google Chat space to monitor
GCHAT_SPACE_ID=spaces/YOUR_SPACE_ID

# Auto-start polling (alternative to Apps Script relay)
GCHAT_AUTO_POLL=false

# Polling interval
GCHAT_POLL_INTERVAL=30
```

#### 4. Start GChat Polling (if not using Apps Script relay)

```bash
# Start polling via API
curl -X POST "http://localhost:8080/gchat/polling/start"

# Check status
curl http://localhost:8080/gchat/polling/status

# Stop polling
curl -X POST http://localhost:8080/gchat/polling/stop
```

### GChat Incidents

GChat incidents are parsed from alert message text and include:
- Service name extraction from alert patterns (e.g., `configuration_name`, alert titles)
- Cloud Logging URL extraction (if present in the message)
- Severity mapping based on alert content
- Full alert metadata stored in `gchat_metadata`

---

## Vertex AI Setup

Vertex AI allows you to use Claude AI models through Google Cloud Platform, avoiding the need for a separate Anthropic API key.

### Prerequisites

- A GCP project with Vertex AI API enabled
- Access to Claude models on Vertex AI (Model Garden)
- `gcloud auth application-default login` for authentication

### Configuration

```bash
# Enable Vertex AI
USE_VERTEX=true

# GCP project with Vertex AI access
VERTEX_PROJECT_ID=your-vertex-project

# Region (must match where Claude is available)
VERTEX_REGION=us-east5

# Model names (Vertex uses different naming format)
VERTEX_TRIAGE_MODEL=claude-sonnet-4-5@20250929
VERTEX_FIXER_MODEL=claude-sonnet-4-5@20250929
```

### Switching Between Backends

The `backend/ai_client.py` module automatically creates the correct client:
- `USE_VERTEX=true` → `AnthropicVertex` client (uses GCP credentials)
- `USE_VERTEX=false` → `Anthropic` client (uses `ANTHROPIC_API_KEY`)

Both clients have identical interfaces (`messages.create()`), so the rest of the codebase doesn't need to change.

### Verify Vertex AI Access

```bash
# Check that Vertex AI is reachable
gcloud ai models list --region=us-east5 --project=your-vertex-project
```

---

## Firestore Setup

To persist incidents and AI agent decisions across restarts:

### 1. Create Firestore Database

```bash
# Create a new Firestore database (or use existing)
gcloud firestore databases create \
  --location=nam5 \
  --project=YOUR_FIRESTORE_PROJECT \
  --database=oncall-helper-db
```

### 2. Configure Environment

```bash
STORAGE_BACKEND=firestore
FIRESTORE_PROJECT_ID=your-firestore-project
FIRESTORE_DATABASE_ID=oncall-helper-db
```

### 3. Collections Created Automatically

| Collection | Purpose |
|------------|---------|
| `incidents` | Incident records |
| `triage_results` | Claude AI triage decisions |
| `fix_results` | Generated code fixes |
| `test_results` | Sandbox test results |
| `verification_results` | Production verification results |
| `seen_gcp_ids` | Deduplication tracking |
| `metrics` | Aggregated metrics |
| `incident_patterns` | Learned error patterns |

### 4. Query Historical Data

```bash
# Get incidents by service
GET /history/by-service/alertservice

# Get incidents by classification
GET /history/by-classification/transient

# Get recent errors summary
GET /history/summary?hours=24

# Get all triage decisions
GET /history/triage-decisions
```

---

## Pattern Learning

Pattern Learning improves triage accuracy by learning from historical incidents.

### How It Works

1. **Error Signature Generation** - Each error gets an MD5 signature based on service + normalized message
2. **Pattern Recording** - After triage, the classification is recorded for the signature
3. **Pattern Matching** - New incidents check for matching historical patterns
4. **Confidence Boosting** - If Claude's classification matches historical pattern, confidence is boosted
5. **Classification Override** - High-confidence patterns can override Claude's decision

### Configuration

```bash
# Enable/disable pattern learning
PATTERN_LEARNING_ENABLED=true

# Minimum occurrences before a pattern can override (default: 3)
PATTERN_MIN_OCCURRENCES=3

# Minimum success rate for override (default: 0.70 = 70%)
PATTERN_OVERRIDE_SUCCESS_RATE=0.70

# Minimum confidence for override (default: 0.80 = 80%)
PATTERN_OVERRIDE_CONFIDENCE=0.80
```

### API Endpoints

```bash
# Get pattern learning statistics
curl http://localhost:8080/patterns/stats

# Response:
{
  "total_patterns": 15,
  "patterns_by_classification": {
    "transient": 8,
    "fixable": 5,
    "infra_issue": 2
  },
  "avg_occurrences": 4.2,
  "avg_success_rate": 0.78
}

# Find similar patterns for an error
curl "http://localhost:8080/patterns/similar?error=nil%20pointer&service=alertservice"

# Get current configuration
curl http://localhost:8080/patterns/config
```

### Override Behavior

When a new incident matches a historical pattern with:
- 3+ occurrences
- 70%+ success rate
- 80%+ confidence

The system may override Claude's classification. This appears in the triage result as:

```json
{
  "classification": "transient",
  "override_reason": "Overridden from FIXABLE based on 5 similar incidents (85% success rate)"
}
```

---

## Using the Dashboard

### Dashboard URL

Open http://localhost:5173 in your browser.

### Dashboard Sections

| Section | Description |
|---------|-------------|
| **Metrics** | Total incidents, auto-fixed, escalated, MTTR |
| **Connection Status** | WebSocket indicator (green = connected) |
| **Filter Tabs** | All, Active, Fixed, Review |
| **Incidents** | List with status badges and severity |
| **Detail Panel** | Full analysis, diffs, and results |

### Incident Statuses

| Status | Description |
|--------|-------------|
| `processing` | Pipeline is running |
| `triaging` | Claude AI analyzing (shows "Analyzing..." in UI) |
| `triaged` | Claude analyzed the error |
| `fixing` | Generating code fix |
| `reviewing` | CodeRabbit reviewing |
| `pr_created` | PR opened on GitHub |
| `escalated` | Requires human attention |
| `resolved` | Fix verified in production |
| `auto_resolved` | Transient error (self-healing) |

### Classification Badges

| Badge | Meaning |
|-------|---------|
| Auto-Fixable | Code bug that can be automatically fixed |
| Self-Healing | Transient error with automatic retry |
| Infra Issue | Infrastructure problem (AlloyDB, Pub/Sub, etc.) |
| Needs Review | Too complex for automated fix |

### Cloud Logging Links

Each incident displays a blue link icon next to its ID that opens GCP Cloud Logging:
- **GCP incidents** - Links directly to the log entry
- **GChat incidents** - Links to a service-name-based search in Cloud Logging
- Visible in both the incident list sidebar and the detail panel

### Feedback Actions

For escalated incidents, a "Does not need human review" button allows operators to provide feedback. This feedback is persisted and survives page refreshes.

---

## API Reference

### Health & Status

```bash
# Health check
GET /health

# Readiness check (verifies repo paths exist)
GET /health/ready

# Liveness check
GET /health/live

# Application info
GET /info

# Application metrics
GET /metrics
```

### Incidents

```bash
# List all incidents (with optional filters)
GET /incidents?status=processing&limit=50
GET /incidents?source=gchat           # Filter by source

# Get specific incident with all results
GET /incidents/{incident_id}

# Get all incidents with complete data
GET /incidents/all/details

# Manually resolve an incident
POST /incidents/{incident_id}/resolve

# Submit feedback on an incident
POST /incidents/{incident_id}/feedback
Content-Type: application/json
{"feedback": "not_needs_human"}
```

### History & Analytics

```bash
# Get incidents by service
GET /history/by-service/{service_name}

# Get incidents by classification
GET /history/by-classification/{classification}

# Get error summary
GET /history/summary?hours=24

# Get triage decisions for audit
GET /history/triage-decisions
```

### Pattern Learning

```bash
# Get pattern statistics
GET /patterns/stats

# Find similar patterns
GET /patterns/similar?error={error_msg}&service={service}

# Get configuration
GET /patterns/config
```

### Webhooks

```bash
# GCP Pub/Sub push endpoint
POST /webhook/gcp-logs

# Google Chat alert webhook
POST /webhook/gchat

# Send test incident
POST /webhook/test
Content-Type: application/json

{
  "error_message": "nil pointer dereference",
  "service_name": "alertservice",
  "tenant_name": "Acme Corp",
  "file_path": "backend/services/alertservice/handler.go",
  "stack_trace": "goroutine 1 [running]:\nmain.Process()"
}
```

### GCP Polling Control

```bash
# Start polling
POST /gcp/polling/start?interval_seconds=30

# Stop polling
POST /gcp/polling/stop

# Check status
GET /gcp/polling/status
```

### Google Chat Polling Control

```bash
# Start polling
POST /gchat/polling/start

# Stop polling
POST /gchat/polling/stop

# Check status
GET /gchat/polling/status
```

### WebSocket

Connect to `ws://localhost:8080/ws` for real-time events:

```javascript
const ws = new WebSocket('ws://localhost:8080/ws')

ws.onmessage = (event) => {
  const data = JSON.parse(event.data)
  console.log('Event:', data.type, data.data)
}
```

Event types:
- `incident_created` - New incident detected (includes `gcp_log_name`, `gchat_metadata`)
- `incident_updated` - Incident status changed
- `incident_resolved` - Incident fixed
- `incident_escalated` - Requires human attention
- `triage_complete` - Triage finished (includes `service_name`)
- `pipeline_stage` - Pipeline progress update
- `agent_thinking` - Claude AI processing
- `code_diff` - Generated fix preview
- `metrics_update` - Dashboard metrics refresh

---

## Troubleshooting

### Backend Won't Start

```bash
# Check Python version
python --version  # Should be 3.9+

# Check dependencies installed
pip list | grep fastapi

# Check .env file exists
cat .env | head -5

# Check for port conflicts
lsof -i :8080
```

### GCP Authentication Errors

GCP credentials expire periodically and need to be refreshed:

```bash
# Re-authenticate
gcloud auth application-default login

# Verify project access
gcloud logging read "severity>=ERROR" --project=YOUR_PROJECT --limit=1

# Check credentials
cat ~/.config/gcloud/application_default_credentials.json | head -3
```

If you see `RefreshError: Reauthentication is needed`, just re-run `gcloud auth application-default login`.

### Dashboard Shows "Disconnected"

1. Check backend is running: `curl http://localhost:8080/health`
2. Check Vite proxy config in `frontend/vite.config.js` points to port 8080
3. Check browser console for WebSocket errors
4. Restart frontend: `cd frontend && npm run dev`

### Incidents Not Appearing

1. Check WebSocket connection in browser dev tools (Network tab)
2. Verify API returns incidents: `curl http://localhost:8080/incidents`
3. Check GCP polling is running: `curl http://localhost:8080/gcp/polling/status`
4. Check backend logs for errors

### GChat Incidents Show "unknown" Service

This is expected for newly parsed alerts. The service name is enriched:
1. At parse time from alert patterns (configuration_name, alert titles)
2. After triage when Claude identifies the service
3. At API response time using fallback text extraction

### Fix Generation Fails

Common reasons:
- File path in error doesn't exist in local Nucleus repo
- Claude's generated code doesn't match source (whitespace issues)
- Anthropic/Vertex AI rate limit

Check backend logs for specific error messages.

### PR Creation Fails

```bash
# Verify gh CLI is authenticated
gh auth status

# Verify you have push access to the repo
cd /path/to/nucleus
git push --dry-run origin main

# Check GITHUB_REPO in .env matches the remote
git remote -v
```

### Pattern Learning Not Working

```bash
# Check if enabled
curl http://localhost:8080/patterns/config

# Check pattern stats
curl http://localhost:8080/patterns/stats

# Verify Firestore is configured
echo $STORAGE_BACKEND  # Should be 'firestore'
```

### Cloudflare Tunnel Issues

Quick tunnels generate ephemeral URLs that change on restart:

```bash
# Restart tunnel
cloudflared tunnel --url http://localhost:8080

# Copy the new URL to your Apps Script WEBHOOK_URL
```

For a permanent URL, set up a named tunnel with a Cloudflare account.

---

## Development Tips

### Running Tests

```bash
source venv/bin/activate
pytest tests/ -v
```

### Viewing Logs

Backend logs go to stdout. For more verbose logging:

```bash
LOG_LEVEL=DEBUG uvicorn backend.main:app --reload --port 8080
```

### Simulating Incidents

Use the test webhook to simulate different error types:

```bash
# Fixable error
curl -X POST http://localhost:8080/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"error_message": "nil pointer", "service_name": "alertservice", "tenant_name": "Acme"}'

# Transient error (will be auto-resolved)
curl -X POST http://localhost:8080/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"error_message": "connection reset by peer", "service_name": "alertservice", "tenant_name": "Acme"}'

# Infrastructure error
curl -X POST http://localhost:8080/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"error_message": "AlloyDB connection pool exhausted", "service_name": "caseservice", "tenant_name": "Acme"}'
```

### Hot Reload

Both backend and frontend support hot reload:
- Backend: Changes to Python files auto-reload with uvicorn `--reload`
- Frontend: Vite provides instant HMR

---

## Support

For issues or questions:
1. Check the [Troubleshooting](#troubleshooting) section
2. Review backend logs for error messages
3. Open an issue on GitHub
