# Deployment Example

This directory is a minimal deployment template for running a `OneStepApp`
under `systemd`.

Included files:

- `env/onestep-app.env.example`: service environment variables
- `systemd/onestep-app.service`: example `systemd` unit
- `bin/onestep-preflight.sh`: startup check script used by `ExecStartPre`
- `web-service-integration.md`: recommended deployment shape for FastAPI/Django and other web apps

## Expected layout

The example assumes:

- your app repo lives at `/srv/onestep-app`
- the virtualenv lives at `/srv/onestep-app/.venv`
- the `OneStepApp` target is `your_package.tasks:app`

Adjust those in `/etc/onestep/onestep-app.env`.

The preflight script prepends `APP_CWD` to `PYTHONPATH` so in-repo modules like
`example.cli_app:app` or `your_package.tasks:app` can be imported reliably from
the console script entrypoint.

## Install

```bash
sudo mkdir -p /etc/onestep
sudo cp /srv/onestep-app/deploy/env/onestep-app.env.example /etc/onestep/onestep-app.env
sudo cp /srv/onestep-app/deploy/systemd/onestep-app.service /etc/systemd/system/onestep-app.service
sudo systemctl daemon-reload
sudo systemctl enable --now onestep-app
```

## Verify

```bash
sudo systemctl status onestep-app
sudo journalctl -u onestep-app -f
```

## Web framework projects

If your project already runs a web service such as FastAPI or Django, do not
start `OneStepApp` inside the web worker process by default. Run the web app
and the `onestep` worker as separate services that share the same codebase and
configuration.

See `deploy/web-service-integration.md` for the recommended process model,
examples, and the cases where embedded startup is acceptable.

## How it works

- `ExecStartPre` runs `deploy/bin/onestep-preflight.sh`
- the preflight script validates required env vars and executes `onestep check`
- `ExecStart` then starts the app with `onestep run`
- `systemctl stop` sends `SIGTERM`, which `OneStepApp.run()` now handles as a normal shutdown request
