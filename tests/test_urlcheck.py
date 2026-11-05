import pytest

from app.urlcheck import UnsafeURL, validate_url

TABLE = {
    "example.com": ["93.184.216.34"],
    "www.example.com": ["93.184.216.34"],
    "evil.example": ["10.0.0.5"],
    "dual.example": ["93.184.216.34", "192.168.1.1"],
    "v6private.example": ["fd00::1"],
    "meta.example": ["169.254.169.254"],
    "mapped.example": ["::ffff:127.0.0.1"],
}


async def fake_resolver(host: str) -> list[str]:
    if host not in TABLE:
        raise OSError("no such host")
    return TABLE[host]


async def test_public_url_passes_and_is_normalised():
    assert await validate_url("Example.com/path", fake_resolver) == "https://example.com/path"
    assert await validate_url("http://www.example.com", fake_resolver) == "http://www.example.com/"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://172.16.0.9/",
        "http://192.168.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://0.0.0.0/",
        "http://evil.example/",
        "http://dual.example/",
        "http://v6private.example/",
        "http://meta.example/",
        "http://mapped.example/",
        "http://user:pw@example.com/",
        "http://nxdomain.example/",
        "",
    ],
)
async def test_blocked_urls(url):
    with pytest.raises(UnsafeURL):
        await validate_url(url, fake_resolver)
