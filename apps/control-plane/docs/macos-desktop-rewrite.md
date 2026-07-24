# OneStep macOS Desktop Rewrite

The macOS desktop app is a new Electron + React workbench. It does not reuse the
old `frontend/` console UI. The desktop renderer lives under `desktop/src/renderer`
and uses a compact ORCA-inspired operator-console design.

## Development

```bash
pnpm install
uv sync --extra dev
pnpm desktop:dev
```

Electron starts the FastAPI sidecar locally and loads the desktop renderer.

## Runtime Data

Desktop data is stored under:

```text
~/Library/Application Support/OneStep/
```

Backend logs are written to:

```text
~/Library/Application Support/OneStep/logs/backend.log
```

## Tests

```bash
uv run pytest -q
pnpm desktop:test
pnpm --filter onestep-control-plane-desktop build
pnpm --filter onestep-control-plane-desktop e2e
```

## Packaging

```bash
pnpm desktop:dist:mac
```

Unsigned test artifacts are written under:

```text
desktop/release/
```

Public distribution requires Apple Developer ID signing and notarization:

```bash
ONESTEP_MAC_RELEASE=1 pnpm desktop:dist:mac
```

## UI Direction

The desktop UI should stay dense, neutral, and operational:

- compact left rail
- context sidebar
- split main workspace
- optional inspector
- icon-first controls
- low radius panels
- status dots and concise badges
- no landing page
- no marketing cards
