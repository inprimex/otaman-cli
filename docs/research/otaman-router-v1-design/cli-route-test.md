# `otaman route test` + `otaman route show` — CLI Design

> **Author**: cli-agent  
> **Date**: 2026-05-27  
> **Tasks**: otaman-router-v1-design 6.1 + 6.2  
> **Output**: Design of `otaman route test` (dry-run routing decision) and
>             `otaman route show` (active configuration + backend availability).

---

## Part 1 — `otaman route test` (task 6.1)

### Purpose

`otaman route test` lets an operator dry-run a routing decision without spawning an
actual session. It answers: *"If I submitted a task of classification X from org Y,
which harness and backend would the router select — and why?"*

Two use cases:
1. **Debugging** — "Why is my PHI task being rejected?"
2. **Pre-flight validation** — "After changing routing.yaml, does compliance routing
   still work as expected before I restart the bridge?"

---

### Command surface

```
otaman route test [options]

options:
  --classification internal|sensitive|phi|pii|regulated
                        Data classification to test (default: internal)
  --org SLUG            Organisation context for per-org overlay lookup
                        (default: active org from ~/.otaman/active-org)
  --task-type TYPE      Task type hint (e.g. code_review, data_analysis, file_edit)
                        Used by rule 2 (specialty routing). Default: unset.
  --preferred-harness HARNESS
                        Preferred harness hint (e.g. claude-code, openai-agents-sdk).
                        Used by rule 2. Default: none.
  --user USER_ID        Simulated user id for compliance evaluation. Default: current user.
  --role ROLE           Simulated role (e.g. developer, operator). Default: developer.
  --budget USD          Remaining cost budget in USD. Used by rule 3. Default: unlimited.
  --live                Call the running router sidecar at POST /route instead of
                        simulating locally. Requires the router to be running.
  --router-url URL      Override the router endpoint (default: http://localhost:8080).
                        Only used with --live.
  --json                Emit JSON output instead of formatted text.
  --verbose             Show rule evaluation trace (which rules were evaluated,
                        which fired, which passed through).
```

---

### Execution modes

#### Default mode: local simulation

When `--live` is NOT passed, `otaman route test` reads `routing.yaml` and applies the
four v1 rules locally — no network call required, no router process needed.

This is the preferred mode for:
- Pre-flight validation after editing `routing.yaml`
- Running in CI (no sidecar available)
- Quick debugging on a dev laptop

Local simulation uses the same rule evaluation logic that the router would use. The
`routing_id` in the output is prefixed `test-` to distinguish from live routing IDs.

#### Live mode: `--live`

Calls `POST <router-url>/route` with a constructed `RoutingRequest`. Returns the actual
decision from the running router instance. Use this to validate end-to-end behaviour
including any runtime state the router holds (e.g., cached LiteLLM pricing data,
backend availability after health checks).

---

### Example outputs

#### Rule 4 (default) — CE deployment

```
$ otaman route test --classification internal --org acme-corp

  Routing dry-run
  ───────────────────────────────────────────────────────────────
  Org:              acme-corp
  Classification:   internal
  Task type:        (unset)
  User:             romans  Role: developer
  Budget:           unlimited
  Edition:          CE  →  only rule 4 (default) is active

  Result
  ───────────────────────────────────────────────────────────────
  ✓ ROUTED
  Rule matched:     4 — default
  Harness:          claude-code
  Backend:          anthropic
  Model:            claude-sonnet-4-6
  Compliance:       ✓ cleared  (internal data; anthropic backend allows [internal, sensitive])
  Cost estimate:    ~$0.012 / session  (source: static manifest)
  Routing ID:       test-a3f9b2c1
```

#### Rule 1 (compliance block) — EE deployment

