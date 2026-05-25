# Version Recommender Service

A Python 3.11 Lambda microservice that recommends the most compatible saved model version for a scientist's current environment. Part of the ML model artifact management platform.

## Prerequisites

- **Node 20** — for CDK
- **Python 3.11** — for the Lambda and tests
- **AWS CLI v2** — for deployments
- **Smithy CLI 1.50+** — installed at `~/bin/smithy`

## Quick start

```bash
# Install Python dev dependencies
pip install -r lambda/requirements-dev.txt

# Run tests
pytest tests/ -v --cov=lambda --cov-report=term-missing

# Full build smoke (Smithy validate → pip dry-run → CDK synth)
./scripts/build.sh
```

## How it works

When a scientist calls `client.load_model("fraud-detector")` without a version, the Python SDK captures their current environment and calls `POST /recommend/{modelName}`. This service fetches all READY versions from the Artifact Management Service, scores each one on Python version, framework, CUDA, and OS compatibility, and returns the best match.

See `docs/IMPLEMENTATION.md` for the full spec.

## Repository layout

```
smithy-model/    Smithy IDL — API contract source of truth
infra-cdk/       CDK TypeScript — all AWS resources
lambda/          Python 3.11 Lambda code
tests/           Unit + integration tests
scripts/         build.sh and ops scripts
docs/            IMPLEMENTATION.md (epic/story breakdown)
.github/         CI workflow
```

## Stages

| Stage | Purpose |
|---|---|
| alpha | Per-developer sandbox (`-alpha-arjun` suffix) |
| beta | Integration testing |
| gamma | Pre-prod, prod-shaped |
| prod | Scientist-facing |

Each stage calls the same-stage Artifact Management Service endpoint.
