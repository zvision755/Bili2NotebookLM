import pytest

from bili2nb.bilibili import downloader as dl
from bili2nb.bilibili.parser import VideoRef, parse_url, resolve_short, safe_name
from bili2nb.errors import Bili2nbError


def no_resolve(u):
    raise AssertionError("resolver must not be called")


@pytest.mark.parametrize(
    "url,vid,part",
    [
        ("https://www.bilibili.com/video/BV1GJ411x7h7", "BV1GJ411x7h7", None),
        ("http://bilibili.com/video/BV1GJ411x7h7/", "BV1GJ411x7h7", None),
        ("https://m.bilibili.com/video/BV1GJ411x7h7?p=3&share_source=copy", "BV1GJ411x7h7", 3),
        ("https://www.bilibili.com/video/av170001", "av170001", None),
        ("  https://www.bilibili.com/video/BV1GJ411x7h7?p=2  ", "BV1GJ411x7h7", 2),
    ],
)
def test_valid_urls(url, vid, part):
    assert parse_url(url, no_resolve) == VideoRef(vid, part)


def test_canonical_url():
    assert VideoRef("BV1GJ411x7h7", 4).url == "https://www.bilibili.com/video/BV1GJ411x7h7?p=4"
    assert VideoRef("BV1GJ411x7h7", None).url == "https://www.bilibili.com/video/BV1GJ411x7h7"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://www.bilibili.com/video/BV1GJ411x7h7",
        "file:///etc/passwd",
        "https://evil.com/video/BV1GJ411x7h7",
        "https://www.bilibili.com.evil.com/video/BV1GJ411x7h7",
        "https://evilbilibili.com/video/BV1GJ411x7h7",
        "https://user:pw@www.bilibili.com/video/BV1GJ411x7h7",
        "https://www.bilibili.com:8080/video/BV1GJ411x7h7",
        "https://www.bilibili.com/bangumi/play/ep1",
        "https://www.bilibili.com/video/BV1GJ411x7h7?p=abc",
        "https://www.bilibili.com/video/BV1GJ411x7h7?p=0",
        "http://127.0.0.1/video/BV1GJ411x7h7",
        "https://169.254.169.254/",
        "not a url",
        "",
        "https://www.bilibili.com/video/BV1;rm -rf",
    ],
)
def test_invalid_urls_rejected(url):
    with pytest.raises(Bili2nbError) as e:
        parse_url(url, no_resolve)
    assert e.value.code == "INVALID_URL"


def test_short_link_uses_resolver_and_revalidates():
    seen = []

    def ok(u):
        seen.append(u)
        return "https://www.bilibili.com/video/BV1GJ411x7h7?p=2"

    assert parse_url("https://b23.tv/abc123", ok) == VideoRef("BV1GJ411x7h7", 2)
    assert seen == ["https://b23.tv/abc123"]

    # a malicious redirect target is rejected even if the resolver returns it
    with pytest.raises(Bili2nbError) as e:
        parse_url("https://b23.tv/abc123", lambda u: "https://evil.com/video/BV1GJ411x7h7")
    assert e.value.code == "INVALID_URL"


def test_resolve_short_rejects_foreign_hosts():
    with pytest.raises(Bili2nbError) as e:
        resolve_short("https://evil.com/x")
    assert e.value.code == "INVALID_URL"


def test_safe_name():
    assert "/" not in safe_name("a/b\\c:d*e?f")
    assert safe_name("../../etc/passwd").startswith("_") or ".." not in safe_name("../../etc/passwd")
    assert safe_name("") == "video"
    assert len(safe_name("x" * 500)) == 60


@pytest.mark.parametrize(
    "msg,code",
    [
        ("HTTP Error 412: Precondition Failed", "RATE_LIMITED"),
        ("HTTP Error 403: Forbidden", "VIDEO_UNAVAILABLE"),
        ("HTTP Error 404: Not Found", "VIDEO_UNAVAILABLE"),
        ("This video requires login", "VIDEO_UNAVAILABLE"),
        ("Read timed out", "NETWORK_ERROR"),
        ("something strange", "MEDIA_ERROR"),
    ],
)
def test_classify_download_error(msg, code):
    assert dl.classify_download_error(msg).code == code


def _ref(part=None):
    from bili2nb.bilibili.parser import VideoRef

    return VideoRef("BV1GJ411x7h7", part)


def test_fetch_info_prechecks_missing_video_and_part_range(monkeypatch):
    monkeypatch.setattr(dl, "fetch_view", lambda v: (-404, "啥都木有", []))
    with pytest.raises(Bili2nbError) as e:
        dl.fetch_info(_ref())
    assert e.value.code == "VIDEO_UNAVAILABLE" and "不存在" in e.value.message

    pages = [{"cid": 1, "page": 1, "part": "a"}, {"cid": 2, "page": 2, "part": "b"}]
    monkeypatch.setattr(dl, "fetch_view", lambda v: (0, "0", pages))
    with pytest.raises(Bili2nbError) as e2:
        dl.fetch_info(_ref(5))
    assert e2.value.code == "VIDEO_UNAVAILABLE" and "只有 2 个分 P" in e2.value.message


def test_fetch_info_risk_control_does_not_claim_video_missing(monkeypatch):
    monkeypatch.setattr(dl, "fetch_view", lambda v: (-412, "request was banned", []))

    class Boom:
        def __enter__(self):
            raise __import__("yt_dlp").utils.DownloadError("HTTP Error 412: Precondition Failed")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dl, "_ydl", lambda o: Boom())
    with pytest.raises(Bili2nbError) as e:
        dl.fetch_info(_ref())
    assert e.value.code == "RATE_LIMITED"


def test_fetch_info_uses_cid_and_part_title(monkeypatch):
    pages = [{"cid": 11, "page": 1, "part": "第一集"}, {"cid": 22, "page": 2, "part": "第二集"}]
    monkeypatch.setattr(dl, "fetch_view", lambda v: (0, "0", pages))

    class Y:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"id": "BV1GJ411x7h7_p2", "title": "合集", "uploader": "U", "duration": 12.5}

    monkeypatch.setattr(dl, "_ydl", lambda o: Y())
    info = dl.fetch_info(_ref(2))
    assert info.cid == "22" and info.part == 2 and info.part_count == 2
    assert info.title == "合集 (P2 第二集)" and info.video_key == "BV1GJ411x7h7_22"


@pytest.mark.parametrize("msg", ["No video formats found!", "caused by KeyError('bvid')"])
def test_yt_dlp_vague_errors_map_to_unavailable(msg):
    assert dl.classify_download_error(msg).code == "VIDEO_UNAVAILABLE"