```
$ otaman route test --classification phi --org acme-healthcare

  Routing dry-run
  ───────────────────────────────────────────────────────────────
  Org:              acme-healthcare
  Classification:   phi
  Org overlay:      ~/orgs/acme-healthcare/routing.yaml  [loaded]
  Edition:          EE  →  all four rules active

  Rule evaluation trace
  ───────────────────────────────────────────────────────────────
  Rule 1 (compliance):  CHECKING  phi classification...
    backend: anthropic        compliance=[internal, sensitive]  →  BLOCKED (phi not in list)
    backend: vllm-local       compliance=[internal, sensitive, phi, pii, regulated]  →  ELIGIBLE
    → Selected: vllm-local

  Result
  ───────────────────────────────────────────────────────────────
  ✓ ROUTED
  Rule matched:     1 — compliance
  Harness:          claude-code
  Backend:          vllm-local
  Model:            meta-llama/Meta-Llama-3-8B-Instruct
  Compliance:       ✓ cleared  (phi classification; vllm-local is fully on-prem)
  Cost estimate:    ~$0.000 / session  (on-prem; no token cost)
  Routing ID:       test-d7e1a4b8
```

#### Rule 1 — no compliant backend available (rejection)

```
$ otaman route test --classification regulated --org acme-corp

  Routing dry-run
  ───────────────────────────────────────────────────────────────
  Org:              acme-corp
  Classification:   regulated
  Edition:          EE

  Rule evaluation trace
  ───────────────────────────────────────────────────────────────
  Rule 1 (compliance):  CHECKING  regulated classification...
    backend: anthropic        compliance=[internal, sensitive]  →  BLOCKED
    backend: azure-openai     compliance=[internal, sensitive, phi, regulated]  →  ELIGIBLE
      BUT: azure-openai status = UNAVAILABLE  (health check failed 3 min ago)
    → No eligible backend found

  Result
  ───────────────────────────────────────────────────────────────
  ✗ BLOCKED
  Rule matched:     1 — compliance
  Reason:           No backend is both (a) cleared for 'regulated' classification
                    and (b) currently available.
  Blocked backends: anthropic (not cleared), azure-openai (unavailable)
  Suggestion:       Check azure-openai backend health: otaman route show --backend azure-openai
                    Or add a compliant backend to routing.yaml.
```

#### Rule 2 (specialty) — preferred harness

```
$ otaman route test --classification internal --task-type data_analysis \
                    --preferred-harness openai-agents-sdk --verbose

  Routing dry-run
  ───────────────────────────────────────────────────────────────
  Org:              (active: _platform)
  Classification:   internal
  Preferred harness: openai-agents-sdk
  Edition:          EE

  Rule evaluation trace
  ───────────────────────────────────────────────────────────────
  Rule 1 (compliance):  PASS  (internal: all backends eligible)
  Rule 2 (specialty):   CHECKING  preferred_harness=openai-agents-sdk...
    backend: anthropic (harness: claude-code)  →  harness mismatch, skip
    backend: azure-openai (harness: openai-agents-sdk)  →  MATCH
    → Selected: azure-openai (openai-agents-sdk harness)
  Rule 3 skipped (rule 2 matched)
  Rule 4 skipped (rule 2 matched)

  Result
  ───────────────────────────────────────────────────────────────
  ✓ ROUTED
  Rule matched:     2 — specialty (preferred harness)
  Harness:          openai-agents-sdk
  Backend:          azure-openai
  Model:            gpt-4o
  Compliance:       ✓ cleared
  Cost estimate:    ~$0.024 / session  (source: litellm)
  Routing ID:       test-f2c8d3a1
```

#### Rule 3 (cost budget)

```
$ otaman route test --classification internal --budget 0.005

  Rule 3 (cost budget):  CHECKING  budget=0.005 USD...
    backend: anthropic   estimate=$0.012  →  OVER BUDGET
    backend: vllm-local  estimate=$0.000  →  WITHIN BUDGET (on-prem)
    → Selected: vllm-local (cheapest eligible)

  ✓ ROUTED via rule 3 (cost budget)  →  vllm-local / claude-code
```

---

### JSON output (`--json`)

