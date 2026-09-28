import io
import json
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from sqlalchemy import text

from app.config import get_settings
from app.db.repository import Repository, transaction
from app.models.schemas import CharacterConfig
from app.rendering.schemas import VisualConfig
from app.rendering.service import catalog_path, checked_bytes
from app.services.workflows import ConflictError
from app.studio.schemas import (
    CategoryCatalog,
    ContentCategory,
    CreateInfluencer,
    InfluencerCatalog,
    StudioCapabilities,
    StudioCounts,
    StudioInfluencer,
    StudioOverview,
    StudioWorkflow,
)


def categories(repo: Repository) -> CategoryCatalog:
    rows = repo.connection.execute(
        text(
            "SELECT id,name,description,accent FROM studio_categories WHERE enabled ORDER BY position,id"
        )
    ).mappings()
    return CategoryCatalog(categories=[ContentCategory.model_validate(dict(row)) for row in rows])


def influencer(repo: Repository, influencer_id: UUID) -> StudioInfluencer:
    item = repo.one("influencers", id=influencer_id)
    versions = repo.all("influencer_versions", influencer_id=influencer_id)
    missions = repo.all("missions", influencer_id=influencer_id)
    if not versions or not missions:
        raise ConflictError(
            "Influencer configuration is incomplete; finish its version and mission setup"
        )
    version = max(versions, key=lambda row: row["version"])
    config = repo.one("character_config_versions", id=version["character_config_version_id"])
    character = CharacterConfig.model_validate(config["payload"])
    registrations = repo.all("studio_creations", influencer_id=influencer_id)
    registration = registrations[0] if registrations else None
    mission = repo.one("missions", id=registration["mission_id"]) if registration else missions[0]
    visuals = repo.all("visual_config_versions", influencer_id=influencer_id)
    visual = max(visuals, key=lambda row: row["version"]) if visuals else None
    if visual:
        VisualConfig.model_validate(visual["payload"])
    return StudioInfluencer(
        id=item["id"],
        name=item["name"],
        category_id=registration["category_id"] if registration else None,
        language=character.language,
        tone=character.tone,
        audience=character.audience,
        objective=mission["objective"],
        mission_id=mission["id"],
        influencer_version_id=version["id"],
        character_config_version_id=config["id"],
        visual_config_version_id=visual["id"] if visual else None,
        opportunity_discovery=bool(
            repo.all("mission_editorial_policies", mission_id=mission["id"])
        ),
        portrait_available=bool(visual and visual["reference_path"]),
        created_at=item["created_at"],
    )


def portrait(repo: Repository, influencer_id: UUID) -> bytes:
    repo.one("influencers", id=influencer_id)
    visuals = repo.all("visual_config_versions", influencer_id=influencer_id)
    visual = max(visuals, key=lambda row: row["version"]) if visuals else None
    if not visual or not visual["reference_path"]:
        raise LookupError("No approved character reference is configured")
    data = checked_bytes(
        catalog_path(visual["reference_path"]), visual["reference_sha256"], 10_000_000
    )
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 20_000_000:
                raise ConflictError("Configured portrait exceeds the preview limit")
            image.thumbnail((768, 768))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="PNG")
            return output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ConflictError("Configured portrait could not be validated") from None


def influencers(repo: Repository) -> InfluencerCatalog:
    return InfluencerCatalog(
        creation_enabled=get_settings().enable_external_creators,
        influencers=[influencer(repo, iid) for iid in configured_influencer_ids(repo)],
    )


def configured_influencer_ids(repo: Repository) -> list[UUID]:
    # Maintenance may stage an identity before its first immutable configuration.
    # Such a row is not yet a usable creator and must not break the whole catalog.
    return list(
        repo.connection.execute(
            text(
                "SELECT i.id FROM influencers i WHERE i.tenant_id=:tenant "
                "AND EXISTS(SELECT 1 FROM influencer_versions v "
                "WHERE v.tenant_id=i.tenant_id AND v.influencer_id=i.id) "
                "AND EXISTS(SELECT 1 FROM missions m "
                "WHERE m.tenant_id=i.tenant_id AND m.influencer_id=i.id) "
                "ORDER BY i.created_at,i.id"
            ),
            {"tenant": repo.tenant_id},
        ).scalars()
    )


def overview(repo: Repository) -> StudioOverview:
    settings = get_settings()
    tenant = repo.one("tenants", id=repo.tenant_id)
    counts = (
        repo.connection.execute(
            text(
                "SELECT count(*) AS workflow_runs, "
                "count(*) FILTER (WHERE state='AWAITING_APPROVAL') AS awaiting_approval "
                "FROM workflow_runs WHERE tenant_id=:tenant"
            ),
            {"tenant": repo.tenant_id},
        )
        .mappings()
        .one()
    )
    workflows = repo.connection.execute(
        text(
            "SELECT w.id,w.influencer_id,w.mission_id,w.state,"
            "coalesce(s.title,'Untitled source') AS title,w.created_at,w.updated_at,"
            "w.asset_version_id,w.research_version_id,w.qa_report_id,w.source_snapshot_id "
            "FROM workflow_runs w LEFT JOIN source_snapshots s "
            "ON s.tenant_id=w.tenant_id AND s.id=w.source_snapshot_id "
            "WHERE w.tenant_id=:tenant ORDER BY w.created_at DESC,w.id DESC LIMIT 50"
        ),
        {"tenant": repo.tenant_id},
    ).mappings()
    return StudioOverview(
        tenant_id=repo.tenant_id,
        tenant_name=tenant["name"],
        counts=StudioCounts(influencers=len(configured_influencer_ids(repo)), **dict(counts)),
        capabilities=StudioCapabilities(
            creator_creation_enabled=settings.enable_external_creators,
            social_connect_enabled=settings.social_connect_enabled,
            social_publish_enabled=settings.social_publish_enabled,
            ai_mode="mock" if settings.ai_mock_mode else "configured",
        ),
        workflows=[StudioWorkflow.model_validate(dict(row)) for row in workflows],
    )


def create_influencer(
    tenant: UUID, token: str, request: CreateInfluencer, correlation_id: UUID
) -> StudioInfluencer:
    if not get_settings().enable_external_creators:
        raise ConflictError("Creator studio creation is disabled by the server")
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        result = repo.connection.execute(
            text("SELECT studio_create_influencer(:key,CAST(:payload AS jsonb),:correlation)"),
            {
                "key": request.idempotency_key,
                "payload": json.dumps(request.model_dump(mode="json", exclude={"idempotency_key"})),
                "correlation": correlation_id,
            },
        ).scalar_one()
        # Validate both stored configurations before committing the atomic creation.
        return influencer(repo, result)
