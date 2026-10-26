# SDK behavior and compatibility

This guide describes the Java SDK in this repository and the differences that matter when
integrating it or moving from the official Python or JavaScript SDKs. Jev Java Unofficial SDK
is independently maintained and is not affiliated with, endorsed by, or supported by TypeSafe AI.

Start with the [usage examples](../README.md#usage). The upstream comparisons below refer to
[pinned versions](#pinned-references), not a claim of parity with every subsequent upstream release.
Request and response data types live in `net.codefinch.jev.model`; client configuration lives in
`net.codefinch.jev`. See the [package guide](../README.md#packages) when updating an older snapshot.

## Requests and validation

| Input | Java behavior |
|---|---|
| Content | Text, JSON objects and arrays are supported. Top-level numbers and booleans are rejected; nested ones are accepted. |
| Mutable input | Maps, lists and Jackson trees are snapshotted. Mutating the original input does not change a built request. |
| State | Must not be Java `null` or `Content.NULL`. |
| Questions | At least one question; ids must be unique and non-blank. |
| Instructions | `Optional.empty()` omits the field; `Content.NULL` sends explicit JSON `null`. |
| Noul criteria | Either side can be omitted with `NoulCriteria.ofTrue(...)` or `ofFalse(...)`. Absent criteria are omitted; explicit `criteria: null` is not represented. |
| Choice criteria | Labels must be unique and non-empty. `.option(label)` makes an undescribed option; insertion order is preserved. |
| Score criteria | At least two non-null levels, ordered from lowest to highest. |
| Model selection | A request's `.withModel(...)` overrides the client's default model. |
| Extra request fields | There is no `extra_body` or arbitrary raw-request escape hatch. |
| Image assets (Jev 1.14) | Not supported: `assets` and the multipart request body cannot be sent. Planned for 0.2.0. |
| Box questions (Jev 1.14) | Not representable. Planned for 0.2.0. |
| Danger-zone headers | `TypeSafe-Danger-Zone-Images` and `TypeSafe-Danger-Zone-Long-Context` are ordinary headers for `defaultHeaders`; the long-context limits are usable with text requests today. |

The SDK validates shapes, not every service limit. It does not enforce token budgets or upper
bounds on choice/score counts. Oversized requests can therefore fail at the service with
`JevUnprocessableEntityException`, `JevBadRequestException` or another API error. Historical
observations are listed below.

## Responses

Required fields and JSON types are checked, including `model`, `answers` and both integer token
counts in `usage`. Invalid successful responses throw `JevResponseValidationException`; inspect
`fieldPath()` to locate the problem. Trailing JSON documents and unrepresentable numbers are rejected.

Unknown fields are ignored. An unknown answer `type` is omitted from the typed answers and emits
a WARNING through the client's configured log filter. The original JSON remains available in
`SystemOneResponse.rawBody()`. Consequently, a non-empty response containing only unknown answer
kinds can produce an empty typed `Answers` map. Jev 1.14's `box` answers are such an unknown kind
in 0.1.0 (`ResponseParserTest.boxAnswerIsDroppedButStaysInRawBody`).

Use `answers.get(id)` when an answer may be absent. Typed accessors such as `answers.noul(id)`
throw `JevMissingAnswerException` for a missing id and `JevAnswerTypeException` for the wrong kind.
Score values can be fractional; legend/probability keys are integer levels. Model `releaseDate()`
values are strings: do not assume a date-only format.

Response headers, raw bodies and the optional request id are retained. Keep the request id when
reporting a service problem; raw bodies can contain application data.

## Errors, retries and deadlines

SDK-specific exception types are in `net.codefinch.jev.exception`. Code written against the earlier
root-package types must update imports and recompile; exception names and inheritance are unchanged.

| Failure | Java result |
|---|---|
| HTTP 400 / 401 / 403 / 404 | `JevBadRequestException` / `JevAuthenticationException` / `JevPermissionDeniedException` / `JevNotFoundException` |
| HTTP 422 | `JevUnprocessableEntityException`; `fieldErrors()` extracts supported validation details. |
| 400 body shape | The schema documents `{"detail": {"error_type", "message"}}` or a string `detail`; the message is extracted, `error_type` is not exposed (`JevApiExceptionTest.messageIsExtractedInUpstreamOrder`). |
| HTTP 429 | `JevRateLimitException`; `retryAfter()` exposes a parsed server delay when present. |
| HTTP 5xx | `JevInternalServerException`; `isOverloaded()` identifies 529. |
| Other unsuccessful HTTP status | `JevApiException` with the status, headers and body. |
| Transport failure / per-attempt timeout | `JevConnectionException` / `JevTimeoutException` |
| Whole-operation deadline | `JevDeadlineExceededException` (a timeout subtype) |
| Interrupted synchronous caller | `JevInterruptedException`; the thread's interrupt flag is restored. |

Invalid arguments can throw standard Java exceptions; calls after client shutdown throw
`IllegalStateException`. For async calls, handle exceptional completion as well as cancellation.

Defaults are a **10 s timeout per attempt** and a **30 s operation deadline**, including executor
queueing, attempts and backoff. Set `noDeadline()` to disable the whole-operation limit, or override
it in `RequestOptions`. The per-attempt timeout still applies.

`RetryPolicy.DEFAULT` permits two retries after the initial attempt for HTTP 408, 429 and 5xx,
connection failures and per-attempt timeouts. Exponential backoff starts at 500 ms, caps at 5 s and
subtracts up to 25% jitter. `retry-after-ms` takes precedence over `Retry-After`; valid delays up to
60 s are used, otherwise normal backoff applies. A server delay never extends the operation
deadline. Cancellation, interruption and deadline expiry are terminal. Use `RetryPolicy.NONE`
to disable retries.

## Configuration and lifecycle

Explicit builder values override `TYPESAFE_*` environment variables, then defaults. The SDK does
not read `.env` files. Keys are stripped of surrounding whitespace and must contain only printable
ASCII without spaces. An explicitly invalid key does not fall back to the environment; validation
errors do not echo it. The [configuration table](../README.md#configuration) lists the settings.

Reuse a client across calls and close it when your application shuts down. By default, `close()`
allows 10 s for outstanding calls, then cancels them and allows 5 s for result publication. A normal
return means all returned futures are done; it does not wait for application callbacks or observer
work to finish. An interrupted shutdown or unmet publication deadline is reported as `JevException`.

Cancelling an async future aborts the HTTP exchange and backoff. Non-async `CompletableFuture`
callbacks follow JDK execution rules and are not guaranteed a particular thread. Use an async
continuation with your own executor if you need a specific execution context.

Caller-supplied transports and executors remain caller-owned. Injected `HttpClient` instances must
use `Redirect.NEVER`; redirects are not followed. Requests identify this SDK as
`jevjavauosdk/<version>` in `User-Agent` and `X-TypeSafe-SDK`. Protocol header names and environment
variables retain their `TypeSafe` names for interoperability.

Logging uses `System.Logger` with WARNING as the default filter. INFO records attempts and retries;
DEBUG adds headers and bodies. Credential headers are redacted, but application data inside bodies
is not. Metrics observers run asynchronously; they may be delivered after a call returns.

## Differences from the pinned official SDKs

| Behavior | Java choice |
|---|---|
| Score minimum | Two levels, matching JavaScript; pinned Python and the pinned schema allow one. |
| Null state or score level | Rejected, matching the pinned schema; JavaScript's content type permits null. |
| Explicit null instructions | Representable, like JavaScript; Python's typed serialization omits top-level `None`. |
| Explicit null noul criteria | Not representable; omission matches Python's serialization. No semantic equivalence is promised. |
| Empty or incomplete `usage` | Rejected to follow the schema; Python's public `Usage` model accepts absent token counts. |
| Mutable inputs | Copied into immutable content, rather than retaining caller-owned containers. |
| Server-directed retry delay | 60 s cap, matching JavaScript; the pinned Python policy has no cap. |
| HTTP/2 | The JDK transport prefers HTTP/2 and can fall back to HTTP/1.1; no optional packaging extra is needed. The server/proxy and any injected transport determine what is used. |
| Operation lifecycle | Adds a whole-operation deadline, Java interruption/cancellation behavior and explicit resource ownership. |

## Pinned references

| Reference | Version / snapshot |
|---|---|
| Official JavaScript SDK | `@typesafe-ai/sdk` 0.6.0, commit `66880ccded6cb642dc1809620c2b108c33730214` |
| Official Python SDK contract baseline | `typesafe-sdk` 0.7.1, commit `0ffd094c72ed9445223060b24ffd7a56aa781fb4` |
| OpenAPI | [`info.version` 0.2.0, retrieved 26 October 2026](upstream/openapi-0.2.0-2026-10-26.json) |

Python `typesafe-sdk` 0.7.2 (26 September 2026) was checked against the pinned 0.7.1 commit:
[the upstream diff](https://github.com/typesafe-ai/typesafe-sdk-python/compare/0ffd094c72ed9445223060b24ffd7a56aa781fb4...f078f1e208a0d885154dc758344ae4fce77ac168)
adds an optional `http2` packaging extra and documentation, with no SDK source changes. The
changelog tracks 0.7.2; the behavioural contract baseline above remains 0.7.1.

JavaScript 0.7.0 and Python 0.7.5 (both 14 October 2026) were reviewed, not audited, when the
schema snapshot was refreshed: they add Jev 1.14 image assets, box answers and zstd request
compression, and JavaScript now tolerates null token counts and requires Node 22. None of that
changes text requests, so the audited baselines above stand; the additions are planned for 0.2.0.
The 26 October schema snapshot adds the same features plus a documented 400 error shape and
shortened validation-error fields, with `info.version` still 0.2.0.

These references document the contract used to implement this SDK. They are historical snapshots
and do not constitute an audit of newer upstream releases.

## Historical service observations

Probes on **21 September 2026**, using model `jev-1.13.0`, found a maximum of 10 score levels and
255 choice options (HTTP 400 above those limits), rejection of an empty choice, and support for a
single score level. Java still requires two score levels. Model release dates included an ISO-8601
timestamp with an offset rather than a date alone. Invalid and missing keys produced HTTP 401 and
403 respectively. These are observations from that run, not guarantees about today's service.

Omitted and null instructions produced similar answers in a small probe; that does not establish
semantic equivalence. Explicit null noul criteria were accepted, but this SDK emits omission.

Re-run on **26 October 2026** against `jev-1.13.0` and `jev-1.14.0` explicitly (the `jev-latest`
alias had moved to 1.14.0 around 21 October): text requests behaved identically on both, each
response naming the requested versioned model. The 10-level score and 255-option choice limits are
now rejected by request validation (HTTP 422 naming the field) instead of HTTP 400; the schema
declares `maxItems: 10` for score criteria, while the choice cap remains server-side only.
`GET /v1/models` lists only the `jev-latest` and `jev-preview` aliases, not versioned ids.

To recheck the supported live probes, follow [the opt-in test instructions](../CONTRIBUTING.md#live-service-tests).
They require `JEV_RUN_LIVE_TESTS=1` and `TYPESAFE_API_KEY`, make real calls and consume account usage.
