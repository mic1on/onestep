from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import is_yaml_target, load_yaml_app

PACKAGE_MANIFEST_NAME = "onestep-package.json"

DEFAULT_EXCLUDE_PATTERNS = (
    ".git/**",
    ".hg/**",
    ".svn/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    "*.pyc",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    ".env",
    ".env.*",
    "build/**",
    "dist/**",
    "*.egg-info/**",
)

DEPENDENCY_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements.lock",
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
)

PACKAGING_METADATA_FILE_PATTERNS = (
    "README",
    "README.*",
    "LICENSE",
    "LICENSE.*",
    "COPYING",
    "COPYING.*",
    "NOTICE",
    "NOTICE.*",
)

PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


class BuildError(RuntimeError):
    """Raised when a worker package cannot be built."""


@dataclass(frozen=True)
class BuildOptions:
    target: str
    output: str = "dist/worker.zip"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    manifest: str | None = None
    check: bool = True
    strict: bool = False
    env_file: str | None = None
    strict_env: bool | None = None


@dataclass(frozen=True)
class BuildManifest:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    entrypoint: str | None = None
    path: Path | None = None


@dataclass
class BuildResult:
    target: Path
    project_root: Path
    output: Path
    entrypoint: str
    files: list[str]
    dependency_mode: str
    checksum_sha256: str
    size_bytes: int
    check_ran: bool
    strict: bool
    manifest_path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "target": str(self.target),
            "project_root": str(self.project_root),
            "output": str(self.output),
            "entrypoint": self.entrypoint,
            "files": self.files,
            "dependency_mode": self.dependency_mode,
            "checksum_sha256": self.checksum_sha256,
            "size_bytes": self.size_bytes,
            "check_ran": self.check_ran,
            "strict": self.strict,
            "manifest_path": str(self.manifest_path) if self.manifest_path is not None else None,
            "warnings": self.warnings,
        }


