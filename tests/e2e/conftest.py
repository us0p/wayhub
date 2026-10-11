from typing import Any

import pytest


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    """Chromium's fake microphone (a test tone), granted without a permission prompt, so the
    voice interview can run headless."""
    return {
        **browser_type_launch_args,
        "args": ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
    }
