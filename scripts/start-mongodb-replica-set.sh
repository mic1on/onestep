#!/usr/bin/env bash
set -euo pipefail

container="${ONESTEP_MONGODB_CONTAINER:-mongodb}"
port="${ONESTEP_MONGODB_PORT:-27017}"

diagnose_failure() {
  local status=$?
  if (( status != 0 )); then
    docker inspect --format '{{json .State}}' "$container" >&2 || true
    docker logs "$container" >&2 || true
  fi
  exit "$status"
}
trap diagnose_failure EXIT

wait_for_mongo() {
  local label="$1"
  local expression="$2"
  for ((attempt = 0; attempt < 60; attempt++)); do
    if [[ "$(docker inspect --format '{{.State.Running}}' "$container")" != true ]]; then
      echo "MongoDB exited while waiting for $label" >&2
      return 1
    fi
    if docker exec "$container" mongosh --quiet --eval "$expression"; then
      return 0
    fi
    sleep 2
  done
  echo "Timed out waiting for MongoDB $label" >&2
  return 1
}

# Containers share the host kernel; record it for upstream compatibility failures.
docker info --format 'Docker host kernel: {{.KernelVersion}}'
docker run -d --name "$container" -p "127.0.0.1:$port:27017" \
  mongo:8.0 mongod --replSet rs0 --bind_ip_all
wait_for_mongo ping 'quit(db.adminCommand({ping:1}).ok === 1 ? 0 : 1)'
docker exec "$container" mongosh --quiet --eval \
  'const result = rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:27017"}]}); printjson(result); quit(result.ok === 1 ? 0 : 1)'
wait_for_mongo primary 'quit(db.hello().isWritablePrimary === true ? 0 : 1)'
