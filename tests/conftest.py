import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from coinpilot_ai.application.bootstrap import create_application


@pytest.fixture(scope="session")
def app():
    return create_application([])

