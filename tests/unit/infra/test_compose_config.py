from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
_REQUIRED_INTERPOLATION = re.compile(r"\$\{[^}]*:\?")


def _load_compose(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_base_compose_has_expected_broker_topology() -> None:
    compose = _load_compose(ROOT / "docker-compose.yml")
    services = compose["services"]  # type: ignore[index]

    network = compose["networks"]["dca-internal"]  # type: ignore[index]
    assert network is None or network.get("internal") is not True  # type: ignore[union-attr]
    assert "rabbitmq-data" in compose["volumes"]  # type: ignore[index]

    mosquitto = services["mosquitto"]  # type: ignore[index]
    rabbitmq = services["rabbitmq"]  # type: ignore[index]
    head = services["head"]  # type: ignore[index]
    bot = services["bot"]  # type: ignore[index]
    ai_worker = services["ai_worker"]  # type: ignore[index]

    assert "ports" not in mosquitto
    assert "ports" not in rabbitmq
    assert mosquitto["healthcheck"]  # type: ignore[index]
    assert rabbitmq["healthcheck"]  # type: ignore[index]
    assert "ai_tasks_results" in str(rabbitmq["healthcheck"]["test"])  # type: ignore[index]
    assert rabbitmq["volumes"][0].startswith("rabbitmq-data:")  # type: ignore[index]

    assert "profiles" not in head
    for service in (bot, ai_worker):
        assert service["profiles"] == ["application"]  # type: ignore[index]
    for service in (head, bot, ai_worker):
        assert service["restart"] == "unless-stopped"  # type: ignore[index]
        assert service["environment"]["APPLICATION_VERSION"] == (  # type: ignore[index]
            "${APPLICATION_VERSION:-v0.1.0}"
        )

    assert head["extra_hosts"] == ["host.docker.internal:host-gateway"]  # type: ignore[index]
    assert head["ports"] == ["127.0.0.1:9800:9800"]  # type: ignore[index]
    assert any(
        str(mount).endswith(":/run/secrets/launcher_ipc_secret:ro")
        for mount in head["volumes"]  # type: ignore[index]
    )
    assert "condition" in head["depends_on"]["mosquitto"]  # type: ignore[index]
    assert "condition" in head["depends_on"]["rabbitmq"]  # type: ignore[index]

    bot_env = bot["environment"]  # type: ignore[index]
    worker_env = ai_worker["environment"]  # type: ignore[index]
    assert bot_env["BOT_NODE_ID"] == "${NODE_ID:-node-local}"
    assert worker_env["AI_WORKER_NODE_ID"] == "${NODE_ID:-node-local}"
    assert bot_env["NODE_ID"] == "${NODE_ID:-node-local}"
    assert worker_env["NODE_ID"] == "${NODE_ID:-node-local}"
    assert bot_env["DCA_RUNTIME_MODE"] == "${DCA_RUNTIME_MODE:-production}"
    assert bot_env["DISCORD_DEVELOPMENT_GUILD_ID"] == "${DISCORD_DEVELOPMENT_GUILD_ID:-}"
    assert bot_env["RABBITMQ_DEFAULT_VHOST"] == "${RABBITMQ_DEFAULT_VHOST:-/discordcombatai}"
    assert worker_env["RABBITMQ_DEFAULT_VHOST"] == "${RABBITMQ_DEFAULT_VHOST:-/discordcombatai}"
    assert bot_env["BOT_GUILD_SYNC_INTERVAL_SEC"] == "${BOT_GUILD_SYNC_INTERVAL_SEC:-3600}"
    assert bot_env["BOT_STATUS_PUSH_INTERVAL_SEC"] == "${BOT_STATUS_PUSH_INTERVAL_SEC:-60}"
    assert bot_env["BOT_SHUTDOWN_GRACE_SEC"] == "${BOT_SHUTDOWN_GRACE_SEC:-30}"
    assert bot_env["BOT_CONTROL_DRAIN_TIMEOUT_SEC"] == "${BOT_CONTROL_DRAIN_TIMEOUT_SEC:-45}"
    assert bot_env["BOT_ACTIVATION_GRANT_MAX_TTL_SEC"] == "${BOT_ACTIVATION_GRANT_MAX_TTL_SEC:-60}"
    assert bot_env["BOT_AZURE_CLIENT_ID"] == "${BOT_AZURE_CLIENT_ID:-}"
    assert bot_env["AZURE_COSMOS_ENDPOINT"] == "${AZURE_COSMOS_ENDPOINT:-}"
    assert bot_env["AZURE_STORAGE_ACCOUNT_NAME"] == "${AZURE_STORAGE_ACCOUNT_NAME:-}"
    assert bot_env["BOT_QUEUE_POLL_INTERVAL_SEC"] == "${BOT_QUEUE_POLL_INTERVAL_SEC:-300}"
    assert (
        bot_env["BOT_SUGGESTION_SWEEP_INTERVAL_SEC"]
        == "${BOT_SUGGESTION_SWEEP_INTERVAL_SEC:-900}"
    )
    assert (
        bot_env["BOT_SUGGESTION_SWEEP_MIN_AGE_SEC"] == "${BOT_SUGGESTION_SWEEP_MIN_AGE_SEC:-600}"
    )
    assert bot_env["BOT_SUGGESTION_MAX_DM_ATTEMPTS"] == "${BOT_SUGGESTION_MAX_DM_ATTEMPTS:-5}"
    assert (
        bot_env["BOT_SUGGESTION_CLAIM_TIMEOUT_SEC"] == "${BOT_SUGGESTION_CLAIM_TIMEOUT_SEC:-120}"
    )
    assert worker_env["AI_WORKER_TRANSPORT_SHELL"] == "${AI_WORKER_TRANSPORT_SHELL:-false}"


def test_dev_compose_adds_local_ports_and_source_mounts() -> None:
    compose = _load_compose(ROOT / "docker-compose.dev.yml")
    services = compose["services"]  # type: ignore[index]

    assert services["mosquitto"]["ports"] == ["127.0.0.1:1883:1883"]  # type: ignore[index]
    assert services["rabbitmq"]["ports"] == [  # type: ignore[index]
        "127.0.0.1:5672:5672",
        "127.0.0.1:15672:15672",
    ]

    head = services["head"]  # type: ignore[index]
    assert head["profiles"] == ["production"]  # type: ignore[index]
    assert "./src:/app/src" in head["volumes"]  # type: ignore[index]

    for service_name in ("bot", "ai_worker"):
        mounts = services[service_name]["volumes"]  # type: ignore[index]
        assert "./src:/app/src" in mounts

    assert "./prompts:/app/prompts:ro" in services["ai_worker"]["volumes"]  # type: ignore[index]
    assert services["bot"]["build"]["dockerfile"] == "src/bot/Dockerfile"  # type: ignore[index]
    assert services["ai_worker"]["build"]["dockerfile"] == "src/ai_worker/Dockerfile"  # type: ignore[index]

    bot_env = services["bot"]["environment"]  # type: ignore[index]
    assert bot_env["DCA_RUNTIME_MODE"] == "development"
    assert bot_env["DEV_COMPOSE_OVERLAY_ACTIVE"] == "true"
    assert bot_env["DEV_SUPPORT_URL"] == "http://dev-support:8080"
    assert bot_env["DISCORD_DEVELOPMENT_GUILD_ID"] == "${DISCORD_DEVELOPMENT_GUILD_ID:-}"
    assert bot_env["DISCORD_DEVELOPMENT_APPLICATION_ID"] == (
        "${DISCORD_DEVELOPMENT_APPLICATION_ID:-}"
    )
    assert bot_env["DISCORD_BOT_TOKEN"] == "${DISCORD_DEVELOPMENT_BOT_TOKEN:-}"
    assert bot_env["AZURE_COSMOS_ENDPOINT"] == ""

    dev_support = services["dev-support"]  # type: ignore[index]
    assert "ports" not in dev_support
    assert "8080" in str(dev_support.get("expose", []))
    assert "dev-support-data" in compose["volumes"]  # type: ignore[index]
    assert any("dev-support-data:" in str(v) for v in dev_support["volumes"])  # type: ignore[index]
    assert "application" in dev_support["profiles"]  # type: ignore[index]
    assert dev_support["environment"]["DISCORD_DEVELOPMENT_GUILD_ID"] == (
        "${DISCORD_DEVELOPMENT_GUILD_ID:-}"
    )

    web = services["web"]  # type: ignore[index]
    assert web["profiles"] == ["application", "development"]  # type: ignore[index]
    assert web["build"]["dockerfile"] == "src/web/Dockerfile"  # type: ignore[index]
    assert web["ports"] == ["127.0.0.1:8088:8080"]  # type: ignore[index]
    assert web["environment"]["DCA_RUNTIME_MODE"] == "development"  # type: ignore[index]
    assert web["environment"]["DEV_SUPPORT_URL"] == "http://dev-support:8080"  # type: ignore[index]
    assert web["environment"]["DISCORD_DEVELOPMENT_GUILD_ID"] == (
        "${DISCORD_DEVELOPMENT_GUILD_ID:-}"
    )
    assert web["environment"]["WEB_LOCAL_ADMIN_OID"] == (
        "${WEB_LOCAL_ADMIN_OID:-local-dev-admin}"
    )
    assert web["environment"]["AZURE_COSMOS_ENDPOINT"] == ""  # type: ignore[index]
    assert web["environment"]["WEB_ENTRA_TENANT_ID"] == ""  # type: ignore[index]
    assert "condition" in web["depends_on"]["dev-support"]  # type: ignore[index]
    assert "./src:/app/src" in web["volumes"]  # type: ignore[index]


def test_base_compose_does_not_include_production_web() -> None:
    compose = _load_compose(ROOT / "docker-compose.yml")
    services = compose["services"]  # type: ignore[index]
    assert "web" not in services


def test_compose_files_allow_broker_only_interpolation() -> None:
    """Docker interpolates every service, including profile-gated ones.

    Required interpolation (`VAR:?error`) would break `docker compose config` and
    broker-only `up` when Discord secrets are unset. Process start remains
    fail-closed in runtime settings.
    """
    for relative in ("docker-compose.yml", "docker-compose.dev.yml"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert _REQUIRED_INTERPOLATION.search(text) is None, (
            f"{relative} must not require env at parse time"
        )
