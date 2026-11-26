#!/usr/bin/env bash
# Deploy one service from an immutable, already-tested image (issue #33).
#
# Run from the repository root on the machine that runs the stack:
#   bash infra/scripts/deploy.sh deploy <service> <image@sha256:digest>
#   bash infra/scripts/deploy.sh rollback <service>
#   bash infra/scripts/deploy.sh start      # start/refresh everything from the recorded images
#   bash infra/scripts/deploy.sh status     # show what each service is running
#
# Services: gateway | identity-workspace | task
#
# Only images referenced by digest are accepted, so what runs is exactly what was
# tested. Only the named service is replaced; the others, the databases and the
# broker are not touched. After the swap the service must pass its health check;
# if it does not, the previous image is restored automatically and the script
# exits non-zero. The recorded versions are kept in infra/.deploy/ (ignored by Git).
#
# Database migrations are NOT rolled back. Identity's migration job runs before
# Identity is replaced, and Task migrates itself on start. A rollback only returns
# to the previous image, so it is safe only while the old image works with the
# migrated schema (see "Deploying a tested image" in infra/README.md).
#
# Settings (environment): DEPLOY_STATE_DIR (default infra/.deploy),
# HEALTH_TIMEOUT seconds (default 120). Works with the macOS default bash (3.2).
set -u -o pipefail

cd "$(dirname "$0")/../.." || exit 2

STATE_DIR=${DEPLOY_STATE_DIR:-infra/.deploy}
PINS="$STATE_DIR/images.env"
HISTORY="$STATE_DIR/history.log"
ENV_FILE=infra/.env
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-120}
LOCK="$STATE_DIR/lock"

usage() {
  sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

die() { echo "ERROR: $*" >&2; exit 2; }
say() { echo "$*"; }

case "${1:-}" in -h | --help | help | "") usage; exit 0 ;; esac

if [ ! -f "$ENV_FILE" ]; then
  die "infra/.env is missing. Run: python3 infra/scripts/setup_env.py"
fi

if [ -z "${GATEWAY_HTTP_PORT:-}" ]; then
  GATEWAY_HTTP_PORT=$(sed -n 's/^GATEWAY_HTTP_PORT=//p' "$ENV_FILE" | tail -n 1)
fi
GATEWAY="http://127.0.0.1:${GATEWAY_HTTP_PORT:-8080}"

mkdir -p "$STATE_DIR" || die "cannot create $STATE_DIR"
[ -f "$PINS" ] || : > "$PINS"

# docker compose with the secrets file first and the recorded image versions after it.
compose() { docker compose -f infra/compose.yaml --env-file "$ENV_FILE" --env-file "$PINS" "$@"; }

# --- Recorded versions ----------------------------------------------------------
get_pin() { sed -n "s/^$1=//p" "$PINS" | tail -n 1; }
set_pin() { # key value
  local tmp="$PINS.tmp"
  { grep -v "^$1=" "$PINS" || true; echo "$1=$2"; } > "$tmp" && mv "$tmp" "$PINS"
}
log_event() { # service from to result
  printf '%s service=%s from=%s to=%s result=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" "$4" >> "$HISTORY"
}

# --- Per-service settings -------------------------------------------------------
# Sets VAR (the compose variable holding the image), DEFAULT (the local build tag),
# TARGETS (containers replaced) and MIGRATE (a job run first, if any).
service_config() {
  MIGRATE=""
  case "$1" in
    gateway)
      VAR=GATEWAY_IMAGE; DEFAULT=taskmgn/gateway:local; TARGETS="gateway" ;;
    identity-workspace)
      VAR=IDENTITY_IMAGE; DEFAULT=taskmgn/identity-workspace:local
      TARGETS="identity-workspace identity-outbox"; MIGRATE=identity-migrate ;;
    task)
      VAR=TASK_IMAGE; DEFAULT=taskmgn/task:local; TARGETS="task" ;;
    *) die "unknown service '$1' (use gateway, identity-workspace or task)" ;;
  esac
}

# --- Health ---------------------------------------------------------------------
PROBE='import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen(sys.argv[1], timeout=5).status)
except urllib.error.HTTPError as error:
    print(error.code)
except Exception:
    print(0)'
inside_code() { # url, requested from inside Identity's container (private network)
  local code
  code=$(compose exec -T identity-workspace python -c "$PROBE" "$1" 2>/dev/null | tr -d '\r\n ')
  echo "${code:-0}"
}
gateway_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$GATEWAY$1" || true; }

health_ok() { # service
  case "$1" in
    gateway)
      # The gateway answers, and it still reaches Identity (no login -> 401).
      [ "$(gateway_code /healthz)" = 200 ] && [ "$(gateway_code /api/v1/me)" = 401 ] ;;
    identity-workspace)
      [ "$(inside_code http://127.0.0.1:8000/health/ready)" = 200 ] ;;
    task)
      [ "$(inside_code http://task:8001/health/ready)" = 200 ] ;;
  esac
}

wait_healthy() { # service
  local waited=0
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    if health_ok "$1"; then return 0; fi
    sleep 2
    waited=$((waited + 2))
  done
  return 1
}

