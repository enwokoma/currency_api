"""Read credentials from request headers only.

Query strings end up in access logs, proxy logs and browser history, so API
keys, session tokens and the admin token are accepted in headers only. A
request that still sends one in the query string is refused with the header
to use, rather than treated as anonymous: a logout that quietly ignored
``?session_token=`` would leave the session alive.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request


def header_credential(
    request: Request,
    header: str,
    query_param: str,
    *,
    status_code: int = 401,
) -> Optional[str]:
    """Return the credential sent in ``header``; refuse it in the query string."""
    value = request.headers.get(header)
    if value:
        return value
    if query_param in request.query_params:
        raise HTTPException(
            status_code=status_code,
            detail=f"Send this credential in the {header} header; "
            f"the '{query_param}' query parameter is not accepted.",
        )
    return None
