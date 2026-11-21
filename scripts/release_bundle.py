"""Inspect, compare and consume a Central publication bundle without uploading it.

Subcommands:
  check     assert the exact bundle contents and write a candidate manifest
  compare   compare two bundles built from the same commit (signatures excluded)
  consumer  build and run an isolated consumer against the bundle's actual JARs
  rehearse  do all of the above locally from a clean worktree at a final version
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUP = 'net.codefinch.jev'
GROUP_PATH = GROUP.replace('.', '/')
PARENT = 'jev-parent'
LIBRARIES = ('jev-core', 'jev-test', 'jev-micrometer')
UNPUBLISHED = ('jev-examples', 'jev-benchmarks')
CHECKSUMS = ('.md5', '.sha1', '.sha256', '.sha512')
VERSION_PATTERN = re.compile(r'\d+\.\d+\.\d+')
MAVEN_FLAGS = ['--batch-mode', '--no-transfer-progress']


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def expected_files(version):
    """Every path the bundle must contain, and nothing else."""
    files = set()

    def component(artifact, classifiers):
        base = f'{GROUP_PATH}/{artifact}/{version}/{artifact}-{version}'
        for suffix in classifiers:
            files.add(base + suffix)
            files.add(base + suffix + '.asc')
            for checksum in CHECKSUMS:
                files.add(base + suffix + checksum)

    component(PARENT, ['.pom'])
    for artifact in LIBRARIES:
        component(artifact, ['.pom', '.jar', '-sources.jar', '-javadoc.jar'])
    return files


def check_bundle(bundle, version):
    """Return {path: sha256} for the bundle, raising on any content deviation."""
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f'Release version must be major.minor.patch, got {version!r}')
    problems = []
    hashes = {}
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        expected = expected_files(version)
        for missing in sorted(expected - names):
            problems.append(f'missing {missing}')
        for extra in sorted(names - expected):
            problems.append(f'unexpected {extra}')
        for name in sorted(names & expected):
            data = archive.read(name)
            hashes[name] = sha256(data)
            if name.endswith('.asc'):
                if b'BEGIN PGP SIGNATURE' not in data:
                    problems.append(f'not a PGP signature: {name}')
            elif name.endswith(CHECKSUMS):
                subject = name[:name.rindex('.')]
                algorithm = name[name.rindex('.') + 1:]
                if subject in names:
                    actual = hashlib.new(algorithm, archive.read(subject)).hexdigest()
                    if data.decode('ascii', 'replace').strip().lower() != actual:
                        problems.append(f'checksum mismatch: {name}')
            elif name.endswith('.pom'):
                problems.extend(check_pom(name, data, version))
            elif name.endswith('-javadoc.jar'):
                problems.extend(check_javadoc(name, data))
            elif name.endswith('.jar'):
                problems.extend(check_jar(name, data, version))
    if problems:
        raise ValueError('Bundle check failed:\n  ' + '\n  '.join(problems))
    return hashes


def check_pom(name, data, version):
    problems = []
    if b'SNAPSHOT' in data:
        problems.append(f'snapshot reference in {name}')
    root = ET.fromstring(data)
    ns = '{http://maven.apache.org/POM/4.0.0}'
    if root.findtext(ns + 'version') != version and root.findtext(f'{ns}parent/{ns}version') != version:
        problems.append(f'{name} does not declare version {version}')
    for field in ('name', 'description', 'url'):
        if not (root.findtext(ns + field) or '').strip():
            problems.append(f'{name} lacks <{field}>')
    if root.find(f'{ns}parent') is None:
        for section in ('licenses', 'developers', 'scm'):
            if root.find(ns + section) is None:
                problems.append(f'{name} lacks <{section}>')
        for element in root.iter(ns + 'repository'):
            problems.append(f'{name} declares a <repository>')
    for dependency in root.iter(ns + 'dependency'):
        artifact = dependency.findtext(ns + 'artifactId') or ''
        if artifact in UNPUBLISHED:
            problems.append(f'{name} depends on unpublished {artifact}')
    return problems


def check_javadoc(name, data):
    with zipfile.ZipFile(_memory(data)) as jar:
        entries = jar.namelist()
    if not any(entry.endswith('index.html') for entry in entries):
        return [f'{name} has no index.html']
    internal = [entry for entry in entries if '/internal/' in entry or entry.endswith('internal/')]
    return [f'{name} documents internal package: {entry}' for entry in internal[:3]]


def check_jar(name, data, version):
    problems = []
    with zipfile.ZipFile(_memory(data)) as jar:
        manifest = jar.read('META-INF/MANIFEST.MF').decode('utf-8', 'replace')
        if name.endswith('-sources.jar'):
            if not any(entry.endswith('.java') for entry in jar.namelist()):
                problems.append(f'{name} contains no sources')
            return problems
        if 'Automatic-Module-Name: ' + GROUP not in manifest:
            problems.append(f'{name} lacks an Automatic-Module-Name under {GROUP}')
        if 'jev-core' in name:
            if 'net/codefinch/jev/version.properties' not in jar.namelist():
                return problems + [f'{name} lacks version.properties']
            properties = jar.read('net/codefinch/jev/version.properties').decode()
            embedded = dict(line.split('=', 1) for line in properties.splitlines() if '=' in line)
            if embedded.get('version', '').strip() != version:
                problems.append(f'{name} embeds a different version: {properties.strip()}')
    return problems


def _memory(data):
    return io.BytesIO(data)


def check_sbom(path, version):
    bom = json.loads(Path(path).read_text())
    component = bom['metadata']['component']
    if (component['name'], component['version']) != (PARENT, version):
        raise ValueError(f'SBOM describes {component["name"]} {component["version"]}, not {PARENT} {version}')
    names = {(c['group'], c['name']) for c in bom['components']}
    for artifact in LIBRARIES:
        if (GROUP, artifact) not in names:
            raise ValueError(f'SBOM lacks {artifact}')
    for artifact in UNPUBLISHED:
        if (GROUP, artifact) in names:
            raise ValueError(f'SBOM includes unpublished {artifact}')
    if any(name.startswith('junit') for _, name in names):
        raise ValueError('SBOM includes a test-scope dependency')
    return sorted(f'{group}:{name}' for group, name in names)


def manifest(bundle, version, hashes, sbom=None, extra=None):
    """Candidate identity: commit, runtime, hashes. Signatures are included as recorded files."""
    data = Path(bundle).read_bytes()
    java = subprocess.run([str(java_executable(None)), '-version'], capture_output=True, text=True)
    result = {
        'version': version,
        'bundle': {'name': Path(bundle).name, 'sha256': sha256(data), 'bytes': len(data)},
        'files': hashes,
        'commit': os.environ.get('GITHUB_SHA') or git(['rev-parse', 'HEAD']),
        'workflowRun': os.environ.get('GITHUB_RUN_ID'),
        'java': (java.stderr or java.stdout).strip().splitlines(),
    }
    if sbom is not None:
        result['sbom'] = {'sha256': sha256(Path(sbom).read_bytes()), 'components': check_sbom(sbom, version)}
    result.update(extra or {})
    return result


def git(args, cwd=ROOT):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def compare_bundles(first, second):
    """Content that must be reproducible; signatures and their checksums are not."""
    def content(bundle):
        with zipfile.ZipFile(bundle) as archive:
            return {name: sha256(archive.read(name)) for name in archive.namelist()
                    if not name.endswith('.asc')}
    a, b = content(first), content(second)
    differences = sorted(set(a) ^ set(b)) + sorted(name for name in a.keys() & b.keys() if a[name] != b[name])
    return differences


def java_executable(home):
    if home is None:
        home = os.environ.get('JAVA_HOME')
    if home is None:
        found = shutil.which('java')
        if found is None:
            raise ValueError('No java on PATH; set JAVA_HOME')
        return Path(found)
    java = Path(home) / 'bin' / ('java.exe' if os.name == 'nt' else 'java')
    if not java.is_file():
        raise ValueError(f'No java runtime at {java}')
    return java


CONSUMER_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>consumer</groupId>
  <artifactId>consumer</artifactId>
  <version>1</version>
  <properties>
    <maven.compiler.release>21</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency><groupId>{group}</groupId><artifactId>jev-core</artifactId><version>{version}</version></dependency>
    <dependency><groupId>{group}</groupId><artifactId>jev-test</artifactId><version>{version}</version></dependency>
    <dependency><groupId>{group}</groupId><artifactId>jev-micrometer</artifactId><version>{version}</version></dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-compiler-plugin</artifactId>
        <version>{compiler}</version>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-dependency-plugin</artifactId>
        <version>{dependency}</version>
        <executions>
          <execution>
            <id>runtime</id>
            <goals><goal>copy-dependencies</goal></goals>
            <phase>package</phase>
            <configuration>
              <includeScope>runtime</includeScope>
              <outputDirectory>${{project.build.directory}}/runtime</outputDirectory>
            </configuration>
          </execution>
        </executions>
      </plugin>
    </plugins>
  </build>
</project>
"""

