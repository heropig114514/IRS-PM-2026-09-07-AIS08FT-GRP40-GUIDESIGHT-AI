#!/usr/bin/env bash
# Responsibilities: Validate and activate one GitHub Actions release for the isolated Phone MVP service.
# Implementation: Extract a source archive into a commit-named release, run read-only Django/dependency
# checks as the dedicated deploy user, atomically switch the MVP current link, restart only
# prs-mvp-web.service, and perform one bounded listener/health observation.
# Related Modules: deploy/prs-mvp-web.service, integration/mobile_asgi.py, PHONE-MVP.md,
# .github/workflows/backend.yml.
# Declaration Index: wait_for_listener; main.
# Variable Index: ARCHIVE, COMMIT, ROOT, RUNTIME, SERVICE, SERVICE_USER, PORT, TARGET, CURRENT,
# STAGED_LINK, ENV_FILE;
# all values are explicit deployment arguments or fixed MVP ownership boundaries.

set -euo pipefail

ARCHIVE=${1:?archive path is required}
COMMIT=${2:?commit SHA is required}
ROOT=${3:?MVP root is required}
RUNTIME=${4:?runtime path is required}
SERVICE=${5:?service name is required}
SERVICE_USER=prs-mvp-web
PORT=18766

if [[ ! "$COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid commit SHA" >&2
  exit 2
fi
if [[ "$ROOT" != "/opt/prs-practice/mvp" ]]; then
  echo "Refusing a deployment root outside the isolated MVP workspace" >&2
  exit 2
fi
if [[ "$SERVICE" != "prs-mvp-web.service" ]]; then
  echo "Refusing to operate on a service outside the isolated MVP service" >&2
  exit 2
fi
if [[ ! "$RUNTIME" =~ ^/opt/prs-practice/runtimes/[A-Za-z0-9_-]+$ ]]; then
  echo "Runtime must be an owned PRS MVP runtime" >&2
  exit 2
fi
if [[ ! -f "$ARCHIVE" ]]; then
  echo "Release archive is missing" >&2
  exit 2
fi

TARGET="$ROOT/releases/$COMMIT"
CURRENT="$ROOT/current"
STAGED_LINK="$ROOT/.current-$COMMIT"
ENV_FILE=/etc/prs-mvp-web/app.env

if [[ -e "$TARGET" ]]; then
  echo "Release already exists: $TARGET" >&2
  exit 2
fi

cleanup() {
  local status=$?
  rm -f -- "$ARCHIVE"
  if (( status != 0 )) && [[ "$(readlink -f "$CURRENT" 2>/dev/null || true)" != "$TARGET" ]]; then
    sudo -n rm -rf -- "$TARGET" "$STAGED_LINK"
  fi
  exit "$status"
}
trap cleanup EXIT

# Functionality: Wait for the dedicated MVP listener after one service restart.
# Inputs: none; uses the fixed loopback port. Outputs: success or a diagnostic failure.
# Logic: observe readiness for at most 20 seconds, then issue one health request.
# Constraints: this is startup observation, not a restart or request retry; no other service is touched.
wait_for_listener() {
  local deadline=$((SECONDS + 20))
  while (( SECONDS < deadline )); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
      exec 3>&-
      return 0
    fi
    sleep 1
  done
  echo "MVP listener did not become ready on 127.0.0.1:$PORT" >&2
  return 1
}

# Functionality: Perform one isolated deployment transaction.
# Inputs: archive, commit, owned root/runtime/service arguments. Outputs: active release or failure.
# Logic: check the inactive release before an atomic link switch; preserve every prior release.
# Constraints: no migration, dependency installation, Nginx reload, automatic rollback, or
# prs-camera restart is allowed by this script; Django source checks run without sudo.
main() {
  sudo -n install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$ROOT" "$ROOT/releases"
  sudo -n install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$TARGET"
  # The workflow archive deliberately keeps the backend/frontend top-level directories so the
  # release layout matches the service working directory and the validation paths below.
  sudo -n tar --extract --gzip --file "$ARCHIVE" --directory "$TARGET"
  sudo -n chown -R "$SERVICE_USER:$SERVICE_USER" "$TARGET"

  if [[ ! -f "$TARGET/backend/manage.py" || ! -f "$TARGET/frontend/index.html" ]]; then
    echo "Release does not contain the backend/frontend application roots" >&2
    exit 1
  fi

  local unit
  unit=$(sudo -n systemctl cat "$SERVICE")
  grep -Fq "WorkingDirectory=$ROOT/current/backend" <<<"$unit"
  grep -Fq -- "--port $PORT" <<<"$unit"
  grep -Fq -- "$RUNTIME/bin/python" <<<"$unit"
  [[ "$(sudo -n systemctl show -p User --value "$SERVICE")" == "$SERVICE_USER" ]]
  [[ -x "$RUNTIME/bin/python" ]]
  [[ -f "$ENV_FILE" ]]

  if [[ ! -r "$ENV_FILE" ]]; then
    echo "The deploy user cannot read the MVP environment file" >&2
    exit 1
  fi
  set -a
  source "$ENV_FILE"
  set +a
  (
    cd "$TARGET/backend"
    "$RUNTIME/bin/python" manage.py check
    "$RUNTIME/bin/python" manage.py makemigrations --check --dry-run
    "$RUNTIME/bin/python" -m pip check
    "$RUNTIME/bin/python" -c "import integration.mobile_asgi"
  )

  sudo -n ln -s "$TARGET" "$STAGED_LINK"
  sudo -n mv -Tf "$STAGED_LINK" "$CURRENT"
  sudo -n systemctl restart "$SERVICE"
  sudo -n systemctl is-active --quiet "$SERVICE"
  wait_for_listener
  curl --fail --silent --show-error --max-time 10 \
    -H 'Host: 127.0.0.1:18766' \
    "http://127.0.0.1:$PORT/api/health/"
  printf '\nActivated MVP release %s\n' "$COMMIT"
}

main "$@"
