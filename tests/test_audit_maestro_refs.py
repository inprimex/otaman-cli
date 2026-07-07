"""Regression test for CI's `audit-maestro-refs.sh` gate.

The script (owned by otaman-core, run as a CI step against this repo's
`src/`) fails the build if any bare-word 'maestro' reference lacks an
inline `# legacy: ...` / `# migration: ...` annotation. It was silently
failing on `main` for several merges — unrelated to any single PR's diff,
since the flagged lines predate them all — because nothing exercised the
script locally before push. Running it here as a real test surfaces the
same failure before it reaches CI.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
AUDIT_SCRIPT = REPO_ROOT.parent / "otaman-core" / "scripts" / "audit-maestro-refs.sh"


@pytest.mark.skipif(
    not AUDIT_SCRIPT.is_file(),
    reason="otaman-core sibling checkout not present locally (always present in CI)",
)
def test_no_unannotated_maestro_references_in_src() -> None:
    result = subprocess.run(
        ["bash", str(AUDIT_SCRIPT), "src/"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"audit-maestro-refs.sh failed (this mirrors a real CI gate):\n"
        f"{result.stdout}\n{result.stderr}"
    )
