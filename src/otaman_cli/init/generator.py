"""File generator for the launcher/ folder (task 1.4).

Accepts a populated `LaunchSettings` and an output path, then writes:

    <output>/launch-settings.yaml        — committed launch config
    <output>/launch-settings.local.yaml  — gitignored commented example
    <output>/launch.sh                   — Linux/macOS launcher (chmod +x)
    <output>/launch.ps1                  — Windows PowerShell launcher
    <output>/.gitignore                  — contains 'launch-settings.local.yaml'

All paths created if missing. Existing files OVERWRITTEN unless caller
guards via the `--force` flag in cmd_init.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from otaman_cli.init.schema import LaunchSettings


_TEMPLATES_DIR = Path(__file__).parent / "templates"


_LOCAL_EXAMPLE = """\
# launch-settings.local.yaml — per-developer overrides, NOT committed.
# Listed in launcher/.gitignore alongside this file.
#
# Override any scalar key from launch-settings.yaml via the same path.
# Lists are REPLACED wholesale (not extended) — keep that in mind for `agents:`.
#
# Example: point the SSH connection at YOUR work server without touching the committed file:
#
# connection:
#   ssh:
#     host: my-actual-server.example.com
#     user: roman
#     key_path: /home/roman/.ssh/work_key
#
# Example: disable an agent locally without changing the committed list:
#
# agents:
#   - name: spec-agent
#     enabled: true
#   - name: backend-agent
#     enabled: false
"""


_GITIGNORE = "launch-settings.local.yaml\n"


@dataclass
class GeneratorResult:
    """What `generate()` wrote, for caller reporting."""

    settings_yaml: Path
    local_example: Path
    launch_sh: Path
    launch_ps1: Path
    gitignore: Path
    launcher_dir: Path
    platform_yaml_copy: Path | None = None


def _ruamel_dump(settings: LaunchSettings) -> str:
    """Render LaunchSettings → YAML.  PyYAML keeps the output portable;
    no comments needed in the generated file (the template is generated, not
    hand-edited)."""
    # Pydantic's model_dump uses field names — we need the yaml aliases where
    # they exist.  For our shape there are no field aliases (we used populate_
    # by_name for consistency, but field names == yaml keys), so model_dump
    # gives the right shape.
    data = settings.model_dump(mode="python", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def _render_template(name: str, settings: LaunchSettings) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(disabled_extensions=("j2", "sh", "ps1")),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(name)
    return template.render(
        connection=settings.connection,
        agents=settings.agents,
        tmux=settings.tmux,
    )


def generate(
    settings: LaunchSettings,
    output_dir: Path,
    *,
    platform_yaml_source: Path | None = None,
) -> GeneratorResult:
    """Write all launcher files to *output_dir* (creates it if missing).

    When *platform_yaml_source* is given and the file exists, also copy it
    into ``launcher/platform.yaml`` so the folder is self-contained — a
    developer can copy ``launcher/`` to a new machine and have everything
    needed in one place (otaman-init-dev-scaffold amendment #1).

    Existing files OVERWRITTEN; callers responsible for prompting/--force
    gating.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    settings_yaml = output_dir / "launch-settings.yaml"
    local_example = output_dir / "launch-settings.local.yaml"
    launch_sh = output_dir / "launch.sh"
    launch_ps1 = output_dir / "launch.ps1"
    gitignore = output_dir / ".gitignore"
    platform_copy: Path | None = None

    # 1. launch-settings.yaml (live config)
    settings_yaml.write_text(_ruamel_dump(settings), encoding="utf-8")

    # 2. launch-settings.local.yaml (commented example, never live)
    local_example.write_text(_LOCAL_EXAMPLE, encoding="utf-8")

    # 3. launch.sh (POSIX) + chmod +x on POSIX systems
    launch_sh.write_text(_render_template("launch.sh.j2", settings), encoding="utf-8")
    if os.name == "posix":
        current = launch_sh.stat().st_mode
        launch_sh.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # 4. launch.ps1 (Windows)
    launch_ps1.write_text(_render_template("launch.ps1.j2", settings), encoding="utf-8")

    # 5. .gitignore (excludes the local override)
    gitignore.write_text(_GITIGNORE, encoding="utf-8")

    # 6. platform.yaml copy (otaman-init-dev-scaffold amendment #1)
    if platform_yaml_source is not None and platform_yaml_source.is_file():
        # Only copy if source != target (skip self-copy when launcher_dir
        # already contains platform.yaml — defensive)
        platform_copy = output_dir / "platform.yaml"
        if platform_yaml_source.resolve() != platform_copy.resolve():
            shutil.copy2(platform_yaml_source, platform_copy)

    return GeneratorResult(
        settings_yaml=settings_yaml,
        local_example=local_example,
        launch_sh=launch_sh,
        launch_ps1=launch_ps1,
        gitignore=gitignore,
        launcher_dir=output_dir,
        platform_yaml_copy=platform_copy,
    )


__all__ = ["generate", "GeneratorResult"]
