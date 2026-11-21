# Quality gates and maintenance signals

Required PR checks are `Build and verify (JDK 21)`, `Build and verify (JDK 25)`,
`pre-commit hooks (non-Maven)`, `Trivy (dependencies and config)` and `Error Prone (JDK 21)`.
The repository rules protect `main`, `release/**` and existing `v*` tags. PRs and resolved review
threads are required; no second-person approval is required for this single-maintainer project.
Force-pushes and deletion are blocked, with no administrator bypass. Tag creation is permitted;
changing or deleting an existing release tag is not. The intended rules live in
[`repository-rules.json`](../../config/quality/repository-rules.json).
Secret scanning, push protection, Dependabot security updates and private reporting are enabled.
Repository settings are independent of workflow files: verify both when changing required checks.
The Error Prone check becomes runnable when this workflow change reaches a PR; older branches
must incorporate the workflow before they can satisfy the new rule.

## Local and CI checks

`./mvnw clean verify` retains formatting, compiler warnings, Checkstyle, tests, Javadoc and SpotBugs.
The shared JaCoCo configuration is enabled only in the three published libraries:

| Module | Measured line coverage before the new tests | Required floor |
|---|---:|---:|
| Core | 97.2% | 85% (existing policy) |
| Test helpers | 97.0% | 95% |
| Micrometer | 100% | 95% |

Test-helper Surefire uses `@{argLine}` so JaCoCo instrumentation and its single-carrier scheduler
settings are both active. New tests cover direct malformed scripted answers and request access
on a recorded models call. Timing-sensitive defensive branches need not be forced just for a number.

Error Prone runs separately on JDK 21 across main and test sources of the published libraries:

```sh
./mvnw -Panalysis -pl jev-core,jev-test,jev-micrometer -am clean verify
python3 -B scripts/prove_gates.py
```

The proof script uses `JAVA_HOME` when set; otherwise it resolves `javac` on `PATH` and uses that
JDK for both Maven and the fixture tools. It fails early if `java`, `javac` or `jar` is missing;
a runtime-only installation is insufficient. Use a complete JDK 21 or newer.