```json
{
  "status": "routed",
  "rule_matched": "compliance",
  "harness": "claude-code",
  "backend": "vllm-local",
  "model": "meta-llama/Meta-Llama-3-8B-Instruct",
  "compliance_cleared": true,
  "cost_estimate_usd": 0.0,
  "routing_id": "test-d7e1a4b8",
  "trace": [
    {"rule": 1, "name": "compliance", "outcome": "matched",
     "backends_evaluated": [
       {"name": "anthropic", "eligible": false, "reason": "phi not in compliance list"},
       {"name": "vllm-local", "eligible": true, "selected": true}
     ]
    },
    {"rule": 2, "name": "specialty", "outcome": "skipped"},
    {"rule": 3, "name": "cost_budget", "outcome": "skipped"},
    {"rule": 4, "name": "default", "outcome": "skipped"}
  ],
  "input": {
    "org_id": "acme-healthcare",
    "classification": "phi",
    "task_type": null,
    "preferred_harness": null,
    "user_id": "romans",
    "roles": ["developer"],
    "cost_budget_remaining_usd": null
  }
}
```

Blocked result:
```json
{
  "status": "blocked",
  "rule_matched": "compliance",
  "reason": "no_eligible_backend",
  "blocked_backends": [
    {"name": "anthropic", "reason": "not_cleared_for_regulated"},
    {"name": "azure-openai", "reason": "backend_unavailable"}
  ],
  "routing_id": "test-c9d2e5f3"
}
```

---

### `--live` mode: RoutingRequest construction

When `--live` is passed, the CLI constructs a `RoutingRequest` JSON and POSTs to
`POST <router-url>/route`:

```python
def _build_routing_request(args, edition: str, org_id: str) -> dict:
    return {
        "session_id": f"test-{uuid.uuid4().hex[:8]}",
        "org_id": org_id,
        "user_id": args.user or os.environ.get("USER", "unknown"),
        "roles": [args.role] if args.role else ["developer"],
        "task_classification": args.classification,
        "task_type": args.task_type,
        "cost_budget_remaining_usd": args.budget,
        "preferred_harness": args.preferred_harness,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
```

The `session_id` uses a `test-` prefix so the router can distinguish dry-run calls
from real sessions in its audit log.

Error handling for `--live`:
- `409 Conflict` → print BLOCKED result with rule + constraint details from response body
- `503 Service Unavailable` → print error: "Router not ready. Try again in a moment or use local simulation (drop --live)."
- Connection refused → print: "Cannot reach router at <url>. Is it running? Use 'otaman route show --check' to verify."

---

### Argparse wiring

```python
p_route = sub.add_parser("route", help="routing policy inspection and testing")
route_sub = p_route.add_subparsers(dest="route_cmd", required=True)

p_test = route_sub.add_parser(
    "test",
    help="dry-run a routing decision without spawning a session",
)
p_test.add_argument(
    "--classification",
    choices=["internal", "sensitive", "phi", "pii", "regulated"],
    default="internal",
    help="data classification to test (default: internal)",
)
p_test.add_argument("--org", metavar="SLUG",
                    help="org context (default: active org)")
p_test.add_argument("--task-type", metavar="TYPE",
                    help="task type hint for specialty routing")
p_test.add_argument("--preferred-harness", metavar="HARNESS",
                    help="preferred harness hint for specialty routing")
p_test.add_argument("--user", metavar="USER_ID",
                    help="simulated user id (default: current user)")
p_test.add_argument("--role", metavar="ROLE", default="developer",
                    help="simulated role (default: developer)")
p_test.add_argument("--budget", type=float, metavar="USD",
                    help="remaining cost budget in USD (default: unlimited)")
p_test.add_argument("--live", action="store_true",
                    help="call the running router sidecar instead of simulating locally")
p_test.add_argument("--router-url", default="http://localhost:8080",
                    metavar="URL",
                    help="router endpoint for --live mode (default: http://localhost:8080)")
p_test.add_argument("--json", action="store_true", help="emit JSON output")
p_test.add_argument("--verbose", action="store_true",
                    help="show per-rule evaluation trace")
p_test.set_defaults(func=cmd_route_test)
```

