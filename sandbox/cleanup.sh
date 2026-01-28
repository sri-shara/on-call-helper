#!/bin/bash
# Cleanup sandbox environment
set -e

CLUSTER_NAME="${1:?Cluster name required}"
WORK_DIR="${2:-}"

echo "Cleaning up sandbox: $CLUSTER_NAME"

# Delete Kind cluster
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "Deleting Kind cluster: $CLUSTER_NAME"
    kind delete cluster --name "$CLUSTER_NAME"
else
    echo "Cluster $CLUSTER_NAME not found, skipping"
fi

# Clean up work directory if specified
if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
    echo "Removing work directory: $WORK_DIR"
    rm -rf "$WORK_DIR"
fi

# Clean up any dangling Docker resources
echo "Cleaning up Docker resources..."
docker system prune -f --filter "label=io.x-k8s.kind.cluster=$CLUSTER_NAME" 2>/dev/null || true

echo "Cleanup complete"
