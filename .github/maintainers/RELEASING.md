# Release strategy

The first proposed release is `0.1.0`. The checks below are the release contract. The candidate and
promotion workflows exist and are rehearsed locally, but no deployment has yet been sent to the
Central Portal: the first real upload is an external action that still has to be observed. Do not
treat a normal `verify` build as a publishable release or tag before the prerequisites are complete.

## Before cutting a release branch

- Finish the agreed API, documentation and quality-gate work on `main`; record a green CI commit.
- Verify repository protections, security monitoring and the private reporting channel using the
  owner account. Local scanner results do not establish GitHub settings.
- Run the [local rehearsal](#publishing-setup) on the intended commit: an unpublished signed
  bundle, exact module checks, reproducibility and isolated consumers.
- Confirm Central namespace access and release-environment credentials without placing secrets
  in source, build logs or evidence archives.
- Record who approves the candidate and where the immutable artifacts and qualification results
  will be retained. Keep failed candidate evidence too.

Current local checks, from the repository root:

```sh
./mvnw verify
pre-commit run --all-files
```

See [CONTRIBUTING](../../CONTRIBUTING.md) for tool setup and live-test opt-in. These commands do
not sign or publish artifacts. The [quality-gate guide](QUALITY_GATES.md) documents API comparison,
Error Prone, release dependency checks and the publication-only SBOM profile. Release workflow
commands must be documented and rehearsed when that automation is implemented.

## Branches and versions

Keep `main` open for development. Cut a release branch when the intended feature set is complete
and its starting commit has passed CI; stabilize and qualify the release there.

| Ref | Purpose | First-release example |
|---|---|---|
| `main` | Features and fixes for the next minor release | Move to `0.2.0-SNAPSHOT` once the first published baseline is available |
| `release/0.1` | Stabilization, then maintenance of the 0.1 line | `0.1.0-SNAPSHOT`, then candidate `0.1.0`, then `0.1.1-SNAPSHOT` |
| `v0.1.0` | Immutable, approved release commit | Created only after candidate qualification |

Version the parent and all modules together. Patch releases preserve compatibility; during 0.x,
put intentional breaking changes in a new minor release and document migration steps. Keeping a
release branch does not promise indefinite maintenance of every line.

Release branches accept fixes, dependency/security corrections and release documentation.
Normally fix `main` first and cherry-pick the relevant commit with `-x`. If a release fix lands
first, forward-port it promptly. Keep version bumps branch-specific; do not merge all of `main`
into a release branch. Require reviewed PRs and passing checks on both branches, and protect
stable tags against replacement or deletion.

## Candidate to release

1. **Cut:** create `release/0.1` from a green, recorded commit. Development continues on `main`.
   For the first release, keep its snapshot version until `0.1.0` is published; then set the API
   baseline and bump `main` together. Later cuts can advance `main` immediately because a published
   compatibility baseline already exists. This avoids treating an unpublished candidate as a
   resolvable Maven Central baseline.
2. **Prepare:** on the release branch, set the final Maven version (`0.1.0`), finalize the
   changelog (date the release and add its tag/compare links), the README dependency version and SCM metadata, and set a fixed reproducible-build timestamp.
   Build from a clean committed checkout on a pinned JDK 21 distribution and patch version.
3. **Archive:** dispatch the **Release candidate** workflow on the release branch. The Temurin
   21 build and the Temurin 25 consumer runtime are pinned to exact builds inside the workflow
   (bumped by pull request, so the candidate commit determines its toolchain) and the workflow
   fails unless the installed runtimes report exactly those builds. It builds and signs one
   publication bundle, rebuilds to prove the bundle reproducible, checks its exact contents, runs
   isolated consumers on JDK 21 and 25 and retains `candidate-<version>-<commit>` (bundle, SBOM,
   `manifest.json`, logs) for 90 days.
   The manifest's commit SHA, workflow run ID and SHA-256 hashes identify candidate 1. Copy the
   artifact to durable storage together with the benchmark JAR and source archive. A candidate is
   an internal label, not a public Maven version or stable Git tag.
4. **Qualify:** complete the checks and benchmark protocol below against that candidate.
   Any change to released code, dependencies or packaging creates a new candidate and resets
   qualification. Retain failed candidates and their results for diagnosis.
5. **Tag and validate with Central:** create the annotated `v0.1.0` tag at the exact candidate
   commit, then dispatch **Release promotion** with the tag, the candidate run ID and step
   `validate`. It verifies the tag commit, version and bundle hash against the manifest, re-checks
   the bundle, uploads it as a `USER_MANAGED` deployment, waits for Portal validation and retains
   the deployment ID as artifact `deployment-<tag>`. Validation does not publish. See the
   [Publisher API](https://central.sonatype.org/publish/publish-portal-api/).
6. **Release:** review the evidence, then dispatch **Release promotion** again with step `publish`.
   The release environment's required reviewers approve each dispatch. It resumes from the
   recorded deployment, publishes only a `VALIDATED` deployment and waits for `PUBLISHED`. The
   existing bundle is published without rebuilding it.
7. **Confirm:** wait for Central publication, resolve all three libraries from a fresh Maven
   repository and run the consumer smoke test again. Publish the GitHub release with installation
   instructions, changelog and benchmark summary. Bump the maintenance branch to `0.1.1-SNAPSHOT`.

Using the final version inside an unpublished candidate avoids an RC-to-final rebuild: this SDK
embeds its version in a resource used by request headers, so changing `-rc.1` to the final version
would change the tested artifacts. Keep candidates out of shared snapshot repositories and
developers' normal Maven caches.

If publication fails after tagging, rerun the promotion step; it inspects the recorded deployment
state before acting. See [recovering a failed promotion](#recovering-a-failed-promotion). Do not
replace the tag or silently rebuild. A defect requiring changed artifacts after a stable tag gets
a new version. Published Central components cannot be replaced or removed through the normal
release process. See [Central immutability](https://central.sonatype.org/publish/requirements/immutability/).

## Release checks

- Run the full Maven verification on JDK 21 and 25, repository pre-commit checks, Trivy and CodeQL
  on the candidate SHA. A failed scanner download is an incomplete check, not a pass. Retry
  transient failures with bounded backoff and respect server retry instructions.
- Build the release artifacts once on JDK 21. Separately test those exact JARs on JDK 21 and 25
  using a small consumer outside the reactor and an isolated Maven repository populated from the
  bundle. Exercise core requests, the recording fake and Micrometer integration without live HTTP.
- Inspect the published POMs and dependency graph: no snapshot dependencies, resolvable parent/BOMs,
  correct Java baseline, license, project/developer/SCM metadata, and no benchmark dependencies in
  the libraries. Verify signatures and that sources and Javadoc match the binary artifacts.
  Generate Javadoc from clean output and assert the JARs and their indexes contain no internal
  package pages; incremental generation can retain obsolete HTML despite package exclusions.
- Review the public API, README examples, release notes and known limitations. From the second
  release onward, run `python3 -B scripts/api_compat.py` against the explicitly pinned previous
  release. Update `config/quality/api-policy.json` when publication makes the new baseline available.
- Run the existing opt-in live probes once before the first release using protected credentials
  and retain a sanitized result. This checks current service compatibility; performance tests
  remain local and need no API key. An unavailable service leaves that check explicitly pending.

## Benchmark the candidate before tagging

Run after functional stabilization, on a quiet dedicated machine rather than a shared CI runner.
Use the [existing benchmark commands](../../jev-benchmarks/README.md) and archive the benchmark JAR
built from the same clean candidate checkout. Verify its SDK class/resource entries match the
release JARs; the current study scripts cannot prove build provenance from a supplied JAR alone.

Use one archived benchmark JAR across pinned JDK 21 and 25 runtimes from the same OpenJDK vendor
(Temurin is already used in CI). Record exact runtime builds and hashes. JDK 27 is an optional
additional result; it does not extend the tested support matrix by itself.

| Workload, per JDK | Protocol | Approximate timed cost |
|---|---|---|
| Components | `RunBenchmarks OUTPUT baseline timing core`, then a separate `baseline gc core` | 20 minutes |
| Local HTTP | Five independent `RunLoad OUTPUT baseline representative` passes; three forks per cell | 30 minutes |

Allow about 100 minutes across both JDKs, plus startup and analysis. Each invocation needs a fresh
output directory. Pin the workload to a recorded set of eight physical cores, account for their
SMT siblings, finish builds first, and record host load/frequency plus the runners' heap/GC settings.
Do not run other tests or benchmarks concurrently.

The first release establishes a baseline. Summarize throughput, p50/p95/p99 latency, allocation
and variability separately by workload and runtime. Average fork rates within each HTTP pass,
then report variation across the five pass means; do not treat individual calls as independent
replicates. Label averaged trial percentiles as such, rather than pooled latency percentiles.
Retain all passes, including unfavorable ones.

Correctness/accounting failures block qualification. Investigate unstable results and material
unexpected changes before signing off; no automatic performance percentage gate exists yet.
The `representative` group lacks a same-fork concurrency-1 control, so it cannot establish server
headroom. Do not turn its transport comparison into a claim about maximum SDK capacity.

For a later release comparison, first declare the practical effect of interest and a paired,
interleaved old/new protocol using matched harness, dependencies where applicable, host and runtime.
The current `run-study.py` supports identical-source A/A only; a real A/B requires reviewed tooling
changes. Historical results from different dependency or runtime builds are context, not a matched
baseline. Use immediate transport or windowed JFR only when a specific result needs explaining.

Keep raw runs out of Git. Attach a compressed evidence archive to the eventual GitHub release,
including raw results, manifests, source/JAR hashes and host/runtime details. Put a concise results
table and limitations in the release notes. CI artifacts can hold candidates, but retain accepted
release evidence durably rather than relying on their expiry window.

## Publishing setup

**Build profile.** `-Prelease` attaches sources and Javadoc, signs every file with the Bouncy
Castle signer (`MAVEN_GPG_KEY` holds the armored secret key, `MAVEN_GPG_PASSPHRASE` its
passphrase; nothing is read from `settings.xml` or gpg-agent) and runs the pinned
`central-publishing-maven-plugin` at the `deploy` phase. Upload is off unless
`-Dcentral.skipUpload=false` is passed, and `autoPublish` is never enabled. The bundle contains
exactly `jev-parent` (POM) and `jev-core`, `jev-test`, `jev-micrometer` (JAR, sources, Javadoc,
POM), each with `.asc`, `.md5`, `.sha1`, `.sha256` and `.sha512`. `jev-examples` and
`jev-benchmarks` set `skipPublishing`, `gpg.skip` and `maven.source.skip` and are also listed in
`excludeArtifacts`; `scripts/release_bundle.py check` fails on any other content.

**External prerequisites** (none are proven by this repository):

1. A Central Portal account with the `net.codefinch` namespace verified by a DNS `TXT` record on
   the `codefinch.net` apex. Sub-namespaces such as `net.codefinch.jev` are covered by it. See
   [namespace registration](https://central.sonatype.org/register/namespace/).
2. A Portal user token, stored as repository environment secrets `CENTRAL_USERNAME` and
   `CENTRAL_TOKEN`. See [artifact requirements](https://central.sonatype.org/publish/requirements/).
3. A release signing key whose public key is on a key server; its armored secret key and
   passphrase stored as `MAVEN_GPG_KEY` and `MAVEN_GPG_PASSPHRASE`.
4. A GitHub environment named `release` holding those four secrets, with required reviewers, so
   every candidate build and every promotion step waits for approval.
5. A tag ruleset protecting `v*` from updates and deletion.

**Local rehearsal** (throwaway key; never the release key):

```sh
export GNUPGHOME="$(mktemp -d)"
gpg --batch --passphrase rehearsal --quick-generate-key "rehearsal <rehearsal@invalid>" ed25519 sign 1d
export MAVEN_GPG_KEY="$(gpg --batch --pinentry-mode loopback --passphrase rehearsal --armor --export-secret-keys)"
export MAVEN_GPG_PASSPHRASE=rehearsal
python3 -B scripts/release_bundle.py rehearse --version 0.1.0 --runtime /path/to/jdk-21 --keep
```

The rehearsal refuses a dirty tree, builds the committed `HEAD` twice in a clean worktree at the
final version with `-Prelease,release-checks,sbom`, compares the two bundles (signatures are the
only permitted difference), checks the first bundle's exact contents, POM metadata, embedded
version, Javadoc (no `internal` pages) and SBOM, writes `manifest.json`, then builds a consumer
outside the reactor from an isolated repository seeded only from the bundle and runs it on the
class path and the module path for each `--runtime`. The two builds use a fresh temporary Maven
repository, never `~/.m2`, because `deploy` installs the unpublished final version locally and a
later build on the same machine must not resolve it as if it were released; pass
`--repository DIR` to reuse a dedicated cache across rehearsals. Individual steps are available as
`check`, `compare` and `consumer` subcommands; `python3 -B -m unittest discover -s scripts` runs
their deliberate-failure tests. On this machine Maven builds on the default JDK; CI builds the
real candidate on the pinned Temurin 21.

**Workflows.** `release-candidate.yml` and `release-promote.yml` are `workflow_dispatch` only,
run in the `release` environment, keep read-only repository permissions, run every step under
`bash` with `pipefail` so a failure behind `| tee` fails the step, and serialize per branch and
per tag. The candidate workflow removes the installed final version from the runner's Maven cache
before that cache is saved. The promotion workflow drives the Portal through
`scripts/central_publish.py`, which uploads with `publishingType=USER_MANAGED`, polls `status`
with a bounded wait, publishes only from `VALIDATED`, treats `PUBLISHING`/`PUBLISHED` as already
done and refuses to drop a published deployment. The record it retains binds the deployment ID
to the bundle hash, the Portal URL, the deployment name and the candidate identity (run ID,
commit, version); a rerun with a matching record resumes without uploading again, and a rerun
whose bundle or candidate differs from the record stops before contacting the Portal.

Snapshot publication from `main`, public RC artifacts and a general performance regression service
can wait until there is a concrete need.

## Recovering a failed promotion

Every recovery starts by reading the recorded state; nothing is rebuilt. The `deployment-<tag>`
artifact holds the deployment ID, and the summary of each promotion run prints it.

| Symptom | Action |
|---|---|
| Candidate verification fails (commit, version or hash mismatch) | The tag or run ID is wrong. Fix the dispatch inputs; if the tag points at a different commit than the candidate, the candidate is not for this release. |
| Upload fails before a deployment ID is recorded | Rerun `validate`; nothing was recorded, so it uploads again. Check the Portal UI for a stray deployment first and drop it there if one exists. |
| Deployment is `FAILED` | Read the `errors` in the run log. Drop it (`central_publish.py drop` with the restored record, or the Portal UI), fix the cause on the release branch, and build a **new candidate**; a patched bundle is never uploaded under the old identity. Before promoting the new candidate, delete every `deployment-<tag>` artifact (Actions UI, or `gh api -X DELETE repos/<owner>/<repo>/actions/artifacts/<id>` with a maintainer token; the workflow token cannot); otherwise `validate` refuses the new bundle as a different candidate. |
| `validate` stops with "belongs to a different candidate" | The restored record describes another bundle, candidate run or Portal. Do not force it. If the recorded deployment is the one to release, dispatch with its candidate run ID and tag. If it is not, drop it and delete the `deployment-<tag>` artifacts as above, then rerun `validate`. |
| Validation is still pending when the wait expires | Rerun `validate`; it resumes polling the recorded deployment. |
| `publish` fails or times out | Rerun `publish`; it inspects the state first and only calls publish when the deployment is `VALIDATED`, otherwise it waits for `PUBLISHED`. |
| The deployment artifact expired (90 days) | Find the deployment ID in the Portal's Deployments page and recreate `deployment/deployment.json` locally with the `deploymentId`, the `bundleSha256` from the candidate manifest, `baseUrl` (`https://central.sonatype.com`), the deployment `name` shown on the Portal and the `candidate` string (`run <id> commit <sha> version <v>`), then run `central_publish.py status` from a trusted machine with the token in the environment. |
| A published component is wrong | It cannot be replaced. Fix on the release branch, bump the patch version, and run the whole candidate and promotion sequence again. |

Operations against the Portal need `CENTRAL_USERNAME`/`CENTRAL_TOKEN` in the environment;
the script reads nothing else and prints no credentials.