---

---

## Part 2 — `otaman route show` (task 6.2)

### Purpose

`otaman route show` gives operators a live view of the current routing configuration:
which backends are declared, which are healthy, which rules are active, and what the
effective policy is for the active org.

Use cases:
1. **Ops dashboard** — quick sanity check after deploying a new `routing.yaml`
2. **Incident investigation** — "Is the azure-openai backend down? Is that why PHI tasks are failing?"
3. **Pre-deployment validation** — verify routing.yaml parsed correctly before restarting the bridge

---

### Command surface

```
otaman route show [options]

options:
  --org SLUG        Show the effective policy for a specific org (platform + overlay merged).
                    Default: active org from ~/.otaman/active-org.
  --backend NAME    Show detailed status for a specific backend only.
  --check           Perform live health checks on all configured backends (HTTP HEAD /
                    connectivity probe). Slower but accurate.
  --json            Emit JSON output.
  --routing-yaml PATH
                    Override the routing.yaml path (default: .otaman/routing.yaml).
```

---

### Example outputs

#### Full show — CE deployment

```
$ otaman route show

  Routing configuration
  ═══════════════════════════════════════════════════════════════
  Config file:   ~/.otaman/routing.yaml
  Edition:       CE  →  rule 4 (default) only
  Active org:    _platform  (no per-org overlay)

  Default
  ───────────────────────────────────────────────────────────────
  Harness:  claude-code
  Backend:  anthropic
  Model:    claude-sonnet-4-6

  Backends (1 declared)
  ───────────────────────────────────────────────────────────────
  NAME          TYPE        MODEL                  COMPLIANCE      STATUS
  anthropic     anthropic   claude-sonnet-4-6      internal,       ● available
                                                   sensitive       (not checked — use --check)

  Rules (CE: rule 4 only)
  ───────────────────────────────────────────────────────────────
  Rule 1  compliance     ○ inactive  (EE + features.compliance_routing required)
  Rule 2  specialty      ○ inactive  (EE required)
  Rule 3  cost_budget    ○ inactive  (EE required)
  Rule 4  default        ● active    → anthropic / claude-code

  Cost data
  ───────────────────────────────────────────────────────────────
  Source:  static manifest  (LiteLLM not configured)
```

#### Full show — EE deployment, multi-backend

```
$ otaman route show --org acme-healthcare --check

  Routing configuration
  ═══════════════════════════════════════════════════════════════
  Config file:   ~/.otaman/routing.yaml
  Org overlay:   ~/orgs/acme-healthcare/routing.yaml  [loaded — 2 backends restricted]
  Edition:       EE  →  all four rules active
  Active org:    acme-healthcare

  Default
  ───────────────────────────────────────────────────────────────
  Harness:  claude-code
  Backend:  vllm-local  (overridden by org overlay — org restricts to on-prem only)
  Model:    meta-llama/Meta-Llama-3-8B-Instruct

  Backends (3 declared, 1 restricted by org overlay)
  ───────────────────────────────────────────────────────────────
  NAME           TYPE              MODEL                    COMPLIANCE                  STATUS
  anthropic      anthropic         claude-sonnet-4-6        internal, sensitive         ✗ restricted (org overlay)
  azure-openai   openai-compat.    gpt-4o                   internal, sensitive,        ✗ restricted (org overlay)
                                                            phi, regulated
  vllm-local     openai-compat.    meta-llama/...           internal, sensitive,        ● available  (200ms)
                                                            phi, pii, regulated

  Rules (EE: all four active)
  ───────────────────────────────────────────────────────────────
  Rule 1  compliance     ● active    (features.compliance_routing = true)
  Rule 2  specialty      ● active
  Rule 3  cost_budget    ● active    budget_enforcement = soft
  Rule 4  default        ● active    → vllm-local / claude-code  (org overlay)

  Cost data
  ───────────────────────────────────────────────────────────────
  Source:     litellm  (http://litellm:4000)
  Status:     ● reachable
  Cache age:  12 minutes  (TTL: 60 min)
  Prices:
    vllm-local  $0.000 / 1M tokens  (on-prem)
```

