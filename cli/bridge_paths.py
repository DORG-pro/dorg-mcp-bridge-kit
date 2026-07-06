"""Resolve paths for per-bridge workspaces under bridges/<name>/."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGES_DIR = REPO_ROOT / "bridges"
RUNTIME_DIR = REPO_ROOT / "bridge"
TEMPLATE_DIR = BRIDGES_DIR / "_template"
BUILD_DIR_NAME = ".build"

_BRIDGE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class BridgeWorkspace:
    """All paths for one bridge instance in bridges/<name>/."""

    name: str
    root: Path

    @property
    def project_config(self) -> Path:
        return self.root / "project.config.json"

    @property
    def manifest(self) -> Path:
        return self.root / "competency.manifest.json"

    @property
    def documentation(self) -> Path:
        return self.root / "DOCUMENTATION.md"

    @property
    def handlers(self) -> Path:
        return self.root / "competency_handlers.py"

    @property
    def build_dir(self) -> Path:
        return self.root / BUILD_DIR_NAME


def validate_bridge_name(name: str) -> str:
    if name.startswith("_"):
        raise ValueError(f"Bridge name cannot start with underscore: {name}")
    if not _BRIDGE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid bridge name '{name}': use lowercase letters, digits and hyphens "
            "(e.g. 'dorghub', 'acme-crm')."
        )
    return name


def resolve_bridge_workspace(name_or_path: str | Path) -> BridgeWorkspace:
    """Resolve bridges/<name> or an explicit directory containing project.config.json."""
    path = Path(name_or_path)
    if path.is_dir() and (path / "project.config.json").exists():
        return BridgeWorkspace(name=path.name, root=path.resolve())

    name = validate_bridge_name(str(name_or_path).strip().replace("\\", "/").split("/")[-1])
    root = (BRIDGES_DIR / name).resolve()
    return BridgeWorkspace(name=name, root=root)


def list_bridges() -> list[str]:
    if not BRIDGES_DIR.exists():
        return []
    return sorted(
        p.name
        for p in BRIDGES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "project.config.json").exists()
    )


def init_bridge(name: str, *, template: str = "default") -> BridgeWorkspace:
    ws = resolve_bridge_workspace(name)
    if ws.root.exists():
        raise FileExistsError(f"Bridge already exists: {ws.root}")

    if template == "passthrough":
        src = TEMPLATE_DIR / "project.config.passthrough.json"
        if not src.exists():
            raise FileNotFoundError(src)
        ws.root.mkdir(parents=True)
        shutil.copy2(src, ws.project_config)
        shutil.copy2(TEMPLATE_DIR / "DOCUMENTATION.md", ws.documentation)
        return ws

    if not TEMPLATE_DIR.exists():
        raise FileNotFoundError(f"Template directory not found: {TEMPLATE_DIR}")
    shutil.copytree(TEMPLATE_DIR, ws.root)
    # Remove alternate template file from default scaffold.
    alt = ws.root / "project.config.passthrough.json"
    if alt.exists():
        alt.unlink()
    return ws


def assemble_build_context(ws: BridgeWorkspace) -> Path:
    """Copy shared runtime + bridge-specific files into bridges/<name>/.build/ for Docker."""
    if not ws.project_config.exists():
        raise FileNotFoundError(f"Missing {ws.project_config}")
    if not ws.manifest.exists():
        raise FileNotFoundError(
            f"Missing {ws.manifest} — run: python -m cli generate-manifest --bridge {ws.name}"
        )

    if ws.build_dir.exists():
        shutil.rmtree(ws.build_dir)
    ws.build_dir.mkdir(parents=True)

    for name in ("requirements.txt", "server.py", "manifest.py", "Dockerfile"):
        shutil.copy2(RUNTIME_DIR / name, ws.build_dir / name)

    shutil.copytree(RUNTIME_DIR / "handlers", ws.build_dir / "handlers")

    shutil.copy2(ws.manifest, ws.build_dir / "competency.manifest.json")

    if ws.documentation.exists():
        shutil.copy2(ws.documentation, ws.build_dir / "DOCUMENTATION.md")
    else:
        template = RUNTIME_DIR / "DOCUMENTATION.md.template"
        if template.exists():
            shutil.copy2(template, ws.build_dir / "DOCUMENTATION.md")

    stub = RUNTIME_DIR / "competency_handlers.stub.py"
    if ws.handlers.exists():
        shutil.copy2(ws.handlers, ws.build_dir / "competency_handlers.py")
    elif stub.exists():
        shutil.copy2(stub, ws.build_dir / "competency_handlers.py")
    else:
        (ws.build_dir / "competency_handlers.py").write_text(
            '"""No tier-2 handlers for this bridge."""\n', encoding="utf-8"
        )

    return ws.build_dir
