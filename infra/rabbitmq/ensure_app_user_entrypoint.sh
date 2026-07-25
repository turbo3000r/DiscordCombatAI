#!/usr/bin/env bash
# Bootstrap Compose-injected credentials, then import topology definitions.
# definitions.json intentionally omits users/secrets.
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
  find /var/lib/rabbitmq ! -user rabbitmq -exec chown rabbitmq '{}' + 2>/dev/null || true
  exec gosu rabbitmq "$0" "$@"
fi

rabbitmq-server &
pid=$!

for _ in $(seq 1 90); do
  if rabbitmqctl await_startup >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
rabbitmqctl await_startup

user="${RABBITMQ_DEFAULT_USER:?RABBITMQ_DEFAULT_USER is required}"
pass="${RABBITMQ_DEFAULT_PASS:?RABBITMQ_DEFAULT_PASS is required}"
vhost="${RABBITMQ_DEFAULT_VHOST:?RABBITMQ_DEFAULT_VHOST is required}"

if ! rabbitmqctl authenticate_user "$user" "$pass" >/dev/null 2>&1; then
  if rabbitmqctl list_users | awk '{print $1}' | grep -qx "$user"; then
    rabbitmqctl change_password "$user" "$pass"
  else
    rabbitmqctl add_user "$user" "$pass"
  fi
fi

rabbitmqctl set_user_tags "$user" administrator
rabbitmqctl add_vhost "$vhost" >/dev/null 2>&1 || true
rabbitmqctl set_permissions -p "$vhost" "$user" ".*" ".*" ".*"
rabbitmqctl import_definitions /etc/rabbitmq/definitions.json

wait "$pid"
