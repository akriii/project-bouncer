def decode_hash(raw) -> dict:
    """Decode Redis hash response from bytes to str."""
    if not isinstance(raw, dict):
        return {}
    decoded = {}
    for key, value in raw.items():
        field = key.decode("utf-8") if isinstance(key, bytes) else key
        decoded[field] = value.decode("utf-8") if isinstance(value, bytes) else value
    return decoded
