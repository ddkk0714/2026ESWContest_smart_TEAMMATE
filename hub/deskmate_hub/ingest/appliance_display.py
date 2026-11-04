"""Display-only projection of validated appliance cache; never an FSM input.

Receipt freshness does not establish sensor measurement freshness or connectivity.
The cache supplies only allowlisted observations, not raw SDK payloads.
"""

from .appliance import ApplianceCache


def display_appliances(cache: ApplianceCache) -> list[dict]:
    """JSON-safe, source-separated snapshots, preserving historical last_good."""
    return [
        {
            name: entry[name]
            for name in (
                "node",
                "logical_device_id",
                "mode",
                "api_status",
                "connection",
                "available",
                "receipt_stale",
                "published_at",
                "expires_at",
                "api_received_at",
                "hub_received_at",
                "observations",
            )
        }
        | {"freshness_basis": "api_receipt_only", "measurement_freshness": "unknown"}
        for _, entry in sorted(cache.snapshot().items())
    ]
