"""Deliberate failures for the publication bundle checks and the Portal promotion client."""
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import central_publish
from central_publish import PortalError, wait_for
from release_bundle import (
    GROUP_PATH,
    LIBRARIES,
    PARENT,
    check_bundle,
    check_sbom,
    compare_bundles,
    expected_files,
)

VERSION = '0.1.0'
POM_NS = 'xmlns="http://maven.apache.org/POM/4.0.0"'


def pom(artifact, version=VERSION, parent=True, url=True):
    head = (f'<parent><artifactId>{PARENT}</artifactId><version>{version}</version></parent>' if parent
            else '<licenses><license/></licenses><developers><developer/></developers><scm><url>x</url></scm>'
                 f'<version>{version}</version>')
    return (f'<project {POM_NS}>{head}<artifactId>{artifact}</artifactId><name>{artifact}</name>'
            f'<description>d</description>{"<url>https://example.invalid</url>" if url else ""}</project>').encode()


def jar(entries, manifest='Manifest-Version: 1.0\nAutomatic-Module-Name: net.codefinch.jev.x\n'):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('META-INF/MANIFEST.MF', manifest)
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def bundle(path, version=VERSION, mutate=None):
    """A structurally valid bundle; `mutate` edits the {path: bytes} map before zipping."""
    files = {}
    for name in expected_files(version):
        if name.endswith(('.asc', '.md5', '.sha1', '.sha256', '.sha512')):
            continue
        artifact = name.split('/')[-2 - 1]
        if name.endswith('.pom'):
            files[name] = pom(artifact, version, parent=artifact != PARENT)
        elif name.endswith('-sources.jar'):
            files[name] = jar({'net/codefinch/jev/A.java': b'class A {}'})
        elif name.endswith('-javadoc.jar'):
            files[name] = jar({'index.html': b'<html/>', 'net/codefinch/jev/A.html': b'<html/>'})
        else:
            files[name] = jar({'net/codefinch/jev/version.properties': f'version={version}\n'.encode()})
    if mutate:
        mutate(files)
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
            if not name.endswith(('.asc', '.md5', '.sha1', '.sha256', '.sha512')):
                archive.writestr(name + '.asc', b'-----BEGIN PGP SIGNATURE-----\nx\n-----END PGP SIGNATURE-----\n')
                for algorithm in ('md5', 'sha1', 'sha256', 'sha512'):
                    archive.writestr(f'{name}.{algorithm}', hashlib.new(algorithm, data).hexdigest())
    return path


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_exact_contents_pass(self):
        hashes = check_bundle(bundle(self.root / 'ok.zip'), VERSION)
        self.assertEqual(set(hashes), expected_files(VERSION))
        self.assertEqual(len(hashes), (1 + 3 * 4) * 6)

    def test_each_deviation_is_named(self):
        core = f'{GROUP_PATH}/jev-core/{VERSION}/jev-core-{VERSION}'
        cases = {
            'missing': lambda f: f.pop(f'{core}-javadoc.jar'),
            'unexpected': lambda f: f.__setitem__(f'{GROUP_PATH}/jev-examples/{VERSION}/x.jar', b''),
            'snapshot': lambda f: f.__setitem__(f'{core}.pom', pom('jev-core', '0.1.0-SNAPSHOT')),
            'lacks <url>': lambda f: f.__setitem__(f'{core}.pom', pom('jev-core', url=False)),
            'internal package': lambda f: f.__setitem__(
                f'{core}-javadoc.jar', jar({'index.html': b'', 'net/codefinch/jev/internal/X.html': b''})),
            'Automatic-Module-Name': lambda f: f.__setitem__(f'{core}.jar', jar({}, manifest='Manifest-Version: 1.0\n')),
            'different version': lambda f: f.__setitem__(
                f'{core}.jar', jar({'net/codefinch/jev/version.properties': b'version=0.1.0-SNAPSHOT'})),
            'unpublished jev-benchmarks': lambda f: f.__setitem__(
                f'{core}.pom', pom('jev-core').replace(b'</project>', b'<dependencies><dependency>'
                                                       b'<artifactId>jev-benchmarks</artifactId></dependency>'
                                                       b'</dependencies></project>')),
        }
        for marker, mutate in cases.items():
            with self.subTest(marker):
                with self.assertRaisesRegex(ValueError, marker):
                    check_bundle(bundle(self.root / f'{marker}.zip', mutate=mutate), VERSION)

    def test_checksum_and_signature_content_are_verified(self):
        path = bundle(self.root / 'bad.zip')
        with zipfile.ZipFile(path) as archive:
            items = {name: archive.read(name) for name in archive.namelist()}
        parent = f'{GROUP_PATH}/{PARENT}/{VERSION}/{PARENT}-{VERSION}.pom'
        items[parent + '.sha1'] = b'0' * 40
        items[parent + '.asc'] = b'not a signature'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in items.items():
                archive.writestr(name, data)
        with self.assertRaises(ValueError) as failure:
            check_bundle(path, VERSION)
        self.assertIn('checksum mismatch', str(failure.exception))
        self.assertIn('not a PGP signature', str(failure.exception))

    def test_version_must_be_final(self):
        with self.assertRaises(ValueError):
            check_bundle(bundle(self.root / 'x.zip'), '0.1.0-SNAPSHOT')

    def test_compare_ignores_signatures_only(self):
        first = bundle(self.root / 'a.zip')
        second = bundle(self.root / 'b.zip')
        self.assertEqual(compare_bundles(first, second), [])
        core = f'{GROUP_PATH}/jev-core/{VERSION}/jev-core-{VERSION}.jar'
        third = bundle(self.root / 'c.zip', mutate=lambda f: f.__setitem__(core, jar({'x': b'y'})))
        differences = compare_bundles(first, third)
        self.assertIn(core, differences)
        self.assertFalse(any(name.endswith('.asc') for name in differences))

    def test_sbom_lists_exactly_the_libraries(self):
        def write(components, name=PARENT):
            path = self.root / 'bom.json'
            path.write_text(json.dumps({'metadata': {'component': {'name': name, 'version': VERSION}},
                                        'components': [{'group': g, 'name': n} for g, n in components]}))
            return path
        libraries = [('net.codefinch.jev', lib) for lib in LIBRARIES]
        self.assertEqual(len(check_sbom(write(libraries + [('x', 'y')]), VERSION)), 4)
        with self.assertRaisesRegex(ValueError, 'lacks jev-test'):
            check_sbom(write(libraries[:1]), VERSION)
        with self.assertRaisesRegex(ValueError, 'unpublished'):
            check_sbom(write(libraries + [('net.codefinch.jev', 'jev-examples')]), VERSION)
        with self.assertRaisesRegex(ValueError, 'test-scope'):
            check_sbom(write(libraries + [('org.junit.jupiter', 'junit-jupiter')]), VERSION)
        with self.assertRaisesRegex(ValueError, 'describes'):
            check_sbom(write(libraries, name='jev-core'), VERSION)


class PortalTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {'CENTRAL_USERNAME': 'user', 'CENTRAL_TOKEN': 'token'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.record = Path(self.temp.name) / 'deployment.json'
        self.bundle = Path(self.temp.name) / 'bundle.zip'
        self.bundle.write_bytes(b'bundle')
        self.digest = hashlib.sha256(b'bundle').hexdigest()

    def test_auth_is_base64_bearer_and_requires_both_values(self):
        self.assertEqual(central_publish.auth_header(), 'Bearer dXNlcjp0b2tlbg==')
        with patch.dict(os.environ, {'CENTRAL_TOKEN': ''}):
            with self.assertRaises(ValueError):
                central_publish.auth_header()

    def test_wait_bounded_and_terminal(self):
        states = iter(['PENDING', 'VALIDATING', 'VALIDATED'])
        with patch('central_publish.status', side_effect=lambda b, i: {'deploymentState': next(states)}):
            report = wait_for('b', 'id', {'VALIDATED'}, timeout=100, sleep=lambda s: None, clock=lambda: 0)
        self.assertEqual(report['deploymentState'], 'VALIDATED')
        with patch('central_publish.status', return_value={'deploymentState': 'FAILED', 'errors': {'a': ['b']}}):
            self.assertEqual(wait_for('b', 'id', {'PUBLISHED'}, 100, sleep=lambda s: None)['deploymentState'],
                             'FAILED')
        ticks = iter([0, 50, 101, 102])
        with patch('central_publish.status', return_value={'deploymentState': 'PENDING'}):
            with self.assertRaisesRegex(PortalError, 'still PENDING'):
                wait_for('b', 'id', {'VALIDATED'}, 100, sleep=lambda s: None, clock=lambda: next(ticks))

    def test_malformed_and_unknown_status_fail(self):
        for payload in (b'not json', b'{}', b'{"deploymentState":"WEIRD"}'):
            with patch('central_publish.request', return_value=(200, payload)):
                with self.assertRaises(PortalError):
                    central_publish.status('b', 'id')

