"""Admin-only, content-addressed visual configuration revisions."""

import hashlib
from uuid import UUID, uuid5

from sqlalchemy import MetaData, select
from sqlalchemy.dialects.postgresql import insert

from app.config import REPO_ROOT
from app.db.repository import canonical_hash
from app.rendering.schemas import VisualConfig


def seed_visual_config(
    engine,
    tenant_id,
    influencer_id,
    display_name,
    disclosure,
    reference_path=None,
    reference_metadata=None,
):
    tenant_id, influencer_id = UUID(str(tenant_id)), UUID(str(influencer_id))
    config = VisualConfig(
        display_name=display_name,
        required_disclosure=disclosure,
        regular_font_path="backend/assets/fonts/Inter-Regular.ttf",
        bold_font_path="backend/assets/fonts/Inter-Bold.ttf",
    )
    payload = config.model_dump(mode="json")
    font_hashes = {
        style: hashlib.sha256((REPO_ROOT / payload[f"{style}_font_path"]).read_bytes()).hexdigest()
        for style in ("regular", "bold")
    }
    reference_hash = (
        hashlib.sha256((REPO_ROOT / reference_path).read_bytes()).hexdigest()
        if reference_path
        else None
    )
    material = {
        "payload": payload,
        "font_hashes": font_hashes,
        "reference_path": reference_path,
        "reference_sha256": reference_hash,
        "reference_metadata": reference_metadata or {},
    }
    digest = canonical_hash(material)
    meta = MetaData()
    meta.reflect(engine, only=["visual_config_versions"])
    table = meta.tables["visual_config_versions"]
    with engine.begin() as conn:
        # Parent influencer row serializes concurrent seed/version allocation.
        influencers = meta.tables["influencers"]
        conn.execute(
            select(influencers)
            .where(influencers.c.tenant_id == tenant_id, influencers.c.id == influencer_id)
            .with_for_update()
        )
        versions = list(
            conn.execute(
                select(table).where(
                    table.c.tenant_id == tenant_id, table.c.influencer_id == influencer_id
                )
            ).mappings()
        )
        latest = max(versions, key=lambda row: row["version"]) if versions else None
        if latest and all(latest[key] == value for key, value in material.items()):
            return latest["id"]
        version = max([row["version"] for row in versions], default=0) + 1
        config_id = uuid5(influencer_id, f"visual:{version}:{digest}")
        conn.execute(
            insert(table).values(
                id=config_id,
                tenant_id=tenant_id,
                influencer_id=influencer_id,
                version=version,
                schema_version=1,
                content_hash=canonical_hash(payload),
                **material,
            )
        )
    return config_id
