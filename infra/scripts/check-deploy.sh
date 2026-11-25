#!/usr/bin/env bash
# Checks for the deployment automation (issue #33), run against the real stack.
#
# Run from the repository root while the stack is up and was built locally:
#   docker compose -f infra/compose.yaml up -d --build --wait
#   bash infra/scripts/check-deploy.sh
#
# It starts a throwaway image registry on 127.0.0.1:${REGISTRY_PORT:-5055}, pushes the
# locally built images to it, then uses infra/scripts/deploy.sh to deploy them by
# digest: a successful deployment of each service, a refused tag, a deliberately
# broken gateway image that must be rolled back, and a manual rollback. It leaves the
# stack running on the local images and removes the registry and its own state.
#
# Exits 0 when every check passes and 1 otherwise. Works with bash 3.2 (macOS).
set -u -o pipefail

cd "$(dirname "$0")/../.." || exit 2
COMPOSE=(docker compose -f infra/compose.yaml)
REGISTRY_PORT=${REGISTRY_PORT:-5055}
REGISTRY="localhost:$REGISTRY_PORT"
REGISTRY_NAME=taskmgn-check-registry
STATE_DIR=$(mktemp -d)
export DEPLOY_STATE_DIR="$STATE_DIR"
export HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-90}
FAILURES=0

if [ -z "${GATEWAY_HTTP_PORT:-}" ] && [ -f infra/.env ]; then
  GATEWAY_HTTP_PORT=$(sed -n 's/^GATEWAY_HTTP_PORT=//p' infra/.env | tail -n 1)
fi
GATEWAY="http://127.0.0.1:${GATEWAY_HTTP_PORT:-8080}"

pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
section() { printf '\n== %s ==\n' "$1"; }
expect() { # description expected actual
  if [ "$3" = "$2" ]; then pass "$1 (got $3)"; else fail "$1 (expected $2, got $3)"; fi
}

cleanup() {
  docker rm -f "$REGISTRY_NAME" >/dev/null 2>&1
  # Return every service to the locally built images, whatever happened above.
  "${COMPOSE[@]}" up -d --no-build --wait >/dev/null 2>&1
  rm -rf "$STATE_DIR"
}
trap cleanup EXIT

container_id() { "${COMPOSE[@]}" ps -q "$1" 2>/dev/null | head -n 1; }
running_image() { docker inspect --format '{{.Config.Image}}' "$(container_id "$1")" 2>/dev/null; }
pinned() { sed -n "s/^$1=//p" "$STATE_DIR/images.env" 2>/dev/null | tail -n 1; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$GATEWAY$1" || true; }
deploy() { bash infra/scripts/deploy.sh "$@" >"$STATE_DIR/last.log" 2>&1; }

# Push a local image to the throwaway registry and print its immutable reference.
push_ref() { # local-image repository
  local digest
  docker tag "$1" "$REGISTRY/$2:check" >/dev/null || return 1
  digest=$(docker push "$REGISTRY/$2:check" 2>&1 | sed -n 's/.*digest: \(sha256:[0-9a-f]*\).*/\1/p' | head -n 1)
  [ -n "$digest" ] && echo "$REGISTRY/$2@$digest"
}

section "Setup: throwaway registry and images built by the stack"
for image in taskmgn/gateway:local taskmgn/identity-workspace:local taskmgn/task:local; do
  if docker image inspect "$image" >/dev/null 2>&1; then pass "$image exists"; else fail "$image is missing (run: docker compose -f infra/compose.yaml up -d --build --wait)"; fi
done
if [ "$FAILURES" -gt 0 ]; then echo; echo "$FAILURES check(s) failed."; exit 1; fi

docker rm -f "$REGISTRY_NAME" >/dev/null 2>&1
if docker run -d --name "$REGISTRY_NAME" -p "127.0.0.1:$REGISTRY_PORT:5000" registry:2 >/dev/null; then
  pass "registry started on $REGISTRY"
else
  fail "could not start the registry on port $REGISTRY_PORT (set REGISTRY_PORT to a free port)"
  echo; echo "$FAILURES check(s) failed."; exit 1
fi
sleep 2

GATEWAY_REF=$(push_ref taskmgn/gateway:local gateway)
IDENTITY_REF=$(push_ref taskmgn/identity-workspace:local identity-workspace)
TASK_REF=$(push_ref taskmgn/task:local task)
for ref in "$GATEWAY_REF" "$IDENTITY_REF" "$TASK_REF"; do
  case "$ref" in *@sha256:*) pass "pushed ${ref%%@*} with a digest" ;; *) fail "could not push an image to the registry" ;; esac
done

