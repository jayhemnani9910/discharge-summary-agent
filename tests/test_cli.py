"""CLI argument handling.

The `run` command talks to a real provider; the no-key `mock` provider is only wired up
for `demo`. So `run --chat-provider mock` must be rejected at parse time rather than
crashing later inside an unconfigured MockProvider.
"""

import pytest

from discharge_agent.cli import main


def test_run_rejects_mock_chat_provider():
    with pytest.raises(SystemExit):
        main(["run", "--transcript", "x.json", "--chat-provider", "mock"])


def test_run_rejects_mock_vision_provider():
    with pytest.raises(SystemExit):
        main(["run", "--pdf", "x.pdf", "--vision-provider", "mock"])
