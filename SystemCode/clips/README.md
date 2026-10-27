# System Code Layout

This directory contains the canonical implementation source for the submitted system. It follows the template's `SystemCode/clips/` location while keeping the application split into its existing backend and frontend modules.

## Components

- [`backend/`](backend/README.md) contains the Django/ASGI services, agent and navigation integrations, inference interfaces, deployment tooling, and backend checks.
- [`frontend/`](frontend/README.md) contains the browser application, navigation and speech clients, camera streaming, integration pages, and frontend checks.

Run commands and runtime boundaries are documented in the component guides. GitHub Actions remains in the root `.github/workflows/` directory because GitHub discovers repository workflows only from that location.