CONSUMER_MODULE = """module consumer {
  requires net.codefinch.jev;
  requires net.codefinch.jev.test;
  requires net.codefinch.jev.micrometer;
  requires micrometer.core;
  requires jdk.httpserver;
}
"""

CONSUMER_MAIN = r'''package consumer;

import com.sun.net.httpserver.HttpServer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import net.codefinch.jev.JevClient;
import net.codefinch.jev.micrometer.JevMetrics;
import net.codefinch.jev.model.ChoiceCriteria;
import net.codefinch.jev.model.NoulCriteria;
import net.codefinch.jev.model.Questions;
import net.codefinch.jev.model.State;
import net.codefinch.jev.model.SystemOneResponse;
import net.codefinch.jev.test.RecordingJevClient;
import net.codefinch.jev.test.ScriptedAnswers;

/** Exercises the published JARs: HTTP client, recording fake and Micrometer, no live API. */
public final class Consumer {
  private static final String RESPONSE =
      """
      {"model":"jev-1.13.0","answers":{
        "refund_requested":{"type":"noul","noul":0.93},
        "department":{"type":"choice","choice":"billing",
          "probabilities":{"billing":0.84,"support":0.11,"other":0.05},"confidence":0.6},
        "severity":{"type":"score","score":1.3,
          "legend":{"0":"Cosmetic","1":"Degraded","2":"Blocking"},
          "probabilities":{"0":0.0,"1":0.7,"2":0.3},"confidence":0.54}},
       "usage":{"input_tokens":210,"output_tokens":31}}
      """;

  private static final Questions QUESTIONS =
      Questions.builder()
          .noul("refund_requested", "Money back?", NoulCriteria.of("Refund wanted", "No refund"))
          .choice("department", "Which team?",
              ChoiceCriteria.builder().option("billing", "Invoices").option("support").build())
          .score("severity", "How bad?", List.of("Cosmetic", "Degraded", "Blocking"))
          .build();

  public static void main(String[] args) throws Exception {
    String expectedVersion = args[0];
    AtomicReference<String> sdkHeader = new AtomicReference<>();
    HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext("/v1/systemone", exchange -> {
      sdkHeader.set(exchange.getRequestHeaders().getFirst("X-TypeSafe-SDK"));
      byte[] body = RESPONSE.getBytes(StandardCharsets.UTF_8);
      exchange.getResponseHeaders().add("Content-Type", "application/json");
      exchange.sendResponseHeaders(200, body.length);
      try (var out = exchange.getResponseBody()) {
        out.write(body);
      }
    });
    server.start();
    try {
      SimpleMeterRegistry registry = new SimpleMeterRegistry();
      URI base = URI.create("http://127.0.0.1:" + server.getAddress().getPort());
      try (JevClient client = JevClient.builder().apiKey("consumer-test").baseUrl(base)
          .observer(JevMetrics.of(registry)).build()) {
        SystemOneResponse response =
            client.systemOne(State.of(Map.of("ticket", "I want my money back")), QUESTIONS);
        check(response.answers().noul("refund_requested").noul() == 0.93, "noul answer");
        check("billing".equals(response.answers().choice("department").choice()), "choice answer");
        check(response.answers().score("severity").score() == 1.3, "score answer");
        check(response.model().equals("jev-1.13.0"), "model");
      }
      String header = sdkHeader.get();
      check(header != null && header.endsWith("/" + expectedVersion), "X-TypeSafe-SDK header " + header);
      // Observer events are delivered off the calling thread; allow a bounded wait.
      long deadline = System.nanoTime() + 10_000_000_000L;
      while (callCount(registry) < 1 && System.nanoTime() < deadline) {
        Thread.sleep(20);
      }
      check(callCount(registry) == 1, "jev.call timer recorded once");
      check(registry.find("jev.tokens").counters().stream().mapToDouble(c -> c.count()).sum() == 241,
          "jev.tokens counted usage");

      RecordingJevClient fake = new RecordingJevClient();
      fake.enqueue(ScriptedAnswers.neutral(QUESTIONS).noul("refund_requested", 0.1));
      SystemOneResponse scripted = fake.systemOne(State.of("quiet"), QUESTIONS);
      check(scripted.answers().noul("refund_requested").noul() == 0.1, "scripted answer");
      check(fake.calls().size() == 1, "recorded call");
      System.out.println("consumer ok: " + header + " on " + Runtime.version()
          + (Consumer.class.getModule().isNamed() ? " (module path)" : " (class path)"));
    } finally {
      server.stop(0);
    }
  }

  private static long callCount(SimpleMeterRegistry registry) {
    return registry.find("jev.call").timers().stream().mapToLong(t -> t.count()).sum();
  }

  private static void check(boolean condition, String what) {
    if (!condition) {
      throw new AssertionError("Consumer check failed: " + what);
    }
  }
}
'''


