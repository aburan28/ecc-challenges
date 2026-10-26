# Second contributor UI pass

Base: merged main `12eb82e9cac96cfb6eed520636617be586d61d30`.
The first UI cleanup and prerequisite skill changes are both included in this
review. The existing palette, type hierarchy and curve illustration remain
appropriate; this pass addresses demonstrated interaction and accessibility
problems using the same frontend-design guidance.

## Findings and changes

- Long commands, wide tables and the wide Pollard rho diagrams did not all have
  keyboard access. Their scrolling containers now have explicit tab stops in
  server-rendered HTML, including packaged Markdown. Keyboard arrows work with
  JavaScript disabled and on tables replaced by live refresh.
- A focused command's outline was clipped by its copy-toolbar container. An
  inset outline now uses the command foreground color for clear contrast.
- The API-key table had an empty action-column heading, now labeled Actions.
- Tiny progress values used scientific notation in `aria-valuenow`, which axe
  rejects as an invalid ARIA number. They now use fixed decimal notation plus
  readable `aria-valuetext`. Scientific work calculations are unchanged.
- The copied command button and solved-result links had accessible names that
  did not include their visible text. The copy label now follows its success,
  failure and reset states; result links retain the visible solved date plus
  their challenge context.

## Verification

- 71 focused pytest tests passed across public pages, contributor onboarding,
  API-key activity, worker health and campaign/catalog behavior. This includes
  a new regression for a nonzero, sub-0.01-percent accessible progress value.
- The existing browser check passed 80 views in light/dark mode at
  320/390/768/1440 pixels, plus its menu, clipboard and no-JavaScript controls.
- axe-core 4.14.0 passed 46 page/state scans: eleven routes at 390/1440 pixels
  in both themes, plus an expanded mobile menu and the copied-command state.
  The scan includes WCAG 2.0/2.1 A/AA, WCAG 2.2 AA and best-practice rules.
  Zero automated violations; incomplete/manual-review rules remain in the
  report and are not counted as passes.
- A final interaction pass added two clean state scans and physically checked
  horizontal keyboard scrolling for code and an empty key table, scrolling
  after live replacement, the command focus outline, and command scrolling
  without JavaScript. Screenshots were inspected.
- JavaScript syntax and whitespace checks passed. Generated site documentation
  is current. The browser fixture intercepts external requests and uses local
  synthetic counts/account details; clipboard contents stay inside the fixture.

These checks are not a full accessibility certification or real signed-in
production onboarding.

Reproduce with the project test dependencies, Playwright, Chrome, and the
axe-core 4.14.0 `axe.min.js` file:

```sh
PYTHONPATH=src python -m pytest -q tests/test_pages.py \
  tests/test_contributor_onboarding.py tests/test_key_activity.py \
  tests/test_worker_health.py tests/test_catalog.py
python verification/ui-second-pass-20261025/check_accessibility.py \
  --axe /absolute/path/to/axe.min.js --out /tmp/ecc-accessibility
python verification/ui-20261025/check_ui.py --label after --out /tmp/ecc-ui
```

## Public release observation

At review time, the public homepage still had the previous palette/navigation
and did not load `/assets/ui.js`. `https://ecc-challenges.com/skill.md` returned
HTTP 404. The merged source and local checks therefore do not establish a
public release of the new UI or canonical skill endpoint. Until release, the
merged repository skill and its bundled prerequisite guide are available from
GitHub. No production writes were part of this review.