    def test_upload_records_id_then_never_uploads_twice(self):
        calls = []

        states = iter(['VALIDATED', 'PENDING', 'VALIDATING', 'VALIDATED'])

        def fake_request(method, url, body=None, content_type=None, attempts=3):
            calls.append((method, url.split('/api/v1/publisher/')[1]))
            if '/upload' in url:
                self.assertTrue(url.endswith('upload?publishingType=USER_MANAGED&name=jev-parent%200.1.0%20abc'))
                self.assertIn(b'name="bundle"', body)
                self.assertTrue(content_type.startswith('multipart/form-data'))
                return 201, b'"dep-1"'
            return 200, json.dumps({'deploymentId': 'dep-1', 'deploymentState': next(states)}).encode()

        arguments = ['--record', str(self.record), 'upload', str(self.bundle),
                     '--name', 'jev-parent 0.1.0 abc', '--expect-sha256', self.digest]
        with patch('central_publish.request', side_effect=fake_request), patch('central_publish.time.sleep'):
            central_publish.main(arguments)
            self.assertEqual(json.loads(self.record.read_text())['deploymentId'], 'dep-1')
            # A rerun with a recorded deployment resumes polling (PENDING, VALIDATING, VALIDATED).
            central_publish.main(arguments)
        uploads = [c for c in calls if c[1].startswith('upload')]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(len([c for c in calls if c[1].startswith('status')]), 4)
        with patch('central_publish.request', return_value=(200, b'{"deploymentState":"FAILED","errors":{}}')):
            with self.assertRaisesRegex(SystemExit, 'FAILED'):
                central_publish.main(arguments)
        with self.assertRaisesRegex(SystemExit, 'does not match'):
            central_publish.main(['--record', str(self.record), 'upload', str(self.bundle),
                                  '--name', 'n', '--expect-sha256', '0' * 64])

    def test_publish_only_from_validated_and_is_idempotent(self):
        self.record.write_text(json.dumps({'deploymentId': 'dep-1'}))
        published = []
        for state, expect_publish in (('VALIDATED', True), ('PUBLISHING', False), ('PUBLISHED', False)):
            published.clear()
            reports = iter([{'deploymentState': state}, {'deploymentState': 'PUBLISHED'}])
            with patch('central_publish.status', side_effect=lambda b, i: next(reports)), \
                    patch('central_publish.publish', side_effect=lambda b, i: published.append(i)), \
                    patch('central_publish.time.sleep'):
                central_publish.main(['--record', str(self.record), 'publish'])
            self.assertEqual(bool(published), expect_publish, state)
        for state in ('PENDING', 'VALIDATING', 'FAILED'):
            with patch('central_publish.status', return_value={'deploymentState': state}):
                with self.assertRaisesRegex(SystemExit, 'Only a VALIDATED'):
                    central_publish.main(['--record', str(self.record), 'publish'])

    def test_drop_refuses_published_and_requires_record(self):
        with self.assertRaisesRegex(SystemExit, 'upload first'):
            central_publish.main(['--record', str(self.record), 'status'])
        self.record.write_text(json.dumps({'deploymentId': 'dep-1'}))
        with patch('central_publish.status', return_value={'deploymentState': 'PUBLISHED'}):
            with self.assertRaisesRegex(SystemExit, 'Cannot drop'):
                central_publish.main(['--record', str(self.record), 'drop'])

