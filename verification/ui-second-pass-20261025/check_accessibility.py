"""Local accessibility and keyboard review; requires axe-core and Playwright."""
from pathlib import Path
import argparse
import importlib.util
import json
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
spec = importlib.util.spec_from_file_location('ui_fixture', ROOT / 'verification/ui-20261025/check_ui.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--axe', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--interactions-only', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'axe_version': None, 'pages': [], 'interactions': []}
    with TestClient(fixture.application(db=fixture.ReviewDB(), multicurve_enabled=True), base_url='https://ecc-ui.invalid') as client, sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        context = browser.new_context(reduced_motion='reduce')
        context.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => { window.copiedCode = text; }}, configurable: true});")
        def serve(route):
            path = urlsplit(route.request.url).path
            if urlsplit(route.request.url).hostname != 'ecc-ui.invalid':
                route.abort()
            elif path == '/dashboard':
                route.fulfill(status=200, content_type='text/html', body=fixture.dashboard())
            else:
                response = client.get(path)
                route.fulfill(status=response.status_code, headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding'}}, body=response.content)
        context.route('**/*', serve)
        page = context.new_page()
        source = args.axe.read_text()
        def audit(path, state):
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (path, state)
            if not page.evaluate("typeof axe !== 'undefined'"):
                page.evaluate(source)
            result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa','best-practice']}})")
            report['axe_version'] = result['testEngine']['version']
            item = {'path': path, 'state': state, 'width': page.viewport_size['width'],
                    'scheme': page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'"),
                    'violations': [{'id':v['id'], 'impact':v['impact'], 'help':v['help'],
                        'nodes':[{'target':n['target'], 'summary':n.get('failureSummary','')} for n in v['nodes']]} for v in result['violations']],
                    'incomplete_rules':[v['id'] for v in result['incomplete']]}
            report['pages'].append(item)
            print(json.dumps({k:v for k,v in item.items() if k != 'incomplete_rules'}), flush=True)
        paths = [] if args.interactions_only else ['/', '/status', '/docs', '/help', '/pollard-rho', '/docs/agents', '/docs/worker-prerequisites', '/docs/running-a-worker', '/dashboard', '/challenges/eccp-359', '/results/eccp-79']
        for scheme in ['light','dark']:
            page.emulate_media(color_scheme=scheme)
            for width in [390,1440]:
                page.set_viewport_size({'width':width,'height':900})
                for path in paths:
                    response=page.goto('https://ecc-ui.invalid'+path,wait_until='networkidle')
                    assert response.status==200
                    audit(path,'initial')
        page.emulate_media(color_scheme='light')
        page.set_viewport_size({'width':320,'height':900})
        page.goto('https://ecc-ui.invalid/')
        page.locator('.mobile-nav summary').click()
        audit('/','menu-open')
        page.goto('https://ecc-ui.invalid/dashboard')
        page.locator('label[for="os-macos"]').click()
        button = page.locator('.os-panel.macos .copy-code').first
        page.evaluate(source)
        button.click()
        assert button.get_attribute('aria-label') == 'Copied code'
        audit('/dashboard','copied-command')
        page.screenshot(path=str(args.out/'copied-command-mobile.png'))
        page.goto('https://ecc-ui.invalid/docs/worker-prerequisites')
        code = next(node for node in page.locator('pre').all()
                    if node.evaluate('node => node.scrollWidth > node.clientWidth'))
        code.focus()
        page.keyboard.press('ArrowRight')
        page.wait_for_function('node => node.scrollLeft > 0', arg=code.element_handle())
        assert code.evaluate('node => { const s = getComputedStyle(node); return s.outlineColor === s.color && parseFloat(s.outlineWidth) >= 2; }')
        report['interactions'].append('Long code examples scroll with keyboard arrows')
        report['interactions'].append('Focused command has a high-contrast inset keyboard outline')
        page.screenshot(path=str(args.out/'keyboard-code-mobile.png'))
        page.goto('https://ecc-ui.invalid/dashboard')
        table = page.locator('.table-wrap').first
        table.focus()
        page.keyboard.press('ArrowRight')
        page.wait_for_function('node => node.scrollLeft > 0', arg=table.element_handle())
        report['interactions'].append('Empty API-key table scrolls with keyboard arrows')
        page.goto('https://ecc-ui.invalid/status')
        original = page.locator('#status-live').element_handle()
        page.locator('[data-live-refresh]').click()
        page.wait_for_function('node => !node.isConnected', arg=original)
        refreshed = page.locator('#status-live .table-wrap').first
        refreshed.focus()
        page.keyboard.press('ArrowRight')
        page.wait_for_function('node => node.scrollLeft > 0', arg=refreshed.element_handle())
        report['interactions'].append('Keyboard scrolling remains available after live refresh')
        plain = browser.new_context(java_script_enabled=False, viewport={'width':320,'height':900})
        plain.route('**/*', serve)
        fallback = plain.new_page()
        fallback.goto('https://ecc-ui.invalid/docs/agents')
        command = fallback.locator('pre').first
        assert command.get_attribute('tabindex') == '0'
        command.focus()
        fallback.keyboard.press('ArrowRight')
        # Browser-side polling timers do not run when JavaScript is disabled.
        for _ in range(40):
            if command.evaluate('node => node.scrollLeft') > 0:
                break
            time.sleep(0.05)
        assert command.evaluate('node => node.scrollLeft') > 0
        report['interactions'].append('Command keyboard scrolling works without JavaScript')
        plain.close()
        browser.close()
    (args.out/'accessibility-results.json').write_text(json.dumps(report,indent=2)+'\n')
    assert not any(item['violations'] for item in report['pages']), 'Accessibility violations remain; inspect the report'
    print(json.dumps({'views':len(report['pages']), 'violations':sum(len(x['violations']) for x in report['pages']), 'interactions':report['interactions']}))


if __name__ == '__main__':
    main()
