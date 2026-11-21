"""Read-only maintenance signals. Fetch errors fail; pins never change automatically."""
import argparse
import difflib
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def headers(url):
    """Authenticate GitHub API calls when a token is available; unauthenticated calls hit a 60/h limit."""
    result = {'User-Agent': 'jevjavauosdk-maintenance'}
    token = os.environ.get('GITHUB_TOKEN')
    if token and urlsplit(url).netloc == 'api.github.com':
        result['Authorization'] = 'Bearer ' + token
    return result


def fetch(url):
    for attempt in range(3):
        try:
            request = Request(url, headers=headers(url))
            with urlopen(request, timeout=20) as response:
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError('Response exceeds 8 MiB')
                return body
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise AssertionError('unreachable')


def digest(body):
    return hashlib.sha256(body).hexdigest()


def inspect_upstream(pins, bodies, local):
    findings = []
    for name in ('schema', 'npm', 'pypi'):
        value = json.loads(bodies[name])
        if name == 'schema':
            if not isinstance(value.get('paths'), dict) or not value['paths'] or not value.get('openapi'):
                raise ValueError('Malformed OpenAPI document')
            version = value['info']['version']
            changed = digest(bodies[name]) != pins[name]['sha256']
        else:
            version = value['info']['version'] if name == 'pypi' else value['version']
            changed = version != pins[name]['version']
        if not isinstance(version, str) or not version.strip():
            raise ValueError(f'Malformed {name} version')
        if changed:
            finding = {'source': name, 'pinned': pins[name]['version'], 'observed': version,
                       'sha256': digest(bodies[name])}
            # Stable identity deduplicates the same signal across repeated runs.
            identity = finding['sha256'] if name == 'schema' else version
            finding['id'] = f'{name}:{identity}'
            if name == 'schema':
                finding['pinnedSha256'] = pins[name]['sha256']
                old = json.dumps(json.loads(local), indent=2, sort_keys=True).splitlines()
                new = json.dumps(value, indent=2, sort_keys=True).splitlines()
                finding['diff'] = list(difflib.unified_diff(old, new, fromfile='pinned', tofile='live'))[:200]
            findings.append(finding)
    acknowledged = {entry['id'] for entry in pins['acknowledgements'] if entry['reason'].strip()}
    for finding in findings:
        finding['acknowledged'] = finding['id'] in acknowledged
    return findings


def validate_pins(root, pins):
    local = (root / pins['schema']['path']).read_bytes()
    if digest(local) != pins['schema']['sha256']:
        raise ValueError('Pinned schema hash mismatch')
    if json.loads(local)['info']['version'] != pins['schema']['version']:
        raise ValueError('Pinned schema version mismatch')
    for file in ('CHANGELOG.md', 'docs/PARITY.md'):
        text = (root / file).read_text()
        for name in ('schema', 'npm', 'pypi'):
            version = re.escape(pins[name]['version'])
            prefixes = {'schema': r'(?:OpenAPI `info.version`|`info.version`) ',
                        'npm': r'`@typesafe-ai/sdk` ',
                        'pypi': r'(?:`typesafe-sdk` \(Python\)|Python `typesafe-sdk`) '}
            if not re.search(prefixes[name] + version + r'(?!\d|\.\d)', text):
                raise ValueError(f'{file} does not document the {name} pin')
    tools = json.loads((root / 'config/quality/tools.json').read_text())
    locations = {
        'pre-commit': ['CONTRIBUTING.md', '.github/workflows/ci.yml'],
        'trivy': ['CONTRIBUTING.md', '.github/workflows/ci.yml'],
        'maven': ['.mvn/wrapper/maven-wrapper.properties'],
    }
    for name, files in locations.items():
        for file in files:
            if tools[name]['version'] not in (root / file).read_text():
                raise ValueError(f'{file} does not match the {name} tool pin')
    return local


def ea_version(body):
    data = json.loads(body)
    feature = data['tip_version']
    if not isinstance(feature, int) or feature <= data['most_recent_feature_release']:
        raise ValueError('No newer EA feature reported by Adoptium')
    return str(feature) + '-ea'


def tool_findings(root):
    """Pins outside Dependabot's Maven/Actions coverage; no automated edits."""
    hook = (root / '.pre-commit-config.yaml').read_text()
    findings = []
    for repo, pin in re.findall(r'repo: https://github.com/([^\s]+)\s+rev: ([^\s]+)', hook):
        data = json.loads(fetch(f'https://api.github.com/repos/{repo}/releases/latest'))
        latest = data['tag_name']
        if latest != pin:
            findings.append({'id': f'tool:{repo}:{latest}', 'pinned': pin, 'observed': latest})
    tools = json.loads((root / 'config/quality/tools.json').read_text())
    for name, pin in tools.items():
        data = json.loads(fetch(pin['url']))
        latest = data['info']['version'] if name == 'pre-commit' else data['tag_name'].removeprefix('maven-').removeprefix('v')
        if latest != pin['version']:
            findings.append({'id': f'tool:{name}:{latest}', 'pinned': pin['version'], 'observed': latest})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('pins', 'upstream', 'tools', 'ea'))
    parser.add_argument('--output', type=Path, default=ROOT / 'target/maintenance')
    args = parser.parse_args()
    if args.mode == 'ea':
        print(ea_version(fetch('https://api.adoptium.net/v3/info/available_releases')))
        return
    pins = json.loads((ROOT / 'config/quality/upstream.json').read_text())
    local = validate_pins(ROOT, pins)
    if args.mode == 'pins':
        print('Pinned contract and documentation agree')
        return
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode == 'upstream':
        bodies = {name: fetch(pins[name]['url']) for name in ('schema', 'npm', 'pypi')}
        for name, body in bodies.items():
            (args.output / (name + '.json')).write_bytes(body)
        findings = inspect_upstream(pins, bodies, local)
    else:
        findings = tool_findings(ROOT)
    report = json.dumps(findings, indent=2) + '\n'
    (args.output / (args.mode + '-findings.json')).write_text(report)
    print(report)
    if path := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(path, 'a') as out:
            out.write(f'### {args.mode} monitoring\n\n```json\n{report}\n```\n')
    if any(not item.get('acknowledged', False) for item in findings):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
