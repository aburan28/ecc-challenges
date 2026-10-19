# Modal non-root volume repair

All five attempts used the pinned public GHCR worker digest `f6333f0c...` and
CPU-only sandboxes. No contributor key, AWS credentials or database data were
supplied. These are filesystem engineering controls, separate from GPU point
generation, gateway admission and sustained service capacity.

| Attempt | Setup | Result |
| --- | --- | --- |
| 1 | `chown('/data', ..., follow_symlinks=False)`, drop UID, write mount root | Failed with remote PermissionError13; local empty-output parsing masked it with IndexError. Original receipt/log/probe retained. |
| 2 | Create private owned child, then run as10001 | Two fresh sandboxes passed; actual0600 ownership and externally read hashes matched. |
| 3 | First shared helper rejected a symlink at `/data` | Failed before worker start. The helper's strict mount check was incompatible with Modal's provider-created alias. |
| 4 | Resolve trusted mount alias; private child and restartable migration | Both sandboxes passed; actual mount alias confirmed as symlink. This version preceded atomic marker publication. |
| 5 | Final shared helper with atomic migration marker and nonce preservation | Both sandboxes passed, counters1 then2, UID/GID10001, actual file owner10001, mode0600, no remaining root checkpoint, external hashes match. |

Attempt5 used Modal app `ap-4AvJGkkjOb7dvVKZIZBHxi`; sandbox IDs and the complete
image digest are in its receipt. Its setup SHA256 is
`9211407a376609a5789aa62b2d2dd5ca1d41730d41ad0dd52e92fb8b55f3704a`.
The first counter's committed hash is
`bbf5bbfdd015a19c5e9c1c025d136a463791f7889abd13c492bb582c5d306d44`;
the second is
`ca9e2da6722f8026c6fbb0dc60cbd6e71f887a71b2b359d7d396d555275b4c85`.

The final probe seeds a public counter in a file named `generic-state.json` to
exercise migration. It is not an actual contributor checkpoint. The launcher
and GPU contributor check embed the same `STATE_SETUP` source; six focused local
tests additionally cover interrupted marker writes, interrupted file moves,
conflicting run identities, path escape and persisted symlinks.

The attempt3/4 frozen source snapshots were reconstructed from the intervening
edits and accepted only after their exact script, setup and boot hashes matched
the already recorded receipts. Attempt1/2 scripts were copied unchanged from
their preserved local sources. Attempt5 sources match the final helper directly.
`manifest.json` binds the raw artifacts. No failed receipt was replaced.

Modal's background volume commits can restore an older complete checkpoint
after interruption. Two-container success does not establish synchronous remote
commit for every worker fsync or distributed filesystem locking. Continue to
use one named writer per volume and preserve stable gateway batch replay.
