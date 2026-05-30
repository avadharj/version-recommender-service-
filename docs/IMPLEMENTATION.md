# Version Recommender Service — Implementation Reference

> **Engineer's reference.** Per-story implementation notes for every epic. The Version Recommender is the third component of the ML model artifact management platform. When this doc and the high-level platform spec disagree, this doc wins on mechanics.

**Tech stack:** Smithy → OpenAPI → REST · API Gateway (AWS_IAM auth) · Python 3.11 Lambda (ARM64) · ElastiCache Redis (prod) / in-memory LRU (alpha/gamma) · CDK TypeScript · CodePipeline (deploy) · GitHub Actions (build/test).

**Sizing:** S = a few hours, M = 1–2 days, L = multi-day.

**Glossary:**
- `model_name` — logical model identifier matching the Artifact Management Service (e.g. `fraud-detector`).
- `env_snapshot` — the calling scientist's current environment at load time: Python version, framework, packages, CUDA, OS.
- `dep_snapshot` — the environment snapshot stored with a saved model version (same schema, different direction).
- `compatibility_score` — composite 0–100 float representing how well a candidate version matches the current environment.
- `recommendation` — the single best-matching version string plus score and human-readable explanation.
- READY — the only status considered for recommendation; PENDING/DELETED/FAILED versions are never candidates.

---

## Current state of extant services

Both upstream services are **fully implemented and deployed** as of 2026-05-23. Do not re-implement or re-deploy them — only the recommender service is new work.

### Artifact Management Service (AMS)

All 35 stories complete. Three stages live:

| Stage | Endpoint |
|---|---|
| alpha | `https://pi5ywcu3ub.execute-api.us-east-1.amazonaws.com/alpha` |
| gamma | `https://idco76hrk9.execute-api.us-east-1.amazonaws.com/gamma` |
| prod  | `https://afwtpvnxe7.execute-api.us-east-1.amazonaws.com/prod` |

Auth: AWS_IAM (SigV4). Region: us-east-1.

**AMS API gotchas that directly affect the recommender's AMSClient (Story 3.1):**

