# RabbitMQ Event Exchange Feasibility

`rabbitmq_event_exchange` is bundled with the official RabbitMQ 3.13-management image family, so the Phase 0 compose layout can enable it from `enabled_plugins` without a custom broker build.

Live enablement is deferred on this workstation because Docker is unavailable here. The config is still committed now so CI and any Docker-capable host can validate the plugin path immediately.
