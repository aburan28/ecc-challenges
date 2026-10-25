# Contributor UI review

Reviewed against main `a1f1e7ba5cc4b13d3dbce8d8395198dc44fd0b87` using
[Anthropic's frontend-design skill](https://github.com/anthropics/skills/blob/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/frontend-design/SKILL.md).
All screenshots use synthetic local data, including the counts and signed-in
account. They are design previews, not production telemetry.

## Design direction

Keep the elliptic-curve construction as the defining visual. Use a cool
scientific-workbench palette: page `#f4f7fc`, surface `#ffffff`, ink `#152642`,
secondary text `#4e607c`, rule `#ced8e6`, and action `#254dc0`. Dark mode uses
navy surfaces and pale blue links. Headings and UI use locally available
Avenir Next/Segoe UI/system sans; mathematical notation retains the serif
stack and commands use monospace. No font download is required.

The reviewed layout puts the invitation and setup paths beside the curve,
then shows current figures and system-specific setup links. Mobile uses a
native disclosure menu instead of removing navigation links. Dashboard
shortcuts take contributors directly to keys, setup and activity.

```text
Desktop: brand + navigation
         invitation / setup actions | curve construction
         current figures
         Linux | macOS | Windows

Mobile:  brand + Menu
         invitation / setup actions
         curve construction
         figures and setup guides
```

The plan was revised away from decorative labels and repeated boxed cards:
sentence-case labels describe their content, OS links use a simple list, and
the existing curve diagram carries the visual emphasis.

## Validation

- 69 focused tests passed across pages, contributor onboarding, API-key
  activity, worker health and campaign/catalog behavior.
- `check_ui.py` passed 80 views: ten routes at 320/390/768/1440 pixels in light
  and dark mode, with reduced motion requested. No page overflow or unhandled
  JavaScript errors. Includes representative small/large challenge pages and
  a published-result page.
- Browser interactions passed: mobile menu opening, visible guide links,
  Escape with focus restoration, keyboard OS selection, exact clipboard
  content, clipboard-denied selection/message, and OS panel switching.
- With JavaScript disabled, navigation, OS selection and command text remain
  usable. Clipboard testing uses an in-browser stub and does not read or
  replace the OS clipboard.
- Main body, secondary, faint and link text meet 4.5:1 against the page in both
  palettes; the lowest checked pair is light faint text at 4.84:1. Code and
  code-comment colors also pass. This is a token contrast check, not a full
  accessibility certification.
- `node --check src/ecc_challenges/assets/ui.js` and `git diff --check` passed.

Reproduce with the project test dependencies, Playwright and Chrome available:

```sh
PYTHONPATH=src python -m pytest -q tests/test_pages.py \
  tests/test_contributor_onboarding.py tests/test_key_activity.py \
  tests/test_worker_health.py tests/test_catalog.py
python verification/ui-20261025/check_ui.py --label after --out /tmp/ecc-ui-review
```

The browser check intercepts requests and renders the local app/templates.
The signed-in dashboard is a template fixture; the PostgreSQL-backed key and
worker flows are covered by the regression tests. Public release and real
signed-in production onboarding remain separate checks.
