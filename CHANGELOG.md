# Changelog

Notable user-facing changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This SDK has its own versioning; upstream versions below are compatibility references.

Upstream tracked: `@typesafe-ai/sdk` 0.6.0, `typesafe-sdk` (Python) 0.7.2, OpenAPI `info.version` 0.2.0
(snapshot of 26 October 2026). Python 0.7.2 adds optional HTTP/2 packaging and documentation; the
audited behavioural baseline remains 0.7.1. JavaScript 0.7.0 and Python 0.7.5 (Jev 1.14: image
assets, box answers, zstd compression) were reviewed but not adopted. See
[pinned references](docs/PARITY.md#pinned-references).

## [Unreleased]

Initial release in preparation; not yet published to Maven Central.

### Added

- Java 21+ synchronous and asynchronous clients for System One requests and model discovery,
  with environment configuration, per-call overrides and immutable request/response types.
- Typed noul, choice and score answers; structured HTTP and validation exceptions retaining
  status, request id and response details.
- Configurable retries, server-directed backoff, attempt timeouts, operation deadlines,
  cancellation and bounded shutdown. Caller-supplied transports and executors remain caller-owned.
  Terminal-state and observer ordering are preserved through cancellation and concurrent shutdown;
  blocked callbacks do not extend the shutdown guarantee.
- Snapshots of nested Jackson content and validation of trailing JSON, required response fields
  and numeric ranges.
- Routing helpers: `NoulThreshold`, `ConfidenceGate`, normalized `Composite` scores and `FanOut`.
  Composite weighting supports extreme finite weights. Applications choose their own thresholds.
- `jev-test`: a recording client and scripted answers for tests without credentials or network
  access. Question-aware `choice` and `score` overloads avoid casts; numeric fixtures may deliberately
  be malformed. The fake does not simulate HTTP retries, timeouts or deadlines.
- `jev-micrometer`: call/attempt timers, token counters and allowlisted per-question summaries.
  Observer delivery is asynchronous and isolated from request execution and shutdown.
- Eight runnable examples and an unpublished [benchmark tool](jev-benchmarks/README.md) with local
  HTTP, synthetic transport, component timing/allocation and restricted JFR diagnostics.
- Automatic-module names `net.codefinch.jev`, `net.codefinch.jev.test` and
  `net.codefinch.jev.micrometer`; classpath use remains supported.
- Coverage gates for all published libraries, focused Error Prone analysis, public API compatibility
  checks and scheduled dependency/upstream/runtime monitoring.
- Contributor instructions, private security reporting, issue forms and public API Javadoc.
  Internal implementation packages are excluded from generated API documentation.
- Text requests verified against models `jev-1.13.0` and `jev-1.14.0`. Jev 1.14's `box` answers are
  ignored with a warning and image assets cannot be sent; both are planned for a later release.

### Changed

- Name the project **jevjavauosdk — Jev Java Unofficial SDK** and clearly state its independence
  from TypeSafe AI. Requests identify it as `jevjavauosdk/<version>`; service-required header and
  environment-variable names remain unchanged.
- Refresh build/test dependencies and Jackson to 2.22.3. Keep Java 21 as the bytecode baseline;
  CI verifies JDK 21 and 25.
- Benchmark study scripts retain their command names and archive formats, but fixed study runs
  now require explicit `--cpus`. Python safe-path mode is supported. A changed harness needs fresh
  performance baselines; historical archives remain readable.

### Snapshot migration

- Move request/response types to `net.codefinch.jev.model` and SDK exceptions to
  `net.codefinch.jev.exception`. Update imports and recompile. Maven coordinates are unchanged.
- Rename `JevMetrics.Builder.questionTags(...)` to `allowQuestions(...)` and its getter to
  `allowedQuestions()`.
- Remove public access to HTTP client configuration/lifecycle test hooks, `Content.Pure`,
  `Content.isContentType`, `RequestOptions.resolveRetry` and `RequestOptions.deadlineDisabled`.
  Use the public builder, model and per-call options APIs instead; `.internal` is unsupported.

### Security

- Reject invalid API keys before creating a header, without echoing them. Invalid explicit keys
  never fall back to environment credentials. Reject unsafe header values without retaining a
  cause that exposes the value; enforce no redirects for owned and injected transports.
- Redact credential headers and filter diagnostics per client. DEBUG/TRACE bodies and exception
  response data may still contain private application data.
- Run Trivy in pre-commit and CI; see [scan coverage and exclusions](CONTRIBUTING.md#setup-and-checks).

Compatibility limits and historical service observations are documented in [PARITY](docs/PARITY.md).
Detailed development and review history remains in Git.

[Unreleased]: https://github.com/Ultonian/jevjavauosdk/commits/main/
