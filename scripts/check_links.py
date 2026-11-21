"""Check repository Markdown links locally; external checks are explicitly opt-in."""
import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from monitor import fetch  # noqa: E402 - support Python safe-path mode

ROOT = Path(__file__).resolve().parents[1]


def prose(text):
    return re.sub(r'^\s*(`{3,}|~{3,}).*?^\s*\1\s*$', '', text, flags=re.M | re.S)


def anchors(text):
    counts, result = {}, set()
    for heading in re.findall(r'^#{1,6}\s+(.+?)(?:\s+#+)?$', prose(text), re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(slug + (f'-{count}' if count else ''))
    result.update(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', text))
    return result


def targets(text):
    text = prose(text)
    definitions = dict((key.casefold(), value) for key, value in
                       re.findall(r'^\s*\[([^\]]+)\]:\s*<?([^\s>]+)>?', text, re.M))
    found = re.findall(r'!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\s*\)', text)
    found.extend(definitions.values())
    for label, key in re.findall(r'\[([^\]\n]+)\]\[([^\]\n]*)\]', text):
        if (key or label).casefold() not in definitions:
            raise ValueError('Undefined link reference: ' + (key or label))
    found.extend(re.findall(r'<(https?://[^>]+)>', text))
    return [value.strip('<>') for value in found]


def check_local(path, root):
    errors, external = [], set()
    for target in targets(path.read_text()):
        parts = urlsplit(target)
        if parts.scheme or parts.netloc:
            if parts.scheme in ('http', 'https'):
                external.add(target.split('#')[0])
            continue
        dest = (root / unquote(parts.path).lstrip('/') if parts.path.startswith('/')
                else path.parent / unquote(parts.path)) if parts.path else path
        if not dest.exists():
            errors.append(f'{path}: missing {target}')
        elif parts.fragment and dest.suffix == '.md' and unquote(parts.fragment) not in anchors(dest.read_text()):
            errors.append(f'{path}: missing anchor {target}')
    return errors, external


def check_external(urls):
    """Fetch each URL once; rate limiting (429) leaves a link unverified rather than broken."""
    errors = []
    for url in sorted(urls):
        try:
            fetch(url)
        except HTTPError as exc:
            if exc.code == 429:
                print(f'::warning::{url}: unverified, {exc}')
            else:
                errors.append(f'{url}: {exc}')
        except (OSError, ValueError) as exc:
            errors.append(f'{url}: {exc}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external', action='store_true')
    args = parser.parse_args()
    files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard',
                                     '*.md'], cwd=ROOT, text=True).splitlines()
    errors, urls = [], set()
    for name in sorted(set(files)):
        path = ROOT / name
        if path.exists():
            problems, links = check_local(path, ROOT)
            errors.extend(problems)
            urls.update(links)
    if args.external:
        errors.extend(check_external(urls))
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Checked links in {len(files)} Markdown files; external checks: {args.external}')


if __name__ == '__main__':
    main()
