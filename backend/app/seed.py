"""Idempotent admin-only seed. No runtime code contains character-specific defaults."""

import hashlib
import json
import os
import secrets
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.dialects.postgresql import insert

from app.config import REPO_ROOT
from app.db.repository import canonical_hash
from app.models.schemas import CharacterConfig


def seed(engine, slug, name, influencer_name, mission_name, config, tokens):
    meta = MetaData()
    meta.reflect(engine)
    tenant_id = uuid5(NAMESPACE_URL, f"mediaos:tenant:{slug}")
    influencer_id = uuid5(tenant_id, "influencer")
    mission_id = uuid5(tenant_id, "mission")
    payload = config.model_dump(mode="json")
    digest = canonical_hash(payload)
    with engine.begin() as conn:

        def put(table, **data):
            statement = insert(meta.tables[table]).values(**data)
            if table == "principals":
                statement = statement.on_conflict_do_update(
                    index_elements=["id"], set_={"token_hash": data["token_hash"]}
                )
            else:
                statement = statement.on_conflict_do_nothing()
            conn.execute(statement)

        put("tenants", id=tenant_id, slug=slug, name=name)
        for role, token in tokens.items():
            pid = uuid5(tenant_id, role)
            put(
                "principals",
                id=pid,
                name=f"{slug}-{role.lower()}",
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
            )
            put("tenant_memberships", tenant_id=tenant_id, principal_id=pid, roles=[role])
        put(
            "influencers",
            id=influencer_id,
            tenant_id=tenant_id,
            slug="primary",
            name=influencer_name,
        )
        put(
            "missions",
            id=mission_id,
            tenant_id=tenant_id,
            influencer_id=influencer_id,
            name=mission_name,
            objective=config.objective,
        )
        configs = meta.tables["character_config_versions"]
        rows = list(
            conn.execute(
                select(configs).where(
                    configs.c.tenant_id == tenant_id, configs.c.influencer_id == influencer_id
                )
            ).mappings()
        )
        latest = max(rows, key=lambda r: r["version"]) if rows else None
        if latest is None or latest["content_hash"] != digest:
            version = max([r["version"] for r in rows], default=0) + 1
            cid = uuid5(influencer_id, f"config:{version}:{digest}")
            put(
                "character_config_versions",
                id=cid,
                tenant_id=tenant_id,
                influencer_id=influencer_id,
                version=version,
                schema_version=1,
                content_hash=digest,
                payload=payload,
            )
            put(
                "influencer_versions",
                tenant_id=tenant_id,
                influencer_id=influencer_id,
                character_config_version_id=cid,
                version=version,
            )
    return {
        "tenant_id": str(tenant_id),
        "influencer_id": str(influencer_id),
        "mission_id": str(mission_id),
    }


def main():
    load_dotenv(REPO_ROOT / ".env")
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
    root = REPO_ROOT / "characters" / "sofia"
    config = CharacterConfig.model_validate_json(
        (root / "runtime.json").read_text(encoding="utf-8-sig")
    )
    data = config.model_dump()
    for field, file in {
        "persona": "persona.md",
        "voice": "voice.md",
        "visual_policy": "visual_identity.md",
        "brand_policy": "lore.md",
    }.items():
        data[field] = (root / file).read_text(encoding="utf-8")
    config = CharacterConfig.model_validate(data)
    secret_path = REPO_ROOT / ".local" / "credentials.json"
    secret_path.parent.mkdir(exist_ok=True)
    if secret_path.exists():
        tokens = json.loads(secret_path.read_text())["tokens"]
    else:
        tokens = {role: secrets.token_urlsafe(32) for role in ("OPERATOR", "APPROVER", "ADMIN")}
    ids = seed(engine, "growie", "Growie", "Sofía", "Spain SMB Growth", config, tokens)
    from app.intelligence.seed import seed_intelligence

    ingestion_path = REPO_ROOT / ".local/ingestion-credentials.json"
    ingestion_tokens = (
        json.loads(ingestion_path.read_text(encoding="utf-8")) if ingestion_path.exists() else {}
    )
    ingestion_tokens.setdefault(ids["tenant_id"], secrets.token_urlsafe(32))
    seed_intelligence(
        engine, ids["tenant_id"], ids["mission_id"], ingestion_tokens[ids["tenant_id"]]
    )
    ingestion_path.write_text(json.dumps(ingestion_tokens, indent=2), encoding="utf-8")
    if os.name != "nt":
        ingestion_path.chmod(0o600)
    secret_path.write_text(json.dumps({**ids, "tokens": tokens}, indent=2), encoding="utf-8")
    if os.name != "nt":
        secret_path.chmod(0o600)
    print("Seed complete. Local credentials: .local/credentials.json (never commit this file).")


if __name__ == "__main__":
    main()
