# RabbitMQ Event Exchange Feasibility

`rabbitmq_event_exchange` is bundled with the official RabbitMQ 3.13-management image family, so the Phase 0 compose layout can enable it from `enabled_plugins` without a custom broker build.

`rabbitmq.conf` sets `rabbitmq_event_exchange.vhost = /discordcombatai` so `amq.rabbitmq.event` is reachable in the same vhost as the application topology (not only the default `/` vhost).

Live enablement is deferred on this workstation when Docker is unavailable. CI and any Docker-capable host should validate the plugin + vhost path via broker integration tests.
