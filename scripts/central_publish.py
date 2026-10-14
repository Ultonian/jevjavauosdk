"""Drive the Central Portal Publisher API for one already-built, already-checked bundle.

The bundle is never rebuilt here. Every action first inspects the deployment's recorded state, so a
rerun after a failed step resumes rather than uploading or publishing twice. The record binds the
deployment to the bundle hash, the Portal, the deployment name and the candidate identity; a rerun
with a different bundle or candidate is refused instead of silently validating or publishing the
deployment that was recorded earlier:

  upload    upload the bundle as a USER_MANAGED deployment, wait for VALIDATED, record the id
  status    print the recorded deployment's state and any validation errors
  publish   publish a deployment that is VALIDATED; PUBLISHING/PUBLISHED count as done
  drop      drop a FAILED or VALIDATED deployment that must not be published

Credentials come only from CENTRAL_USERNAME and CENTRAL_TOKEN (a Portal user token).
See https://central.sonatype.org/publish/publish-portal-api/
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_BASE = 'https://central.sonatype.com'
TERMINAL = {'VALIDATED', 'PUBLISHED', 'FAILED'}
KNOWN_STATES = {'PENDING', 'VALIDATING', 'VALIDATED', 'PUBLISHING', 'PUBLISHED', 'FAILED'}


class PortalError(RuntimeError):
    """An HTTP or protocol failure that a retry will not fix without inspection."""


def auth_header():
    username = os.environ.get('CENTRAL_USERNAME', '')
    token = os.environ.get('CENTRAL_TOKEN', '')
    if not username or not token:
        raise ValueError('CENTRAL_USERNAME and CENTRAL_TOKEN must be set')
    return 'Bearer ' + base64.b64encode(f'{username}:{token}'.encode()).decode()


def request(method, url, body=None, content_type=None, attempts=3):
    """One HTTP exchange with bounded retries on connection failures and 5xx only."""
    headers = {'Authorization': auth_header(), 'User-Agent': 'jevjavauosdk-release'}
    if content_type:
        headers['Content-Type'] = content_type
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, data=body, method=method, headers=headers), timeout=120) as response:
                return response.status, response.read()
        except HTTPError as error:
            payload = error.read()
            if error.code >= 500 and attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise PortalError(f'{method} {url} -> {error.code}: {payload[:2000].decode("utf-8", "replace")}')
        except URLError as error:
            if attempt == attempts - 1:
                raise PortalError(f'{method} {url} unreachable: {error.reason}')
            time.sleep(2 ** attempt)
    raise AssertionError('unreachable')


def multipart(field, filename, data):
    boundary = uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    return body, f'multipart/form-data; boundary={boundary}'


def upload(base, bundle, name):
    data = Path(bundle).read_bytes()
    body, content_type = multipart('bundle', Path(bundle).name, data)
    url = f'{base}/api/v1/publisher/upload?publishingType=USER_MANAGED&name={quote(name, safe="")}'
    status, payload = request('POST', url, body, content_type, attempts=1)
    if status != 201:
        raise PortalError(f'Upload returned {status}: {payload[:500]!r}')
    deployment_id = payload.decode().strip().strip('"')
    if not deployment_id:
        raise PortalError('Upload returned no deployment id')
    return deployment_id, hashlib.sha256(data).hexdigest()


def status(base, deployment_id):
    code, payload = request('POST', f'{base}/api/v1/publisher/status?id={deployment_id}')
    try:
        report = json.loads(payload)
        state = report['deploymentState']
    except (ValueError, KeyError, TypeError):
        raise PortalError(f'Malformed status response: {payload[:500]!r}')
    if state not in KNOWN_STATES:
        raise PortalError(f'Unknown deployment state {state!r}')
    return report


def publish(base, deployment_id):
    code, _ = request('POST', f'{base}/api/v1/publisher/deployment/{deployment_id}', attempts=1)
    if code != 204:
        raise PortalError(f'Publish returned {code}')


def drop(base, deployment_id):
    code, _ = request('DELETE', f'{base}/api/v1/publisher/deployment/{deployment_id}', attempts=1)
    if code != 204:
        raise PortalError(f'Drop returned {code}')


def wait_for(base, deployment_id, wanted, timeout, interval=10, sleep=None, clock=None):
    """Poll until the deployment reaches a state in `wanted` or a terminal state; never loops forever."""
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    end = clock() + timeout
    while True:
        report = status(base, deployment_id)
        state = report['deploymentState']
        if state in wanted or state in TERMINAL:
            return report
        if clock() >= end:
            raise PortalError(f'Deployment {deployment_id} still {state} after {timeout}s')
        sleep(interval)


def load_record(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else None


def save_record(path, record):
    Path(path).write_text(json.dumps(record, indent=2) + '\n')


def describe(report):
    lines = [f'deployment {report.get("deploymentId")} ({report.get("deploymentName")}): {report["deploymentState"]}']
    for path, errors in (report.get('errors') or {}).items():
        lines.append(f'  {path}: {errors}')
    return '\n'.join(lines)


def check_record(record, expected):
    """Refuse a record that describes a different bundle, Portal or candidate than this run's."""
    mismatches = [f'{key}: recorded {record.get(key)!r}, this run {value!r}'
                  for key, value in expected.items() if record.get(key) != value]
    if mismatches:
        raise SystemExit(f'Recorded deployment {record.get("deploymentId")} belongs to a different candidate:\n  '
                         + '\n  '.join(mismatches)
                         + '\nIf that deployment must not be published, drop it; then remove its record '
                           '(delete the deployment-<tag> artifacts) before uploading this candidate.')


