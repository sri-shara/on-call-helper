#!/bin/bash
# Deploy Nucleus to Kind cluster for sandbox testing
set -e

CLUSTER_NAME="${1:?Cluster name required}"
WORK_DIR="${2:?Work directory required}"

echo "Deploying to cluster: $CLUSTER_NAME"
echo "Work directory: $WORK_DIR"

# Switch kubectl context to the Kind cluster
kubectl config use-context "kind-$CLUSTER_NAME"

# Verify cluster is ready
echo "Waiting for cluster to be ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=120s

# Build Docker images if Dockerfile exists
if [ -f "$WORK_DIR/Dockerfile" ]; then
    echo "Building Docker images..."
    cd "$WORK_DIR"

    # Check for Taskfile
    if [ -f "Taskfile.yaml" ] && command -v task &> /dev/null; then
        task build:docker 2>/dev/null || docker build -t nucleus:local .
    else
        docker build -t nucleus:local .
    fi

    # Load image into Kind cluster
    echo "Loading images into Kind cluster..."
    kind load docker-image nucleus:local --name "$CLUSTER_NAME"
fi

# Deploy using available method
if [ -f "$WORK_DIR/k8s/charts/nucleus/Chart.yaml" ] && command -v helm &> /dev/null; then
    echo "Deploying with Helm..."
    helm upgrade --install nucleus "$WORK_DIR/k8s/charts/nucleus" \
        --namespace default \
        --set image.tag=local \
        --set image.pullPolicy=Never \
        --wait --timeout 5m
elif [ -d "$WORK_DIR/k8s" ]; then
    echo "Deploying with kubectl..."
    kubectl apply -f "$WORK_DIR/k8s/" --recursive || true
else
    echo "No deployment manifests found, skipping deployment"
fi

echo "Deployment complete"
