import pytest


@pytest.fixture(autouse=True)
def _no_chunked_install_by_default(monkeypatch, request):
    """Adapter unit tests drive fake clients that have no SDK upload pipeline. Tests that exercise
    the real install path opt back in with the `real_chunked` marker."""
    if "real_chunked" in request.keywords:
        monkeypatch.setenv("BILI2NB_CHUNKED_UPLOAD", "1")
    else:
        monkeypatch.setenv("BILI2NB_CHUNKED_UPLOAD", "0")
