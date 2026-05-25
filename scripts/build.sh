#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> [1/3] Building Smithy model (validate + generate OpenAPI)..."
(cd "$ROOT/smithy-model" && smithy build)

echo "==> [2/3] Validating Python dependencies (dry-run)..."
pip install -r "$ROOT/lambda/requirements.txt" --dry-run --quiet

echo "==> [3/3] Synthesizing CDK stacks (alpha)..."
(cd "$ROOT/infra-cdk" && npm ci --silent && npx cdk synth -c stage=alpha)

echo ""
echo "Build complete."
