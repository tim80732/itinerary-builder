#!/bin/bash
# Update Itinerary Builder on Unraid from GitHub: pull, rebuild the image, recreate the container.
#   bash /mnt/user/appdata/itinerary-builder/src/unraid/deploy.sh
set -euo pipefail

SRC="${SRC:-/mnt/user/appdata/itinerary-builder/src}"
NAME="${CONTAINER:-itinerary-builder}"
REBUILD=/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/rebuild_container

cd "$SRC"

# rebuild_container leaves a container stopped unless it has autostart on,
# so remember whether it was running and start it again if so.
was_running=$(docker ps --format '{{.Names}}' | grep -qx "$NAME" && echo yes || echo no)

git pull --ff-only
echo "Now at $(git log -1 --format='%h %s')"
docker build -t itinerary-builder:latest .
"$REBUILD" "$NAME"
if [ "$was_running" = yes ] && ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  docker start "$NAME" >/dev/null
fi
docker image prune -f >/dev/null
echo "Done."
