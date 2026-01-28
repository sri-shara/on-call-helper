#!/bin/bash
# Run tests in sandbox environment
set -e

WORK_DIR="${1:?Work directory required}"
TEST_TYPE="${2:-all}"  # all, unit, smoke

echo "Running tests in: $WORK_DIR"
echo "Test type: $TEST_TYPE"

cd "$WORK_DIR"

# Determine test command based on available tools
run_unit_tests() {
    echo "=== Running Unit Tests ==="

    if [ -f "Taskfile.yaml" ] && command -v task &> /dev/null; then
        task test 2>&1
    elif [ -f "Makefile" ]; then
        make test 2>&1
    elif [ -f "go.mod" ]; then
        go test ./... -v 2>&1
    elif [ -f "package.json" ]; then
        npm test 2>&1
    else
        echo "No test runner found"
        return 1
    fi
}

run_smoke_tests() {
    echo "=== Running Smoke Tests ==="

    if [ -f "Taskfile.yaml" ] && command -v task &> /dev/null; then
        task test:smoke 2>&1 || task smoke 2>&1 || echo "No smoke tests defined"
    elif [ -d "test/smoke" ]; then
        go test ./test/smoke/... -v 2>&1
    elif [ -f "Makefile" ]; then
        make smoke 2>&1 || make test-smoke 2>&1 || echo "No smoke tests defined"
    else
        echo "No smoke tests found"
    fi
}

# Run requested tests
case "$TEST_TYPE" in
    unit)
        run_unit_tests
        ;;
    smoke)
        run_smoke_tests
        ;;
    all)
        run_unit_tests
        UNIT_EXIT=$?
        if [ $UNIT_EXIT -eq 0 ]; then
            run_smoke_tests
        else
            echo "Unit tests failed, skipping smoke tests"
            exit $UNIT_EXIT
        fi
        ;;
    *)
        echo "Unknown test type: $TEST_TYPE"
        exit 1
        ;;
esac

echo "=== Tests Complete ==="