#### Backend-level detail (`--backend <name>`)

```
$ otaman route show --backend azure-openai --check

  Backend: azure-openai
  ─────────────────────────────────────────────────────────────
  Type:            openai-compatible
  Endpoint:        ${secret:azure_openai_endpoint}  →  resolved ✓  (https://acme.openai.azure.com)
  API key:         ${secret:azure_openai_key}  →  resolved ✓  (****)
  Model:           gpt-4o
  Compliance:      internal, sensitive, phi, regulated
  Harness:         openai-agents-sdk

  Health check (--check):
    GET https://acme.openai.azure.com/openai/deployments — 200 OK  (143ms)
    Status: ● available

  Org restrictions:
    acme-corp         available
    acme-healthcare   ✗ restricted (org overlay excludes azure-openai)
    staging           available
```

---

### JSON output (`--json`)

```json
{
  "edition": "ee",
  "config_file": "/home/user/.otaman/routing.yaml",
  "org": "acme-healthcare",
  "org_overlay": "/home/user/orgs/acme-healthcare/routing.yaml",
  "default": {
    "harness": "claude-code",
    "backend": "vllm-local",
    "model": "meta-llama/Meta-Llama-3-8B-Instruct",
    "source": "org_overlay"
  },
  "backends": [
    {
      "name": "anthropic",
      "type": "anthropic",
      "model": "claude-sonnet-4-6",
      "compliance": ["internal", "sensitive"],
      "status": "restricted",
      "restriction_source": "org_overlay",
      "health": null
    },
    {
      "name": "vllm-local",
      "type": "openai-compatible",
      "model": "meta-llama/Meta-Llama-3-8B-Instruct",
      "compliance": ["internal", "sensitive", "phi", "pii", "regulated"],
      "status": "available",
      "health": {"latency_ms": 200, "checked_at": "2026-05-27T14:55:00Z"}
    }
  ],
  "rules": [
    {"id": 1, "name": "compliance", "active": true, "gate": "features.compliance_routing"},
    {"id": 2, "name": "specialty", "active": true, "gate": null},
    {"id": 3, "name": "cost_budget", "active": true, "gate": null,
     "budget_enforcement": "soft"},
    {"id": 4, "name": "default", "active": true, "gate": null,
     "resolved_backend": "vllm-local"}
  ],
  "cost": {
    "source": "litellm",
    "endpoint": "http://litellm:4000",
    "status": "reachable",
    "cache_age_minutes": 12
  }
}
```

---

### `--check` health probe implementation

For each backend whose status is not `restricted`, the CLI performs a lightweight
connectivity check:

```python
def _probe_backend(backend: dict) -> dict:
    """Quick health check: resolve secrets + HEAD probe."""
    btype = backend["type"]
    if btype == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": _resolve_secret(backend.get("api_key", ""))}
    elif btype == "openai-compatible":
        url = backend["endpoint"].rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {_resolve_secret(backend.get('api_key', ''))}"}
    else:
        return {"status": "unknown", "reason": f"no probe for type {btype!r}"}
    try:
        start = time.monotonic()
        r = requests.head(url, headers=headers, timeout=5)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {"status": "available" if r.ok else "error",
                "http_status": r.status_code, "latency_ms": elapsed_ms}
    except requests.ConnectionError:
        return {"status": "unreachable", "reason": "connection refused"}
    except requests.Timeout:
        return {"status": "timeout", "reason": "exceeded 5s timeout"}
```

