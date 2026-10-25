"""Review local templates and controls with synthetic data; no external requests."""
from contextlib import contextmanager
from pathlib import Path
import argparse
import json
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests')]
from fastapi.testclient import TestClient
from starlette.requests import Request
from playwright.sync_api import sync_playwright
from ecc_challenges.app import TEMPLATES
from ecc_challenges.catalog import CHALLENGES
from test_catalog import application, FakeConnection


class ReviewConnection(FakeConnection):
    def execute(self, statement, params=None):
        if "ecc_public.dp_" in statement:
            return SimpleNamespace(fetchall=lambda: [])
        return super().execute(statement, params)


class ReviewDB:
    @contextmanager
    def connection(self):
        yield ReviewConnection()


def dashboard():
    request = Request({'type': 'http', 'method': 'GET', 'path': '/dashboard',
        'query_string': b'campaign=eccp-131', 'headers': [], 'session': {'github_id': 1},
        'scheme': 'https', 'server': ('ecc-ui.invalid', 443)})
    request.state.csp_nonce = 'local-ui-fixture'
    return TEMPLATES.get_template('dashboard.html').render(
        request=request, login='review-fixture', keys=[], csrf='fixture', issued=None,
        challenges=CHALLENGES, worker_image='example.invalid/worker@sha256:' + 'a' * 64,
        cpu_worker_image='example.invalid/cpu@sha256:' + 'b' * 64,
        activity=SimpleNamespace(active_runs=0, points=0, total_runs=0,
            points_per_hour=0, points_last_hour=0, runs=[], recent=[]),
        campaign_status=None, multicurve_enabled=True, live_as_of='Local review fixture',
        names={c.id: c.public()['name'] for c in CHALLENGES}, selected_campaign='eccp-131',
        client_os='linux', selected_dp_weight=34, recommended_dp_weight=34,
        sigma_dp_weights=[34, 32, 30])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', choices=['before', 'after'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'label': args.label, 'views': [], 'controls': []}
    with TestClient(application(db=ReviewDB(), multicurve_enabled=True), base_url='https://ecc-ui.invalid') as client, sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        context = browser.new_context(reduced_motion='reduce')
        # Exercise the clipboard contract without reading or changing the OS clipboard.
        context.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => { window.copiedCode = text; }}, configurable: true});")
        errors = []
        def serve(route):
            url = route.request.url
            path = urlsplit(url).path
            if urlsplit(url).hostname != 'ecc-ui.invalid':
                route.abort()
                return
            if path == '/dashboard':
                route.fulfill(status=200, content_type='text/html', body=dashboard())
                return
            response = client.get(path)
            route.fulfill(status=response.status_code, headers={k: v for k, v in response.headers.items()
                if k.lower() not in {'content-length', 'content-encoding'}}, body=response.content)
        context.route('**/*', serve)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        paths = ['/', '/status', '/docs', '/docs/agents', '/docs/running-a-worker', '/help', '/dashboard', '/challenges/ecc2k-130', '/challenges/eccp-359', '/results/eccp-79']
        widths = [320, 390, 768, 1440] if args.label == 'after' else [390, 1440]
        schemes = ['light', 'dark'] if args.label == 'after' else ['light']
        for scheme in schemes:
            page.emulate_media(color_scheme=scheme)
            for width in widths:
                page.set_viewport_size({'width': width, 'height': 1000})
                for path in paths:
                    response = page.goto('https://ecc-ui.invalid' + path, wait_until='networkidle')
                    assert response.status == 200, (path, response.status)
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (path, width, scheme)
                    assert not errors, errors
                    report['views'].append({'path': path, 'width': width, 'scheme': scheme})
                    if width in [390, 1440] and path in ['/', '/docs', '/dashboard']:
                        name = {'/': 'home', '/docs': 'docs', '/dashboard': 'dashboard'}[path]
                        page.screenshot(path=str(args.out / f'{args.label}-{name}-{width}-{scheme}.png'))
        if args.label == 'after':
            page.emulate_media(color_scheme='light')
            page.set_viewport_size({'width': 320, 'height': 900})
            page.goto('https://ecc-ui.invalid/')
            menu = page.locator('.mobile-nav')
            menu.locator('summary').click()
            assert menu.get_attribute('open') is not None
            assert menu.get_by_role('link', name='Docs', exact=True).is_visible()
            sign_in = menu.get_by_role('link', name='Sign in with GitHub', exact=True)
            background = sign_in.evaluate('node => getComputedStyle(node).backgroundColor')
            sign_in.hover()
            assert sign_in.evaluate('node => getComputedStyle(node).backgroundColor') == background
            report['controls'].append('Mobile sign-in keeps its button background on hover')
            page.keyboard.press('Escape')
            assert menu.get_attribute('open') is None
            assert menu.locator('summary').evaluate('node => node === document.activeElement')
            report['controls'].append('Mobile navigation opens, exposes guides, and closes with Escape and focus restoration')
            page.goto('https://ecc-ui.invalid/dashboard')
            page.locator('#os-linux').focus()
            page.keyboard.press('ArrowRight')
            assert page.locator('#os-macos').is_checked()
            report['controls'].append('OS radio options support keyboard arrow navigation')
            command = page.locator('.os-panel.macos pre').first
            page.locator('.os-panel.macos').scroll_into_view_if_needed()
            page.screenshot(path=str(args.out / 'after-setup-320-light.png'))
            expected = command.text_content()
            copy = command.locator('..').get_by_role('button', name='Copy code', exact=True)
            copy.click()
            assert page.evaluate('window.copiedCode') == expected
            assert copy.inner_text() == 'Copied'
            report['controls'].append('Copied command is byte-for-byte the displayed command')
            page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async () => { throw new Error('denied'); }}, configurable: true})")
            copy.click()
            assert page.evaluate('window.getSelection().toString()') == expected
            assert command.locator('..').get_by_role('status').inner_text() == 'Clipboard unavailable. Code selected; copy it manually.'
            report['controls'].append('Clipboard denial selects the exact command and explains manual copying')
            page.locator('label[for="os-windows"]').click()
            assert page.locator('.os-panel.windows').is_visible()
            report['controls'].append('OS setup selection works')
            plain = browser.new_context(java_script_enabled=False, viewport={'width': 320, 'height': 900})
            plain.route('**/*', serve)
            fallback = plain.new_page()
            fallback.goto('https://ecc-ui.invalid/')
            fallback.locator('.mobile-nav summary').click()
            assert fallback.locator('.mobile-nav').get_by_role('link', name='Docs', exact=True).is_visible()
            fallback.goto('https://ecc-ui.invalid/dashboard')
            fallback.locator('label[for="os-windows"]').click()
            assert fallback.locator('.os-panel.windows pre').first.is_visible()
            assert fallback.locator('.copy-code').count() == 0
            report['controls'].append('Navigation, setup selection, and commands remain usable without JavaScript')
            plain.close()
        browser.close()
    (args.out / f'{args.label}-results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'views': len(report['views']), 'controls': report['controls']}))


if __name__ == '__main__':
    main()
