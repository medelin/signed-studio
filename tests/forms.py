"""Form posts in tests carry the CSRF token, as the browser does (studio.auth)."""


def csrf(client) -> str:
    """The double-submit token: any GET sets the cookie; forms echo it."""
    if not client.cookies.get("studio_csrf"):
        client.get("/healthz")
    return client.cookies.get("studio_csrf")
