# Launch cleanup pass — 26 October 2026

Base: `main` at `0f416ee` (PR #65 merged), plus the draft PR #64 commit
`abbd047` cherry-picked (keyboard access and accessible labels).

## What changed and why

- **Solved challenges read as done.** The home and status tables showed every
  solved challenge as "100% · Search complete" with a progress bar, which looks
  like a live search that happened to finish. They now show a check mark,
  "Done", the solve date and who reported it (section 6.1 of Certicom's 2009
  report), and the status cell links to the published result. Group headings
  say "Open — 14 unsolved challenges" and "Solved — 10 challenges, closed
  between 1997 and 2004".
- **Honest headline count.** "Open here 24 of 24 challenges" counted the ten
  solved curves, which only accept test runs. The stat is now "Open challenges
  14 — 10 of 24 were solved by 2004" on both pages.
- **Status page progress cards** are shown only for challenges with worker
  activity (a run seen in the last 24 hours, a nonzero rate, or at least 0.01%
  of expected work). Thirteen all-zero cards ("— operations · 0 points ·
  0.0%") added a screen of nothing; a one-line note now says how many open
  challenges are waiting for a worker.
- **Open rows use the accent color**, not the warning amber, and the tag reads
  "Accepting work" instead of "Open here".
- **Dashboard order follows the account.** An account with no keys or no runs
  sees the three setup steps first (create a key, run the worker, check it);
  an account with runs sees its progress and runners first. The key table's
  empty state and the step headings say what to do next.
- **Less chrome.** Eyebrow labels that repeated the heading ("Status / Live
  status", "Dashboard / Your workers", "Guides / Documentation", "Support /
  Help") are gone; the result page gets a breadcrumb and names who solved it.
- **README** opens with "Take part in three steps" and a machine → command
  table; the release-status paragraph moved under its own heading. The docs
  bundle was rebuilt.
- **CI**: pushes to `main` are no longer cancelled by the next merge
  (`cancel-in-progress` now applies to pull requests only), so main's history
  shows a result for every commit instead of "cancelled".

## Checks

- `python -m pytest -q tests` with a local PostgreSQL (see the result in the
  pull request). Tests asserting the old "100% / Search complete" markup and
  the old group headings were updated to the new copy.
- Browser review of `/`, `/status`, `/dashboard` (empty and active accounts),
  `/challenges/eccp-131`, `/results/ecc2-79`, `/docs`, `/docs/project`,
  `/docs/running-a-worker`, `/help` and `/pollard-rho` at desktop and 375 px
  widths in light and dark mode, served locally from synthetic data with no
  database, AWS or GitHub access.
- The public site at review time served `git-12eb82e9cac9`, deployed while this
  pass was in progress; none of these changes are live until the next deploy.

## Second pass: copy and consistency

A full read of every published page and document, done after the changes
above.

- **Terminology.** One name per thing: "worker" for the process ("Your runs",
  not "Your runners"), "Protocol 1 (ECC2K-130 sigma)" and "Protocol 2 (all
  curves)" instead of original/new worker, "state directory" to match
  `ECC_STATE_DIR`, "Archived points" and "Collision odds" on every page that
  shows them, "distinguished point" spelled out before "DP" is used, "arm64"
  and "x86-64" for Linux and macOS, "Accepting work" on the challenge page
  too.
- **Attribution.** Solved rows and result pages say "reported by", matching
  section 6.1 of Certicom's document, which lists who communicated each
  result; "solved by" overstated it for team efforts.
- **Dates.** The catalog date renders as "10 November 2009" (new `longdate`
  filter) instead of ISO; the remaining prose dates read "19 October 2026".
- **Accuracy fixes.** The home page's definition of a distinguished point no
  longer says "a run of zero bits" (wrong for both walks); the rho page's √n
  for 130 bits is 2^65, not 2^64; the README names `ECC_GPU` (the dashboard's
  `GPU_ID` is a shell variable that feeds it); the Compose doc says the example
  digest is amd64-only and names `ECC_CPU_THREADS`; the README's image table no
  longer lists `:latest` tags, since the gateway publishes digests only; the
  operations doc describes the `point-index-merge` log line as the code prints
  it; the worker guide names "Refresh now", the control that exists.
- **Help page** items are now a symptom quoted from the worker's own messages
  followed by one action each.
- **Filler removed** across README, CONTRIBUTING, the docs, the problems
  records, the skill and every template; no bolded sentences, callouts or
  rhetorical questions remain.

Checks: `pytest tests` against a temporary PostgreSQL with a UTF-8 locale
(267 passed, 1 skipped; a `C`-locale `initdb` makes psycopg return bytes and
fails 50 tests, which is the environment, not the code);
`scripts/build_site_docs.py --check`; browser review of the same routes as
above at desktop and 375 px, light and dark, from synthetic data.

Known and left alone: the worker binary's own `ECC_CAMPAIGN` default is
`ecc2k-130` (`worker/protocol.py`) while the installers, Compose file and
dashboard commands all set `eccp-131`; the docs state both. The live site at
review time returned 404 for `/skill.md`, `/docs/agents` and
`/docs/worker-prerequisites`, so production is running a build older than
`main`; the next deploy fixes that.
