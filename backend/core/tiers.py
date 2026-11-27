TIER_FLAGS = ("tier1_queue", "tier2_waf", "tier3_lottery")


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
