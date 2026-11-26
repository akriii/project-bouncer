TIER_FLAGS = ("tier1_queue", "tier2_waf", "tier3_lottery")


def decode_hash(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    decoded = {}
    for key, value in raw.items():
        field = key.decode("utf-8") if isinstance(key, bytes) else key
        decoded[field] = value.decode("utf-8") if isinstance(value, bytes) else value
    return decoded


def resolve_tiers(client_id: str, client_config: dict) -> list[str]:
    """Return the tier flags this client actually subscribed to.

    Flags are independent. An empty default_client config keeps the local
    waiting-room demo on Tier 1. Any explicit flag, including a false one,
    disables that fallback. A registered client with no true flags has no access.
    """
    config = client_config or {}
    selected = [name for name in TIER_FLAGS if config.get(name) == "true"]
    explicit = any(name in config for name in TIER_FLAGS)
    if not selected and client_id == "default_client" and not explicit:
        return ["tier1_queue"]
    return selected
