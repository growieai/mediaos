import hashlib
from uuid import UUID, uuid5

from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import insert


def seed_social(engine, tenant_id, token):
    tenant_id = UUID(str(tenant_id))
    principal = uuid5(tenant_id, "social-connector")
    meta = MetaData()
    meta.reflect(engine, only=["principals", "tenant_memberships"])
    digest = hashlib.sha256(token.encode()).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            insert(meta.tables["principals"])
            .values(
                id=principal,
                name="social-connector",
                token_hash=digest,
            )
            .on_conflict_do_update(index_elements=["id"], set_={"token_hash": digest})
        )
        connection.execute(
            insert(meta.tables["tenant_memberships"])
            .values(
                tenant_id=tenant_id,
                principal_id=principal,
                roles=["SOCIAL"],
            )
            .on_conflict_do_nothing()
        )
