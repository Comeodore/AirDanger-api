#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:?set HOST, e.g. user@your-server}"
SSH_PORT="${SSH_PORT:-22}"
REMOTE_DIR="${REMOTE_DIR:-/root/airdanger-api}"
IMAGE="${IMAGE:-airdanger-api}"
CONTAINER="${CONTAINER:-airdanger-api}"

ssh_do() { ssh -p "$SSH_PORT" "$HOST" "$@"; }

echo "==> ensure remote dir (.env lives there, not in vcs)"
ssh_do "mkdir -p $REMOTE_DIR"

echo "==> rsync code -> $REMOTE_DIR (secrets & vcs excluded)"
rsync -az --delete -e "ssh -p $SSH_PORT" \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude '.env*' \
  ./ "$HOST:$REMOTE_DIR/"

echo "==> detect current published host port (reuse it so routing is unchanged)"
HOSTPORT="$(ssh_do "docker port $CONTAINER 8000/tcp 2>/dev/null" | sed 's/.*://' | head -1 || true)"
HOSTPORT="${HOSTPORT:-9994}"
echo "    host port = $HOSTPORT"

echo "==> tag current image as :prev for rollback, then build :latest"
ssh_do "docker image inspect $IMAGE:latest >/dev/null 2>&1 && docker tag $IMAGE:latest $IMAGE:prev || true"
ssh_do "cd $REMOTE_DIR && docker build -t $IMAGE:latest ."

echo "==> recreate container '$CONTAINER'"
ssh_do "docker rm -f $CONTAINER 2>/dev/null || true; \
  docker run -d --name $CONTAINER --restart unless-stopped \
    --env-file $REMOTE_DIR/.env \
    --log-opt max-size=20m --log-opt max-file=5 \
    -p $HOSTPORT:8000 $IMAGE:latest && \
  docker ps --filter name=$CONTAINER --format '{{.Names}} {{.Status}} {{.Ports}}'"

echo "==> health check"
sleep 4
ssh_do "curl -fsS http://localhost:$HOSTPORT/health && echo"
echo "==> deployed. Rollback: docker tag $IMAGE:prev $IMAGE:latest && re-run recreate."
