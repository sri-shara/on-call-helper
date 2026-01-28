# On Call Helper - Setup & Usage Guide

Complete guide for setting up and using On Call Helper to monitor your Nucleus production errors.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Running the Application](#running-the-application)
5. [GCP Cloud Logging Setup](#gcp-cloud-logging-setup)
6. [Using the Dashboard](#using-the-dashboard)
7. [API Reference](#api-reference)
8. [Troubleshooting](#troubleshooting)

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

- **Anthropic API Key** - For Claude AI (triage and fix generation)
- **GCP Project Access** - Read access to Cloud Logging (Logging Viewer role)
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
git clone https://github.com/your-org/on-call-helper.git
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
# REQUIRED - AI Features
# ═══════════════════════════════════════════════════════════════
# Get from: https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx

# ═══════════════════════════════════════════════════════════════
# REQUIRED - GCP Cloud Logging
# ═══════════════════════════════════════════════════════════════
# Your GCP project ID
GCP_PROJECT_ID=nucleus-449303

# Log filter (default catches all errors)
GCP_LOG_FILTER=severity>=ERROR

# Optional: Path to service account key (if not using ADC)
# GCP_CREDENTIALS_PATH=./credentials.json

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

# Optional: GitHub token (if not using gh CLI auth)
# GITHUB_TOKEN=ghp_xxxxx

# ═══════════════════════════════════════════════════════════════
# OPTIONAL - Additional Settings
# ═══════════════════════════════════════════════════════════════
DEBUG=true
LOG_LEVEL=INFO

# CodeRabbit review retries
CODERABBIT_MAX_RETRIES=3

# Sandbox testing timeout (minutes)
SANDBOX_TIMEOUT_MINUTES=15
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

# Run on port 8001 (to avoid conflicts with other services)
PORT=8001 python -m backend.main

# Or with auto-reload for development
PORT=8001 uvicorn backend.main:app --reload --host 0.0.0.0 --port 8001
```

The backend will be available at:
- API: http://localhost:8001
- API Docs: http://localhost:8001/docs
- Health Check: http://localhost:8001/health

### Start Frontend Dashboard

In a separate terminal:

```bash
cd frontend
npm run dev
```

The dashboard will be available at:
- Dashboard: http://localhost:5173

### Verify Everything is Running

```bash
# Check backend health
curl http://localhost:8001/health

# Check frontend proxy to backend
curl http://localhost:5173/api/health
```

---

## GCP Cloud Logging Setup

On Call Helper supports two modes for receiving GCP logs:

### Option 1: Polling Mode (Recommended for Read-Only Access)

If you only have read access to GCP Cloud Logging:

```bash
# 1. Authenticate with GCP
gcloud auth application-default login

# 2. Start polling via API
curl -X POST "http://localhost:8001/gcp/polling/start?interval_seconds=30"

# 3. Check polling status
curl http://localhost:8001/gcp/polling/status

# 4. Stop polling when done
curl -X POST http://localhost:8001/gcp/polling/stop
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

## Using the Dashboard

### Dashboard URL

Open http://localhost:5173 in your browser.

### Dashboard Sections

| Section | Description |
|---------|-------------|
| **Metrics** | Total incidents, auto-fixed count, escalated count |
| **Connection Status** | WebSocket connection indicator (green = connected) |
| **Incidents** | List of incidents with status and details |
| **Agent Activity** | Real-time Claude AI thinking/processing |
| **Code Diff** | Generated code fixes with before/after |

### Incident Statuses

| Status | Description |
|--------|-------------|
| `processing` | Pipeline is running |
| `triaged` | Claude analyzed the error |
| `fixing` | Generating code fix |
| `reviewing` | CodeRabbit reviewing |
| `pr_created` | PR opened on GitHub |
| `escalated` | Requires human attention |
| `resolved` | Fix verified in production |

---

## API Reference

### Health & Status

```bash
# Health check
GET /health

# Readiness check (verifies repo paths exist)
GET /health/ready

# Application metrics
GET /metrics
```

### Incidents

```bash
# List all incidents
GET /incidents

# Get specific incident
GET /incidents/{incident_id}
```

### Webhooks (Manual Testing)

```bash
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

### WebSocket Events

Connect to `ws://localhost:8001/ws` for real-time events:

```javascript
const ws = new WebSocket('ws://localhost:8001/ws')

ws.onmessage = (event) => {
  const data = JSON.parse(event.data)
  console.log('Event:', data.type, data.data)
}
```

Event types:
- `incident_created` - New incident detected
- `incident_resolved` - Incident fixed
- `incident_escalated` - Requires human attention
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
```

### GCP Authentication Errors

```bash
# Re-authenticate
gcloud auth application-default login

# Verify project access
gcloud logging read "severity>=ERROR" --project=YOUR_PROJECT --limit=1
```

### Dashboard Shows "Disconnected"

1. Check backend is running: `curl http://localhost:8001/health`
2. Check Vite proxy config in `frontend/vite.config.js` points to correct port
3. Restart frontend: `cd frontend && npm run dev`

### Incidents Not Appearing

1. Check WebSocket connection in browser dev tools (Network tab)
2. Verify API returns incidents: `curl http://localhost:8001/incidents`
3. Check backend logs for errors

### Fix Generation Fails

Common reasons:
- File path in error doesn't exist in local Nucleus repo
- Claude's generated code doesn't match source (whitespace issues)
- Anthropic API rate limit

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
LOG_LEVEL=DEBUG PORT=8001 python -m backend.main
```

### Simulating Incidents

Use the test webhook to simulate different error types:

```bash
# Fixable error
curl -X POST http://localhost:8001/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"error_message": "nil pointer", "service_name": "test", "tenant_name": "Acme"}'

# Transient error (will be filtered)
curl -X POST http://localhost:8001/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"error_message": "connection reset by peer", "service_name": "test", "tenant_name": "Acme"}'
```

### Hot Reload

Both backend and frontend support hot reload:
- Backend: Changes to Python files auto-reload with uvicorn
- Frontend: Vite provides instant HMR

---

## Support

For issues or questions:
1. Check the [Troubleshooting](#troubleshooting) section
2. Review backend logs for error messages
3. Open an issue on GitHub