def plugin_versions():
    """Reuse the reactor's pinned compiler plugin; the dependency plugin is pinned here."""
    root = ET.parse(ROOT / 'pom.xml').getroot()
    ns = '{http://maven.apache.org/POM/4.0.0}'
    versions = {}
    for plugin in root.iter(ns + 'plugin'):
        version = plugin.findtext(ns + 'version')
        if version:
            versions.setdefault(plugin.findtext(ns + 'artifactId'), version)
    return {'compiler': versions['maven-compiler-plugin'], 'dependency': '3.11.0'}


def run_consumer(bundle, version, runtimes, workdir, mvnw=None):
    """Populate an isolated repository from the bundle and build a consumer offline against it."""
    workdir = Path(workdir)
    repo = workdir / 'repository'
    project = workdir / 'consumer'
    src = project / 'src/main/java'
    (src / 'consumer').mkdir(parents=True)
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(repo)
    (project / 'pom.xml').write_text(CONSUMER_POM.format(group=GROUP, version=version, **plugin_versions()))
    (src / 'consumer/Consumer.java').write_text(CONSUMER_MAIN)
    (src / 'module-info.java').write_text(CONSUMER_MODULE)
    mvn = [str(mvnw or ROOT / ('mvnw.cmd' if os.name == 'nt' else 'mvnw')), *MAVEN_FLAGS,
           f'-Dmaven.repo.local={repo}']
    # Third-party dependencies and plugins come from Central; the SDK must resolve from the bundle only.
    subprocess.run([*mvn, 'package'], cwd=project, check=True)
    for artifact in LIBRARIES:
        pom = repo / GROUP_PATH / artifact / version / f'{artifact}-{version}.pom'
        remote = pom.with_name('_remote.repositories')
        if remote.exists() and b'central' in remote.read_bytes():
            raise ValueError(f'{artifact} was downloaded rather than taken from the bundle')
    jars = sorted((project / 'target/runtime').glob('*.jar'))
    consumer = project / 'target/consumer-1.jar'
    outputs = []
    for runtime in runtimes:
        java = str(java_executable(runtime))
        classpath = os.pathsep.join([str(consumer), *map(str, jars)])
        for command in ([java, '-cp', classpath, 'consumer.Consumer', version],
                        # The SDK JARs are automatic modules: nothing declares their Jackson, Micrometer
                        # or java.net.http requirements, so a module-path consumer adds them itself.
                        [java, '-p', os.pathsep.join([str(consumer), str(project / 'target/runtime')]),
                         '--add-modules', 'java.net.http,ALL-MODULE-PATH',
                         '-m', 'consumer/consumer.Consumer', version]):
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise AssertionError(f'{command}\n{result.stdout}{result.stderr}')
            outputs.append(result.stdout.strip())
    return outputs