- **`ListVersions` returns sparse responses.** The `GET /models/{modelName}/versions` response includes only `version`, `status`, and `createdAt` per entry. The `depSnapshot` field is **absent** from list responses. The AMSClient must do a two-phase fetch: (1) list to get version strings and status, (2) call `GET /models/{modelName}/versions/{version}` for each READY entry to obtain the full dep_snapshot needed for scoring. Full responses with `depSnapshot` come only from `GetVersion`, `GetLatestVersion`, and `ConfirmVersion`.
- **`ListVersions` response envelope key is `"versions"`** (not `"items"` — that's `ListModels`).
- **No server-side status filter on `ListVersions`.** There is no `?status=READY` query parameter. Filter to READY versions on the client side after fetching.
- **Version path param is a dotted string** (e.g. `"3.2"`). Never split into major/minor path segments.
- **`checksumSha256` is base64-encoded SHA-256**, not hex. (Relevant if the AMSClient ever reads this field from a version response.)

### Python SDK (`artifact-mgmt-client`)

All 7 epics complete. Published at `https://github.com/avadharj/artifact-mgmt-python-sdk`.

The recommender service does **not** call the SDK directly — it calls AMS via its own `AMSClient`. Epic 7 of the recommender adds recommender integration back into the SDK (a separate story set in the SDK repo, driven by this service's deployment).

---

## Context

### What problem this solves

When a scientist calls `client.load_model("fraud-detector")` without specifying a version, the Python SDK returns the *latest* version by creation date. But "latest" and "most compatible" are different things. A model saved last week with Python 3.10 + PyTorch 2.0 may be the latest entry, yet the scientist is running Python 3.12 + PyTorch 2.3. An older version saved with Python 3.12 + PyTorch 2.2 would load with fewer warnings and is far less likely to produce silent numerical differences from framework version skew.

The Version Recommender Service answers: **given your current Python environment, which saved version of this model is most compatible with what you are running right now?**

The service is a pure computation layer — it calls the Artifact Management Service to read version metadata, scores each READY version against the environment snapshot supplied by the SDK, and returns the best match. It has no database of its own.

### What problem this does NOT solve

- It does not reserialize or reformat models. The recommended version's bytes are the original bytes.
- It does not block load — if the recommender is unreachable, the SDK falls back to `latest` silently.
- It does not gate deploys. Scientists retain full control and can always specify `version="latest"` to bypass it.
- It does not handle cross-framework recommendations. A PyTorch model is never recommended to a TensorFlow scientist.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  Scientist's machine                                               │
│                                                                    │
│  model = client.load_model("fraud-detector")                       │
│                    │                                               │
│              use_recommender=True (default)                        │
│              version not specified                                 │
│                    │                                               │
│        ┌───────────▼────────────┐                                  │
│        │  ArtifactMgmtClient   │                                  │
│        │  _snapshot.capture()  │  ← current env captured here     │
│        └───────────┬────────────┘                                  │
│                    │  POST /recommend/{model_name}                 │
│                    │  body: env_snapshot JSON                      │
└────────────────────┼───────────────────────────────────────────────┘
                     │  AWS_IAM (SigV4)
                     ▼
┌────────────────────────────────────────────────────────────────────┐
│  API Gateway (Version Recommender)  — separate stack, same region  │
│                                                                    │
│  POST /recommend/{model_name}                                      │
│                    │                                               │
│                    ▼                                               │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  RecommenderHandler  (Python 3.11 Lambda, ARM64)           │   │
│  │                                                             │   │
│  │  1. Parse + validate env_snapshot from request body        │   │
│  │  2. Check in-memory LRU cache (60s TTL)                    │   │
│  │  3. GET /models/{model_name}/versions?limit=200            │   │ ─── SigV4 ──▶  Artifact Mgmt API
│  │     (calls Artifact Management Service)                    │   │
│  │  4. Filter READY versions only                             │   │
│  │  5. ScoreEngine.score_all(env_snapshot, candidates)        │   │
│  │  6. Return top recommendation                              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                     │  version string returned
                     ▼
┌────────────────────────────────────────────────────────────────────┐
│  ArtifactMgmtClient (continued)                                    │
│                                                                    │
│  Uses recommended version instead of "latest"                      │
│  GET /models/{model_name}/versions/{recommended_version}           │
│    → download_url → deserialize → ArtifactModel                   │
└────────────────────────────────────────────────────────────────────┘
```

### Component relationships

| Component | Role | Data flow direction |
|---|---|---|
| Artifact Management Service | Owns version metadata + dep_snapshots | Recommender reads from it |
| Version Recommender Service | Pure computation over metadata | Reads from AMS; writes nothing |
| Python SDK (`artifact-mgmt-client`) | Caller | Passes env_snapshot; uses returned version |

### Why a separate service, not a Lambda function inside AMS

- **Separation of concerns.** AMS is Java 21 + SnapStart; mixing in Python ML heuristics there would complicate both.
- **Independent scaling.** Recommendation calls are bursty (every `load_model`) and can have a different traffic profile from write paths.
- **Independent deployment.** Scoring algorithm changes don't require AMS re-deploys.
- **SnapStart inapplicable.** Python Lambda does not support SnapStart (Java-only). Grouping with the Java service gains nothing.

---

## Stages

Four stages following the same shape as the Artifact Management Service:

| Stage | Purpose | Auto-deploy | Bake time | Approval |
|---|---|---|---|---|
| **alpha** | Per-developer sandbox, suffixed `-alpha-arjun` | On push to feature branch | none | none |
| **beta** | Integration testing | On merge to `main` | 30 min | automated (integration tests pass) |
| **gamma** | Pre-prod, prod-shaped | After beta bakes clean | 2 hours | automated (synthetic canaries pass) |
| **prod** | Scientist-facing | After gamma bakes clean | 1 hour | manual approval (one-time per release) |

Each stage has its own Lambda functions, API Gateway, caching layer, alarms, and dashboards. No cross-stage data sharing. Recommender always calls the same-stage Artifact Management Service endpoint — alpha recommender calls alpha AMS, gamma recommender calls gamma AMS.

---

## Scoring algorithm

### Overview

Each candidate version (READY status only) is scored against the scientist's current environment using four dimensions. The final score is a weighted sum normalized to 0–100. When scores tie, recency breaks the tie (the more recently created version wins).

### Dimension scores

**1. Python version match (weight: 35 pts max)**

Parse `major.minor.patch` from both `dep_snapshot.pythonVersion` and `env_snapshot.pythonVersion`. Compare only `major.minor` — patch differences are ignored.

| Condition | Points |
|---|---|
| Same `major.minor` | 35 |
| Same `major`, different `minor` | 21 (60% of 35) |
| Different `major` | 0 |

**2. Framework version match (weight: 35 pts max)**

Parse `major.minor` from both `dep_snapshot.framework.version` and `env_snapshot.framework.version`. The `dep_snapshot.framework.name` must match `env_snapshot.framework.name`; if names differ, the version is **automatically disqualified** (score = -∞, excluded from results).

| Condition | Points |
|---|---|
| Exact `major.minor` match | 35 |
| Same `major`, different `minor` (compatible minor) | 24.5 (70% of 35) |
| Different `major` | 7 (20% of 35) |
| Different framework name | Disqualified — excluded |

**3. CUDA compatibility (weight: 20 pts max)**

| Condition | Points |
|---|---|
| Both CPU-only (`cuda_version` is null on both) | 20 |
| Both GPU, same CUDA major version | 18 (90% of 20) |
| Both GPU, different CUDA major version | 10 |
| One CPU, one GPU (mismatch) | 8 (40% of 20) |

**4. OS family match (weight: 10 pts max)**

Extract OS family from the `os` field string (e.g. `"linux-x86_64"` → `"linux"`, `"darwin-arm64"` → `"darwin"`, `"windows-amd64"` → `"windows"`). Compare family only; architecture suffix is ignored.

| Condition | Points |
|---|---|
| Same OS family | 10 |
| Different OS family | 5 (50% of 10) |

**Total max score: 100 pts (35 + 35 + 20 + 10)**

### Recency tiebreaker

When two candidates have the same final score (rounded to two decimal places), the version with the later `created_at` timestamp wins. This means that among equally compatible versions, the team's most recent work is preferred.

### Disqualification rules

A version is excluded entirely (not ranked last, simply omitted) if:

- Status is not `READY`.
- `dep_snapshot.framework.name` does not match `env_snapshot.framework.name` (different framework family entirely — a PyTorch model is useless to a TensorFlow scientist).

### Example scored output

```
env: Python 3.11, PyTorch 2.1, CPU, linux-x86_64

v3.2  Python 3.11 (+35), PyTorch 2.1 (+35), CPU (+20), linux (+10) = 100.0  ← winner
v2.1  Python 3.11 (+35), PyTorch 2.2 (+24.5), CPU (+20), linux (+10) = 89.5
v1.8  Python 3.10 (+21), PyTorch 2.1 (+35), CPU (+20), linux (+10) = 86.0
v4.0  Python 3.11 (+35), PyTorch 3.0 (+7), CPU (+20), darwin (+5) = 67.0
```

Explanation string for v3.2: `"v3.2 is the best match: exact Python 3.11, exact PyTorch 2.1, CPU environment, same OS (linux)"`

### Score stability contract

A given `(model_name, env_snapshot)` pair produces a deterministic score. There is no randomness. Caching correctness relies on this: a cached result for a 60-second window must be identical to what a fresh computation would return.

---

## API contract

### Smithy shapes

```smithy
$version: "2.0"
namespace com.anthropic.versionrecommender

@title("VersionRecommenderService")
@aws.protocols#restJson1
service VersionRecommender {
    version: "2026-05-23"
    operations: [RecommendVersion, HealthCheck]
    errors: [ValidationException, InternalServerException, ThrottlingException]
}

// ─── Primary operation ────────────────────────────────────────────────────

@http(method: "POST", uri: "/recommend/{modelName}", code: 200)
operation RecommendVersion {
    input: RecommendVersionInput
    output: RecommendVersionOutput
    errors: [
        ModelNotFoundException,
        NoCompatibleVersionException,
        ValidationException,
    ]
}

structure RecommendVersionInput {
    @required @httpLabel modelName: ModelName
    @required envSnapshot: EnvSnapshot
}

structure RecommendVersionOutput {
    @required recommendedVersion: VersionId    // e.g. "3.2"
    @required score: Float                     // 0.0 – 100.0
    @required explanation: String              // human-readable
    @required candidatesEvaluated: Integer     // how many READY versions were scored
    @required cached: Boolean                  // true if served from cache
}

// ─── Health check ─────────────────────────────────────────────────────────

@readonly
@http(method: "GET", uri: "/health", code: 200)
operation HealthCheck {
    output: HealthCheckOutput
}

structure HealthCheckOutput {
    @required status: String    // "ok"
    @required stage: String
    @required version: String   // Lambda package version
}

// ─── Shapes ───────────────────────────────────────────────────────────────

@pattern("^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
@length(min: 3, max: 64)
string ModelName

@pattern("^\\d+\\.\\d+$")
string VersionId

structure EnvSnapshot {
    @required pythonVersion: String      // "3.11.7"
    @required framework: FrameworkInfo
    cudaVersion: String                  // null = CPU-only
    @required os: String                 // "linux-x86_64"
}

structure FrameworkInfo {
    @required name: String               // "pytorch" | "tensorflow" | "sklearn" | "huggingface"
    @required version: String            // "2.1.0"
}

// ─── Errors ───────────────────────────────────────────────────────────────

@error("client")
@httpError(404)
structure ModelNotFoundException {
    @required code: String        // "ModelNotFound"
    @required message: String
    @required requestId: String
}

@error("client")
@httpError(422)
structure NoCompatibleVersionException {
    @required code: String        // "NoCompatibleVersion"
    @required message: String
    @required requestId: String
    @required reason: String      // "all_disqualified" | "no_ready_versions" | "model_empty"
}

@error("client")
@httpError(400)
structure ValidationException {
    @required code: String        // "ValidationError"
    @required message: String
    @required requestId: String
    details: Document
}

@error("server")
@httpError(500)
structure InternalServerException {
    @required code: String        // "InternalError"
    @required message: String
    @required requestId: String
}

@error("server")
@httpError(429)
structure ThrottlingException {
    @required code: String        // "Throttled"
    @required message: String
    @required requestId: String
    retryAfterSeconds: Integer
}
```

### Wire format examples

**Request:**
```http
POST /recommend/fraud-detector
Content-Type: application/json
Authorization: AWS4-HMAC-SHA256 ...

{
  "envSnapshot": {
    "pythonVersion": "3.11.7",
    "framework": { "name": "pytorch", "version": "2.1.0" },
    "cudaVersion": null,
    "os": "linux-x86_64"
  }
}
```

**Response (200):**
```json
{
  "recommendedVersion": "3.2",
  "score": 100.0,
  "explanation": "v3.2 is the best match: exact Python 3.11, exact PyTorch 2.1, CPU environment, same OS (linux)",
  "candidatesEvaluated": 4,
  "cached": false
}
```

**Response (422 NoCompatibleVersion):**
```json
{
  "code": "NoCompatibleVersion",
  "message": "No READY versions of 'fraud-detector' are compatible with your environment",
  "requestId": "abc123",
  "reason": "all_disqualified"
}
```

---

## Repository layout

```
version-recommender/
├── smithy-model/
│   ├── smithy-build.json
│   └── model/
│       └── version-recommender.smithy
├── infra-cdk/
│   ├── package.json
│   ├── cdk.json
│   ├── bin/app.ts
│   └── lib/
│       ├── stage-config.ts
│       ├── recommender-stack.ts       # Lambda + API Gateway
│       └── observability-stack.ts
├── lambda/
│   ├── handler.py                     # Lambda entry point
│   ├── scorer.py                      # ScoreEngine — pure computation
│   ├── ams_client.py                  # HTTP client for Artifact Management Service
│   ├── cache.py                       # In-memory LRU + optional Redis adapter
│   ├── models.py                      # Dataclasses: EnvSnapshot, Candidate, Recommendation
│   ├── exceptions.py                  # Exception hierarchy
│   └── requirements.txt              # pinned, hash-checked
├── tests/
│   ├── conftest.py
│   ├── test_scorer.py
│   ├── test_handler.py
│   ├── test_ams_client.py
│   ├── test_cache.py
│   └── integration/
│       └── test_recommend_e2e.py
├── scripts/
│   ├── build.sh
│   └── ops/
│       └── warm-cache.sh
├── docs/
│   └── IMPLEMENTATION.md              # this file
├── .github/workflows/
│   └── ci.yml
└── README.md
```

---

# Epic 1 — Project bootstrap & Smithy contract

**Goal:** Lock the API contract before any handler code. Stand up the repo skeleton and CI.

## Story 1.1 — Initialize repo with Smithy, CDK, Lambda skeleton [M]

**Description:** Create the repository with three top-level packages and the bootstrap script.

**Layout:** As shown in the Repository layout section above.

**Implementation notes:**
- Top-level `build.sh`: runs `smithy build` → `pip install -r lambda/requirements.txt --dry-run` (validate deps) → `npm ci && npx cdk synth` (in `infra-cdk/`). Exits non-zero on any step failure.
- `smithy-model/smithy-build.json` declares the `openapi` projection with `aws-apigateway-openapi` plugin dependency. No Java SDK generation needed for this service — the Lambda is Python.
- Python 3.11 pinned via `.python-version` file and enforced in CI via `actions/setup-python`.
- TypeScript: `package-lock.json` committed; `npm ci` in CI.
- Pre-commit hooks: `pre-commit` framework with hooks for `ruff`, `mypy --strict`, and `smithy validate`.
- Smithy CLI at `~/bin/smithy` (same install as AMS — see AMS IMPLEMENTATION.md "Local development setup").

**Acceptance criteria:**
- `./scripts/build.sh` from a clean checkout produces a CDK synth output with no errors.
- `README.md` documents prereqs: Node 20, Python 3.11, AWS CLI v2, Smithy CLI 1.50+.
- Pre-commit hook blocks a commit that fails `ruff` or `smithy validate`.
- `.gitignore` excludes `__pycache__/`, `*.pyc`, `cdk.out/`, `node_modules/`, `.venv/`.

**Completed notes (2026-05-25):**
- `lambda` is a Python keyword — `from lambda.models import ...` is a SyntaxError. All test imports use bare module names (`from models import ...`). `tests/conftest.py` does `sys.path.insert(0, .../lambda)` and `pyproject.toml` sets `pythonpath = ["lambda"]`. This pattern applies to ALL test files in the project.
- CDK local bundler added to `recommender-stack.ts` so `cdk synth` works without Docker running. `BundlingOptions.local.tryBundle` runs `pip install` natively; returns `false` to fall back to Docker if pip unavailable.
- CDK snapshot tests use an optional `lambdaCode?: lambda.Code` prop + `infra-cdk/test/fixtures/stub-handler.zip` to avoid Docker bundling in CI.
- Smithy symlink at `~/bin/smithy` → `/tmp/smithy-cli-darwin-aarch64/bin/smithy`. The `/tmp` target is cleared by macOS between restarts. Reinstall: `curl -Lo /tmp/smithy.zip https://github.com/awslabs/smithy/releases/download/1.50.0/smithy-cli-darwin-aarch64.zip && unzip -o /tmp/smithy.zip -d /tmp/ && ln -sf /tmp/smithy-cli-darwin-aarch64/bin/smithy ~/bin/smithy`
- `build.sh` uses `smithy validate --config smithy-model/smithy-build.json smithy-model/model/` (not `smithy build` or bare `smithy validate smithy-model/`).

---

## Story 1.2 — Smithy model: RecommendVersion operation [M]

**Description:** Define all Smithy shapes for the service — the `RecommendVersion` operation, `HealthCheck`, shared error shapes, and the `EnvSnapshot` input structure.

**File:** `smithy-model/model/version-recommender.smithy`

**Implementation notes:**
- Use the exact shapes from the API contract section above verbatim.
- All error shapes use `with [ServiceError]` mixin pattern (same as AMS `common.smithy`).
- `@readonly` on `HealthCheck`.
- `@sensitive` on `EnvSnapshot` type — the scientist's package list and framework versions are considered PII-adjacent; generated SDKs will redact it from logs.
- `EnvSnapshot.cudaVersion` is not `@required` (null = CPU-only is a valid and common case).
- `ModelName` and `VersionId` reuse the same constraint patterns as the AMS model.

**Acceptance criteria:**
- `smithy validate` passes with zero warnings.
- `RecommendVersion` has `@http` trait with correct URI template `"/recommend/{modelName}"`.
- `HealthCheck` has `@readonly`.
- `EnvSnapshot` marked `@sensitive`.
- Negative test: `EnvSnapshot` missing `pythonVersion` fails Smithy lint at the operation `@required` level.

**Completed notes (2026-05-25):**
- `ThrottlingException` must be `@error("client")`, not `@error("server")`. The spec prose says "server" but Smithy rejects any `@error("server")` shape with a 4xx `@httpError` code. 429 is always a client error.
- `ServiceError` mixin carries `@required requestId: String`. All five error structures use `with [ServiceError]`. Per-error extras: `NoCompatibleVersionException` adds `@required reason: String`; `ValidationException` adds optional `details: Document`; `ThrottlingException` adds optional `retryAfterSeconds: Integer`.
- `smithy validate --config smithy-model/smithy-build.json smithy-model/model/` → 447 shapes, SUCCESS. API Gateway integration warnings ("No API Gateway integration trait found") are expected — wired in Story 6.2.
- `ModelName` constraint: `@pattern("^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$") @length(min: 3, max: 64)`. `VersionId` constraint: `@pattern("^\\d+\\.\\d+$")`.

---

## Story 1.3 — GitHub Actions: PR validation [S]

**Description:** Single workflow that runs on every PR; failure blocks merge.

**File:** `.github/workflows/ci.yml`

**Steps:**
1. Checkout, setup Python 3.11, setup Node 20.
2. Cache: `~/.cache/pip`, `~/.npm`.
3. `pip install -r lambda/requirements-dev.txt` (hash-pinned).
4. `ruff check lambda/ tests/`.
5. `mypy lambda/ --strict`.
6. `pytest tests/ -v --cov=lambda --cov-fail-under=90 --cov-report=xml`.
7. `smithy validate smithy-model/` (resolve via `~/bin/smithy` added to PATH).
8. `cd infra-cdk && npm ci && npm test` — CDK snapshot tests.
9. `./scripts/build.sh` — end-to-end build smoke.
10. Upload coverage XML as artifact.

**Implementation notes:**
- Trigger on `pull_request` to `main`.
- Concurrency group: `pr-${{ github.head_ref }}` with `cancel-in-progress: true`.
- Smithy CLI installed in CI via the same curl-unzip-symlink pattern as AMS.
- Coverage reported via `codecov/codecov-action` (optional) or artifact upload.
- `permissions: contents: read`.

**Acceptance criteria:**
- Workflow runs on every PR.
- Failure on any step fails the workflow (no `continue-on-error`).
- Re-run on unchanged PR completes under 2 minutes from cache.
- `ruff` and `mypy` run against `lambda/` and `tests/`.

**Completed notes (2026-05-25):**
- `smithy validate smithy-model/` does NOT work: picks up `build/` artifacts causing shape conflicts, and cannot resolve `aws.protocols#restJson1` without Maven deps. Correct invocation everywhere (CI, build.sh, pre-commit): `smithy validate --config smithy-model/smithy-build.json smithy-model/model/`
- Smithy Maven deps cached at `~/.m2/repository/software/amazon/smithy` keyed on `smithy-build.json` hash; pre-populated before the validate step so the 2-minute re-run AC holds.
- `mypy` runs against `lambda/ tests/ --strict` (both directories per AC).
- No `continue-on-error` on any step. Step order: checkout/setup → caches → pip install → ruff → mypy → pytest → smithy validate → CDK test → build.sh → upload coverage.

---

# Epic 2 — Scoring engine

**Goal:** A pure, well-tested Python module with no I/O. This is the core business logic of the service. It is implemented and fully tested before any Lambda wiring.

## Story 2.1 — Data models [S]

**Description:** Dataclasses for all internal types used by the scorer and handler.

**File:** `lambda/models.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class FrameworkInfo:
    name: str       # "pytorch" | "tensorflow" | "sklearn" | "huggingface"
    version: str    # "2.1.0"

@dataclass
class EnvSnapshot:
    python_version: str          # "3.11.7"
    framework: FrameworkInfo
    os: str                      # "linux-x86_64"
    cuda_version: str | None = None

@dataclass
class DepSnapshot:
    """Mirrors the AMS DepSnapshot shape; only the fields needed for scoring."""
    python_version: str
    framework: FrameworkInfo
    os: str
    cuda_version: str | None = None
    # packages, captured_at not needed for scoring — omit to keep model small

@dataclass
class Candidate:
    version: str             # "3.2"
    dep_snapshot: DepSnapshot
    created_at: str          # ISO-8601 string for tiebreaker

@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: float             # 0.0 – 100.0
    breakdown: dict[str, float]  # {"python": 35.0, "framework": 35.0, "cuda": 20.0, "os": 10.0}
    disqualified: bool = False
    disqualification_reason: str | None = None

@dataclass
class Recommendation:
    recommended_version: str
    score: float
    explanation: str
    candidates_evaluated: int
    cached: bool = False
```

**Implementation notes:**
- All fields typed. No `Any` in `models.py`.
- `EnvSnapshot` and `DepSnapshot` are structurally similar but semantically distinct — do not merge them. `EnvSnapshot` comes from the SDK caller; `DepSnapshot` comes from AMS.
- `ScoredCandidate.breakdown` is for observability/logging only; it is not returned in the HTTP response.

**Acceptance criteria:**
- All dataclasses importable from `lambda/models.py`.
- Round-trip: `dataclasses.asdict(obj)` and back preserves all fields.
- `Candidate.dep_snapshot.cuda_version` is `None`-safe.
- Coverage ≥ 90% on `models.py`.

**Completed notes (2026-05-25):**
- Test imports use bare names (`from models import ...`) — see Story 1.1 notes on the `lambda` keyword problem.
- `pyproject.toml` is the canonical config for the project: pytest `pythonpath`, coverage `source = ["lambda"]` + `fail_under = 90`, mypy `strict + mypy_path = "lambda"`, ruff `line-length = 100`. Do not add per-file tool config elsewhere.
- Coverage measurement: run `pytest --cov` with no `--cov=` path argument. `pyproject.toml [tool.coverage.run]` sets the source. `--cov=lambda/models` is a syntax error; `--cov=lambda.models` silently measures nothing.
- Total coverage is below 90% while stub files exist (0% each). Story-specific AC ("coverage ≥ 90% on models.py") is verified per-file. Total exceeds 90% once all substantive stories are implemented.
- Result: 19/19 tests, `models.py` 100%.

---

## Story 2.2 — ScoreEngine: dimension scorers [M]

**Description:** The pure scoring logic, broken into four dimension functions plus a combiner.

**File:** `lambda/scorer.py`

```python
import re
from datetime import datetime
from lambda.models import EnvSnapshot, DepSnapshot, Candidate, ScoredCandidate, Recommendation

class ScoreEngine:

    def score_all(
        self,
        env: EnvSnapshot,
        candidates: list[Candidate],
    ) -> list[ScoredCandidate]:
        """Score all candidates. Disqualified entries are included with disqualified=True."""
        ...

    def recommend(
        self,
        env: EnvSnapshot,
        candidates: list[Candidate],
    ) -> Recommendation:
        """Returns the top non-disqualified candidate. Raises NoCompatibleVersionError if none."""
        ...

    # ── Dimension scorers (package-private, tested directly) ──────────────

    @staticmethod
    def _score_python(env_version: str, dep_version: str) -> float:
        """Returns 35.0, 21.0, or 0.0."""
        ...

    @staticmethod
    def _score_framework(env_fw: FrameworkInfo, dep_fw: FrameworkInfo) -> float | None:
        """Returns float or None (None = disqualified — different framework name)."""
        ...

    @staticmethod
    def _score_cuda(env_cuda: str | None, dep_cuda: str | None) -> float:
        """Returns 20.0, 18.0, 10.0, or 8.0."""
        ...

    @staticmethod
    def _score_os(env_os: str, dep_os: str) -> float:
        """Returns 10.0 or 5.0."""
        ...

    @staticmethod
    def _extract_os_family(os_string: str) -> str:
        """'linux-x86_64' → 'linux', 'darwin-arm64' → 'darwin', 'windows-amd64' → 'windows'."""
        ...

    @staticmethod
    def _parse_major_minor(version_string: str) -> tuple[int, int]:
        """'3.11.7' → (3, 11). '2.1' → (2, 1). Raises ValueError on bad input."""
        ...

    @staticmethod
    def _explanation(env: EnvSnapshot, winner: ScoredCandidate) -> str:
        """Build human-readable explanation string."""
        ...
```

**Implementation notes:**
- `_parse_major_minor`: use `re.split(r'[.\-]', version_string)` to split on dots and hyphens; take first two numeric segments. Raise `ValueError` for non-numeric segments.
- `_score_python` and `_score_framework` both use `_parse_major_minor`; `ValueError` from that function should surface as `ValidationException` at the handler level, not silently return 0.
- `_score_cuda`: CUDA major version extracted as `int(cuda_version.split('.')[0])`. Handle both `"11.8"` and `"11"` (some environments only report major).
- `_explanation` must produce a deterministic string for a given input (used in tests for snapshot-style assertions). Example: `"v3.2 is the best match: exact Python 3.11, exact PyTorch 2.1, CPU environment, same OS (linux)"`.
- `score_all` includes disqualified entries in the return list (for observability logging) but marks them with `disqualified=True`. The `recommend` method filters these out.
- `recommend` raises `NoCompatibleVersionError` (from `lambda/exceptions.py`) if all candidates are disqualified or the list is empty.
- Tiebreaker: parse `created_at` as `datetime.fromisoformat(candidate.created_at.rstrip('Z') + '+00:00')`. Later timestamp wins.
- **No I/O in `scorer.py`.** No HTTP, no cache, no logging. Pure functions only.

**Acceptance criteria:**
- `_score_python`:
  - `"3.11.7"` vs `"3.11.2"` → 35.0 (same major.minor)
  - `"3.11.7"` vs `"3.10.4"` → 21.0 (same major, different minor)
  - `"3.11.7"` vs `"2.7.18"` → 0.0 (different major)
- `_score_framework`:
  - `pytorch 2.1.0` vs `pytorch 2.1.2` → 35.0
  - `pytorch 2.1.0` vs `pytorch 2.2.0` → 24.5
  - `pytorch 2.1.0` vs `pytorch 3.0.0` → 7.0
  - `pytorch 2.1.0` vs `tensorflow 2.13.0` → `None` (disqualified)
- `_score_cuda`:
  - both `None` → 20.0
  - `"11.8"` vs `"11.6"` → 18.0
  - `"11.8"` vs `"12.1"` → 10.0
  - `None` vs `"11.8"` → 8.0
- `_score_os`:
  - `"linux-x86_64"` vs `"linux-aarch64"` → 10.0 (same family, different arch)
  - `"linux-x86_64"` vs `"darwin-arm64"` → 5.0
- `recommend` on an empty list raises `NoCompatibleVersionError` with `reason="model_empty"`.
- `recommend` on a list where all are disqualified raises `NoCompatibleVersionError` with `reason="all_disqualified"`.
- `recommend` returns the highest-scoring non-disqualified candidate.
- Tiebreaker: two candidates with equal score → newer `created_at` wins.
- Coverage ≥ 90% on `scorer.py`.

**Completed notes (2026-05-30):**
- All imports in `scorer.py` and tests use bare module names (`from exceptions import ...`, `from models import ...`, `from scorer import ScoreEngine`).
- `exceptions.py` was fully implemented as part of this story (required by scorer). Story 3.2 formally specifies the exception hierarchy but nothing further needs to be added there.
- Exact scoring constants (deviating = automatic reviewer FAIL): Python 35/21/0; Framework 35/24.5/7/None; CUDA 20/18/10/8; OS 10/5.
- `score_all` includes disqualified entries with `disqualified=True` for observability logging. `recommend` filters them. Disqualification reason string: `"framework_mismatch"`.
- `recommend` raises `NoCompatibleVersionError(reason="model_empty")` for empty list; `reason="all_disqualified"` when all candidates are disqualified.
- Tiebreaker key: `max(..., key=lambda s: (round(s.score, 2), _created_at_dt(s.candidate)))`. Parsing: `datetime.fromisoformat(ts.rstrip("Z") + "+00:00")`.
- `_explanation` output format (deterministic): `"v{version} is the best match: exact Python 3.11, exact Pytorch 2.1, CPU environment, same OS (linux)"`. Framework name uses `.capitalize()`.
- Spec example verified: v3.2=100.0, v2.1=89.5, v1.8=86.0, v4.0=67.0.
- `scorer.py` has zero I/O imports — reviewer auto-fails on `requests`, `boto3`, `logging`, `cache`, or any `os.` calls.
- Result: 51/51 tests, `scorer.py` 100%, `exceptions.py` 100%, total coverage 93.87%.

---

## Story 2.3 — ScoreEngine: property and fuzz tests [M]

**Description:** Additional correctness guarantees beyond the unit tests: property-based tests with `hypothesis` and a determinism test.

**File:** `tests/test_scorer_properties.py`

**Implementation notes:**
- Use `hypothesis` with `@given(st.text(...))` strategies for version strings to verify no panics on malformed input.
- Determinism test: generate 100 random valid env/candidate pairs, score each pair twice, assert both results are byte-for-byte identical.
- Score bounds test: for any valid input, all dimension scores are within their stated max range; total score is in [0.0, 100.0].
- Disqualification is all-or-nothing: a candidate marked `disqualified=True` has `score == 0.0`.
- Tiebreaker is transitive: if A and B tie and B and C tie, then A and C tie and the ordering between them respects `created_at`.
- `hypothesis` added to `lambda/requirements-dev.txt` only (not production requirements).

**Acceptance criteria:**
- No panics on any version string `hypothesis` generates (malformed strings → `ValidationException`, not uncaught `ValueError` or `AttributeError`).
- Score bounds property passes 10,000 examples.
- Determinism property passes 1,000 examples.
- Coverage contribution to `scorer.py` ≥ 90% cumulative with Story 2.2.

---

# Epic 3 — Artifact Management Service client

**Goal:** A thin HTTP client that fetches the version list from AMS. Uses SigV4 signing.

## Story 3.1 — AMSClient: version list fetch [M]

**Description:** HTTP client wrapper for the one AMS call the recommender makes: `GET /models/{modelName}/versions`.

**File:** `lambda/ams_client.py`

```python
import requests
from requests_aws4auth import AWS4Auth
import boto3
from lambda.models import Candidate, DepSnapshot, FrameworkInfo

class AMSClient:
    def __init__(self, endpoint_url: str, region: str = "us-east-1",
                 connect_timeout: float = 3.0, read_timeout: float = 5.0):
        creds = boto3.Session().get_credentials().get_frozen_credentials()
        self._auth = AWS4Auth(creds.access_key, creds.secret_key,
                              region, "execute-api",
                              session_token=creds.token)
        self._endpoint = endpoint_url.rstrip("/")
        self._timeout = (connect_timeout, read_timeout)

    def list_ready_versions(self, model_name: str) -> list[Candidate]:
        """
        Two-phase fetch — required because AMS ListVersions returns sparse responses.

        Phase 1: Page through GET /models/{modelName}/versions to collect (version, createdAt)
                 tuples for all READY versions. Cap at 1000 to prevent runaway paging.
        Phase 2: For each READY version, call GET /models/{modelName}/versions/{version}
                 to get the full response including depSnapshot (absent from list responses).

        Returns a list of Candidate dataclasses ready for scoring.
        """
        ...

    def _list_page(self, model_name: str, page_token: str | None) -> tuple[list[dict], str | None]:
        """Fetch one page of sparse version entries. Returns (raw_items, next_page_token)."""
        ...

    def _get_version(self, model_name: str, version: str) -> Candidate:
        """
        Fetch a single full version object (includes depSnapshot).
        Called once per READY version from list_ready_versions.
        """
        ...

    @staticmethod
    def _parse_candidate(raw: dict) -> Candidate:
        """
        Parse a full GetVersion response → Candidate.
        raw must include depSnapshot (only present in GetVersion/GetLatestVersion responses,
        NOT in ListVersions responses — see AMS API gotchas in the 'Current state' section).
        """
        ...
```

**Implementation notes:**
- **Two-phase fetch is mandatory** — the AMS `ListVersions` response does not include `depSnapshot` (sparse response). `_parse_candidate` must only be called on full `GetVersion` responses. Attempting to read `depSnapshot` from a list entry raises `KeyError` at runtime.
- Phase 1 (`_list_page`): `GET /models/{modelName}/versions` → response key is `"versions"` (not `"items"`). Each entry has `version`, `status`, `createdAt`. Filter to `status == "READY"` immediately.
- Phase 2 (`_get_version`): for each READY version string, `GET /models/{modelName}/versions/{version}`. These calls are sequential to keep the implementation simple; concurrency is not needed because results are cached for 60 seconds.
- Version cap: after collecting all READY versions from paging (Phase 1), if count > 200, take the 200 most recent by `createdAt` before entering Phase 2. Emit a `WARNING` log if the cap truncates results. The cap prevents an explosion of `GetVersion` calls on models with thousands of versions.
- `_parse_candidate` extracts only the four fields needed for scoring: `pythonVersion`, `framework.name`, `framework.version`, `cudaVersion`, `os` from the `depSnapshot` field. `packages` and `capturedAt` are not needed for scoring.
- 404 from AMS (model not found in Phase 1) → raise `ModelNotFoundException`.
- 5xx from AMS (either phase) → raise `AMSUnavailableError`. The handler returns 500 to the SDK, triggering fallback to `latest`.
- Timeout: `(connect_timeout, read_timeout)` applied to every request. Defaults: 3s connect, 5s read.
- **Unit tests mock HTTP** via the `responses` library. No real AMS calls in unit tests.

**Acceptance criteria:**
- `list_ready_versions` two-phase behavior: mock list returning 5 versions (3 READY, 2 PENDING) → exactly 3 `GetVersion` calls made → 3 candidates returned.
- `list_ready_versions` pages correctly in Phase 1: mock three list pages of 200/200/5 READY versions → 405 `GetVersion` calls in Phase 2 → 405 candidates returned.
- PENDING/DELETED/FAILED versions filtered after Phase 1 — zero `GetVersion` calls made for non-READY versions.
- Version cap: mock list returns 250 READY versions → only 200 `GetVersion` calls made → warning logged.
- AMS 404 on list → `ModelNotFoundException` with model name in message.
- AMS 500 on any request → `AMSUnavailableError`.
- Timeout parameters passed through to every request (verified via `responses` library).
- Coverage ≥ 90% on `ams_client.py`.

---

## Story 3.2 — Exception hierarchy [S]

**Description:** All exceptions raised by the recommender service.

**File:** `lambda/exceptions.py`

```python
class RecommenderError(Exception): ...              # base

class ModelNotFoundException(RecommenderError): ... # AMS returned 404
class NoCompatibleVersionError(RecommenderError):   # all candidates disqualified
    reason: str  # "all_disqualified" | "no_ready_versions" | "model_empty"
class AMSUnavailableError(RecommenderError): ...    # AMS returned 5xx or timed out
class ValidationError(RecommenderError): ...        # bad EnvSnapshot from SDK
class CacheError(RecommenderError): ...             # non-fatal, logged and ignored
```

**Implementation notes:**
- `NoCompatibleVersionError` carries a `reason` string that maps directly to the `reason` field in the `NoCompatibleVersionException` Smithy shape.
- `CacheError` is caught silently by the handler — cache failures never propagate to the caller.
- All exceptions are instances of `RecommenderError` — callers can catch the base class.

**Acceptance criteria:**
- All exception classes importable from `lambda.exceptions`.
- `NoCompatibleVersionError` carries `reason` attribute.
- `CacheError` is a subclass of `RecommenderError`.
- Unit test: each exception is an instance of `RecommenderError`.
- Coverage ≥ 90% on `exceptions.py`.

---

# Epic 4 — Caching layer

**Goal:** A 60-second TTL cache keyed on `(model_name, env_snapshot_hash)`. Alpha/gamma use in-memory LRU; production can optionally use Redis. Cache failures must never surface to the caller.

## Story 4.1 — In-memory LRU cache [M]

**Description:** Thread-safe in-memory cache using `functools.lru_cache` with TTL enforcement.

**File:** `lambda/cache.py`

```python
import hashlib
import json
import time
from dataclasses import asdict
from lambda.models import EnvSnapshot, Recommendation

class RecommendationCache:
    """
    In-memory LRU cache for Recommendation results.
    TTL is enforced per-entry; stale entries are evicted on access.
    Thread-safe for concurrent Lambda invocations within the same instance.
    """

    def __init__(self, max_size: int = 256, ttl_seconds: int = 60):
        ...

    def get(self, model_name: str, env: EnvSnapshot) -> Recommendation | None:
        """Return cached Recommendation or None if missing/expired."""
        ...

    def put(self, model_name: str, env: EnvSnapshot, recommendation: Recommendation) -> None:
        """Cache a Recommendation."""
        ...

    def invalidate(self, model_name: str) -> None:
        """Evict all entries for a model_name. Called when AMS signals a version update (future)."""
        ...

    def clear(self) -> None:
        """Clear all entries. Used in tests."""
        ...

    @staticmethod
    def _cache_key(model_name: str, env: EnvSnapshot) -> str:
        """
        Deterministic key from model_name + env snapshot.
        Hashed to keep key size bounded.
        """
        payload = json.dumps({"model_name": model_name, **asdict(env)},
                              sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()
```

**Implementation notes:**
- Use a plain `dict` with `(key → (value, expires_at))` tuples. Do not use `functools.lru_cache` — it does not support TTL.
- Eviction: on `get`, check `time.monotonic() > expires_at` and evict if true. Do not background-evict (Lambda invocations are short-lived; background threads complicate Lambda lifecycle).
- Max-size enforcement: use an `OrderedDict` (insertion-ordered in Python 3.7+) and evict the oldest entry when `len >= max_size`.
- Thread safety: wrap all dict mutations in `threading.Lock`. Lambda may serve concurrent requests within the same instance.
- Cache key determinism: the `json.dumps` with `sort_keys=True` ensures that two `EnvSnapshot` objects with the same field values produce the same key regardless of dict insertion order in `asdict()`.
- The `cached: bool` field on `Recommendation` is set by the handler — not by the cache itself. Cache returns a `Recommendation` object; handler sets `cached=True` on the returned copy.

**Acceptance criteria:**
- `put` then `get` (before TTL expiry) returns the recommendation.
- `get` after TTL expiry returns `None` (expired entry evicted).
- Max-size eviction: fill to `max_size`, add one more → oldest entry gone, all others present.
- Thread-safety: 10 concurrent threads calling `get`/`put` simultaneously produces no `KeyError` or corruption.
- `invalidate("fraud-detector")` removes all entries keyed on that model name.
- Cache key is deterministic: same `(model_name, env)` produces same key across calls.
- `CacheError` is never raised by the in-memory implementation — all errors propagate as normal Python exceptions (caught by handler, logged, and ignored).
- Coverage ≥ 90% on `cache.py`.

---

# Epic 5 — Lambda handler

**Goal:** Wire scoring + AMS client + cache into the Lambda entry point. Handle errors, emit metrics, write structured logs.

## Story 5.1 — Handler scaffold + request parsing [M]

**Description:** Lambda entry point, request parsing, routing.

**File:** `lambda/handler.py`

```python
import json
import logging
import os
import time
import uuid
from lambda.models import EnvSnapshot, FrameworkInfo
from lambda.scorer import ScoreEngine
from lambda.ams_client import AMSClient
from lambda.cache import RecommendationCache
from lambda.exceptions import (
    ModelNotFoundException, NoCompatibleVersionError,
    AMSUnavailableError, ValidationError, CacheError,
)

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Module-level singletons — created once per Lambda instance (warm reuse)
_ams_client: AMSClient | None = None
_cache: RecommendationCache | None = None
_score_engine: ScoreEngine = ScoreEngine()

def _get_ams_client() -> AMSClient:
    global _ams_client
    if _ams_client is None:
        _ams_client = AMSClient(
            endpoint_url=os.environ["AMS_ENDPOINT_URL"],
            region=os.environ.get("AWS_REGION", "us-east-1"),
        )
    return _ams_client

def _get_cache() -> RecommendationCache:
    global _cache
    if _cache is None:
        _cache = RecommendationCache(
            ttl_seconds=int(os.environ.get("CACHE_TTL_SECONDS", "60")),
            max_size=int(os.environ.get("CACHE_MAX_SIZE", "256")),
        )
    return _cache

def lambda_handler(event: dict, context: object) -> dict:
    request_id = context.aws_request_id if hasattr(context, "aws_request_id") else str(uuid.uuid4())
    start_ms = time.monotonic() * 1000

    try:
        return _route(event, request_id)
    except Exception as exc:
        logger.exception("Unhandled exception", extra={"request_id": request_id})
        return _error_response(500, "InternalError", "An internal error occurred", request_id)
    finally:
        elapsed = time.monotonic() * 1000 - start_ms
        logger.info("Request complete", extra={"elapsed_ms": elapsed, "request_id": request_id})

def _route(event: dict, request_id: str) -> dict:
    method = event.get("httpMethod", "")
    resource = event.get("resource", "")

    if method == "GET" and resource == "/health":
        return _handle_health()
    if method == "POST" and resource == "/recommend/{modelName}":
        return _handle_recommend(event, request_id)

    return _error_response(400, "ValidationError", f"Unknown route: {method} {resource}", request_id)
```

**Implementation notes:**
- Module-level singletons for `AMSClient`, `RecommendationCache`, and `ScoreEngine` — created on first invocation and reused across warm Lambda invocations.
- `AMS_ENDPOINT_URL` environment variable is mandatory; Lambda will error on cold start if absent.
- `LOG_LEVEL` environment variable controls log verbosity (`DEBUG` in alpha/beta, `INFO` in gamma/prod).
- All structured log lines include `request_id` for CloudWatch Insights correlation.
- `_route` dispatches on `event["httpMethod"]` and `event["resource"]` (API Gateway proxy event format).

**Acceptance criteria:**
- `lambda_handler` returns a valid API Gateway proxy response dict for all code paths.
- Module-level singletons are created exactly once per Lambda instance (tested by calling handler twice in the same process and asserting no re-initialization).
- Unknown route returns 400.
- `AMS_ENDPOINT_URL` missing → cold-start error (checked via unit test with env var unset).
- Coverage ≥ 90% on `handler.py`.

---

## Story 5.2 — RecommendVersion request handler [M]

**Description:** The core handler method for `POST /recommend/{model_name}`.

**File:** `lambda/handler.py` (continued — `_handle_recommend` function)

```python
def _handle_recommend(event: dict, request_id: str) -> dict:
    # 1. Extract model_name from path parameters
    model_name = event.get("pathParameters", {}).get("modelName", "")

    # 2. Parse and validate the request body
    env = _parse_env_snapshot(event.get("body") or "{}", request_id)

    # 3. Cache lookup
    cache = _get_cache()
    try:
        cached = cache.get(model_name, env)
    except CacheError:
        cached = None
        logger.warning("Cache read failed, continuing without cache", ...)

    if cached is not None:
        import copy
        result = copy.replace(cached, cached=True)
        _emit_metric("RecommendationServedFromCache")
        return _success_response(result)

    # 4. Fetch candidates from AMS
    ams = _get_ams_client()
    try:
        candidates = ams.list_ready_versions(model_name)
    except ModelNotFoundException:
        return _error_response(404, "ModelNotFound", f"Model '{model_name}' not found", request_id)
    except AMSUnavailableError as exc:
        logger.error("AMS unavailable", extra={"error": str(exc), "request_id": request_id})
        return _error_response(500, "InternalError", "Upstream service unavailable", request_id)

    # 5. Score and recommend
    try:
        recommendation = _score_engine.recommend(env, candidates)
    except NoCompatibleVersionError as exc:
        return _error_response(422, "NoCompatibleVersion",
                               f"No compatible version found for '{model_name}'",
                               request_id, extra={"reason": exc.reason})

    # 6. Cache the result
    try:
        cache.put(model_name, env, recommendation)
    except CacheError:
        logger.warning("Cache write failed, continuing", ...)

    # 7. Emit metrics and return
    _emit_metric("RecommendationServed", dimensions={"model_name": model_name})
    _emit_metric("CandidatesEvaluated", value=recommendation.candidates_evaluated)
    return _success_response(recommendation)
```

**Implementation notes:**
- `_parse_env_snapshot`: parse JSON body, map camelCase keys (`pythonVersion`, `framework`, `cudaVersion`, `os`) to `EnvSnapshot` dataclass. Missing `@required` fields → raise `ValidationError` with field name.
- `_success_response`: serialize `Recommendation` dataclass to camelCase JSON with `statusCode: 200`.
- `_error_response`: return `{"statusCode": N, "body": json.dumps({...})}` with `Content-Type: application/json`.
- `_emit_metric`: CloudWatch EMF — use `aws_embedded_metrics` library to emit structured metrics. Metric namespace `VersionRecommender`.
- **Never log `env_snapshot` contents** — treat the package list and version details as sensitive. Log only `model_name`, `request_id`, and the recommended version string.

**Acceptance criteria:**
- Happy path: returns 200 with correct `recommendedVersion`, `score`, `explanation`, `candidatesEvaluated`, `cached=false`.
- Cache hit: second call with same input returns 200 with `cached=true`, no AMS HTTP call.
- AMS 404 → 404 response (model not found).
- AMS 5xx → 500 response.
- All candidates disqualified → 422 with `reason`.
- Cache write failure (injected `CacheError`) → 200 response still returned (cache failure is non-fatal).
- `env_snapshot` contents not logged (verified by asserting no log lines contain `pythonVersion` key).
- Coverage ≥ 90% on `_handle_recommend` and helpers in `handler.py`.

---

## Story 5.3 — HealthCheck handler [S]

**Description:** `GET /health` for ALB/Route53 health checks and basic liveness probes.

**File:** `lambda/handler.py` (`_handle_health` function)

```python
def _handle_health() -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "status": "ok",
            "stage": os.environ.get("STAGE", "unknown"),
            "version": os.environ.get("LAMBDA_PACKAGE_VERSION", "dev"),
        }),
    }
```

**Implementation notes:**
- No auth check needed — health check is publicly accessible (API Gateway resource policy allows unauthenticated GET /health).
- `LAMBDA_PACKAGE_VERSION` is set as an environment variable by the CDK stack from the deployment package version string.
- Does not call AMS or touch the cache.

**Acceptance criteria:**
- Returns 200 with `status: "ok"`, `stage`, and `version` fields.
- `stage` reflects `STAGE` env var.
- Unit test: call with `STAGE=gamma` env var → response body contains `"stage": "gamma"`.
- Coverage ≥ 90% on `_handle_health`.

---

## Story 5.4 — Observability: structured logging and CloudWatch EMF metrics [M]

**Description:** All log lines are JSON-structured; metrics emitted via Embedded Metric Format (zero cost PutMetricData).

**File:** `lambda/handler.py` + `lambda/metrics.py`

**Metrics list:**

| Metric name | Unit | Dimensions | Emitted when |
|---|---|---|---|
| `RecommendationServed` | Count | `stage`, `model_name` | Successful recommendation |
| `RecommendationServedFromCache` | Count | `stage` | Cache hit |
| `RecommendationFailed` | Count | `stage`, `reason` | `NoCompatibleVersionError` |
| `AMSCallLatencyMs` | Milliseconds | `stage` | After each AMS page fetch |
| `CandidatesEvaluated` | Count | `stage` | After scoring |
| `ScoreEngineLatencyMs` | Milliseconds | `stage` | After `score_all()` returns |
| `CacheHitRate` | Percent | `stage` | Sampled 1-in-10 invocations |

**Implementation notes:**
- Use `aws-embedded-metrics` Python library (`aws_embedded_metrics` package). Add to `lambda/requirements.txt`.
- Wrap the handler with `@metric_scope` decorator from `aws_embedded_metrics` — this handles flushing the EMF blob to stdout at end of invocation.
- Every log record includes `request_id`, `model_name` (when available), `stage`.
- JSON formatter configured on the root logger: `logging.config.dictConfig(...)` called once at module level.
- `LOG_LEVEL=DEBUG` in alpha/beta emits one extra log line per dimension score (useful for debugging unexpected recommendations).
- **Never log `env_snapshot.framework.version` or `env_snapshot.python_version`** — they are sensitive. Log only `framework.name` and `model_name`.

**Acceptance criteria:**
- All seven metrics emitted on the happy path.
- `AMSCallLatencyMs` emitted once per page (tested by verifying count matches page count in the mock).
- Log lines are valid JSON (parsed with `json.loads` in the test).
- `env_snapshot` version fields absent from all log lines.
- `aws-embedded-metrics` in `lambda/requirements.txt`.
- Coverage ≥ 90% on `metrics.py`.

---

# Epic 6 — CDK infrastructure

**Goal:** All AWS resources provisioned via CDK TypeScript. Separate stacks from AMS. Same four-stage pipeline.

## Story 6.1 — Stage config and stack scaffold [M]

**Description:** Per-stage configuration object and one stack instance per stage.

**File:** `infra-cdk/lib/stage-config.ts`

```typescript
export interface StageConfig {
  stage: 'alpha' | 'beta' | 'gamma' | 'prod';
  account: string;
  region: string;
  removalPolicy: cdk.RemovalPolicy;
  lambdaMemoryMB: number;
  lambdaTimeoutSeconds: number;
  cacheMaxSize: number;
  cacheTtlSeconds: number;
  amsEndpointUrl: string;       // same-stage AMS API Gateway URL
  alarmEmail: string;
  enableXRay: boolean;
  logLevel: 'DEBUG' | 'INFO' | 'WARNING';
}

export const STAGES: Record<string, StageConfig> = {
  alpha: {
    stage: 'alpha',
    account: '...',
    region: 'us-east-1',
    removalPolicy: cdk.RemovalPolicy.DESTROY,
    lambdaMemoryMB: 256,
    lambdaTimeoutSeconds: 10,
    cacheMaxSize: 64,
    cacheTtlSeconds: 60,
    amsEndpointUrl: 'https://pi5ywcu3ub.execute-api.us-east-1.amazonaws.com/alpha',
    alarmEmail: 'avadhani.a@northeastern.edu',
    enableXRay: true,
    logLevel: 'DEBUG',
  },
  // beta, gamma, prod follow same pattern with appropriate AMS URLs
};
```

**Implementation notes:**
- `amsEndpointUrl` must reference the same-stage AMS API Gateway endpoint. This is set as a Lambda environment variable `AMS_ENDPOINT_URL`.
- `removalPolicy: DESTROY` for alpha/beta; `RETAIN` is not applicable here (no stateful resources — only Lambda + API GW).
- No DynamoDB tables, no S3 buckets — this service has no state of its own.
- Resource naming: `version-recommender-{resource}-{stage}`.
- Tagging: every resource carries `Stage`, `Service=VersionRecommender`, `Owner=arjun`.

**Acceptance criteria:**
- `npx cdk synth -c stage=alpha` produces a CloudFormation template with alpha-suffixed resources.
- Synthesizing all four stages produces four distinct templates.
- CDK snapshot tests in `infra-cdk/test/` pin the synthesized template per stage.
- AMS endpoint URL is passed to Lambda as an env var (not hardcoded in Lambda code).

---

## Story 6.2 — Lambda function and API Gateway [M]

**Description:** Provision the Lambda function (Python 3.11, ARM64) and wire it to API Gateway from the generated OpenAPI spec.

**File:** `infra-cdk/lib/recommender-stack.ts`

```typescript
const handler = new lambda.Function(this, 'RecommenderHandler', {
  functionName: `version-recommender-handler-${cfg.stage}`,
  runtime: lambda.Runtime.PYTHON_3_11,
  architecture: lambda.Architecture.ARM_64,
  handler: 'handler.lambda_handler',
  code: lambda.Code.fromAsset('../lambda', {
    bundling: {
      image: lambda.Runtime.PYTHON_3_11.bundlingImage,
      command: [
        'bash', '-c',
        'pip install -r requirements.txt -t /asset-output && cp -r . /asset-output',
      ],
    },
  }),
  memorySize: cfg.lambdaMemoryMB,
  timeout: cdk.Duration.seconds(cfg.lambdaTimeoutSeconds),
  tracing: cfg.enableXRay ? lambda.Tracing.ACTIVE : lambda.Tracing.DISABLED,
  environment: {
    AMS_ENDPOINT_URL: cfg.amsEndpointUrl,
    STAGE: cfg.stage,
    CACHE_TTL_SECONDS: String(cfg.cacheTtlSeconds),
    CACHE_MAX_SIZE: String(cfg.cacheMaxSize),
    LOG_LEVEL: cfg.logLevel,
    LAMBDA_PACKAGE_VERSION: process.env.BUILD_VERSION ?? 'dev',
    POWERTOOLS_SERVICE_NAME: 'version-recommender',
  },
});
```

**API Gateway notes:**
- Load OpenAPI from Smithy build output: `smithy-model/build/smithyprojections/openapi/version-recommender.openapi.json`.
- Inject `x-amazon-apigateway-integration` into every operation pointing at the Lambda ARN.
- Auth: `AWS_IAM` on `POST /recommend/{modelName}`; `NONE` on `GET /health` (public liveness probe).
- Throttling: 200 req/s burst, 100 req/s sustained (relaxed vs AMS — recommendations are read-only and cheap).
- Request validation enabled via API Gateway (body schema from OpenAPI).
- `dataTraceEnabled: false` on all stages — body contains `env_snapshot`.

**Lambda IAM policy:**
- `execute-api:Invoke` on the same-stage AMS API Gateway ARN.
- `cloudwatch:PutMetricData` is not needed (EMF writes to stdout; CloudWatch Logs agent handles it).
- `xray:PutTraceSegments`, `xray:PutTelemetryRecords` if X-Ray enabled.
- No DDB, no S3, no SQS — this is the minimal viable policy.

**Acceptance criteria:**
- Lambda created with Python 3.11 runtime, ARM64 architecture.
- No SnapStart (Python Lambda does not support it; the CDK construct would error if specified).
- API Gateway enforces AWS_IAM on the recommend endpoint.
- `GET /health` is accessible without AWS_IAM auth (verified via `aws apigateway test-invoke-method`).
- Lambda IAM role has no DDB/S3/SQS permissions.
- CDK snapshot test covers the Lambda + APIGW configuration.
- `dataTraceEnabled: false` in all stages.

---

## Story 6.3 — CodePipeline: alpha → beta → gamma → prod [L]

**Description:** Self-mutating CodePipeline with the four-stage chain, integration tests, and bake checks. Mirrors the AMS pipeline structure.

**File:** `pipeline/lib/recommender-pipeline-stack.ts`

**Stages:**
1. **Source** — GitHub webhook on `main`.
2. **Build** — CodeBuild: `./scripts/build.sh` + `pytest tests/` + CDK synth for all four stages.
3. **Self-mutate** — pipeline updates itself if `pipeline/` has changed.
4. **Deploy alpha** — auto-deploy. No bake.
5. **Deploy beta** — auto-deploy + integration tests (`tests/integration/test_recommend_e2e.py`) against beta. 30-min bake.
6. **Deploy gamma** — 2-hour bake.
7. **Deploy prod** — manual approval gate. 1-hour bake post-deploy.

**Implementation notes:**
- Use `pipelines.CodePipeline` (L3 construct) identical to AMS pipeline.
- Bake check polls the `RecommendationFailed` alarm for the stage.
- Integration test step uses `./scripts/integ-test.sh {stage}` — signs SigV4 requests as a test IAM role provisioned in the stack.
- Build environment: Python 3.11 + Node 20. No Java needed (no Smithy Maven plugins for validate-only).
- `BUILD_VERSION` environment variable set in CodeBuild from `git describe --tags` — flows into `LAMBDA_PACKAGE_VERSION` env var on Lambda.

**Acceptance criteria:**
- Push to `main` triggers pipeline within 1 minute.
- Alpha deploy succeeds without manual intervention.
- Beta deploy gated on integration tests.
- Prod requires explicit manual approval.
- Pipeline self-mutates.

---

## Story 6.4 — CloudWatch alarms and dashboard [M]

**Description:** Alarms and dashboard for the recommender service.

**File:** `infra-cdk/lib/observability-stack.ts`

**Alarms:**

| Alarm name | Metric | Threshold | Period |
|---|---|---|---|
| `HighRecommendationFailRate` | `RecommendationFailed` / `RecommendationServed` | > 5% | 5 min |
| `HighAMSLatency` | `AMSCallLatencyMs` p99 | > 2000 ms | 5 min |
| `HighHandlerErrorRate` | Lambda `Errors` / `Invocations` | > 1% | 5 min |
| `HighHandlerLatency` | Lambda `Duration` p99 | > 800 ms | 5 min |

**Dashboard widgets:**
- Recommendation served vs served-from-cache (stacked area, 1-min period).
- `RecommendationFailed` rate by reason.
- `CandidatesEvaluated` p50/p95 (distribution of how many versions are scored per call).
- `AMSCallLatencyMs` p50/p99.
- Lambda duration p50/p99 and error rate.
- Cache hit rate (sampled).

**Implementation notes:**
- All alarms wired to the same SNS topic as AMS — same on-call email.
- All alarms have `OK` actions to clear pages.
- `TreatMissingData.NOT_BREACHING` on all alarms (missing data is not a failure).

**Acceptance criteria:**
- All four alarms created.
- Dashboard renders all listed widgets.
- Alarms wired to SNS with OK actions.
- CDK snapshot covers observability stack.

---

# Epic 7 — SDK integration

**Goal:** The Python SDK (`artifact-mgmt-client`) gains a `use_recommender` flag and a circuit breaker on the recommender call. SDK changes are driven by this service and documented here.

## Story 7.1 — RecommenderClient: HTTP client for the recommender [M]

**Description:** A thin HTTP client inside the SDK that calls the recommender service. Wraps SigV4 signing, timeout, and circuit breaker.

**File:** `artifact_mgmt/_recommender_client.py` (new file in the SDK repo)

```python
import hashlib, json, time, logging
from typing import TYPE_CHECKING
import requests
from requests_aws4auth import AWS4Auth
import boto3
from artifact_mgmt._types import DepSnapshot
from artifact_mgmt._exceptions import RecommenderUnavailableError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

class RecommenderClient:
    """
    HTTP client for the Version Recommender Service.
    Implements a simple consecutive-failure circuit breaker:
    after OPEN_THRESHOLD consecutive failures, all calls return None for
    OPEN_DURATION_SECONDS without hitting the network.
    """

    OPEN_THRESHOLD = 3            # consecutive failures to trip breaker
    OPEN_DURATION_SECONDS = 60    # how long to stay open before retry

    def __init__(self, endpoint_url: str, region: str = "us-east-1"):
        creds = boto3.Session().get_credentials().get_frozen_credentials()
        self._auth = AWS4Auth(creds.access_key, creds.secret_key,
                              region, "execute-api",
                              session_token=creds.token)
        self._endpoint = endpoint_url.rstrip("/")
        self._failures = 0
        self._open_until: float = 0.0   # monotonic time

    def recommend(self, model_name: str, env_snapshot: DepSnapshot) -> str | None:
        """
        Returns the recommended version string, or None if the recommender
        is unavailable (circuit open, timeout, 5xx).
        Never raises — callers should treat None as "fall back to latest".
        """
        ...

    def _build_request_body(self, env_snapshot: DepSnapshot) -> dict:
        """Convert DepSnapshot to the EnvSnapshot wire format."""
        ...
```

**Implementation notes:**
- Circuit breaker state is per-instance (per-`ArtifactMgmtClient` instance) — not global.
- Timeout: 500ms total (connect + read). This is deliberately tight — recommendation latency must not delay model loading.
- On any non-200 response, increment `_failures`. On 200, reset `_failures = 0`.
- When circuit is open (`time.monotonic() < self._open_until`), return `None` immediately without making any HTTP call.
- After `OPEN_DURATION_SECONDS`, reset to closed state and make a probe request.
- `_build_request_body` maps `DepSnapshot` fields to the `EnvSnapshot` wire format (camelCase JSON). Note: `DepSnapshot` has all required fields since it is auto-captured from the current environment.
- `RecommenderUnavailableError` is added to `artifact_mgmt/_exceptions.py` (not raised externally — swallowed internally and `None` returned).

**Acceptance criteria:**
- `recommend()` returns the `recommendedVersion` string from the response on 200.
- `recommend()` returns `None` on 500, 503, timeout — never raises.
- Circuit breaker: after 3 consecutive failures, next call returns `None` without HTTP.
- Circuit resets after `OPEN_DURATION_SECONDS`: probe request fires, success resets `_failures`.
- 500ms timeout: mock a slow server (>500ms) → `recommend()` returns `None` within 600ms.
- Unit tests use `responses` library (no real network).
- Coverage ≥ 90% on `_recommender_client.py`.

---

## Story 7.2 — ArtifactMgmtClient: use_recommender flag [M]

**Description:** Add `use_recommender` parameter to `ArtifactMgmtClient.__init__` and wire it into `load_model`.

**File:** `artifact_mgmt/client.py`

**Updated constructor signature:**
```python
def __init__(
    self,
    stage: str | None = None,
    *,
    endpoint_url: str | None = None,
    cache_dir: str | Path | None = None,
    use_recommender: bool = True,
    recommender_url: str | None = None,
    region: str = "us-east-1",
) -> None:
```

**Updated load_model signature:**
```python
def load_model(
    self,
    model_name: str,
    *,
    version: str | None = None,
    use_recommender: bool | None = None,   # None = use instance default
) -> ArtifactModel:
```

**Load model flow (updated):**
```
1. Determine effective use_recommender:
   call_level_flag if not None else self._use_recommender

2. If effective use_recommender AND version is None:
   a. snapshot = _snapshot.capture(framework_hint=None)  # env-only capture, no model needed
   b. recommended = self._recommender.recommend(model_name, snapshot)
   c. if recommended is not None:
       version = recommended
   d. else:
       logger.debug("Recommender unavailable, falling back to latest")
       # version remains None → get_latest_version used below

3. Continue with existing load_model logic (get_version or get_latest_version)
```

**Implementation notes:**
- **`_snapshot.capture()` must be enhanced** before this story can be implemented. The current `capture(model, ...)` signature requires a model to detect the serializer/framework. For the recommender path, there is no model yet — only the scientist's current environment matters. Add a `framework_hint: str | None = None` parameter: when `model` is `None` and `serializer` is `None`, scan installed packages via `importlib.metadata` and pick the first recognized ML framework (`torch` → `"pytorch"`, `transformers` → `"huggingface"`, `scikit-learn` → `"sklearn"`, `tensorflow` → `"tensorflow"`). If none found, use `"unknown"`. This enhancement is scoped to Story 7.2.
- `recommender_url` defaults to a per-stage constant (added to `_RECOMMENDER_ENDPOINTS` dict analogous to `_STAGE_ENDPOINTS`). Users can override to point at a custom deployment.
- `_RECOMMENDER_ENDPOINTS` is populated once all four stages are deployed. For alpha: use the alpha recommender API Gateway URL.
- `RecommenderClient` is instantiated lazily on first `load_model` call (to avoid AWS credential lookups at import time).
- When `use_recommender=False` is passed to `load_model`, it overrides the instance default for that call only.
- `version="latest"` (explicit string) bypasses the recommender even when `use_recommender=True` — if the scientist spells out `"latest"` they want the latest.
- If `stage` is not a named stage and `recommender_url` is also not provided, `use_recommender` is silently coerced to `False` with a `DEBUG` log (custom deployments may not have a recommender).

**Acceptance criteria:**
- `client = ArtifactMgmtClient(stage="gamma")` → `use_recommender=True` by default.
- `client.load_model("fraud-detector")` with a healthy recommender → uses recommended version (not latest).
- `client.load_model("fraud-detector", version="latest")` → bypasses recommender, uses `GetLatestVersion`.
- `client.load_model("fraud-detector", use_recommender=False)` → bypasses recommender for this call.
- Recommender unavailable (circuit open) → falls back to `latest` without raising.
- `ArtifactMgmtClient(stage="gamma", use_recommender=False)` → recommender never called.
- `ArtifactMgmtClient(stage="gamma", recommender_url="https://custom/")` → uses custom URL.
- Unit tests cover all branches with `responses` mocks for both AMS and recommender endpoints.
- Coverage ≥ 90% on changed paths in `client.py` and on `_recommender_client.py`.

---

## Story 7.3 — New exceptions and _RECOMMENDER_ENDPOINTS constant [S]

**Description:** Add `RecommenderUnavailableError` to the exception hierarchy and populate the endpoint constant.

**Files:**
- `artifact_mgmt/_exceptions.py` — add `RecommenderUnavailableError`
- `artifact_mgmt/client.py` — add `_RECOMMENDER_ENDPOINTS` dict
- `artifact_mgmt/__init__.py` — export `RecommenderUnavailableError`

**Implementation notes:**
- `RecommenderUnavailableError` inherits from `ArtifactMgmtError`. Exposed publicly so scientists can explicitly catch it if they want to log or handle recommender degradation themselves.
- However, `RecommenderClient.recommend()` never raises it — it returns `None` instead. The exception is for advanced users who call `_recommender_client.recommend()` directly (not recommended but not blocked).
- `_RECOMMENDER_ENDPOINTS`:

```python
_RECOMMENDER_ENDPOINTS: dict[str, str] = {
    "alpha": "https://<alpha-recommender-api-id>.execute-api.us-east-1.amazonaws.com/alpha",
    "gamma": "https://<gamma-recommender-api-id>.execute-api.us-east-1.amazonaws.com/gamma",
    "prod":  "https://<prod-recommender-api-id>.execute-api.us-east-1.amazonaws.com/prod",
}
```

Fill in actual API Gateway IDs after the CDK stack is first deployed to each stage.

**Acceptance criteria:**
- `RecommenderUnavailableError` importable from `artifact_mgmt`.
- `RecommenderUnavailableError` is a subclass of `ArtifactMgmtError`.
- `_RECOMMENDER_ENDPOINTS` populated for `alpha`, `gamma`, `prod`.
- Unit test: each new exception is an instance of `ArtifactMgmtError`.
- Coverage ≥ 90% on modified files.

---

## Story 7.4 — SDK integration tests: recommender integration [M]

**Description:** Unit tests for the `load_model` recommender path, plus an integration test against beta.

**Files:**
- `tests/test_client_recommender.py` (new unit test file in SDK repo)
- `tests/integration/test_recommender_e2e.py` (new integration test file in SDK repo)

**Unit tests (`test_client_recommender.py`):**
- Mock both AMS and recommender endpoints via `responses`.
- Test: `load_model` with healthy recommender → recommender called → version from recommender used in AMS fetch.
- Test: recommender returns 500 → fallback to `GetLatestVersion` → no exception raised.
- Test: `load_model(version="latest")` → recommender not called.
- Test: `load_model(use_recommender=False)` → recommender not called.
- Test: circuit breaker — 3 consecutive recommender 500s → 4th call doesn't hit recommender.
- Test: `ArtifactMgmtClient(use_recommender=False)` constructor → recommender never called regardless.

**Integration tests (`test_recommender_e2e.py`):**
- Deploy test fixture creates a model, saves three versions with different `dep_snapshot` Python versions.
- Call `load_model` with a known Python version → assert recommended version is the one with matching Python version.
- Call `load_model(use_recommender=False)` → assert latest version returned (not recommender's choice).
- Cleanup: delete model and all versions.

**Acceptance criteria:**
- All unit tests pass with `responses` mocked (no real network).
- Integration tests pass against beta stage.
- Circuit breaker test: verify the 4th call makes zero HTTP calls to the recommender.
- Coverage ≥ 90% on `test_client_recommender.py` and the new paths in `client.py`.

---

## Story 7.5 — Dependency pinning for SDK additions [S]

**Description:** Lock the new `_recommender_client.py` dependency graph. No new production dependencies are introduced (it reuses `requests`, `requests-aws4auth`, `boto3`) but the lockfile must be regenerated after the new code paths are exercised.

**Files:** `requirements.in`, `requirements.txt`, `requirements-dev.in`, `requirements-dev.txt`

**Implementation notes:**
- No new entries in `requirements.in` — `_recommender_client.py` only uses packages already in the base deps.
- Regenerate `requirements.txt` and `requirements-dev.txt` via `pip-compile --generate-hashes` after installing the new code.
- If `hypothesis` is added for property tests, add it to `requirements-dev.in` only.
- CI lockfile staleness check (introduced in SDK Story 7.4) continues to enforce correctness.

**Acceptance criteria:**
- `requirements.txt` regenerated and committed with current hash pins.
- No new entries in `requirements.in` (no new production dependencies).
- CI lockfile check passes.

---

# Epic 8 — Integration tests for the recommender service itself

**Goal:** End-to-end tests hitting the deployed beta recommender stack. These tests are run by the CodePipeline beta deploy stage post-deploy step.

## Story 8.1 — Integration test suite [L]

**Description:** Pytest suite that:
1. Deploys a model with three distinct versions (different Python versions via `dep_snapshot`).
2. Calls `POST /recommend/{model_name}` directly against the beta recommender API.
3. Asserts the correct version is recommended for each env snapshot.
4. Tears down the model after (via AMS directly).

**File:** `tests/integration/test_recommend_e2e.py`

**Test cases:**

```python
# Setup: create 3 versions of the same model via AMS beta
# v1.0: Python 3.10, PyTorch 2.0, CPU, linux
# v2.0: Python 3.11, PyTorch 2.1, CPU, linux
# v3.0: Python 3.11, PyTorch 2.1, CUDA 11.8, linux

def test_recommends_exact_python_match():
    """Scientist on Python 3.11, PyTorch 2.1, CPU → should get v2.0"""

def test_recommends_cuda_match_over_cpu():
    """Scientist on Python 3.11, PyTorch 2.1, CUDA 11.8 → should get v3.0"""

def test_recommends_closest_python_on_minor_miss():
    """Scientist on Python 3.11, PyTorch 2.0 → should get v1.0 (matching pytorch) vs v2.0"""
    # This tests framework dimension dominance

def test_different_framework_returns_no_compatible():
    """Scientist on Python 3.11, TensorFlow 2.13 → 422 NoCompatibleVersion"""

def test_fallback_when_recommender_down():
    """SDK circuit breaker: kill recommender Lambda (set reserved concurrency=0),
    call load_model → version falls back to latest, no exception"""

def test_cache_hit_returns_same_result():
    """Call recommend twice with same input → second response has cached=true"""

def test_health_check():
    """GET /health → 200, status=ok"""
```

**Implementation notes:**
- Test setup creates models and versions directly via `AMSClient` (not the high-level SDK) to control `dep_snapshot` precisely.
- SigV4-signed requests to the recommender API use a test IAM role provisioned in the CDK integ stack.
- The `fallback_when_recommender_down` test uses `boto3` to temporarily set `ReservedConcurrentExecutions=0` on the Lambda, then restores it in a `finally` block.
- All versions deleted in a `finally` block to prevent test state leakage between runs.
- `pytest.mark.integration` marker on all tests; run only when `INTEGRATION_STAGE` env var is set.

**Acceptance criteria:**
- All seven test cases pass against beta.
- `test_different_framework_returns_no_compatible` gets HTTP 422 with `reason: "all_disqualified"`.
- `test_fallback_when_recommender_down` completes in under 2 seconds (circuit breaker fires quickly).
- `test_cache_hit_returns_same_result` verifies `cached: true` on second call.
- Suite runs in under 8 minutes including setup/teardown.

---

## Story 8.2 — Smoke test script [S]

**Description:** A runnable script under `scripts/smoke-test.sh` that validates the recommender is live on a given stage.

**File:** `scripts/smoke-test.sh`

```bash
#!/bin/bash
# Usage: ./scripts/smoke-test.sh gamma
# Requires: AWS credentials, stage-named recommender endpoint in environment or RECOMMENDER_ENDPOINT_URL.

STAGE="${1:?Usage: $0 <stage>}"
ENDPOINT="${RECOMMENDER_ENDPOINT_URL:-$(python scripts/get-endpoint.py "$STAGE")}"

# POST /recommend/smoke-test-model with a basic env snapshot
# Expect 404 (model not found) or 200 — either means the service is alive
response=$(aws-sigv4-curl POST "$ENDPOINT/recommend/smoke-test-model" \
  --data '{"envSnapshot":{"pythonVersion":"3.11.0","framework":{"name":"pytorch","version":"2.1.0"},"os":"linux-x86_64"}}' \
  --header "Content-Type: application/json" 2>&1)

status=$(echo "$response" | jq -r '.status // .code')
if [[ "$status" == "ok" || "$status" == "ModelNotFound" || "$?" -eq 0 ]]; then
  echo "PASS: recommender is live on $STAGE"
  exit 0
else
  echo "FAIL: unexpected response: $response"
  exit 1
fi
```

**Acceptance criteria:**
- Script accepts `--stage` argument.
- Returns exit code 0 for 200 or 404 (service is alive in both cases).
- Returns exit code 1 for 5xx or timeout.

---

# Epic 9 — Dependency pinning and local dev setup

## Story 9.1 — Lambda dependency pinning [S]

**Description:** Lock all Lambda dependencies to exact hash-pinned versions.

**Files:** `lambda/requirements.in`, `lambda/requirements.txt`, `lambda/requirements-dev.in`, `lambda/requirements-dev.txt`

**Production dependencies (`requirements.in`):**
```
requests
requests-aws4auth
boto3
aws-embedded-metrics
```

**Dev dependencies (`requirements-dev.in`):**
```
-r requirements.in
pytest>=8.0
pytest-cov>=5.0
pytest-mock>=3.12
responses>=0.25
ruff>=0.4
mypy>=1.10
hypothesis>=6.100
boto3-stubs[execute-api]>=1.34
```

**Implementation notes:**
- Run `pip-compile --generate-hashes requirements.in -o requirements.txt` and similarly for dev.
- Commit both `.in` and generated `.txt` files.
- CI runs `pip-compile --generate-hashes requirements.in -o /tmp/req-check.txt && diff requirements.txt /tmp/req-check.txt` to catch lockfile drift.
- Lambda packaging: the CDK bundling step uses `requirements.txt` (not `requirements-dev.txt`) so test deps are never deployed.

**Acceptance criteria:**
- `pip install -r lambda/requirements.txt` installs exactly the pinned versions.
- CI fails if lockfile is stale.
- `hypothesis` in dev requirements only.
- Lambda deployment package does not include `hypothesis`, `pytest`, `responses`, or `mypy`.

---

# Sequencing

| Phase | Weeks | Epics | Critical path | Dependency |
|---|---|---|---|---|
| 1 | 1 | 1 | Smithy contract + repo skeleton | none |
| 2 | 1–2 | 2 | Scoring engine — pure logic, no I/O | Story 2.1 (models) unblocks all of Epic 2 |
| 3 | 2 | 3 | AMS client | Epic 2 complete |
| 4 | 2–3 | 4 | Caching layer | Epic 2 complete (models only) |
| 5 | 3 | 5 | Lambda handler | Epics 2, 3, 4 complete |
| 6 | 3–4 | 6 | CDK + pipeline | Can start after Epic 1; full deploy needs Epic 5 |
| 7 | 4 | 7 | SDK integration | Epic 5 deployed to alpha; SDK story 7.1 needs recommender endpoint |
| 8 | 4–5 | 8 | Integration tests | Epic 7 complete; beta deployed |
| 9 | 5 | 9 | Dependency pinning | Run last to pin stable dependency graph |

**Total: ~10 working days**

---

# What's deferred

- **ElastiCache Redis caching.** The in-memory LRU (Epic 4) is sufficient for alpha and gamma where single-Lambda warm concurrency is low. Redis provides a shared cache across concurrent Lambda instances and survives Lambda cold starts. Architecture is designed for it: `RecommendationCache` can be backed by a Redis adapter without changing `handler.py`. Deferred until prod traffic warrants it.

- **Synthetic canaries** (would be Story 6.5). Recommended before prod launch — a CloudWatch Synthetics canary that calls `POST /recommend/canary-model` every 5 minutes and alerts on latency or failure rate.

- **Proactive cache invalidation.** Today the 60-second TTL means the recommender may serve a stale recommendation for up to 60 seconds after a new version is confirmed in AMS. A future enhancement: AMS publishes a `VersionConfirmed` event to EventBridge; the recommender Lambda subscribes and calls `cache.invalidate(model_name)`. Deferred because 60-second staleness is acceptable for model loading.

- **Multi-model batch recommendations.** A scientist loading 10 models could benefit from a single `POST /recommend/batch` call. Deferred; the per-model call is sufficient and keeps the Smithy contract simple.

- **Recommendation history / audit log.** Recording which version was recommended to whom, and whether the model loaded successfully. Useful for feedback loops (was the recommendation actually better?). Deferred to a future analytics sprint.

- **Cross-region recommender.** Current deployment is `us-east-1` only, same as AMS. Multi-region adds latency complexity and is out of scope for v1.

- **Version pinning for the recommender-to-AMS call.** The recommender calls the AMS `ListVersions` endpoint which pages through all versions. For models with thousands of versions, this is slow. A future optimization: add a `?status=READY&limit=50` server-side filter to AMS `ListVersions` to reduce data transfer. Requires an AMS story.