Health checks run concurrently via `concurrent.futures.ThreadPoolExecutor` to keep
total latency under 5 seconds even with multiple backends.

**Secret resolution during health check**: the CLI calls `_resolve_secret()` which
reads from the same `SecretBackend` chain used by the router. On CE/dev with env-file
backend, this reads from `~/.otaman/secrets.env`. Unresolvable secrets show `→  NOT RESOLVED  ✗`
and the probe is skipped.

---

### Argparse wiring

```python
p_show = route_sub.add_parser(
    "show",
    help="show active routing configuration and backend availability",
    aliases=["status"],   # otaman route status == otaman route show
)
p_show.add_argument("--org", metavar="SLUG",
                    help="show effective policy for a specific org (default: active org)")
p_show.add_argument("--backend", metavar="NAME",
                    help="show detail for a specific backend only")
p_show.add_argument("--check", action="store_true",
                    help="perform live health checks on all backends (slower)")
p_show.add_argument("--json", action="store_true", help="emit JSON output")
p_show.add_argument("--routing-yaml", metavar="PATH",
                    help="override routing.yaml path")
p_show.set_defaults(func=cmd_route_show)
```

`route status` as an alias for `route show` is intentional — both read naturally in
conversation ("what's the route status?") and operators can use either form.

---

---

## Combined implementation sketch

```
src/otaman_cli/route/
  __init__.py
  cli.py          # cmd_route_test(), cmd_route_show() — argparse dispatch
  loader.py       # load_routing_yaml(path), load_org_overlay(org_slug) → merged config
  simulator.py    # local_simulate(request, config) → RoutingDecision | BlockedDecision
                  #   applies four rules in order; no network call
  live.py         # live_route(request, router_url) → RoutingDecision | BlockedDecision
                  #   POSTs to /route, handles 409/503
  health.py       # probe_backend(backend) → HealthResult; run_probes(backends) → list
  format.py       # format_decision(decision, verbose), format_show(config, edition, ...)
```

Key invariant: `simulator.py` and `live.py` both return the same `RoutingDecision`
dataclass so `cli.py` renders them identically regardless of mode.

---

## CE/EE gating

`route test` and `route show` themselves are available on ALL editions — operators need
visibility into routing config regardless of edition. The gating is in what the output
shows:

| Output element | CE | EE |
|---|---|---|
| Rule 4 (default) shown as active | ✓ | ✓ |
| Rules 1–3 shown as inactive + EE note | ✓ | — |
| Rules 1–3 shown as active | — | ✓ |
| Compliance-block test results | ✓ (simulated — always uses rule 4) | ✓ (full evaluation) |
| Per-org overlay loading | ✓ (reads file if present) | ✓ |

On CE, `route test --classification phi` still runs but always returns rule 4
(default), with a note: *"CE edition: compliance routing (rule 1) is inactive.
Result is the default backend regardless of classification."*

---

## Coordination notes

- **`routing.yaml` config loading** aligns with Q1 decision (design.md): `.otaman/routing.yaml` + `orgs/<slug>/routing.yaml` overlay. The `loader.py` module implements the merge.
- **`RoutingRequest` / `RoutingDecision` types** from core-agent tasks 1.2 + 1.3 are used verbatim in `--live` mode. Local simulation uses dict-based equivalents for now (no core-agent import) to avoid circular dependency during research phase.
- **`DataClassification` enum** from core-agent task 1.1 drives the `--classification` argparse choices.
- **CE/EE gating** aligns with license-agent task 7.1 (rule 1 requires `features.compliance_routing` EE flag).
- **`${secret:...}` resolution** in `--check` health probes uses the `SecretBackend` Protocol from `pluggable-secret-backend` once implemented. During research phase, direct env-var lookup is the fallback.
- **`route show --check`** backend probes are intentionally lightweight (HEAD or /models endpoint). Full validation of backend credentials is out of scope for the CLI — that belongs to `otaman secret backends configure <name>` (per `cli-secret-subcommand.md`).
