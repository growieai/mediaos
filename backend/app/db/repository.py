import hashlib
import json
from contextlib import contextmanager
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy.engine import Connection

from app.config import get_settings


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        ).encode()
    ).hexdigest()


@lru_cache
def engine():
    return create_engine(
        get_settings().database_url.get_secret_value(),
        hide_parameters=True,
        pool_size=8,
        max_overflow=4,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 15},
    )


@lru_cache
def metadata():
    meta = MetaData()
    meta.reflect(
        engine(),
        only=[
            "tenants",
            "tenant_memberships",
            "influencers",
            "influencer_versions",
            "missions",
            "character_config_versions",
            "workflow_runs",
            "source_snapshots",
            "research_packs",
            "research_pack_versions",
            "research_pack_sources",
            "facts",
            "research_pack_facts",
            "content_briefs",
            "brief_facts",
            "content_assets",
            "content_asset_versions",
            "content_claims",
            "skill_runs",
            "qa_reports",
            "approval_records",
            "audit_events",
            "cost_events",
            "source_definitions",
            "ingestion_runs",
            "raw_source_documents",
            "source_observations",
            "ingestion_attempts",
            "opportunities",
            "opportunity_versions",
            "source_links",
            "opportunity_facts",
            "verification_conflicts",
            "change_events",
            "duplicate_candidates",
            "audience_segments",
            "mission_editorial_policies",
            "relevance_scores",
            "editorial_decisions",
            "workflow_opportunities",
        ],
    )
    return meta


class Repository:
    def __init__(self, connection: Connection, tenant_id: UUID, token: str):
        self.connection = connection
        self.tenant_id = tenant_id
        self.principal_id = connection.execute(
            text("SELECT authenticate(:token,:tenant)"), {"token": token, "tenant": tenant_id}
        ).scalar_one()

    def table(self, name: str):
        return metadata().tables[name]

    def one(self, name: str, **filters):
        rows = self.all(name, **filters)
        if not rows:
            raise LookupError(f"{name} not found")
        return rows[0]

    def all(self, name: str, **filters):
        table = self.table(name)
        query = select(table)
        if "tenant_id" in table.c:
            query = query.where(table.c.tenant_id == self.tenant_id)
        for key, value in filters.items():
            query = query.where(table.c[key] == value)
        if "sequence" in table.c:
            query = query.order_by(table.c.sequence)
        elif "created_at" in table.c:
            query = query.order_by(table.c.created_at, table.c.id)
        return [dict(row) for row in self.connection.execute(query).mappings()]

    def insert(self, name: str, **values):
        table = self.table(name)
        return dict(
            self.connection.execute(
                table.insert().values(tenant_id=self.tenant_id, **values).returning(table)
            )
            .mappings()
            .one()
        )

    def update_skill(self, sid: UUID, **values):
        table = self.table("skill_runs")
        self.connection.execute(
            table.update()
            .where(table.c.tenant_id == self.tenant_id, table.c.id == sid)
            .values(**values)
        )

    def require(self, role: str):
        if not self.connection.execute(text("SELECT has_role(:role)"), {"role": role}).scalar_one():
            raise PermissionError(f"{role} permission required")

    def checkpoint(self, run_id: UUID, state: str, artifact_id: UUID | None = None):
        self.connection.execute(
            text("SELECT checkpoint(:rid,:state,:artifact)"),
            {"rid": run_id, "state": state, "artifact": artifact_id},
        )


@contextmanager
def transaction(tenant_id: UUID, token: str):
    with engine().begin() as connection:
        yield Repository(connection, tenant_id, token)