The profile enables Error Prone's default error-level checks plus explicit checks for discarded
exceptions, incompatible collection arguments/equality, reference equality, ignored required
return values/futures, self-assignment and ThreadLocal usage. Other warning-level checks remain
disabled so `-Werror` does not make every style suggestion mandatory. Method-scoped suppressions
explain intentionally unobserved dependent futures in lifecycle tests, the fake's cleanup stage,
and a test's thread-identity comparison. No sources in those three modules are excluded. Examples
and JMH are outside the dedicated analysis command; normal verification still checks them.
The profile follows the [Error Prone compiler integration](https://errorprone.info/docs/installation).
Its compiler-module flags do not affect SDK consumer JVMs. `prove_gates.py` builds disposable
fixtures using the configured plugins, including compatible/breaking API changes, a discarded
exception, array equality (a default error check), and genuinely unexecuted classes for each
coverage gate.

Pre-commit retains full Maven verification and Trivy. Scoped checks add actionlint, local Markdown
files/anchors, contract-pin consistency and maintenance-script tests. Python tooling uses only the
standard library and has a 3.12+ floor. Run its fast checks with:

```sh
python3 -B scripts/check_repository.py
python3 -B -m unittest discover -s jev-benchmarks/scripts -v
pre-commit run --all-files
```

External links are checked weekly/manual, with three attempts and a 20-second timeout per request;
they do not block routine PRs. There are currently no external-link exclusions.

## Public API policy

API comparison runs in the required JDK 21 CI job. It is intentionally absent from pre-commit:
once a baseline exists, it would repeat verification of three modules on every commit. Run
`python3 -B scripts/api_compat.py` manually when needed. It reads
[`api-policy.json`](../../config/quality/api-policy.json) and invokes japicmp for core, test helpers
and Micrometer. Protected and public surfaces are checked for binary and source compatibility;
`net.codefinch.jev.internal` is excluded. Missing classes and unresolved artifacts fail.
Only `0.1.0` (including its development snapshot) may omit the first published baseline.
When that version is released, set `baseline` to `0.1.0` before advancing development versions.
Never use automatic "latest" resolution for the baseline.

Patch releases must remain compatible. A deliberate breaking 0.x minor requires an exact
`acceptedBreakingMinor` version and a nonempty `reason` in that same file, plus migration notes
in CHANGELOG. The comparison still runs and produces reports; only its incompatibility failure
is waived for that named minor. The waiver cannot apply to a patch or a different minor. Clear it
and advance the baseline after publication. Fixture checks cover both binary removal and a
source-only generic return-type change, and prove a missing baseline fails closed.

## Dependencies and monitoring

Dependabot checks Maven and Actions monthly, with grouped PRs and limits of three and two open
routine PRs respectively. Security updates are independent. Nothing auto-merges. Python scripts
have no third-party runtime packages and therefore no pip updater.

The formatter is intentionally pinned to 1.36.1: Spotless 3.10.3 calls `Style.valueOf`, removed in
1.37. To revisit it, upgrade Spotless and formatter together on a branch, run `spotless:apply`,
inspect the formatting diff, and verify on 21/25 before removing the Dependabot exclusion.
The weekly tool monitor covers pre-commit repositories, pre-commit itself, Trivy and Maven;
[`tools.json`](../../config/quality/tools.json) records pins outside Dependabot's coverage.

CI runs weekly as well as on pushes/PRs. JDK 21/25 remain required; latest GA is advisory and EA
runs weekly/manual, resolved from Adoptium metadata. Failed probes remain failed in the Checks
tab and make the workflow red, with resolved builds and failure details in job summaries. They
are absent from the five required checks, so their failures do not block merging.

The **Maintenance signals** workflow separately checks upstream drift, tool pins and external links:

```sh
python3 -B scripts/monitor.py upstream
python3 -B scripts/monitor.py tools
python3 -B scripts/check_links.py --external
```

[`upstream.json`](../../config/quality/upstream.json) is the single pin manifest. The monitor compares
schema bytes even when `info.version` stays the same and compares official npm/PyPI releases.
Fetch/parse failures fail visibly; they never mean "unchanged". Schema changes retain both hashes
and a bounded structural diff; fetched responses and JSON findings are uploaded for 30 days.
No contract pins are updated automatically.

Inspect failed jobs and their artifacts in Actions. Findings have stable IDs so repeated observations
can be recognized. A deliberately retained upstream pin can be acknowledged in `acknowledgements`
as an object with `id` and a nonempty `reason`; the finding remains visible but no longer fails.
A new schema hash/version produces a new finding. SDK packaging-only changes require human triage.
The workflows have read-only permissions and create no issues or comments, avoiding duplicate
notification threads. GitHub's own failure notifications depend on each maintainer's settings.
Schedules run on the default branch, may be delayed, and public-repository schedules can be disabled
after inactivity. Check Actions periodically; absence of mail is not a health signal.

## Release checks prepared here

`artifact:check-buildplan` runs during ordinary validation. Artifact Plugin 3.6.1 is pinned:
3.7.0 overwrites the plugin-failure flag with its dependency-range result, logging errors without
failing. The deliberate old-JAR-plugin fixture must fail before removing this pin. It checks known plugin reproducibility
problems; it does not prove byte-identical artifacts. The release rehearsal and the candidate
workflow compare two clean builds of the actual bundle with `scripts/release_bundle.py compare`.

`-Prelease-checks` enforces a release version, release dependencies, no dynamic dependency versions
and valid profile IDs. It intentionally rejects the development snapshot. The separate SBOM profile
can be exercised before selecting a release version:

```sh
./mvnw -Psbom -pl jev-core,jev-test,jev-micrometer -am clean verify
```

The aggregate `target/bom.json` includes published libraries and compile/runtime dependencies,
including test helpers but excluding JUnit, examples and benchmark dependencies. It is evidence,
not attached for deployment. Signed bundles, exact-artifact SBOM validation, reproducibility
comparison, isolated consumers and the Portal promotion path are described in
[RELEASING](RELEASING.md#publishing-setup).
