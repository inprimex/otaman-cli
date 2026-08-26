"""Otaman standalone CLI package.

Stage 2 of the Step 1 carve incrementally moves cli/maestro.py's content  # legacy: filename
from legacy maestro-plugin into this package.  # legacy: plugin repo name Stage 2A establishes the
skeleton; subsequent sub-stages move per-subcommand modules.
"""

# Keep in sync with [project].version in pyproject.toml. The runtime
# authoritative value is importlib.metadata.version("otaman-cli") (see
# main._resolve_version); this constant is a fallback/reference only.
__version__ = "0.5.0"
