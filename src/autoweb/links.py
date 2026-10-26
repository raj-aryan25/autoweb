"""Strict, shared magic-link URL rules without browser/provider dependencies."""

import ipaddress
import re
from urllib.parse import unquote, urlsplit


def link_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (not isinstance(value, str) or not host or parsed.username is not None
                or parsed.password is not None or parsed.scheme not in {"http", "https"}
                or "\\" in value or any(ord(char) <= 32 or ord(char) == 127 for char in value)
                or not host.isascii() or "%" in host):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                       for label in host.split(".")):
                return None
        if parsed.scheme == "http":
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host == "localhost"
            if not local:
                return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return f"{parsed.scheme}://{host}:{port}"
    except (ValueError, TypeError, AttributeError):
        return None


def valid_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (link_origin(value) is not None and parsed.path in {"", "/"}
                and not parsed.query and not parsed.fragment)
    except (ValueError, TypeError):
        return False


def allowed_destination(value: str, allowed_origins) -> bool:
    origin = link_origin(value)
    return origin is not None and origin in {link_origin(item) for item in allowed_origins}


def valid_magic_link(value: str, allowed_origins, path_prefix: str) -> bool:
    if not allowed_destination(value, allowed_origins):
        return False
    parsed = urlsplit(value)
    path = unquote(parsed.path)
    prefix = path_prefix.rstrip("/")
    return (not parsed.fragment and "\\" not in path
            and not any(segment in {".", ".."} for segment in path.split("/"))
            and (path == prefix or path.startswith(prefix + "/")))
