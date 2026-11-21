# jevjavauosdk — Jev Java Unofficial SDK

**jevjavauosdk** (Jev Java Unofficial SDK) is an independently maintained Java 21+ SDK for
[TypeSafe AI](https://typesafe.ai)'s Jev (System One API).
This project is not affiliated with, endorsed by, or supported by TypeSafe AI.
References to TypeSafe AI and Jev identify the service this SDK interoperates with.

Status: pre-release, not yet on Maven Central. Supports synchronous and asynchronous requests,
routing helpers, Micrometer metrics and an in-memory test client. Start below or use the
[documentation index](docs/README.md); the [compatibility guide](docs/PARITY.md) explains validation,
runtime behavior and differences from the pinned official SDKs.

| Module | Runtime dependencies | What it is |
|---|---|---|
| `jev-core` | Jackson only | The client, request/answer types, `patterns` helpers |
| `jev-micrometer` | `jev-core`, Micrometer | `JevMetrics`, a `CallObserver` recording timers, token counters and allowlisted confidence summaries |
| `jev-test` | `jev-core` | `RecordingJevClient`, an in-memory `JevClient` with scripted answers and recorded requests (use with `test` scope) |
| `jev-examples` | — | eight runnable examples mirroring the docs; not published |
| [`jev-benchmarks`](jev-benchmarks/README.md) | `jev-core`, `jev-micrometer`, JMH, HdrHistogram | component, loopback HTTP and in-memory transport benchmarks; run manually; not published |

## Requirements and compatibility

Java 21 is the minimum runtime and bytecode target. CI requires Temurin JDK 21 and 25. Rolling
latest-GA checks and weekly early-access probes are advisory; their failures are reported without
expanding the required support matrix. Core uses the Jackson 2.22 runtime family; the Micrometer
module adds Micrometer dependencies.

The built-in JDK HTTP client prefers HTTP/2 and falls back to HTTP/1.1 according to the server,
proxy and transport configuration; no extra HTTP/2 library is needed. An injected `HttpClient`
controls its own protocol preference. See [JDK protocol selection](https://docs.oracle.com/en/java/javase/21/docs/api/java.net.http/java/net/http/HttpClient.Builder.html#version(java.net.http.HttpClient.Version)).
The tracked API schema is OpenAPI `info.version` 0.2.0, snapshot of 26 October 2026;
[pinned references](docs/PARITY.md#pinned-references) record the exact upstream inputs and the
limits of that comparison. Text requests were verified against models `jev-1.13.0` and `jev-1.14.0`.
Jev 1.14's image assets and box answers are not supported yet; a `box` answer is ignored with a warning.

## Versioning and stability

This SDK uses its own semantic versioning, independent of the service, model and official SDK
versions. Before 1.0, a new minor release may break source or binary compatibility; patch releases
preserve the public API. Unreleased snapshots can change between builds. All published modules
use the same version. The changelog's "Upstream tracked" line identifies comparison baselines,
not this SDK's release number or a guarantee about newer upstream releases.

## Usage

Not on Maven Central yet. Until it is, install locally and depend on the snapshot:

```sh
git clone https://github.com/Ultonian/jevjavauosdk && cd jevjavauosdk && ./mvnw -q -DskipTests install
```

```xml
<dependency>
  <groupId>net.codefinch.jev</groupId>
  <artifactId>jev-core</artifactId>
  <version>0.1.0-SNAPSHOT</version>
</dependency>
```

Core usage examples are exercised in CI by
[`ReadmeUsageTest`](jev-examples/src/test/java/net/codefinch/jev/examples/ReadmeUsageTest.java).
Snippets omit imports and application-specific callbacks such as `route` and `suggest`.

### Packages

| Package | Use it for |
|---|---|
| `net.codefinch.jev` | `JevClient`, `JevClientBuilder`, `RequestOptions`, `RetryPolicy`, `CallObserver` |
| `net.codefinch.jev.model` | Request/response data: `State`, `Content`, `Questions`, criteria, answers, usage and model metadata |
| `net.codefinch.jev.exception` | SDK-specific `Jev…Exception` types |
| `net.codefinch.jev.patterns` | Routing thresholds, confidence gates, composites and fan-out |

`net.codefinch.jev.internal` contains implementation details and is not a supported application API.
Earlier snapshots placed the model and exception types directly in `net.codefinch.jev`. Update
those imports and rebuild dependent applications; this pre-release move changes binary names.
Maven coordinates, class names and behavior are unchanged by the package moves. See the
[changelog](CHANGELOG.md) for other snapshot API changes, including the metrics allowlist rename.

The library JARs reserve automatic-module names `net.codefinch.jev`, `net.codefinch.jev.test`
and `net.codefinch.jev.micrometer`. They can also be used on the classpath. Automatic modules
export all packages; the internal package remains unsupported rather than being hidden by JPMS.
Because automatic modules declare no dependencies, a module-path application must resolve the
SDK's own requirements itself: `requires java.net.http;` (or `--add-modules java.net.http`) and
either `requires com.fasterxml.jackson.databind;` or `--add-modules ALL-MODULE-PATH` for Jackson
and Micrometer. The release checks run a consumer both ways on JDK 21 and 25.

### Create a client

```java
JevClient client = JevClient.fromEnv();          // TYPESAFE_API_KEY, optional TYPESAFE_BASE_URL etc.

JevClient configured =
    JevClient.builder()
        .apiKey(apiKey)
        .defaultModel("jev-latest")                // or a versioned id such as "jev-1.13.0"
        .timeout(Duration.ofSeconds(10))           // per HTTP attempt, headers and body
        .deadline(Duration.ofSeconds(30))          // whole operation incl. retries; noDeadline() to disable
        .retryPolicy(RetryPolicy.DEFAULT.withMaxRetries(3))
        .build();
```

A client is thread-safe and meant to live as long as your application; `close()` it on shutdown
(it is `AutoCloseable`).
The SDK reads environment variables, but does not automatically load `.env` files. Supply an API
key explicitly or export `TYPESAFE_API_KEY` before starting your application. Invalid keys fail
when the client is built, with an error that does not include the key.

### Ask

The **state** is what every question is judged against — text, or a JSON object when the context
has several parts. Each **question** is one of three primitives; ids are for your code and are
not shown to the model, so each question must say what it means.

```java
State state = State.of(Map.of("ticket", Map.of("text", ticketText, "customer_tier", "gold")));

Questions questions =
    Questions.builder()
        .noul("refund_requested", "Does `ticket.text` ask for money back?",
            NoulCriteria.of("Wants a refund, chargeback or credit", "No request for money back"))
        .choice("department", "Which team should handle `ticket.text`?",
            ChoiceCriteria.builder()
                .option("billing", "Invoices, charges, refunds")
                .option("shipping", "Delivery, tracking, damaged parcels")
                .option("other")                                   // undescribed: the name alone
                .build())
        .score("severity", "How severe is the problem for the customer?",
            List.of("Cosmetic", "Inconvenient, workaround exists", "Blocking"))
        .build();

SystemOneResponse response = client.systemOne(state, questions);
```

### Read the answers

```java
Answers answers = response.answers();
double refund = answers.noul("refund_requested").noul();        // probability of yes, 0..1
ChoiceAnswer dept = answers.choice("department");              // choice(), probabilities(), confidence()
ScoreAnswer severity = answers.score("severity");              // score() may fall between levels; legend()

String summary =
    switch (answers.get("department").orElseThrow()) {         // sealed: the switch is exhaustive
      case NoulAnswer n -> "yes with p=" + n.noul();
      case ChoiceAnswer c -> c.choice() + " (confidence " + c.confidence() + ")";
      case ScoreAnswer sc -> "level " + sc.score() + " of " + sc.topLevel();
    };

response.model();       // the versioned model that answered, e.g. "jev-1.13.0"
response.usage();       // input and output tokens
response.requestId();   // x-typesafe-request-id, for support
```

Typed accessors throw `JevMissingAnswerException` for an unknown id and `JevAnswerTypeException`
for the wrong primitive; `answers.get(id)` returns an `Optional` instead.

### Per-call options and models

```java
SystemOneRequest request = SystemOneRequest.of(state, questions).withModel("jev-preview");
RequestOptions options =
    RequestOptions.builder()
        .timeout(Duration.ofSeconds(5))
        .retry(p -> p.withMaxRetries(0))          // partial override of the client's policy
        .header("X-Request-Source", "batch-job")
        .build();
SystemOneResponse r = client.systemOne(request, options);
```

### Async

```java
CompletableFuture<SystemOneResponse> future = client.systemOneAsync(state, questions);
future.thenAccept(r -> route(r.answers()));      // standard CompletableFuture callback semantics
future.cancel(true);                             // aborts the HTTP exchange and any backoff
```

### Errors and retries

Request and response-processing failures use `JevException` (unchecked). Invalid builder/value
arguments can also throw `IllegalArgumentException` or `NullPointerException`; using a closed
client throws `IllegalStateException`. HTTP errors are `JevApiException`
subclasses named after the status — `JevAuthenticationException` (401),
`JevPermissionDeniedException` (403),
`JevUnprocessableEntityException` (422, with `fieldErrors()`), `JevRateLimitException` (429,
with `retryAfter()`), `JevInternalServerException` (any 5xx, `isOverloaded()` for 529) — each
carrying the status, headers, raw body and request id. Transport failures are
`JevConnectionException` / `JevTimeoutException`; an expired deadline is
`JevDeadlineExceededException`.

The default policy retries HTTP 408, 429 and 5xx, connection errors and per-attempt timeouts.
It allows two retries after the first attempt, with exponential backoff starting at 500 ms,
capped at 5 s, and up to 25 % subtracted as jitter. Valid server delays of up to 60 s are honored;
the 30 s operation deadline still limits the whole call. Use `RetryPolicy.NONE` to disable retries.
Async failures are exposed by the future; `join()` wraps failures in `CompletionException`,
while cancellation throws `CancellationException`.

```java
try {
  client.systemOne(state, questions);
} catch (JevRateLimitException e) {
  e.retryAfter().ifPresent(delay -> log.warn("rate limited; server suggests {}", delay));
} catch (JevApiException e) {
  log.error("{} {} request_id={}", e.status(), e.getMessage(), e.requestId().orElse("-"));
}
```

### Configuration

Explicit builder values take precedence over environment variables, then defaults.

| Setting | Builder | Environment | Default |
|---|---|---|---|
| API key | `apiKey` | `TYPESAFE_API_KEY` | required |
| Base URL | `baseUrl` | `TYPESAFE_BASE_URL` | `https://api.typesafe.ai` |
| Default model | `defaultModel` | `TYPESAFE_DEFAULT_MODEL` | `jev-latest` |
| Log level | `logLevel` | `TYPESAFE_LOG_LEVEL` | `warn`. As in the official SDKs: `info` = one line per attempt and retry; `debug` adds headers (credentials redacted) and bodies. Emitted via `System.Logger` |
| Per-attempt timeout | `timeout` | — | 10 s |
| Operation deadline | `deadline` / `noDeadline()` | — | 30 s |
| Shutdown grace | `closeGracePeriod` | — | 10 s |
| Result publication during shutdown | `publicationTimeout` | — | 5 s |
| Retry policy | `retryPolicy` | — | `RetryPolicy.DEFAULT` |
| Transport / executor | `httpClient` / `executor` | — | SDK-owned; caller-supplied ones are never shut down |

An injected `HttpClient` must use `Redirect.NEVER`. DEBUG logs include request and response
bodies; header redaction does not remove sensitive data from those bodies.

## Routing on answers

Thresholds are always yours; the SDK ships none.

```java
NoulThreshold refund = NoulThreshold.of(0.2, 0.8);      // NO / UNSURE / YES
ConfidenceGate routing = ConfidenceGate.of(0.5, 0.85);  // ESCALATE / CONFIRM / ACT
switch (routing.decide(r.answers().choice("department"))) {
  case ACT -> route(r.answers().choice("department").choice());
  case CONFIRM -> suggest(...);
  case ESCALATE -> queueForTriage();
}
```

`FanOut` asks one question per item in a single call, binding each item into its own
instructions (ids are never shown to the model), and maps the answers back:

```java
FanOut<String> spam = FanOut.noul(messages, FanOut.text(), "Is this message unsolicited advertising?");
for (ItemAnswer<String> a : spam.answers(client.systemOne(state, spam.questions()))) {
  if (a.noul().noul() > 0.9) { ... }
}
```

## Metrics and testing

Add `net.codefinch.jev:jev-micrometer`, using the same version as `jev-core`, for metrics. Provide your application's
Micrometer `MeterRegistry` and an API key:

```java
JevMetrics metrics = JevMetrics.builder(registry).allowQuestions(Set.of("department")).build();
JevClient client = JevClient.builder().apiKey(apiKey).observer(metrics).build();
```

`JevMetrics` is in `net.codefinch.jev.micrometer`. It records `jev.call` and `jev.attempt` timers,
`jev.tokens` counters, and `jev.confidence` / `jev.noul` summaries only for allowlisted question ids.
Observer delivery is asynchronous, so metrics may arrive after a request returns. Close the client
on application shutdown; your application owns the registry.

For application tests, add `net.codefinch.jev:jev-test` with Maven's `test` scope and the same
version as `jev-core`:

```java
try (RecordingJevClient fake = new RecordingJevClient()
    .enqueue(ScriptedAnswers.neutral(questions).noul("refund_requested", 0.95))) {
  // Pass fake to your application as its JevClient, then inspect fake.requests().
}
```

`RecordingJevClient` and `ScriptedAnswers` are in `net.codefinch.jev.test`. The fake needs no API
key or network access. Scripts are consumed in order; once exhausted, the default responder
returns neutral answers. Use `enqueueFailure(...)` for error paths. It does not simulate HTTP
retries, timeouts or deadlines. After `ScriptedAnswers.neutral(questions)`, use
`choice("department", "billing", 0.9, 0.8)` or `score("severity", 1.5, 0.8)` to override an answer
using its original question definition. Scripts created with `empty()` need an explicit question
or a raw answer. Numeric values are intentionally not range-validated, so tests can model
malformed responses.

## Examples

Eight runnable examples in [`jev-examples`](jev-examples/README.md), each mirroring a page of the
TypeSafe docs (quickstart, speculative fan-out, composite scoring, confidence-gated routing,
intent routing, line search, re-ranking, entity alignment) with its questions and thresholds in one file. They
run against the live API with `TYPESAFE_API_KEY`, otherwise against a scripted fake.

```sh
./mvnw -q -DskipTests install && ./mvnw -q -pl jev-examples exec:java
```

## Build from source

Use JDK 21+; the Maven wrapper downloads the pinned Maven version.

```sh
./mvnw verify
```

See [CONTRIBUTING](CONTRIBUTING.md) for tool setup, pre-commit checks and opt-in live tests.
Report vulnerabilities through the private channel in [SECURITY](SECURITY.md).
Release and snapshot migration notes are in the [changelog](CHANGELOG.md).
