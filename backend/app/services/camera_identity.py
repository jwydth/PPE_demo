from urllib.parse import urlsplit, urlunsplit


def normalize_camera_source_key(value: str) -> str:
    """Return the stable database identity for a camera source."""
    normalized = value.strip()
    if not normalized:
        return normalized

    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        if parsed.scheme.lower() not in {"rtsp", "rtsps"} or hostname is None:
            return normalized
        if hostname.lower() not in {"localhost", "::1"}:
            return normalized

        userinfo, separator, _host_port = parsed.netloc.rpartition("@")
        port = f":{parsed.port}" if parsed.port is not None else ""
        canonical_host_port = f"127.0.0.1{port}"
        canonical_netloc = (
            f"{userinfo}{separator}{canonical_host_port}"
            if separator
            else canonical_host_port
        )
        return urlunsplit(parsed._replace(netloc=canonical_netloc))
    except ValueError:
        return normalized
