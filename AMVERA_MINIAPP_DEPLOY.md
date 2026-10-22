# Amvera: Telegram bot + Mini App in one container

## What changed

- Added `app/server.py`: FastAPI mock API and optional static hosting from `mini_app/dist`.
- Added `run_server.py` and `supervisor.conf`: Supervisor runs the existing `python -m app` polling bot and Uvicorn API as separate supervised processes.
- Dockerfile uses a Node build stage for React, then copies the compiled static bundle into the Python runtime image. Node/npm are not installed in the runtime image.
- Added FastAPI and Uvicorn to both dependency manifests used by deployment and CI.
- Changed Amvera container port from 80 to 8000.
- Did not edit `app/__main__.py` or bot handlers.

## Endpoints

- `GET /api/health` -> `{"status":"ok"}`
- `GET /api/club` -> mock club
- `GET /api/squad` -> mock squad
- `GET /api/match` -> mock match
- `/` and other non-API paths serve the Vite build from `mini_app/dist`.
- If the built `mini_app/dist/index.html` is absent, `/` returns `{"detail":"Mini App not built"}` with HTTP 200; API routes continue to work.

## Local checks

- `python -m py_compile app/server.py run_server.py` passed.
- FastAPI TestClient checks passed for all four API routes and the missing-build fallback.
- The frontend `npm install && npm run build` did not finish within the available environment timeout. The Docker build will run those commands in its Node stage; a successful production image build is therefore not claimed here.

## Deployment notes

Amvera must expose container port 8000. Supervisor keeps both processes running and restarts either process if it exits. The bot still uses its existing polling startup (`python -m app`). The API currently returns mock data only; it intentionally does not touch the existing database.

Before deployment, verify the project's Amvera plan and configuration allow the configured container port and Docker multi-stage build. The app has not been deployed to the user's Amvera account from this workspace.
