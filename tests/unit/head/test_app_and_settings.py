from __future__ import annotations

import pytest
from pydantic import ValidationError

from head.app import HeadApplication
from head.settings import HeadSettings


class FakeComponent:
    def __init__(self, journal: list[str], name: str) -> None:
        self.journal = journal
        self.name = name

    async def start(self) -> None:
        self.journal.append(f"start:{self.name}")

    async def close(self) -> None:
        self.journal.append(f"close:{self.name}")


@pytest.mark.asyncio
async def test_application_closes_components_once_in_reverse_order() -> None:
    journal: list[str] = []
    first = FakeComponent(journal, "first")
    second = FakeComponent(journal, "second")
    application = HeadApplication(components=[first, second])

    await application.start()
    await application.close()
    await application.close()

    assert journal == ["start:first", "start:second", "close:second", "close:first"]


def test_settings_reject_unsafe_cadence_and_group_relationships() -> None:
    with pytest.raises(ValidationError):
        HeadSettings(
            node_id="node-a",
            application_version="v1.0.0",
            github_repo="owner/repo",
            rabbitmq_user="discordcombatai",
            rabbitmq_pass="change-me-in-env",
            election_heartbeat_sec=60,
            lease_duration_sec=60,
            pubsub_cluster_group="same",
            pubsub_dashboard_group="same",
        )