    def test_recorded_deployment_must_describe_this_bundle_and_candidate(self):
        arguments = ['--record', str(self.record), 'upload', str(self.bundle), '--name', 'jev-parent 0.1.0 abc run 7',
                     '--candidate', 'run 7 commit abc version 0.1.0', '--expect-sha256', self.digest]
        good = {'deploymentId': 'dep-1', 'bundleSha256': self.digest, 'baseUrl': central_publish.DEFAULT_BASE,
                'name': 'jev-parent 0.1.0 abc run 7', 'candidate': 'run 7 commit abc version 0.1.0'}
        rejected = {
            'other bundle': {**good, 'bundleSha256': '0' * 64},
            'hash missing': {k: v for k, v in good.items() if k != 'bundleSha256'},
            'other Portal': {**good, 'baseUrl': 'https://portal.invalid'},
            'other candidate run': {**good, 'name': 'jev-parent 0.1.0 abc run 6', 'candidate': 'run 6 commit abc version 0.1.0'},
        }
        for reason, record in rejected.items():
            self.record.write_text(json.dumps(record))
            with patch('central_publish.request') as network:
                with self.assertRaisesRegex(SystemExit, 'different candidate', msg=reason):
                    central_publish.main(arguments)
            network.assert_not_called()
        # A record describing exactly this bundle and candidate resumes without a new upload, and the
        # Portal's own name for the deployment must agree with the record.
        self.record.write_text(json.dumps(good))
        report = {'deploymentId': 'dep-1', 'deploymentName': 'jev-parent 0.1.0 abc run 7', 'deploymentState': 'VALIDATED'}
        with patch('central_publish.request', return_value=(200, json.dumps(report).encode())) as network:
            central_publish.main(arguments)
        self.assertEqual([c.args[1].split('publisher/')[1][:6] for c in network.call_args_list], ['status'])
        with patch('central_publish.request', return_value=(200, json.dumps({**report, 'deploymentName': 'stray'}).encode())):
            with self.assertRaisesRegex(PortalError, 'does not describe this deployment'):
                central_publish.main(arguments)
            with self.assertRaisesRegex(PortalError, 'does not describe this deployment'):
                central_publish.main(['--record', str(self.record), 'publish'])
        with self.assertRaisesRegex(SystemExit, 'not https://portal.invalid'):
            central_publish.main(['--record', str(self.record), '--base-url', 'https://portal.invalid', 'publish'])

    def test_http_errors_are_not_retried_except_5xx(self):
        from urllib.error import HTTPError
        attempts = []

        def fail(request, timeout):
            attempts.append(request.full_url)
            raise HTTPError(request.full_url, 401, 'nope', {}, io.BytesIO(b'denied'))

        with patch('central_publish.urlopen', side_effect=fail), patch('central_publish.time.sleep'):
            with self.assertRaisesRegex(PortalError, '401'):
                central_publish.request('POST', 'https://example.invalid/x')
        self.assertEqual(len(attempts), 1)

        def flaky(request, timeout):
            attempts.append(request.full_url)
            raise HTTPError(request.full_url, 503, 'busy', {}, io.BytesIO(b''))

        attempts.clear()
        with patch('central_publish.urlopen', side_effect=flaky), patch('central_publish.time.sleep'):
            with self.assertRaisesRegex(PortalError, '503'):
                central_publish.request('POST', 'https://example.invalid/x')
        self.assertEqual(len(attempts), 3)


class WorkflowTest(unittest.TestCase):
    """Text-level guards for the release workflows (the hook's interpreter has no YAML library)."""

    WORKFLOWS = Path(__file__).resolve().parents[1] / '.github/workflows'

    def test_logged_pipelines_fail_on_the_first_failing_command(self):
        for name in ('release-candidate.yml', 'release-promote.yml'):
            text = (self.WORKFLOWS / name).read_text()
            self.assertRegex(text, r'\ndefaults:\n  run:\n(    #.*\n)*    shell: bash\n', name)

    def test_candidate_toolchain_is_pinned_in_the_workflow_not_chosen_at_dispatch(self):
        text = (self.WORKFLOWS / 'release-candidate.yml').read_text()
        self.assertNotIn('inputs', text)
        pins = dict(re.findall(r'^  (BUILD_JDK|BUILD_JDK_RUNTIME|CONSUMER_JDK|CONSUMER_JDK_RUNTIME): "([^"]+)"$', text, re.M))
        self.assertEqual(sorted(pins), ['BUILD_JDK', 'BUILD_JDK_RUNTIME', 'CONSUMER_JDK', 'CONSUMER_JDK_RUNTIME'])
        self.assertRegex(pins['BUILD_JDK'], r'^21\.\d+\.\d+$')
        self.assertRegex(pins['CONSUMER_JDK'], r'^25\.\d+\.\d+$')
        for spec, runtime in (('BUILD_JDK', 'BUILD_JDK_RUNTIME'), ('CONSUMER_JDK', 'CONSUMER_JDK_RUNTIME')):
            self.assertRegex(pins[runtime], '^' + re.escape(pins[spec]) + r'(\.\d+)?\+\d+-LTS$')
        self.assertIn('verify "$JDK21" "$BUILD_JDK_RUNTIME"', text)
        self.assertIn('verify "$JDK25" "$CONSUMER_JDK_RUNTIME"', text)

    def test_promotion_binds_the_deployment_to_the_candidate(self):
        text = (self.WORKFLOWS / 'release-promote.yml').read_text()
        self.assertIn('--candidate "run $RUN_ID commit $commit version $version"', text)
        self.assertIn('--expect-sha256', text)


if __name__ == '__main__':
    unittest.main()
