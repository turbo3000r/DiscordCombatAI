"""Shared Bot UI views."""

from bot.modules.UI.views.lobby import LobbyView
from bot.modules.UI.views.sequential_collector import SequentialCollector
from bot.modules.UI.views.staged_settings import StagedSettingsView
from bot.modules.UI.views.task_progress_container import TaskProgressContainer

__all__ = ["LobbyView", "SequentialCollector", "StagedSettingsView", "TaskProgressContainer"]