def check_report(report, record):
    """The Portal's own view of the deployment must agree with the record's name."""
    name = report.get('deploymentName')
    if name and record.get('name') and name != record['name']:
        raise PortalError(f'Deployment {record.get("deploymentId")} is named {name!r} on the Portal, '
                          f'not {record["name"]!r}: the record does not describe this deployment')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', default=os.environ.get('CENTRAL_BASE_URL', DEFAULT_BASE))
    parser.add_argument('--record', required=True, help='JSON file holding the deployment id and bundle hash')
    parser.add_argument('--timeout', type=int, default=1800, help='seconds to wait for validation/publication')
    commands = parser.add_subparsers(dest='command', required=True)
    up = commands.add_parser('upload')
    up.add_argument('bundle')
    up.add_argument('--name', required=True, help='deployment name shown on the Portal, e.g. jev-parent 0.1.0')
    up.add_argument('--expect-sha256', required=True, help='bundle hash from the candidate manifest')
    up.add_argument('--candidate', default='',
                    help='candidate identity recorded with the deployment, e.g. "run 123 commit abc version 0.1.0"')
    commands.add_parser('status')
    commands.add_parser('publish')
    commands.add_parser('drop')
    args = parser.parse_args(argv)
    base = args.base_url.rstrip('/')
    record = load_record(args.record)

    if args.command == 'upload':
        actual = hashlib.sha256(Path(args.bundle).read_bytes()).hexdigest()
        if actual != args.expect_sha256:
            raise SystemExit(f'Bundle hash {actual} does not match the manifest {args.expect_sha256}')
        identity = {'bundleSha256': actual, 'baseUrl': base, 'name': args.name, 'candidate': args.candidate}
        if record:
            check_record(record, identity)
            deployment_id = record['deploymentId']
            print(f'Deployment {deployment_id} already recorded for this bundle; not uploading again.')
        else:
            deployment_id, digest = upload(base, args.bundle, args.name)
            record = {'deploymentId': deployment_id, **identity}
            save_record(args.record, record)
            print(f'Uploaded deployment {deployment_id}; waiting for validation')
        report = check_report(wait_for(base, deployment_id, {'VALIDATED', 'PUBLISHING', 'PUBLISHED'}, args.timeout),
                              record)
        print(describe(report))
        if report['deploymentState'] == 'FAILED':
            raise SystemExit('Deployment FAILED: drop it and fix the cause with a new candidate')
        if report['deploymentState'] not in ('VALIDATED', 'PUBLISHING', 'PUBLISHED'):
            raise SystemExit('Validation is not complete; rerun validate to resume waiting')
        return

    if not record:
        raise SystemExit(f'No deployment recorded in {args.record}; upload first')
    deployment_id = record['deploymentId']
    if record.get('baseUrl', base) != base:
        raise SystemExit(f'Deployment {deployment_id} was recorded on {record["baseUrl"]}, not {base}')
    if args.command == 'status':
        print(describe(check_report(status(base, deployment_id), record)))
    elif args.command == 'publish':
        report = check_report(status(base, deployment_id), record)
        state = report['deploymentState']
        if state in ('PUBLISHING', 'PUBLISHED'):
            print('Publication already requested.\n' + describe(report))
        elif state != 'VALIDATED':
            raise SystemExit('Only a VALIDATED deployment can be published.\n' + describe(report))
        else:
            publish(base, deployment_id)
            print(f'Publish requested for {deployment_id}')
        report = wait_for(base, deployment_id, {'PUBLISHED'}, args.timeout)
        print(describe(report))
        if report['deploymentState'] != 'PUBLISHED':
            raise SystemExit('Publication is not complete; rerun publish to resume waiting')
    elif args.command == 'drop':
        report = check_report(status(base, deployment_id), record)
        if report['deploymentState'] in ('PUBLISHING', 'PUBLISHED'):
            raise SystemExit('Cannot drop a deployment that is publishing or published')
        drop(base, deployment_id)
        print(f'Dropped {deployment_id}; remove its record ({args.record} and any deployment-<tag> '
              'artifacts) before uploading a new candidate')


if __name__ == '__main__':
    try:
        main()
    except (PortalError, ValueError) as error:
        sys.exit(str(error))
