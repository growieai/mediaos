"""Admin-only immutable target configuration; never connects a social account."""

from uuid import UUID, uuid5

from sqlalchemy import MetaData, select, text
from sqlalchemy.dialects.postgresql import insert

from app.db.repository import canonical_hash
from app.delivery.schemas import DeliveryTargetConfig


def seed_delivery_target(engine, tenant_id, target_key, display_name, platform, enabled=True):
    tenant_id = UUID(str(tenant_id))
    payload = DeliveryTargetConfig().model_dump(mode="json")
    material = {
        "display_name": display_name,
        "platform": platform,
        "enabled": enabled,
        "mode": "DRY_RUN",
        "adapter_key": "dry-run-v1",
        "payload": payload,
    }
    digest = canonical_hash(material)
    metadata = MetaData()
    metadata.reflect(engine, only=["delivery_targets"])
    table = metadata.tables["delivery_targets"]
    with engine.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"delivery-target:{tenant_id}:{target_key}"},
        )
        versions = list(
            conn.execute(
                select(table).where(
                    table.c.tenant_id == tenant_id, table.c.target_key == target_key
                )
            ).mappings()
        )
        latest = max(versions, key=lambda row: row["version"]) if versions else None
        if latest and all(latest[key] == value for key, value in material.items()):
            return latest["id"]
        version = max((row["version"] for row in versions), default=0) + 1
        target_id = uuid5(tenant_id, f"delivery-target:{target_key}:{version}:{digest}")
        conn.execute(
            insert(table).values(
                id=target_id,
                tenant_id=tenant_id,
                target_key=target_key,
                version=version,
                schema_version=1,
                content_hash=canonical_hash(payload),
                **material,
            )
        )
    return target_id


def seed_delivery_targets(engine, tenant_id, *, enabled=True):
    return seed_delivery_target(
        engine,
        tenant_id,
        "instagram-rehearsal",
        "Instagram · internal rehearsal only",
        "INSTAGRAM",
        enabled,
    )
