import hashlib
from uuid import UUID, uuid5

from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import insert

from app.intelligence.schemas import Audience, EditorialPolicy


def seed_intelligence(engine, tenant_id, mission_id, ingestor_token):
    tenant_id, mission_id = UUID(str(tenant_id)), UUID(str(mission_id))
    meta = MetaData()
    meta.reflect(engine)
    with engine.begin() as conn:

        def put(name, **values):
            conn.execute(insert(meta.tables[name]).values(**values).on_conflict_do_nothing())

        pid = uuid5(tenant_id, "intelligence-ingestor")
        principals = meta.tables["principals"]
        conn.execute(
            insert(principals)
            .values(
                id=pid,
                name="official-source-ingestor",
                token_hash=hashlib.sha256(ingestor_token.encode()).hexdigest(),
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"token_hash": hashlib.sha256(ingestor_token.encode()).hexdigest()},
            )
        )
        put("tenant_memberships", tenant_id=tenant_id, principal_id=pid, roles=["INGESTOR"])
        for key, parser, enabled, access, country in [
            ("BDNS", "bdns-1", True, "PUBLIC_API", "ES"),
            ("BOE", "boe-1", True, "PUBLIC_API", "ES"),
            ("CAMARA", "camara-1", False, "PERMISSION_REQUIRED", "ES"),
        ]:
            put(
                "source_definitions",
                id=uuid5(tenant_id, "source:" + key),
                tenant_id=tenant_id,
                source_key=key,
                country=country,
                source_type="OFFICIAL",
                connector=key,
                authority_level="PRIMARY",
                trust_level="OFFICIAL",
                enabled=enabled,
                polling_policy={
                    "minimum_interval_seconds": 1.5,
                    "cache_seconds": 900,
                    "max_attempts": 3,
                },
                connector_configuration={
                    "schedule_enabled": False,
                    "allowed_hosts": {
                        "BDNS": ["www.infosubvenciones.es"],
                        "BOE": ["www.boe.es", "boe.es"],
                        "CAMARA": ["sede.camara.es"],
                    }[key],
                    "normalization_priority": {"BDNS": 0, "BOE": 1, "CAMARA": 2}[key],
                },
                parser_version=parser,
                access_policy=access,
            )
        for code, name, prefixes, keywords in [
            ("GENERIC_SMB", "Spanish small businesses", [], ["pyme", "empresa", "autónom"]),
            ("SALON_BEAUTY", "Salons and beauty businesses", ["96"], ["peluquer", "belleza"]),
            ("DENTAL_CLINIC", "Dental clinics", ["86"], ["dental", "odontol"]),
            ("PREMIUM_RETAIL", "Specialist retail", ["47"], ["comercio", "minorista"]),
            ("RESTAURANT", "Restaurants", ["56"], ["restaur", "hosteler"]),
            ("GYM", "Gyms", ["93"], ["gimnas", "deporte"]),
            ("LOCAL_SERVICES", "Local services", ["95", "96"], ["servicio"]),
        ]:
            audience = Audience(
                code=code, name=name, country="ES", nace_prefixes=prefixes, keywords=keywords
            )
            put(
                "audience_segments",
                id=uuid5(tenant_id, "audience:" + code),
                tenant_id=tenant_id,
                code=code,
                schema_version=1,
                payload=audience.model_dump(mode="json"),
            )
        put(
            "mission_editorial_policies",
            id=uuid5(mission_id, "editorial-policy:1"),
            tenant_id=tenant_id,
            mission_id=mission_id,
            version=1,
            schema_version=1,
            payload=EditorialPolicy().model_dump(mode="json"),
        )
