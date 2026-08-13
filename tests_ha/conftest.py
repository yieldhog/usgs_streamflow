"""Fixtures for the Home Assistant test-harness suite.

Runs against the *real* Home Assistant test framework via
``pytest-homeassistant-custom-component`` (unlike the dependency-free
``tests/`` suite, which stubs Home Assistant). Install the deps with
``pip install -r requirements_test.txt`` and run ``pytest``.
"""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.usgs_streamflow.const import (
    CONF_API_KEY,
    CONF_BACKEND,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DOMAIN,
)

from .helpers import SITE_ID, SITE_NAME


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow the custom integration to load in every test."""
    yield


@pytest.fixture
def legacy_entry() -> MockConfigEntry:
    """A configured entry on the default (legacy) backend."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"usgs_{SITE_ID}",
        title=SITE_NAME,
        data={CONF_SITE_ID: SITE_ID, CONF_SITE_NAME: SITE_NAME},
        options={},
    )


@pytest.fixture
def modern_entry() -> MockConfigEntry:
    """A configured entry on the modern backend with an API key."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"usgs_{SITE_ID}",
        title=SITE_NAME,
        data={CONF_SITE_ID: SITE_ID, CONF_SITE_NAME: SITE_NAME},
        options={CONF_BACKEND: "modern", CONF_API_KEY: "OLD_KEY"},
    )