def rehearse(version, runtimes, keep, repository=None):
    """Clean worktree at the final version, two signed builds, checks, comparison, consumers."""
    for name in ('MAVEN_GPG_KEY', 'MAVEN_GPG_PASSPHRASE'):
        if not os.environ.get(name):
            raise ValueError(f'{name} must be set (use a throwaway key for a local rehearsal)')
    if git(['status', '--porcelain']):
        raise ValueError('Commit or stash changes first: a rehearsal builds the committed tree only')
    temp = Path(tempfile.mkdtemp(prefix='jev-rehearsal-'))
    worktree = temp / 'worktree'
    # `deploy` installs the unpublished final version into the local repository, so the builds never
    # use ~/.m2: later builds on this machine must not resolve a rehearsal as if it were released.
    repository = Path(repository).resolve() if repository else temp / 'repository'
    try:
        git(['worktree', 'add', '--detach', str(worktree), 'HEAD'])
        for pom in [worktree / 'pom.xml', *worktree.glob('jev-*/pom.xml')]:
            pom.write_text(pom.read_text().replace('<version>0.1.0-SNAPSHOT</version>', f'<version>{version}</version>'))
        mvn = [str(worktree / 'mvnw'), *MAVEN_FLAGS, f'-Dmaven.repo.local={repository}',
               '-Prelease,release-checks,sbom', '-DskipTests']
        bundles = []
        for attempt in (1, 2):
            subprocess.run([*mvn, 'clean', 'deploy'], cwd=worktree, check=True)
            bundle = temp / f'bundle-{attempt}.zip'
            shutil.copyfile(worktree / 'target/central-publishing/central-bundle.zip', bundle)
            bundles.append(bundle)
        shutil.copyfile(worktree / 'target/bom.json', temp / 'bom.json')
        hashes = check_bundle(bundles[0], version)
        differences = compare_bundles(*bundles)
        if differences:
            raise ValueError('Builds are not reproducible:\n  ' + '\n  '.join(differences))
        result = manifest(bundles[0], version, hashes, temp / 'bom.json',
                          {'commit': git(['rev-parse', 'HEAD'], worktree), 'rehearsal': True})
        (temp / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
        outputs = run_consumer(bundles[0], version, runtimes, temp / 'consumer')
        print('\n'.join(outputs))
        print(f'Rehearsal passed: {len(hashes)} files, bundle {result["bundle"]["sha256"]}')
        print(f'Evidence in {temp}' if keep else 'Evidence discarded (use --keep to retain)')
    finally:
        subprocess.run(['git', 'worktree', 'remove', '--force', str(worktree)], cwd=ROOT,
                       capture_output=True)
        if not keep:
            shutil.rmtree(temp, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='assert bundle contents and write a manifest')
    check.add_argument('bundle')
    check.add_argument('--version', required=True)
    check.add_argument('--sbom')
    check.add_argument('--manifest', help='write the candidate manifest JSON here')
    compare = commands.add_parser('compare', help='compare two bundles, ignoring signatures')
    compare.add_argument('first')
    compare.add_argument('second')
    consumer = commands.add_parser('consumer', help='isolated consumer build and run')
    consumer.add_argument('bundle')
    consumer.add_argument('--version', required=True)
    consumer.add_argument('--runtime', action='append', default=[], metavar='JAVA_HOME',
                          help='runtime to execute the consumer on (repeatable; default: current)')
    consumer.add_argument('--workdir', help='keep the isolated repository and project here')
    rehearsal = commands.add_parser('rehearse', help='local end-to-end rehearsal from a clean worktree')
    rehearsal.add_argument('--version', default='0.1.0')
    rehearsal.add_argument('--runtime', action='append', default=[], metavar='JAVA_HOME')
    rehearsal.add_argument('--keep', action='store_true', help='retain the evidence directory')
    rehearsal.add_argument('--repository', metavar='DIR',
                           help='Maven repository for the rehearsal builds (default: fresh and temporary; never ~/.m2)')
    args = parser.parse_args(argv)

    if args.command == 'check':
        hashes = check_bundle(args.bundle, args.version)
        result = manifest(args.bundle, args.version, hashes, args.sbom)
        if args.manifest:
            Path(args.manifest).write_text(json.dumps(result, indent=2) + '\n')
        print(f'Bundle ok: {len(hashes)} files, sha256 {result["bundle"]["sha256"]}')
    elif args.command == 'compare':
        differences = compare_bundles(args.first, args.second)
        if differences:
            raise SystemExit('Bundles differ:\n  ' + '\n  '.join(differences))
        print('Bundles are identical apart from signatures')
    elif args.command == 'consumer':
        runtimes = args.runtime or [None]
        if args.workdir:
            Path(args.workdir).mkdir(parents=True, exist_ok=True)
            print('\n'.join(run_consumer(args.bundle, args.version, runtimes, args.workdir)))
        else:
            with tempfile.TemporaryDirectory(prefix='jev-consumer-') as workdir:
                print('\n'.join(run_consumer(args.bundle, args.version, runtimes, workdir)))
    else:
        rehearse(args.version, args.runtime or [None], args.keep, args.repository)


if __name__ == '__main__':
    main()