# A second, different, healthy gateway version (for the rollback check) and a broken one.
printf 'FROM %s\nLABEL taskmgn.check=second\n' "$GATEWAY_REF" | docker build -q -t "$REGISTRY/gateway:second" - >/dev/null 2>&1
printf 'FROM %s\nCMD ["sh","-c","exit 1"]\n' "$GATEWAY_REF" | docker build -q -t "$REGISTRY/gateway:broken" - >/dev/null 2>&1
SECOND_REF=$(docker push "$REGISTRY/gateway:second" 2>&1 | sed -n 's/.*digest: \(sha256:[0-9a-f]*\).*/\1/p' | head -n 1)
BROKEN_REF=$(docker push "$REGISTRY/gateway:broken" 2>&1 | sed -n 's/.*digest: \(sha256:[0-9a-f]*\).*/\1/p' | head -n 1)
SECOND_REF="$REGISTRY/gateway@$SECOND_REF"
BROKEN_REF="$REGISTRY/gateway@$BROKEN_REF"
case "$SECOND_REF$BROKEN_REF" in *@@*|*@) fail "could not prepare the second and broken gateway images" ;; *) pass "prepared a second healthy and a deliberately broken gateway image" ;; esac

IDENTITY_BEFORE=$(container_id identity-workspace)
TASK_BEFORE=$(container_id task)

section "Only immutable, pullable images are accepted"
deploy deploy gateway nginx:latest; status=$?
expect "a tag instead of a digest is refused (exit status)" 2 "$status"
expect "nothing was recorded after the refusal" "" "$(pinned GATEWAY_IMAGE)"
deploy deploy gateway "$REGISTRY/gateway@sha256:$(printf '0%.0s' $(seq 64))"; status=$?
expect "an image that cannot be pulled is refused (exit status)" 2 "$status"
expect "the gateway still serves after the refused deployment" 200 "$(code /healthz)"

section "Deploy one service by digest; the others are untouched"
deploy deploy gateway "$GATEWAY_REF"; status=$?
expect "gateway deployment succeeds (exit status)" 0 "$status"
expect "gateway runs the requested digest" "$GATEWAY_REF" "$(running_image gateway)"
expect "the deployed version is recorded" "$GATEWAY_REF" "$(pinned GATEWAY_IMAGE)"
expect "gateway serves after the deployment" 200 "$(code /healthz)"
expect "gateway still reaches Identity (no login -> 401)" 401 "$(code /api/v1/me)"
expect "Identity container was not replaced" "$IDENTITY_BEFORE" "$(container_id identity-workspace)"
expect "Task container was not replaced" "$TASK_BEFORE" "$(container_id task)"

section "Failed rollout: the previous version is restored"
deploy deploy gateway "$BROKEN_REF"; status=$?
expect "the broken image is reported as a failed deployment (exit status)" 1 "$status"
expect "gateway is back on the previous digest" "$GATEWAY_REF" "$(running_image gateway)"
expect "the recorded version did not change" "$GATEWAY_REF" "$(pinned GATEWAY_IMAGE)"
expect "gateway serves again after the rollback" 200 "$(code /healthz)"
if grep -q 'result=rolled-back' "$STATE_DIR/history.log" 2>/dev/null; then pass "the rollback is in the deployment history"; else fail "the rollback is missing from the deployment history"; fi

section "Manual rollback to the previous recorded version"
deploy deploy gateway "$SECOND_REF"; status=$?
expect "second healthy gateway version deploys (exit status)" 0 "$status"
expect "gateway runs the second version" "$SECOND_REF" "$(running_image gateway)"
deploy rollback gateway; status=$?
expect "rollback command succeeds (exit status)" 0 "$status"
expect "gateway is back on the first version" "$GATEWAY_REF" "$(running_image gateway)"

section "Identity (migrations first) and Task deploy independently"
deploy deploy identity-workspace "$IDENTITY_REF"; status=$?
expect "Identity deployment succeeds (exit status)" 0 "$status"
expect "Identity runs the requested digest" "$IDENTITY_REF" "$(running_image identity-workspace)"
if grep -q 'database migrations' "$STATE_DIR/last.log"; then pass "the migration job ran before Identity was replaced"; else fail "the migration job did not run"; fi
expect "Task container was not replaced by the Identity deployment" "$TASK_BEFORE" "$(container_id task)"
deploy deploy task "$TASK_REF"; status=$?
expect "Task deployment succeeds (exit status)" 0 "$status"
expect "Task runs the requested digest" "$TASK_REF" "$(running_image task)"
expect "gateway still reaches Identity after all deployments" 401 "$(code /api/v1/me)"

section "Not verified by this script"
echo "  - Deployment to the Oracle VM: it needs the VM from issue #32. The deploy"
echo "    workflow's SSH step is untested until then."
echo "  - Task's own workflow does not publish its image to the registry yet (its"
echo "    owner must add that step); Identity and Gateway publish from main."

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "All checks passed."
  exit 0
fi
echo "$FAILURES check(s) failed."
exit 1