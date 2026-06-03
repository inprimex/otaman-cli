# OpenSpec

This directory holds OpenSpec-format change proposals + capability specs.

Layout:

- `specs/<capability>/spec.md` — long-lived capability specifications
- `changes/<change-name>/` — proposed changes (proposal.md + design.md + tasks.md)
- `archive/` — completed changes (created on first archive)

Use the `otaman` CLI (or `openspec` CLI directly) to author + manage entries:

- `otaman propose <title>` — propose a change via the bus
- `otaman assign <change>` — map a change's tasks.md to repo owners
- `otaman complete <change> --tasks T` — mark assigned tasks complete
