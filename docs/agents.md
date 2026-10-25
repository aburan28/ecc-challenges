# Set up a worker with a coding agent

Give your agent the [ECC Challenges worker skill](https://ecc-challenges.com/skill.md)
and tell it which machine, challenge and resource limits to use. The skill
installs missing prerequisites, including Docker and NVIDIA container support
when needed, then covers worker installation, safe restarts, health checks and
troubleshooting on Linux, macOS and Windows. Your agent helps run the
contributor program that searches the curve.

## Use the skill

Paste this into a coding agent that can read URLs and run commands on your
machine:

```text
Read https://ecc-challenges.com/skill.md and help me run one ECC Challenges
worker on this machine for eccp-131. Inspect existing workers first, install
missing prerequisites for the selected setup, use a persistent state directory,
and give me the stop, resume and log commands.
Use my existing hardware; ask before renting anything. Have me enter my API
key locally. Check the worker's heartbeat and explain what is still pending.
```

Replace the challenge and specify a CPU limit or GPU if you have a preference.
A contributor API key belongs in a local private file or the installer's
hidden prompt; you do not need to paste it into your agent's chat.

Agents that support the Skills CLI can install the skill from this repository:

```sh
npx skills add aburan28/ecc-challenges --skill ecc-challenges-worker
```

The Skills CLI uses Node.js/npm. It is optional: reading `/skill.md` directly
needs neither Node.js nor the CLI.

Then ask the agent to use `ecc-challenges-worker`. Agents that accept a plain
instruction file can read `/skill.md` directly without installing anything.
In a repository checkout, the source is
`skills/ecc-challenges-worker/SKILL.md`.

## Pick the easiest setup

| Your machine | Start here |
| --- | --- |
| Linux CPU or NVIDIA GPU | The skill installs missing Docker/runtime tools, then uses your dashboard command |
| Mac | Use the native installer in the worker guide; it builds the Metal backend |
| Linux without Docker | Use the native installer in the worker guide |
| Windows | The skill installs Python, CMake and C++ build tools before the PowerShell worker installer |
| Existing cloud or cluster allocation | Use the matching cloud, Compose, Kubernetes, systemd or Slurm recipe |

The [prerequisite guide](../skills/ecc-challenges-worker/references/prerequisites.md) covers Docker, GPU support and
native build tools, including installation commands and readiness checks. The
[worker guide](running-a-worker.md) has launch commands for each system, and the
[help page](https://ecc-challenges.com/help) covers common errors. Start with
one worker. Extra API keys share the account's run limits.

## Know it is working

The dashboard shows worker heartbeat and health even before the first point
arrives. The worker logs its run ID and accepted uploads. First-point time
depends on the curve and hardware, so ask your agent to distinguish a running
process, a heartbeat, an accepted upload and a verified/indexed submission.
The public status page has aggregate figures; your signed-in dashboard has
your worker's details.

Keep the state directory when stopping or restarting. Docker commands now use
a stable container name so `docker stop NAME` pauses the worker and
`docker start NAME` resumes it. Native workers stop with Ctrl-C and resume
using the command printed by the installer. Allow up to two minutes to save.

For protocol 2, `ECC_ACTION=close` retires a run after pending uploads are
flushed; use it when you are finished with a worker. Original protocol-1
ECC2K-130 workers have a separate lifecycle. Stopping a worker temporarily
does not require closing its run.
