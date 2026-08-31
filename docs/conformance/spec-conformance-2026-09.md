# spec-conformance-2026-09 — otaman-cli canon-vs-reality report

**Author:** cli-agent · **Directive:** Roman-directed stability audit (via spec-agent, 2026-08-31)
**Method:** every capability spec in `otaman-specs/openspec/specs/` naming cli/its domain (16 specs),
audited requirement-by-requirement against the shipped code, plus a command-centric pass for
delivered-but-never-specced surfaces. Findings only — no spec or code was edited (spec-agent authors
repairs; code fixes triaged after).

## Scoreboard

16 specs audited. **CONFORMS: ~48 · DRIFT: 10 · SPEC-WRONG: 1 · UNSPECCED (in-domain): 4 · UNSPECCED (whole commands): 7 clusters.**
Fully-conformant specs (no drift): `agent-status-schema`, `status-auto-hooks`, `bus-uri-addressing`,
`outcome-proposal-routing` (cli parts), `destructive-command-safety`, `git-flow-branch-config` (cli parts),
`stable-runner-token` (cli parts).

---

## DRIFT (spec says X, code does Y)

1. **agent-identity-resolution — precedence chain is stale.** Spec: chain is `OTAMAN_AGENT env → .otaman agent: → current-agent`, "OTAMAN_AGENT takes precedence." Code (`identity.py:244-259`) resolves the CWD owner (platform.yaml + owner-paths) **first** and, on disagreement, **overrides `OTAMAN_AGENT` with the CWD owner** + warns. → **Code is RIGHT** (deliberate R3 security fix, poisoned-tmux incident 2026-07-08, documented `identity.py:31-44`). Spec needs a rewrite; the "env wins" scenario only passes in isolation (no platform.yaml owner for the CWD).

2. **otaman-status-command — `--json` shape.** Spec: "SHALL output a **JSON array** of agent status objects." Code (`status_cluster.py:208-221`) emits `{"enabled":…,"generated_at":…,"agents":[…]}` — a wrapping object. → Live contract mismatch; a spec-conformant consumer doing `json.loads(...)[0]` breaks. The envelope is arguably useful (matches the presence-disabled JSON at `:187`); **reconcile spec→object** is my lean, but it must be decided, not left divergent.

3. **otaman-status-command — `--agent <name>` is a filter, not a detail view.** Spec: "SHALL print **all fields** … including `outcome`, `blocked_by`, `since`, `updated_at`." Code (`status_cluster.py:199-200`) filters the compact table, which never prints `outcome`/`updated_at`. → **Spec is RIGHT**; code under-delivers.

4. **set-status-command — `--task` "required for working and waiting".** Spec lists `--task` required for those states. Code (`status_cluster.py:117`) never enforces it (falls back to existing/None). → **Code is RIGHT**: strict enforcement would break the heartbeat re-call (`otaman set-status working` no `--task`) the same spec mandates. Spec should drop "required."

5. **shared-contracts — no broadcast-whitelist warning.** Spec: `task-complete` with `to: all` → cli emits WARNING "task-complete should not broadcast; use targeted routing." Code: no `to: all` whitelist check anywhere in `bus_messaging.py:68-386`. → **Spec is right / code gap**; the warning is cheap and explicitly assigned to the cli.

6. **shared-contracts — unregistered-type handling.** Spec scenario: `type: foobar` → cli WARNs (warn-and-allow). Code (`bus_messaging.py:144-147`) **hard-rejects** unknown types (exit 2). Its `MESSAGE_TYPES` frozenset also omits registry types that are legitimately sendable (`agent-registry-change`, `watchdog-idle`, `spec-update-requested`, `pm-issue-*`), so `otaman send --type watchdog-idle` is refused. → **Code is arguably safer (typo guard)** but diverges from the spec's warn-and-allow and from the registry table. Needs reconciliation (narrow the registry's "sendable" set, or soften cli to warn).

7. **shared-contracts — fan-out traceability marker.** Spec: each copy carries `x-fanout-of: <origin-id>`. Code: CC copies carry `x-cc: true` (boolean, no origin-id; `cc_fanout.py:145-159`); notify-change copies carry **no** correlation marker (`notify_change.py:188-213`). `grep x-fanout-of` = 0. → **Spec right on intent, code under-implements**; copies can't be correlated to one logical send by id.

8. **hitl-stack — `otaman assign` default mode.** Spec: no-annotation tasks default to "the **deployment's configured default mode**." Code (`hitl/mode_annotations.py`) hard-codes `interactive` (`resolve_task_mode` returns `("interactive", False, …)`), no config lookup. → **Spec is RIGHT.** Mitigated: the cli path only *reports* mode counts; authoritative `annotation:` emission is in `otaman_plugin.map_tasks` (cli shells to it, `bus_messaging.py:882`).

9. **human-roster — doctor rejects the `approver` role as "unknown".** Spec: roster "SHALL recognize `approver` as a well-known role" + accept arbitrary extra roles. Code (`doctor.py:1485`) hard-codes `valid_roles = {cofounder,cto,cpo,developer}` and warns "unknown role(s) ['approver']" (`:1517-1528`). → **Code is WRONG** (spec internally inconsistent — older pm-sync req never updated for the newer approver req). Add `approver` to the set. Confined to pm-sync deployments (`check_human_roster` early-returns otherwise).

