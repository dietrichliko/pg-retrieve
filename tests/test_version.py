from importlib.metadata import version

import pg_retrieve


def test_version_matches_package_metadata() -> None:
    assert pg_retrieve.__version__ == version("pg-retrieve")
