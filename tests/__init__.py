"""Test package for usgs_streamflow.

Importing this package installs the Home Assistant stubs (see ``_ha``) before
any test module imports the integration, so tests run without a real Home
Assistant install. Run with:

    python -m unittest discover -s tests -t .
"""
from . import _ha  # noqa: F401  (side effect: installs stubs + sys.path)
