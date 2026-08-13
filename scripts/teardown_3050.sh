#!/usr/bin/env bash
# Full removal of AgentFailDB from the 3050 box. SCOPED — only this project's
# systemd units, compose containers/volumes/images, and files. No `system prune`,
# so other projects on the shared box are untouched.
# Launch: SUDOPASS=... bash /tmp/teardown_3050.sh
set -x
SUDO() { echo "$SUDOPASS" | sudo -S "$@"; }

echo "######## 1. systemd services ########"
for s in agentfaildb-bench-main agentfaildb-bench-autogen agentfaildb-dashboard; do
  SUDO systemctl stop "$s" 2>/dev/null
  SUDO systemctl disable "$s" 2>/dev/null
  SUDO rm -f "/etc/systemd/system/$s.service" 2>/dev/null
done
SUDO systemctl daemon-reload 2>/dev/null
SUDO systemctl reset-failed 2>/dev/null

echo "######## 2. docker compose down (containers + volumes + our images) ########"
if [ -d "$HOME/agentfaildb" ]; then
  ( cd "$HOME/agentfaildb" && docker compose down -v --rmi all --remove-orphans 2>&1 | tail -12 )
fi
# belt-and-suspenders: named volumes + images (scoped, skip if in use elsewhere)
docker volume rm agentfaildb_postgres_data agentfaildb_redis_data agentfaildb_ollama_data agentfaildb_pip_cache 2>/dev/null
docker rmi ollama/ollama:latest postgres:16-alpine redis:7-alpine 2>/dev/null

echo "######## 3. files ########"
cd "$HOME"
rm -rf "$HOME/agentfaildb" "$HOME/agentfaildb_db_backup.sql.gz" "$HOME/.config/crewai"
SUDO rm -f /tmp/afdb-*.service 2>/dev/null
rm -f /tmp/ollama_pull.sh 2>/dev/null

echo "######## VERIFY ########"
echo "-- containers (ours):"; docker ps -a --format '{{.Names}}' | grep -iE 'agentfaildb|ollama|postgres|redis' || echo "none"
echo "-- volumes (ours):";    docker volume ls --format '{{.Name}}' | grep -i agentfaildb || echo "none"
echo "-- images (ours):";     docker images --format '{{.Repository}}:{{.Tag}}' | grep -iE 'ollama|postgres:16|redis:7|agentfaildb' || echo "none"
echo "-- services:";          systemctl list-unit-files 2>/dev/null | grep -i agentfaildb || echo "none"
echo "-- repo dir:";          ls -d "$HOME/agentfaildb" 2>/dev/null || echo "removed"
echo "######## TEARDOWN DONE ########"