def build_worker_package(options: BuildOptions) -> BuildResult:
    target_path, project_root, manifest = _resolve_target(options)
    if not is_yaml_target(str(target_path)):
        raise BuildError("onestep build currently supports YAML worker targets only")

    output_path = _resolve_output_path(options.output)
    _ensure_import_paths(project_root, target_path.parent)

    if options.check:
        try:
            load_yaml_app(
                str(target_path),
                strict=options.strict,
                env_file=options.env_file,
                strict_env=options.strict_env,
            )
        except Exception as exc:  # noqa: BLE001 - surface the same message in CLI
            raise BuildError(f"check failed for {target_path}: {exc}") from exc

    warnings: list[str] = []
    refs = _extract_callable_refs(target_path)
    auto_files = _default_files(
        project_root=project_root,
        target_path=target_path,
        callable_refs=refs,
        warnings=warnings,
    )

    include_patterns = [*manifest.include, *options.include]
    exclude_patterns = [*DEFAULT_EXCLUDE_PATTERNS, *manifest.exclude, *options.exclude]
    output_rel = _relative_to(output_path, project_root)
    if output_rel is not None:
        exclude_patterns.append(output_rel.as_posix())

    files = set(auto_files)
    files.update(_expand_include_patterns(project_root, include_patterns, warnings=warnings))
    files = {
        path
        for path in files
        if path.is_file() and not _is_excluded(project_root, path, exclude_patterns)
    }

    entrypoint = _relative_to(target_path, project_root)
    if entrypoint is None:
        raise BuildError(f"target must be inside project root: {target_path}")
    files.add(target_path)

    relative_files = sorted(_relative_to(path, project_root).as_posix() for path in files)
    dependency_mode = _detect_dependency_mode(project_root)
    package_manifest = _build_package_manifest(
        entrypoint=entrypoint.as_posix(),
        files=relative_files,
        dependency_mode=dependency_mode,
        warnings=warnings,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in relative_files:
            if rel == PACKAGE_MANIFEST_NAME:
                continue
            archive.write(project_root / rel, rel)
        archive.writestr(
            PACKAGE_MANIFEST_NAME,
            json.dumps(package_manifest, indent=2, sort_keys=True) + "\n",
        )

    content = output_path.read_bytes()
    return BuildResult(
        target=target_path,
        project_root=project_root,
        output=output_path,
        entrypoint=entrypoint.as_posix(),
        files=[*relative_files, PACKAGE_MANIFEST_NAME],
        dependency_mode=dependency_mode,
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        check_ran=options.check,
        strict=options.strict,
        manifest_path=manifest.path,
        warnings=warnings,
    )


def _resolve_target(options: BuildOptions) -> tuple[Path, Path, BuildManifest]:
    raw_target = Path(options.target).expanduser()
    if not raw_target.is_absolute():
        raw_target = Path.cwd() / raw_target
    raw_target = raw_target.resolve()

    if raw_target.is_dir():
        project_root = raw_target
        manifest = _load_manifest(project_root, options.manifest)
        entrypoint = manifest.entrypoint or "worker.yaml"
        target_path = (project_root / entrypoint).resolve()
        return target_path, project_root, manifest

    project_root = _find_project_root(raw_target.parent) or raw_target.parent
    manifest = _load_manifest(project_root, options.manifest)
    return raw_target, project_root.resolve(), manifest


def _resolve_output_path(output: str) -> Path:
    path = Path(output).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _load_manifest(project_root: Path, manifest_path: str | None) -> BuildManifest:
    if manifest_path is not None:
        path = Path(manifest_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.exists():
            raise BuildError(f"build manifest does not exist: {path}")
        return _read_manifest_file(path)

    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        return _read_pyproject_manifest(pyproject)
    return BuildManifest()


def _read_manifest_file(path: Path) -> BuildManifest:
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise BuildError(f"build manifest must be a JSON object: {path}")
        return _manifest_from_mapping(payload, path=path)
    if path.name == "pyproject.toml" or path.suffix == ".toml":
        return _read_pyproject_manifest(path)
    raise BuildError("build manifest must be pyproject.toml, *.toml, or *.json")


def _read_pyproject_manifest(path: Path) -> BuildManifest:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return _read_pyproject_manifest_fallback(path)

    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    section = payload.get("tool", {}).get("onestep", {}).get("build", {})
    if not isinstance(section, Mapping):
        return BuildManifest(path=path)
    manifest = _manifest_from_mapping(section, path=path)
    if not manifest.include and not manifest.exclude and manifest.entrypoint is None:
        return BuildManifest()
    return manifest


def _read_pyproject_manifest_fallback(path: Path) -> BuildManifest:
    section = _read_simple_toml_section(path, "tool.onestep.build")
    if not section:
        return BuildManifest()
    return _manifest_from_mapping(section, path=path)


def _manifest_from_mapping(payload: Mapping[str, Any], *, path: Path) -> BuildManifest:
    include = _coerce_str_tuple(payload.get("include"), field="include", path=path)
    exclude = _coerce_str_tuple(payload.get("exclude"), field="exclude", path=path)
    raw_entrypoint = payload.get("entrypoint")
    if raw_entrypoint is not None and not isinstance(raw_entrypoint, str):
        raise BuildError(f"build manifest field 'entrypoint' must be a string: {path}")
    entrypoint = raw_entrypoint.strip() if isinstance(raw_entrypoint, str) and raw_entrypoint.strip() else None
    return BuildManifest(include=include, exclude=exclude, entrypoint=entrypoint, path=path)


def _coerce_str_tuple(value: Any, *, field: str, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BuildError(f"build manifest field {field!r} must be a list of strings: {path}")
    return tuple(item for item in (part.strip() for part in value) if item)


def _read_simple_toml_section(path: Path, section_name: str) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_section = False
    values: dict[str, object] = {}
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        index += 1
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section:
                break
            in_section = stripped == f"[{section_name}]"
            continue
        if not in_section or "=" not in stripped:
            continue
        key, raw_value = [part.strip() for part in stripped.split("=", 1)]
        while raw_value.startswith("[") and not raw_value.endswith("]") and index < len(lines):
            raw_value += "\n" + lines[index].strip()
            index += 1
        try:
            values[key] = ast.literal_eval(raw_value)
        except (SyntaxError, ValueError) as exc:
            raise BuildError(f"unsupported build manifest value for {key!r}: {path}") from exc
    return values


def _find_project_root(start: Path) -> Path | None:
    current = start.resolve()
    while True:
        if any((current / marker).exists() for marker in PROJECT_MARKERS):
            return current
        if current.parent == current:
            return None
        current = current.parent


def _ensure_import_paths(project_root: Path, target_dir: Path) -> None:
    candidates = [
        project_root,
        project_root / "src",
        target_dir,
        target_dir / "src",
    ]
    for path in reversed(candidates):
        if not path.is_dir():
            continue
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def _extract_callable_refs(target_path: Path) -> set[str]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised by optional dependency users
        raise BuildError(
            "YAML package build requires PyYAML. Install it with: pip install 'onestep[yaml]'"
        ) from exc

    with target_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    refs: set[str] = set()
    _collect_callable_refs(payload, refs)
    return refs


def _collect_callable_refs(value: Any, refs: set[str], *, key: str | None = None) -> None:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            child_key_str = str(child_key)
            if child_key_str == "ref" and isinstance(child_value, str):
                _add_callable_ref(child_value, refs)
            elif child_key_str in {"handler", "when"} and isinstance(child_value, str):
                _add_callable_ref(child_value, refs)
            else:
                _collect_callable_refs(child_value, refs, key=child_key_str)
        return

    if isinstance(value, list):
        for item in value:
            _collect_callable_refs(item, refs, key=key)
        return

    if key in {"startup", "shutdown", "events", "before", "after_success", "on_failure"} and isinstance(value, str):
        _add_callable_ref(value, refs)


def _add_callable_ref(value: str, refs: set[str]) -> None:
    if _looks_like_callable_ref(value):
        refs.add(value)


def _looks_like_callable_ref(value: str) -> bool:
    if "://" in value or ":" not in value:
        return False
    module, _, attr = value.partition(":")
    if not module or not attr:
        return False
    return all(part.isidentifier() for part in module.split("."))


def _default_files(
    *,
    project_root: Path,
    target_path: Path,
    callable_refs: Iterable[str],
    warnings: list[str],
) -> set[Path]:
    files: set[Path] = {target_path}
    for filename in DEPENDENCY_FILES:
        candidate = project_root / filename
        if candidate.is_file():
            files.add(candidate)
    files.update(_packaging_metadata_files(project_root, warnings=warnings))

    source_roots = _source_roots(project_root, target_path.parent)
    for ref in sorted(callable_refs):
        module_name = ref.partition(":")[0]
        located = _locate_module_package(module_name, project_root=project_root, source_roots=source_roots)
        if located is None:
            warnings.append(f"could not locate local module for callable ref {ref!r}")
            continue
        files.update(_iter_files(located))
    return files


def _source_roots(project_root: Path, target_dir: Path) -> list[Path]:
    candidates = [project_root, project_root / "src", target_dir, target_dir / "src"]
    roots: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        roots.append(resolved)
    return roots


def _locate_module_package(
    module_name: str,
    *,
    project_root: Path,
    source_roots: list[Path],
) -> Path | None:
    module_parts = module_name.split(".")
    module_path = Path(*module_parts)
    for source_root in source_roots:
        module_file = source_root / module_path.with_suffix(".py")
        package_dir = source_root / module_path
        if module_file.is_file():
            top = source_root / module_parts[0]
            return top if top.is_dir() else module_file
        if (package_dir / "__init__.py").is_file():
            top = source_root / module_parts[0]
            return top if top.is_dir() else package_dir

    root_module = project_root / module_path.with_suffix(".py")
    return root_module if root_module.is_file() else None


def _iter_files(path: Path) -> set[Path]:
    if path.is_file():
        return {path}
    return {item for item in path.rglob("*") if item.is_file()}


def _packaging_metadata_files(project_root: Path, *, warnings: list[str]) -> set[Path]:
    files: set[Path] = set()
    for pattern in PACKAGING_METADATA_FILE_PATTERNS:
        files.update(item.resolve() for item in project_root.glob(pattern) if item.is_file())

    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return files

    for reference in _pyproject_referenced_metadata_paths(pyproject):
        files.update(_resolve_packaging_metadata_reference(project_root, reference, warnings=warnings))
    return files


def _pyproject_referenced_metadata_paths(path: Path) -> tuple[str, ...]:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return _pyproject_referenced_metadata_paths_fallback(path)

    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    project = payload.get("project", {})
    if not isinstance(project, Mapping):
        return ()

    references: list[str] = []
    references.extend(_metadata_file_refs(project.get("readme"), string_is_path=True))
    references.extend(_metadata_file_refs(project.get("license"), string_is_path=False))
    license_files = project.get("license-files")
    if isinstance(license_files, list):
        references.extend(item for item in license_files if isinstance(item, str))
    return tuple(references)


def _metadata_file_refs(value: Any, *, string_is_path: bool) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if string_is_path else ()
    if isinstance(value, Mapping):
        file_value = value.get("file")
        if isinstance(file_value, str):
            return (file_value,)
    return ()


def _pyproject_referenced_metadata_paths_fallback(path: Path) -> tuple[str, ...]:
    references: list[str] = []
    in_project = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_project:
                break
            in_project = stripped == "[project]"
            continue
        if not in_project or "=" not in stripped:
            continue

        key, raw_value = [part.strip() for part in stripped.split("=", 1)]
        if key == "readme":
            value = _literal_string_value(raw_value) or _inline_table_file_value(raw_value)
            if value is not None:
                references.append(value)
        elif key == "license":
            value = _inline_table_file_value(raw_value)
            if value is not None:
                references.append(value)
        elif key == "license-files":
            value = _literal_string_list_value(raw_value)
            if value is not None:
                references.extend(value)
    return tuple(references)


def _literal_string_value(raw_value: str) -> str | None:
    raw_value = raw_value.strip()
    if not raw_value or raw_value[0] not in {"'", '"'}:
        return None
    try:
        value = ast.literal_eval(raw_value)
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _literal_string_list_value(raw_value: str) -> tuple[str, ...] | None:
    try:
        value = ast.literal_eval(raw_value)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, list):
        return None
    return tuple(item for item in value if isinstance(item, str))


def _inline_table_file_value(raw_value: str) -> str | None:
    if "file" not in raw_value:
        return None
    after_file = raw_value.split("file", 1)[1]
    if "=" not in after_file:
        return None
    raw_file_value = after_file.split("=", 1)[1].strip()
    for quote in ("'", '"'):
        start = raw_file_value.find(quote)
        if start < 0:
            continue
        end = raw_file_value.find(quote, start + 1)
        if end > start:
            return raw_file_value[start + 1 : end]
    return None


def _resolve_packaging_metadata_reference(
    project_root: Path,
    reference: str,
    *,
    warnings: list[str],
) -> set[Path]:
    normalized = reference.replace("\\", "/").strip()
    if not normalized:
        return set()
    if Path(normalized).is_absolute():
        warnings.append(f"pyproject.toml references packaging metadata outside project root: {reference!r}")
        return set()

    if any(char in normalized for char in "*?["):
        matches = {
            item.resolve()
            for item in project_root.glob(normalized)
            if item.is_file() and _relative_to(item, project_root) is not None
        }
        if not matches:
            warnings.append(f"pyproject.toml metadata pattern matched no files: {reference!r}")
        return matches

    path = (project_root / normalized).resolve()
    if _relative_to(path, project_root) is None:
        warnings.append(f"pyproject.toml references packaging metadata outside project root: {reference!r}")
        return set()
    if not path.is_file():
        warnings.append(f"pyproject.toml references missing packaging metadata file: {reference!r}")
        return set()
    return {path}


def _expand_include_patterns(
    project_root: Path,
    patterns: Iterable[str],
    *,
    warnings: list[str],
) -> set[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        matches = _expand_include_pattern(project_root, pattern)
        if not matches:
            warnings.append(f"include pattern matched no files: {pattern!r}")
        files.update(matches)
    return files


def _expand_include_pattern(project_root: Path, pattern: str) -> set[Path]:
    normalized = pattern.replace("\\", "/").strip()
    if not normalized:
        return set()
    raw_path = Path(normalized)
    if raw_path.is_absolute():
        direct = raw_path.resolve()
        if direct.is_file():
            return {direct}
        if direct.is_dir():
            return _iter_files(direct)
        return set()
    direct = (project_root / normalized).resolve()
    if direct.is_file():
        return {direct}
    if direct.is_dir():
        return _iter_files(direct)
    if normalized.endswith("/**"):
        directory = (project_root / normalized[:-3].rstrip("/")).resolve()
        if directory.is_dir():
            return _iter_files(directory)
    return {
        item.resolve()
        for item in project_root.glob(normalized)
        if item.is_file()
    }


def _is_excluded(project_root: Path, path: Path, patterns: Iterable[str]) -> bool:
    rel = _relative_to(path, project_root)
    if rel is None:
        return True
    rel_posix = rel.as_posix()
    return any(_matches_pattern(rel_posix, pattern) for pattern in patterns)


def _matches_pattern(rel_path: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/").strip()
    if not normalized:
        return False
    if normalized.endswith("/**"):
        prefix = normalized[:-3].rstrip("/")
        return rel_path == prefix or rel_path.startswith(f"{prefix}/")
    if "/" not in normalized and fnmatch.fnmatch(Path(rel_path).name, normalized):
        return True
    return fnmatch.fnmatch(rel_path, normalized)


def _relative_to(path: Path, parent: Path) -> Path | None:
    try:
        return path.resolve().relative_to(parent.resolve())
    except ValueError:
        return None


def _detect_dependency_mode(project_root: Path) -> str:
    has_pyproject = (project_root / "pyproject.toml").is_file()
    has_requirements = (project_root / "requirements.txt").is_file()
    if has_pyproject and has_requirements:
        return "pyproject+requirements"
    if has_pyproject:
        return "pyproject"
    if has_requirements:
        return "requirements"
    return "none"


def _build_package_manifest(
    *,
    entrypoint: str,
    files: list[str],
    dependency_mode: str,
    warnings: list[str],
) -> dict[str, object]:
    return {
        "format": "onestep.workflow_package.v1",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "entrypoint": entrypoint,
        "dependency_mode": dependency_mode,
        "files": files,
        "warnings": warnings,
    }


__all__ = [
    "BuildError",
    "BuildManifest",
    "BuildOptions",
    "BuildResult",
    "build_worker_package",
]
