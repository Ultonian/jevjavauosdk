"""Deliberate failures for maintenance policy, link checking and upstream signals."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from api_compat import compatibility_required
from check_links import anchors, check_external, check_local, targets
from monitor import digest, ea_version, fetch, headers, inspect_upstream, validate_pins
from prove_gates import find_jdk


class JdkTest(unittest.TestCase):
    def test_explicit_home_and_path_compiler_resolution(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            (home / 'bin').mkdir()
            suffix = '.exe' if os.name == 'nt' else ''
            for name in ('java', 'javac', 'jar'):
                tool = home / 'bin' / (name + suffix)
                tool.touch()
                tool.chmod(0o755)
            with patch.dict(os.environ, {'JAVA_HOME': str(home)}), patch('prove_gates.shutil.which') as which:
                self.assertEqual(find_jdk(), home)
                which.assert_not_called()
            with patch.dict(os.environ, {}, clear=True), patch('prove_gates.shutil.which',
                                                               return_value=str(home / 'bin' / ('javac' + suffix))):
                self.assertEqual(find_jdk(), home)
            (home / 'bin' / ('jar' + suffix)).unlink()
            with patch.dict(os.environ, {'JAVA_HOME': str(home)}):
                with self.assertRaisesRegex(ValueError, 'lacks executable jar'):
                    find_jdk()

    def test_missing_compiler_has_actionable_error(self):
        with patch.dict(os.environ, {}, clear=True), patch('prove_gates.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'set JAVA_HOME'):
                find_jdk()


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = {'baseline': None, 'initialVersion': '0.1.0',
                       'acceptedBreakingMinor': None, 'reason': None}

    def test_only_initial_version_skips(self):
        for version in ('0.1.0', '0.1.0-SNAPSHOT'):
            self.assertIsNone(compatibility_required(version, self.policy))
        for version in ('0.1.1', '0.2.0-SNAPSHOT', '1.0.0'):
            with self.assertRaises(ValueError):
                compatibility_required(version, self.policy)

    def test_patch_and_unapproved_minor_are_strict(self):
        self.policy['baseline'] = '0.1.0'
        for version in ('0.1.1', '0.2.0-SNAPSHOT'):
            self.assertTrue(compatibility_required(version, self.policy))

    def test_acceptance_is_narrow_and_explained(self):
        self.policy.update(baseline='0.1.0', acceptedBreakingMinor='0.2.0', reason='Changed public model')
        self.assertFalse(compatibility_required('0.2.0-SNAPSHOT', self.policy))
        for version in ('0.1.1', '0.2.1', '0.3.0', '1.0.0'):
            with self.assertRaises(ValueError):
                compatibility_required(version, self.policy)
        self.policy['reason'] = ''
        with self.assertRaises(ValueError):
            compatibility_required('0.2.0', self.policy)

    def test_bad_baseline_fails(self):
        for baseline in ('LATEST', '[0,)', '0.1.0-SNAPSHOT', '0.3.0'):
            self.policy['baseline'] = baseline
            with self.assertRaises(ValueError):
                compatibility_required('0.2.0', self.policy)


class LinksTest(unittest.TestCase):
    def test_files_anchors_references_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = root / 'README.md'
            (root / 'other.md').write_text('# Hello world\n# Hello world\n')
            page.write_text('[ok](other.md#hello-world-1)\n[ref][target]\n[target]: other.md#hello-world\n')
            self.assertEqual(check_local(page, root)[0], [])
            page.write_text('[broken](missing.md)\n[bad](other.md#absent)')
            self.assertEqual(len(check_local(page, root)[0]), 2)
        self.assertEqual(anchors('# A\n# A'), {'a', 'a-1'})

    def test_undefined_reference_fails_and_examples_are_ignored(self):
        with self.assertRaises(ValueError):
            targets('[text][unknown]')
        self.assertEqual(targets('```md\n[example](absent)\n```'), [])
        self.assertEqual(targets('[ok](<with spaces.md>)'), ['with spaces.md'])

    def test_link_cli_supports_safe_path(self):
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, PYTHONSAFEPATH='1')
            env.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, '-B', '-P',
                                     str(Path(__file__).with_name('check_links.py').resolve()), '--help'],
                                    cwd=temp, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--external', result.stdout)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.schema = json.dumps({'openapi': '3.0.0', 'info': {'version': '0.2.0'}, 'paths': {'/a': {}}}).encode()
        self.pins = {'schema': {'version': '0.2.0', 'sha256': digest(self.schema)},
                     'npm': {'version': '0.6.0'}, 'pypi': {'version': '0.7.2'}, 'acknowledgements': []}
        self.bodies = {'schema': self.schema, 'npm': b'{"version":"0.6.0"}',
                       'pypi': b'{"info":{"version":"0.7.2"}}'}

    def test_unchanged(self):
        self.assertEqual(inspect_upstream(self.pins, self.bodies, self.schema), [])

    def test_same_version_schema_change_and_sdk_bump(self):
        self.bodies['schema'] = self.schema.replace(b'/a', b'/b')
        self.bodies['npm'] = b'{"version":"0.6.1"}'
        findings = inspect_upstream(self.pins, self.bodies, self.schema)
        self.assertEqual(len(findings), 2)
        self.assertTrue(findings[0]['diff'])
        self.assertFalse(findings[0]['acknowledged'])
        self.pins['acknowledgements'] = [{'id': findings[0]['id'], 'reason': 'Additive endpoint not used'}]
        self.assertTrue(inspect_upstream(self.pins, self.bodies, self.schema)[0]['acknowledged'])
        self.assertEqual(findings[0]['id'], inspect_upstream(self.pins, self.bodies, self.schema)[0]['id'])

    def test_malformed_is_not_no_drift(self):
        for name, body in [('schema', b'{}'), ('npm', b'null'), ('pypi', b'{"info":{}}'),
                           ('npm', b'{"version":null}'), ('schema', b'not json')]:
            bodies = copy.deepcopy(self.bodies)
            bodies[name] = body
            with self.assertRaises((ValueError, KeyError, TypeError)):
                inspect_upstream(self.pins, bodies, self.schema)

    def test_unavailable_exhausts_bounded_retries(self):
        with patch('monitor.urlopen', side_effect=OSError('offline')) as url, patch('monitor.time.sleep'):
            with self.assertRaisesRegex(OSError, 'offline'):
                fetch('https://example.invalid')
            self.assertEqual(url.call_count, 3)

    def test_documented_pins_are_checked_in_context(self):
        repo = Path(__file__).resolve().parents[1]
        pins = json.loads((repo / 'config/quality/upstream.json').read_text())
        files = [pins['schema']['path'], 'CHANGELOG.md', 'docs/PARITY.md',
                 'config/quality/tools.json', 'CONTRIBUTING.md', '.github/workflows/ci.yml',
                 '.mvn/wrapper/maven-wrapper.properties']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in files:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repo / name, root / name)
            validate_pins(root, pins)
            changelog = root / 'CHANGELOG.md'
            text = changelog.read_text()
            changelog.write_text(text.replace('`@typesafe-ai/sdk` ' + pins['npm']['version'],
                                              '`@typesafe-ai/sdk` 99.0.0'))
            with self.assertRaisesRegex(ValueError, 'npm pin'):
                validate_pins(root, pins)
            changelog.write_text(text)
            (root / pins['schema']['path']).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validate_pins(root, pins)

    def test_github_api_is_authenticated_only_with_a_token_and_only_for_the_api_host(self):
        api = 'https://api.github.com/repos/aquasecurity/trivy/releases/latest'
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'secret'}):
            self.assertEqual(headers(api)['Authorization'], 'Bearer secret')
            for url in ('https://github.com/Ultonian/jevjavauosdk/commits/main/',
                        'https://pypi.org/pypi/pre-commit/json', 'https://api.github.com.evil.example/'):
                self.assertNotIn('Authorization', headers(url))
        with patch.dict(os.environ, {'GITHUB_TOKEN': ''}):
            self.assertNotIn('Authorization', headers(api))

    def test_rate_limited_links_are_unverified_not_broken(self):
        def fetch(url):
            if 'limited' in url:
                raise HTTPError(url, 429, 'Too Many Requests', None, None)
            if 'gone' in url:
                raise HTTPError(url, 404, 'Not Found', None, None)
            return b''
        with patch('check_links.fetch', side_effect=fetch), patch('builtins.print') as out:
            errors = check_external({'https://x/limited', 'https://x/gone', 'https://x/ok'})
        self.assertEqual(errors, ['https://x/gone: HTTP Error 404: Not Found'])
        self.assertIn('::warning::https://x/limited', out.call_args[0][0])

    def test_ea_tracks_vendor_and_rejects_no_future_release(self):
        self.assertEqual(ea_version(b'{"tip_version":28,"most_recent_feature_release":27}'), '28-ea')
        with self.assertRaises(ValueError):
            ea_version(b'{"tip_version":27,"most_recent_feature_release":27}')


if __name__ == '__main__':
    unittest.main()
