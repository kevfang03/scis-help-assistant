"""The Anthropic API key, kept only in the system keychain (keyring service ``scis-help``).

The key never goes into config, logs, output or exception messages.
"""

import keyring

KEYRING_SERVICE = "scis-help"
KEYRING_API_KEY_USER = "anthropic-api-key"
API_KEY_PREFIX = "sk-ant-"


def get_api_key() -> str | None:
    """The stored key, or None when nothing is stored. keyring errors propagate."""
    return keyring.get_password(KEYRING_SERVICE, KEYRING_API_KEY_USER) or None


def set_api_key(key: str) -> None:
    """Check the prefix, then store the key. The error message never quotes the key."""
    key = key.strip()
    if not key.startswith(API_KEY_PREFIX):
        raise ValueError(f"not an Anthropic API key (it should start with {API_KEY_PREFIX})")
    keyring.set_password(KEYRING_SERVICE, KEYRING_API_KEY_USER, key)