# --- Apply an image to one service ---------------------------------------------
apply_image() { # service image
  service_config "$1"
  export "$VAR=$2"
  if [ -n "$MIGRATE" ]; then
    say "  running $MIGRATE (database migrations) before replacing $1"
    compose run --rm --no-deps -T "$MIGRATE" || return 1
  fi
  # shellcheck disable=SC2086
  compose up -d --no-build --no-deps --wait "--wait-timeout=$HEALTH_TIMEOUT" $TARGETS || return 1
  wait_healthy "$1"
}

take_lock() {
  mkdir "$LOCK" 2>/dev/null || die "another deployment is running (remove $LOCK if it is stale)"
  trap 'rmdir "$LOCK" 2>/dev/null' EXIT
}

# --- Commands -------------------------------------------------------------------
cmd_deploy() { # service image
  local service=$1 image=${2:-} current previous_ref
  [ -n "$service" ] && [ -n "$image" ] || die "usage: deploy.sh deploy <service> <image@sha256:digest>"
  service_config "$service"
  local digest_pattern='^[^@[:space:]]+@sha256:[0-9a-f]{64}$'
  if ! [[ $image =~ $digest_pattern ]]; then
    die "'$image' is not an immutable reference. Use <image>@sha256:<64 hex digits>, not a tag."
  fi
  take_lock

  current=$(get_pin "$VAR")
  previous_ref=${current:-$DEFAULT}
  if [ "$current" = "$image" ]; then
    say "$service already runs this image; checking health only."
    wait_healthy "$service" && { say "OK: $service is healthy."; return 0; }
    die "$service runs the recorded image but is not healthy"
  fi

  say "Deploying $service"
  say "  from: $previous_ref"
  say "  to:   $image"
  if ! docker pull "$image" >/dev/null; then
    log_event "$service" "$previous_ref" "$image" "pull-failed"
    die "could not pull $image; nothing was changed"
  fi

  if apply_image "$service" "$image"; then
    set_pin "${VAR}_PREVIOUS" "$previous_ref"
    set_pin "$VAR" "$image"
    log_event "$service" "$previous_ref" "$image" "deployed"
    say "OK: $service is healthy on the new image."
    return 0
  fi

  echo "FAILED: $service could not be deployed (migration, start or health check). Restoring $previous_ref" >&2
  if apply_image "$service" "$previous_ref"; then
    log_event "$service" "$previous_ref" "$image" "rolled-back"
    echo "Rolled back: $service is healthy again on the previous image. Recorded version unchanged." >&2
    return 1
  fi
  log_event "$service" "$previous_ref" "$image" "rollback-failed"
  echo "ERROR: the rollback of $service also failed. Manual recovery is needed:" >&2
  echo "  docker compose -f infra/compose.yaml logs $service" >&2
  return 2
}

cmd_rollback() { # service
  local service=${1:-} current previous
  [ -n "$service" ] || die "usage: deploy.sh rollback <service>"
  service_config "$service"
  take_lock
  current=$(get_pin "$VAR")
  previous=$(get_pin "${VAR}_PREVIOUS")
  [ -n "$previous" ] || die "no previous version is recorded for $service"
  say "Rolling $service back to $previous"
  if apply_image "$service" "$previous"; then
    set_pin "${VAR}_PREVIOUS" "${current:-$DEFAULT}"
    set_pin "$VAR" "$previous"
    log_event "$service" "${current:-$DEFAULT}" "$previous" "rollback"
    say "OK: $service is healthy on the previous image."
    return 0
  fi
  log_event "$service" "${current:-$DEFAULT}" "$previous" "rollback-failed"
  die "rollback of $service failed; see: docker compose -f infra/compose.yaml logs $service"
}

cmd_start() {
  take_lock
  say "Starting the stack from the recorded images (no build)"
  compose up -d --no-build --wait
}

cmd_status() {
  local service ref previous running
  for service in gateway identity-workspace task; do
    service_config "$service"
    ref=$(get_pin "$VAR"); previous=$(get_pin "${VAR}_PREVIOUS")
    running=$(docker inspect --format '{{.Config.Image}}' "$(compose ps -q "${TARGETS%% *}" 2>/dev/null | head -n 1)" 2>/dev/null || true)
    printf '%-20s recorded: %s\n' "$service" "${ref:-(none, local build)}"
    printf '%-20s previous: %s\n' "" "${previous:-(none)}"
    printf '%-20s running:  %s\n' "" "${running:-(not running)}"
  done
  if [ -s "$HISTORY" ]; then
    echo; echo "Last deployments ($HISTORY):"; tail -n 5 "$HISTORY"
  fi
}

case "${1:-}" in
  deploy) shift; cmd_deploy "${1:-}" "${2:-}" ;;
  rollback) shift; cmd_rollback "${1:-}" ;;
  start) cmd_start ;;
  status) cmd_status ;;
  -h | --help | help | "") usage ;;
  *) echo "Unknown command: $1" >&2; usage >&2; exit 2 ;;
esac