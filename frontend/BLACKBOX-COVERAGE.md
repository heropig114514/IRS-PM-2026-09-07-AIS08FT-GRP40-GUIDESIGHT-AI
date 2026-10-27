# Code and online functional coverage

## Code coverage gates

Run the backend suite from `backend`:

```powershell
python -m coverage run manage.py test
python -m coverage report
```

The backend gate measures branch-aware coverage for the production `core` package, excludes test modules, and requires at least 85% combined statement/branch coverage.

Run the frontend suite from `frontend`:

```powershell
npm run test:coverage
```

This gate excludes test and browser-tool files from the measured application source and requires 85% lines, 80% branches, and 80% functions. CI runs both gates on Node 22 and Python 3.12.

## Online black-box functional coverage

`tools/verify_blackbox_coverage.mjs` requires five completed reports and validates 56 required scenarios across live Qwen assistant interactions, the formal product page, camera permission boundaries, page lifecycle boundaries, and the public relay. It checks report failures and each recorded evidence field; a missing report, failed run, or missing scenario exits nonzero. Synthetic agent routes and GPS remain labeled as such. The relay risk-alert case requires a real inference frame, matching track, backend speech start, and completed playback. A synthetic scheduler alert cannot satisfy it.

The browser checks require a working public deployment, Chrome, the local Playwright package path, and a caller-supplied Y4M scene file for actual model inference. The strict relay run also needs a scene that produces a server-required alert. These checks do not claim physical-phone GPS, camera, vibration, or speaker audibility.

Generate fresh reports with the corresponding browser checkers, then run the aggregate gate. The example uses new report names; the scripts refuse to overwrite existing reports.

```powershell
$env:PRS_PLAYWRIGHT_PATH = '<absolute path to the installed Playwright package>'
$env:PRS_FAKE_CAMERA_VIDEO = '<absolute path to a caller-supplied Y4M scene>'
$env:PYTHON = '<absolute path to the project Python executable>'

node tools/check_agent_browser.mjs https://47.239.50.129/mvp/maps/test ../backend/.venv/tmp/blackbox-agent.json --live-qwen --audio=<absolute path to a caller-supplied WAV>
node tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ ../backend/.venv/tmp/blackbox-product.json
node tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ ../backend/.venv/tmp/blackbox-camera.json --camera-boundaries
node tools/check_assist_lifecycle_browser.mjs https://47.239.50.129/mvp/maps/test ../backend/.venv/tmp/blackbox-lifecycle.json
node tools/check_phone_relay_browser.mjs https://47.239.50.129/mvp/maps/test ../backend/.venv/tmp/blackbox-relay.json --require-model-alert

npm run test:blackbox-coverage -- --agent ../backend/.venv/tmp/blackbox-agent.json --product ../backend/.venv/tmp/blackbox-product.json --camera ../backend/.venv/tmp/blackbox-camera.json --lifecycle ../backend/.venv/tmp/blackbox-lifecycle.json --relay ../backend/.venv/tmp/blackbox-relay.json
```

The agent report requires a real Qwen connection and explicitly activates test-page speech through a user gesture; the lifecycle report accepts either the documented public test entry or its loopback entry and uses Python for MCP calls. The product/camera/lifecycle reports include explicit browser fixtures; the relay report calls the deployed ORS, inference, and speech services. Keep reports from the same deployment run when combining them.
