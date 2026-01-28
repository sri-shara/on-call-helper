#!/bin/bash
# On Call Helper - Demo Script
# Simulates incidents for demonstration purposes

set -e

# Configuration
API_URL="${API_URL:-http://localhost:8000}"
DELAY_BETWEEN_INCIDENTS="${DELAY_BETWEEN_INCIDENTS:-5}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     On Call Helper - Live Demo               ║${NC}"
echo -e "${CYAN}║     AI-Powered Incident Response             ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

# Check if backend is running
check_backend() {
    echo -n "Checking backend availability... "
    if curl -s "${API_URL}/health" > /dev/null 2>&1; then
        echo -e "${GREEN}OK${NC}"
        return 0
    else
        echo -e "${RED}FAILED${NC}"
        echo ""
        echo -e "${RED}Error: Backend is not running at ${API_URL}${NC}"
        echo "Please start the backend first:"
        echo "  source venv/bin/activate"
        echo "  uvicorn backend.main:app --reload --port 8000"
        return 1
    fi
}

# Send a test incident
send_incident() {
    local service="$1"
    local error_type="$2"
    local error_message="$3"
    local severity="$4"
    local stack_trace="$5"

    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Simulating: ${error_type} in ${service}${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    response=$(curl -s -X POST "${API_URL}/webhook/test" \
        -H "Content-Type: application/json" \
        -d "{
            \"error_message\": \"${error_message}\",
            \"service_name\": \"${service}\",
            \"severity\": \"${severity}\",
            \"stack_trace\": \"${stack_trace}\"
        }")

    if echo "$response" | grep -q "incident_id"; then
        incident_id=$(echo "$response" | grep -o '"incident_id":"[^"]*"' | cut -d'"' -f4)
        echo -e "${GREEN}✓${NC} Incident created: ${CYAN}${incident_id}${NC}"
        echo ""
        echo -e "  Service:  ${service}"
        echo -e "  Severity: ${severity}"
        echo -e "  Error:    ${error_type}"
        echo ""
        echo -e "${BLUE}Watch the dashboard to see the AI process this incident:${NC}"
        echo -e "  → Triage (analyzing root cause)"
        echo -e "  → Fix Generation (creating code fix)"
        echo -e "  → Code Review (CodeRabbit)"
        echo -e "  → Testing (sandbox environment)"
        echo -e "  → PR Creation (GitHub)"
    else
        echo -e "${RED}✗${NC} Failed to create incident"
        echo "$response"
    fi
}

# Demo scenarios
demo_null_pointer() {
    send_incident \
        "caseservice" \
        "NullPointerException" \
        "panic: runtime error: invalid memory address or nil pointer dereference" \
        "ERROR" \
        "goroutine 1 [running]:\nmain.processCase(0xc0001a4000)\n\t/backend/services/caseservice/handler.go:142 +0x234\nmain.main()\n\t/backend/services/caseservice/main.go:28 +0x1a4"
}

demo_index_out_of_bounds() {
    send_incident \
        "alertservice" \
        "IndexOutOfBoundsException" \
        "panic: runtime error: index out of range [5] with length 3" \
        "ERROR" \
        "goroutine 23 [running]:\nmain.processAlerts(0xc000180000)\n\t/backend/services/alertservice/processor.go:89 +0x156\nmain.handleRequest()\n\t/backend/services/alertservice/handler.go:45 +0x98"
}

demo_timeout() {
    send_incident \
        "ingestionservice" \
        "TimeoutException" \
        "context deadline exceeded: database query timeout after 30s" \
        "WARNING" \
        "database/sql.(*DB).QueryContext()\n\t/usr/local/go/src/database/sql/sql.go:1687 +0x207\nmain.fetchTenantData()\n\t/backend/services/ingestionservice/db.go:156 +0x1a9"
}

demo_api_error() {
    send_incident \
        "notificationservice" \
        "APIError" \
        "External API returned error: 503 Service Unavailable - PagerDuty API" \
        "ERROR" \
        "net/http.(*Client).Do()\n\t/usr/local/go/src/net/http/client.go:708 +0x6a3\nmain.sendPagerDutyAlert()\n\t/backend/services/notificationservice/pagerduty.go:78 +0x245"
}

demo_authentication_error() {
    send_incident \
        "authservice" \
        "AuthenticationError" \
        "JWT token validation failed: token signature is invalid" \
        "ERROR" \
        "github.com/golang-jwt/jwt/v5.(*Parser).Parse()\n\t/go/pkg/mod/github.com/golang-jwt/jwt/v5@v5.2.0/parser.go:98 +0x3a5\nmain.validateToken()\n\t/backend/services/authservice/jwt.go:45 +0x189"
}

# Main demo flow
main() {
    check_backend || exit 1

    echo ""
    echo -e "${MAGENTA}Starting demo sequence...${NC}"
    echo -e "${MAGENTA}Open the dashboard at http://localhost:3000 to watch in real-time${NC}"
    echo ""

    # Ask user to select demo mode
    echo "Select demo mode:"
    echo "  1) Single incident (NullPointerException)"
    echo "  2) Multiple incidents (full showcase)"
    echo "  3) Custom incident"
    echo ""
    read -p "Enter choice [1-3]: " choice

    case $choice in
        1)
            echo ""
            demo_null_pointer
            ;;
        2)
            echo ""
            echo -e "${CYAN}Running full demo sequence...${NC}"
            echo ""

            demo_null_pointer
            echo ""
            echo -e "${YELLOW}Waiting ${DELAY_BETWEEN_INCIDENTS}s before next incident...${NC}"
            sleep "$DELAY_BETWEEN_INCIDENTS"

            demo_index_out_of_bounds
            echo ""
            echo -e "${YELLOW}Waiting ${DELAY_BETWEEN_INCIDENTS}s before next incident...${NC}"
            sleep "$DELAY_BETWEEN_INCIDENTS"

            demo_timeout
            echo ""
            echo -e "${YELLOW}Waiting ${DELAY_BETWEEN_INCIDENTS}s before next incident...${NC}"
            sleep "$DELAY_BETWEEN_INCIDENTS"

            demo_api_error
            echo ""
            echo -e "${YELLOW}Waiting ${DELAY_BETWEEN_INCIDENTS}s before next incident...${NC}"
            sleep "$DELAY_BETWEEN_INCIDENTS"

            demo_authentication_error
            ;;
        3)
            echo ""
            read -p "Service name: " service
            read -p "Error type: " error_type
            read -p "Error message: " error_message
            read -p "Severity (ERROR/WARNING): " severity
            send_incident "$service" "$error_type" "$error_message" "$severity" "custom stack trace"
            ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            exit 1
            ;;
    esac

    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}Demo complete!${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "View results:"
    echo "  Dashboard: http://localhost:3000"
    echo "  Incidents: curl ${API_URL}/incidents | jq"
    echo "  Metrics:   curl ${API_URL}/metrics | jq"
    echo ""
}

main "$@"
