---
name: plugin-release-check
description: >-
  Validate, test, lint, deploy locally, and smoke-test omarchy-battlestation
  after making changes to Python modules, QML files, or manifest.json.
---

# Plugin Verification & Local Deploy

Follow these steps whenever modifying code in `omarchy-battlestation`.

## 1. Run Automated Checks

```bash
make check
```

This runs:
- `make test` — stdlib `unittest` suite in `tests/`
- `make lint` — `qmllint` on `*.qml` and `qml/*.qml`, plus `py_compile` on
  `bin/battlestation` and `lib/battlestation/*.py`
- `make validate` — `omarchy-plugin-validate .` (manifest schema, entry points,
  and strict no-symlink enforcement)

All three must exit 0.

## 2. Sync to Live Omarchy Plugin Directory (when deploying locally)

```bash
make dev
```

`make dev` uses `rsync --delete` into
`~/.config/omarchy/plugins/io.github.samueljenkinsml.battlestation/` (excluding
`.git`, `.agents`, `AGENTS.md`, `tests`, `__pycache__`, `*.pyc`). Quickshell
hot-reloads QML changes automatically.

## 3. Smoke-Test the Running Plugin

```bash
make logs
omarchy-shell io.github.samueljenkinsml.battlestation state
./bin/battlestation doctor
```

Verify:
- No new QML runtime errors or warnings from `battlestation` in `make logs`.
- `omarchy-shell ... state` returns valid JSON with expected scene and display fields.
- `battlestation doctor` reports no unexpected failures.
