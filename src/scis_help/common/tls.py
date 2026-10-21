"""TLS with the operating system's trust store (truststore).

uv's Python on macOS ships no CA bundle, so every HTTPS client here verifies against the OS
store. Never disable verification.
"""

import ssl

import truststore

_injected = False


def ssl_context() -> ssl.SSLContext:
    """A verifying client context backed by the OS trust store, for httpx ``verify=``."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def use_system_trust_for_downloads() -> None:
    """Make libraries that build their own HTTPS clients (huggingface_hub, which fastembed uses
    to download the model) verify against the OS store. Idempotent; call it right before such a
    library may open a connection, not at import time."""
    global _injected
    if not _injected:
        truststore.inject_into_ssl()
        _injected = True
