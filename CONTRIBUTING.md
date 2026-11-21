# Contributing

Use JDK 21 or 25, Git and the checked-in Maven wrapper. Java 21 is the compilation baseline;
CI requires Temurin 21 and 25. Rolling latest-GA checks and weekly EA probes are advisory;
their failures are reported without expanding the required support matrix.
The normal gate uses local fixtures and needs no API key. Commands below use a POSIX shell.

## Setup and checks

Install Python 3.12+ for the contributor tools, `pre-commit` 4.7.0 and
[Trivy 0.76.1](https://github.com/aquasecurity/trivy/releases/tag/v0.76.1) on your `PATH`.
The wrapper downloads the pinned Maven distribution; pre-commit installs its pinned hook tools.

```sh
pipx install pre-commit==4.7.0
pre-commit install --install-hooks
./mvnw verify
pre-commit run --all-files
```

`verify` compiles with warnings treated as errors, checks formatting and Checkstyle, runs the
tests, requires at least 85% line coverage in core and 95% in test helpers/Micrometer, validates
Javadoc and runs SpotBugs. Fix Java formatting with `./mvnw spotless:apply`. Pre-commit also checks
file hygiene, Python tooling, workflows, local links and contract pins, and scans with Trivy.
It does not run timed benchmarks.

Trivy runs on every commit with the same [policy](trivy.yaml) as CI: HIGH/CRITICAL findings from
vulnerability, misconfiguration and secret scanners fail the scan; vulnerability findings without
available fixes are excluded. Generated output, benchmark archives and local `.env` files are
excluded. The POM scan covers compile/runtime dependencies, not Maven plugins or test dependencies.
Do not infer complete dependency coverage from a green scan.

```sh
trivy fs --config trivy.yaml .
python3 -B -m unittest discover -s jev-benchmarks/scripts -v
```

The first Trivy scan downloads its database; subsequent scans cache and refresh it. Refreshes and
uncached Maven metadata require network access. A missing scanner, failed download or scan error
blocks the commit. CI runs Trivy after Maven builds, restores their dependency cache, and resolves
the reactor with `./mvnw -DskipTests install` if that cache is unavailable. Its separate pre-commit
job skips Maven and Trivy because those have dedicated checks. API comparison runs in the
required JDK 21 CI job or manually; it does not repeat Maven verification in pre-commit.
Error Prone runs separately on JDK 21. API compatibility permits no baseline only for the initial
release. See the [quality-gate guide](.github/maintainers/QUALITY_GATES.md) for commands, policy,
coverage floors, suppression rationale and scheduled monitoring.

## Tests and examples

Put regressions beside the affected module. Prefer deterministic fixtures for retries, deadlines,
cancellation and publication; preserve the shared HTTP/fake-client contract. A focused iteration
can use `./mvnw -pl jev-core -am test`, but run the full gate before submitting a change.

Run all examples without contacting the service:

```sh
./mvnw -q -DskipTests install
env -u TYPESAFE_API_KEY ./mvnw -q -pl jev-examples exec:java
```

The [benchmark guide](jev-benchmarks/README.md) describes explicit local measurements. Keep raw
runs outside Git and separate correctness checks from performance claims. A changed harness
requires fresh matched baselines.

## API documentation

Generate distributable public API documentation from a clean build:

```sh
./mvnw clean verify javadoc:jar
```

The library modules produce `target/*-javadoc.jar`. Internal packages are excluded from generation.
The clean step matters: Maven can retain obsolete HTML after package moves or changes to exclusions,
and an incremental Javadoc JAR can include those leftover files. Release candidates must use a clean
checkout and their Javadoc contents must be inspected before publication.
Keep `verify` in the same invocation so Javadoc uses the current reactor dependencies rather than
older snapshots that may be installed in the local Maven repository.

## Live-service tests

Live tests require both `JEV_RUN_LIVE_TESTS=1` and `TYPESAFE_API_KEY`. They make real API calls and
consume account usage. Copy `.env.example` to the Git-ignored `.env`, fill it in locally, then:

```sh
set -a; . ./.env; set +a
./mvnw verify
```

The SDK does not load `.env` itself. Never commit keys, private request bodies, responses or raw
DEBUG/TRACE logs. Disable live-test opt-in again before routine development. Executable examples
select the live service whenever a non-blank key is present, independently of the test opt-in flag.

## Pull requests

Describe the problem, resulting behaviour and relevant validation. Include a small sanitized
reproducer for a bug. Public API, dependency, default or wire changes need a changelog entry and
any necessary migration guidance; intentional 0.x breaking changes belong in a new minor release.
Keep all module versions aligned and update the README's single dependency version at release time.

User documentation lives in the root README, module READMEs and `docs/`. Keep implementation and
release details in the maintainer notes, not in user-facing review reports:

- [Internal architecture](.github/maintainers/INTERNAL_ARCHITECTURE.md)
- [Release strategy and prerequisites](.github/maintainers/RELEASING.md)
- [Upstream fixture provenance](.github/maintainers/UPSTREAM_FIXTURES.md)

Report suspected vulnerabilities through [SECURITY](SECURITY.md), not a public issue or PR.