10. **user-facing-terminology — unannotated bare-word `MAESTRO_*` env names in user-visible strings.** Spec exempts only `.maestro` marker + `MAESTRO_ROOT`. Code emits other `MAESTRO_*` env-var names in help/output unannotated: `launch_resolve.py:220-222` (`MAESTRO_ACTIVE_CONNECTION/ACCOUNT`, `MAESTRO_CONNECTION_TYPE`), `accounts.py:764` (`--help` default `MAESTRO_TG_BOT_<ACCOUNT>`), `_launchers_registry.py:39,42` (`MAESTRO_LAUNCHERS_REGISTRY`). → **Mixed**: these are live cross-process runtime env contracts (renaming unilaterally breaks consumers). Spec's exemption list is incomplete (add a runtime-env-var exemption or schedule a coordinated migration) **and** code should carry `legacy:` annotations (it doesn't).

---

## SPEC-WRONG (claim factually false / obsolete)

1. **set-status-command — identity "resolved from `.agents/current-agent`".** Spec: "resolve the calling agent's identity from `.agents/current-agent` (same mechanism as `otaman check`)." Code (`status_cluster.py:83`) uses the full `resolve_agent_identity` chain (env / `.otaman` / platform / current-agent-as-deprecated-last-resort). The sentence predates `agent-identity-resolution` and is factually obsolete.

---

## UNSPECCED — delivered behavior with no spec requirement (in-domain)

1. **agent-identity-resolution — extra resolution steps.** `identity.py:244,267` insert a `CWD → platform.yaml (+owner-paths) → owner` step between `.otaman` and `current-agent`, and `:283-289` validate `current-agent` against declared agents. None are in the spec's 3-step chain. Resolution order is the whole contract — worth speccing.

2. **blocked-subcommand — richer surface than the 2-requirement spec.** `--clear` substring/Proposal-stem fallback with an ambiguity guard (`blocked.py:166-200`, exits 1 on >1 match), the `otaman blocked clear <stem>` **cross-agent tombstone** subcommand (`:203-285`, writes *other* agents' files by design), and `otaman blocked <slug> [--blocked-by]` registration + status hook (`:98-121`). All real, none specced.

3. **shared-contracts — types not in the registry table.** cli accepts `proposal` and `lifecycle-change` (`bus_messaging.py:46,51`), both absent from the shared-contracts type-registry table. `lifecycle-change` is registered in core `VALID_TYPES` (PR #31) but the shared-contracts registry was never updated → canonical registry is stale vs. what cli/core emit.

4. **spec-tooling — `otaman propose --help` footgun.** `propose --help` parses `--help` as the positional **title** and writes a real `spec-change-request` bus message + a blocked entry titled "--help" (`main.py:741-750` → `propose_team.py:20-124`). No per-subcommand `--help` handling exists; the same class affects every subcommand, but `propose`/`complete` have side effects. Spec says nothing about `--help`. → Genuine footgun; worth a UX/spec requirement. (A separate low-pri bug report from spec-agent 2026-08-31 already tracks the fix.)

---

## UNSPECCED — whole commands with no spec home anywhere (`specs/` **and** `changes/`)

These shipped `otaman` sub-commands have no capability-spec and no in-flight change — the "missed specs" Roman wants surfaced. Significance noted:

- **`compliance`** — generates HIPAA / ISO / GDPR compliance audit reports. High significance (regulatory output with no spec contract).
- **`presale` / `pm` / `solution` / `discovery`** — the pre-sale estimation cluster (benchmarks, component estimates, domain-expert knowledge). Significant product surface, entirely unspecced.
- **`export`** — data/workspace export. Unspecced output format.
- **`handoff`** — session/task handoff. Unspecced.
- **`retrospective`** — retrospective capture. Unspecced.
- **`gate`** — a gating verb. Unspecced.
- **`team`** — team orchestration. Unspecced.

(For contrast: `program` and `acting-lock` DO have homes — `program-lifecycle-states` change and the now-archived `single-acting-session-guard`, archived 2026-08-31.)

---

## Highest-signal for triage (Roman)

- **Two live contract mismatches consumers can hit today:** `status --json` returns an object not the spec'd array (DRIFT #2); `status --agent` never became the full-record detail view (DRIFT #3).
- **The identity precedence spec is materially wrong** (DRIFT #1) — it still says "env wins," but shipped code subordinates `OTAMAN_AGENT` to the CWD owner for a security reason. Spec rewrite, not a code change.
- **shared-contracts registry is stale** vs. what cli/core actually send (DRIFT #6, UNSPECCED #3) — affects the canonical message-type documentation.
- **`compliance` and the `presale` cluster are entirely unspecced** — the largest delivered-but-uncanonized surfaces.

## Coverage honesty

- Out-of-cli-scope requirements (bridge watch-units, runner spawn/cleanup, plugin `map_tasks` emission, `generate_agent_config.py`, otaman-core token minting, platform-schema validation) were noted but **not** verified from this repo.
- Cross-host / cross-repo facts left **UNVERIFIED**: openspec pin identity on CI + spec-agent host; the otaman-core 1.0 `find_maestro_root`/`maestro_root` internal rename (gated on core's 1.0 tag).
