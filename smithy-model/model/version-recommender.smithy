$version: "2.0"
namespace com.anthropic.versionrecommender

@title("VersionRecommenderService")
@aws.protocols#restJson1
service VersionRecommender {
    version: "2026-05-23"
    operations: [RecommendVersion, HealthCheck]
    errors: [ValidationException, InternalServerException, ThrottlingException]
}

// ─── Primary operation ────────────────────────────────────────────────────────

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
    @required
    @httpLabel
    modelName: ModelName

    @required
    envSnapshot: EnvSnapshot
}

structure RecommendVersionOutput {
    @required
    recommendedVersion: VersionId

    @required
    score: Float

    @required
    explanation: String

    @required
    candidatesEvaluated: Integer

    @required
    cached: Boolean
}

// ─── Health check ─────────────────────────────────────────────────────────────

@readonly
@http(method: "GET", uri: "/health", code: 200)
operation HealthCheck {
    output: HealthCheckOutput
}

structure HealthCheckOutput {
    @required
    status: String

    @required
    stage: String

    @required
    version: String
}

// ─── Shapes ───────────────────────────────────────────────────────────────────

@pattern("^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
@length(min: 3, max: 64)
string ModelName

@pattern("^\\d+\\.\\d+$")
string VersionId

@sensitive
structure EnvSnapshot {
    @required
    pythonVersion: String

    @required
    framework: FrameworkInfo

    cudaVersion: String

    @required
    os: String
}

structure FrameworkInfo {
    @required
    name: String

    @required
    version: String
}

// ─── Errors ───────────────────────────────────────────────────────────────────

@error("client")
@httpError(404)
structure ModelNotFoundException {
    @required
    code: String

    @required
    message: String

    @required
    requestId: String
}

@error("client")
@httpError(422)
structure NoCompatibleVersionException {
    @required
    code: String

    @required
    message: String

    @required
    requestId: String

    @required
    reason: String
}

@error("client")
@httpError(400)
structure ValidationException {
    @required
    code: String

    @required
    message: String

    @required
    requestId: String

    details: Document
}

@error("server")
@httpError(500)
structure InternalServerException {
    @required
    code: String

    @required
    message: String

    @required
    requestId: String
}

@error("client")
@httpError(429)
structure ThrottlingException {
    @required
    code: String

    @required
    message: String

    @required
    requestId: String

    retryAfterSeconds: Integer
}
