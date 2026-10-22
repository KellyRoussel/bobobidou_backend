from urllib.parse import urlparse, quote, urlunparse


def sanitize_db_url(url: str) -> str:
    """URL-encode the password in a database URL to handle special characters."""
    parsed = urlparse(url)
    if parsed.password:
        encoded_password = quote(parsed.password, safe="")
        netloc = f"{parsed.username}:{encoded_password}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return url
