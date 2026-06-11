import pytest

from tests.scenario import TrainConfig


@pytest.fixture
def scenario() -> type[TrainConfig]:
    return TrainConfig
