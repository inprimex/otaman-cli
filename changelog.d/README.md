# changelog.d — one fragment per shipped change

A **fragment** is the customer-facing sentence about a change, written at the
time the change is made, by the person who made it. At release, the assembler
collects every repo's fragments into one set of notes.

This exists because the alternative does not work. Release notes drafted after
the fact by summarizing commit and PR history spliced raw internal error strings
into public copy — the history is written for us, not for customers, and no
amount of care at cut time recovers intent that was never recorded.

## Writing one

Add a file named for your PR:

```
changelog.d/<pr-number>.<category>.md
```

Categories: `feature`, `fix`, `doc`, `removal`, `misc`.

Write one or two sentences **for a customer**: what changed and why it matters
to them. Not what you refactored — what they can now do, or no longer have to
work around.

```markdown
<!-- changelog.d/168.fix.md -->
Console surfaces no longer re-read the same files on every keystroke, so large
programs open several times faster.
```

## The merge gate

CI blocks a PR that changes shipped code and carries no fragment:

```
python -m otaman_core.changelog_fragment --check --base origin/main --pr-body-file <f>
```

Humans get the same verdict from `otaman policy check-changelog` — same
evaluator, same rules, so local and CI never disagree.

Docs-only, CI-only or test-only PRs pass without a fragment. If a shipped-code
PR genuinely needs no customer-facing note, put `changelog: exempt` in the PR
body and say why in the description.

## Clearing

Do **not** empty this directory by hand or by glob. At a cut, the release
records a manifest of exactly which fragments it consumed, and the owner clears
by that manifest:

```
otaman release clear-fragments <manifest>
```

It deletes only the manifest-named files, verifies each one's content hash
first, and keeps any fragment edited since the cut — that text was never
released, so it belongs in the next one. This README is never removed.
