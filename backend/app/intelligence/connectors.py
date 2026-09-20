import json
import re
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlencode, urljoin
from xml.etree import ElementTree

from app.intelligence.http import FetchResult, SourceNotFound, SourcePolicyError
from app.intelligence.schemas import (
    DiscoveryPage,
    DiscoveryRequest,
    DocumentRef,
    ExtractedField,
    GrantProfile,
    NormalizedOpportunity,
)


class SourceConnector(Protocol):
    version: str
    hosts: tuple[str, ...]

    def discover(self, http, request: DiscoveryRequest, cursor: str) -> DiscoveryPage: ...

    def normalize(self, response: FetchResult, reference: DocumentRef) -> NormalizedOpportunity: ...


def iso(value):
    return date.fromisoformat(value) if value else None


def monetary_value(value):
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError("Official budget must be numeric")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Official budget must be finite and nonnegative")
    return str(value)


class EvidenceBuilder:
    def __init__(self):
        self.text = ""
        self.facts: list[ExtractedField] = []

    def add(self, field, value, locator, label="", unit=None):
        if value is None or value == [] or value == "":
            return
        display = "; ".join(value) if isinstance(value, list) else str(value)
        statement = f"{label}{display}"
        start = len(self.text)
        self.text += statement + "\n"
        if len(statement) <= 2000:
            self.facts.append(
                ExtractedField(
                    field=field,
                    value=value if isinstance(value, list) else str(value),
                    statement=statement,
                    start=start,
                    end=start + len(statement),
                    locator=locator,
                    unit=unit,
                )
            )


class BDNSConnector:
    version = "bdns-1"
    hosts: tuple[str, ...] = ("www.infosubvenciones.es",)
    base = "https://www.infosubvenciones.es/bdnstrans/api"

    def discover(self, http, request, cursor):
        page = int(cursor or "0")
        params = {
            "page": page,
            "pageSize": request.page_size,
            "order": "fechaRecepcion",
            "direccion": "desc",
            "fechaDesde": request.since.strftime("%d/%m/%Y"),
            "fechaHasta": request.until.strftime("%d/%m/%Y"),
            "descripcion": request.query,
        }
        data = json.loads(http.get(self.base + "/convocatorias/busqueda?" + urlencode(params)).body)
        if not isinstance(data.get("content"), list) or type(data.get("last")) is not bool:
            raise ValueError("Unexpected BDNS page contract")
        refs = []
        for item in data["content"]:
            code = str(item["numeroConvocatoria"])
            if not re.fullmatch(r"[0-9]{1,20}", code):
                raise ValueError("Invalid BDNS code")
            refs.append(
                DocumentRef(external_id=code, url=self.base + "/convocatorias?numConv=" + code)
            )
        return DiscoveryPage(
            documents=refs, next_cursor=str(page + 1) if not data["last"] and refs else None
        )

    def normalize(self, response, reference):
        data = json.loads(response.body)
        code = str(data["codigoBDNS"])
        if code != reference.external_id:
            raise ValueError("BDNS detail does not match discovery")

        def descriptions(key):
            return [v["descripcion"].strip() for v in data.get(key, [])]

        applicants = descriptions("tiposBeneficiarios")
        geography = descriptions("regiones")
        profile = GrantProfile(
            applicant_types=applicants,
            geography=geography,
            nace=[str(v["codigo"]) for v in data.get("sectores", []) if v.get("codigo")],
            industries=descriptions("sectores"),
            sme="YES"
            if any(
                a.upper()
                in {"PYME", "PYMES", "PYME Y PERSONAS FÍSICAS QUE DESARROLLAN ACTIVIDAD ECONÓMICA"}
                for a in applicants
            )
            else "UNKNOWN",
            # The broad BDNS category does not establish all self-employed or microenterprise conditions.
            application_start=iso(data.get("fechaInicioSolicitud")),
            application_deadline=iso(data.get("fechaFinSolicitud")),
            available_budget=monetary_value(data.get("presupuestoTotal")),
            application_mechanism=data.get("sedeElectronica") or None,
            de_minimis="YES"
            if re.search(
                r"^REG\s*\(UE\)\s*\d{4}/\d+\s+de minimis\b",
                (data.get("reglamento") or {}).get("descripcion", ""),
                re.I,
            )
            else "UNKNOWN",
        )
        evidence = EvidenceBuilder()
        evidence.add("title", data["descripcion"], "/descripcion")
        evidence.add(
            "applicant_types",
            applicants,
            "/tiposBeneficiarios",
            "Tipos de beneficiarios registrados: ",
        )
        evidence.add("geography", geography, "/regiones", "Regiones de impacto registradas: ")
        evidence.add(
            "opening_date",
            data.get("fechaInicioSolicitud"),
            "/fechaInicioSolicitud",
            "Inicio de solicitud registrado: ",
        )
        evidence.add(
            "closing_date",
            data.get("fechaFinSolicitud"),
            "/fechaFinSolicitud",
            "Fin de solicitud registrado: ",
        )
        evidence.add(
            "available_budget",
            profile.available_budget,
            "/presupuestoTotal",
            "Presupuesto total de la convocatoria (no importe por solicitante): ",
        )
        evidence.add("industries", profile.industries, "/sectores", "Sectores registrados: ")
        evidence.add("nace", profile.nace, "/sectores/*/codigo", "Códigos NACE registrados: ")
        evidence.add(
            "de_minimis_regulation",
            (data.get("reglamento") or {}).get("descripcion"),
            "/reglamento/descripcion",
            "Reglamento registrado: ",
        )
        evidence.add(
            "opening_rule", data.get("textInicio"), "/textInicio", "Regla de apertura publicada: "
        )
        evidence.add("closing_rule", data.get("textFin"), "/textFin", "Regla de cierre publicada: ")
        links = [v for v in [data.get("urlBasesReguladoras"), data.get("sedeElectronica")] if v]
        for item in data.get("anuncios", []):
            links.extend(
                v for k, v in item.items() if isinstance(v, str) and v.startswith("https://")
            )
        return NormalizedOpportunity(
            country="ES",
            canonical_external_id=f"ES:BDNS:{code}",
            external_ids={"BDNS": code},
            title=data["descripcion"],
            issuing_body=data.get("organo", {}).get("nivel3")
            or data.get("organo", {}).get("nivel2")
            or "BDNS",
            application_url=data.get("sedeElectronica") or None,
            publication_date=iso(data.get("fechaRecepcion")),
            profile=profile,
            linked_material=links,
            source_text=evidence.text,
            fields=evidence.facts,
            uncertainty=[
                "La categoría general de BDNS no confirma la elegibilidad individual; consultar las bases."
            ]
            + (
                ["Opening date is unknown; relative rule is preserved without calculation."]
                if not profile.application_start
                else []
            )
            + (["Application deadline is unknown."] if not profile.application_deadline else []),
        )


def walk_items(value):
    if isinstance(value, dict):
        if "identificador" in value and "titulo" in value:
            yield value
        for nested in value.values():
            yield from walk_items(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_items(nested)


def bdns_reference(text):
    codes = set(re.findall(r"BDNS\s*(?:\(\s*Identif\.?\s*\))?\s*[:：]?\s*(\d{5,20})", text, re.I))
    codes.update(re.findall(r"bdnstrans/(?:GE/es/)?convocatoria/(\d{5,20})", text))
    return next(iter(codes)) if len(codes) == 1 else None


class BOEConnector:
    version = "boe-1"
    hosts: tuple[str, ...] = ("www.boe.es", "boe.es")

    def discover(self, http, request, cursor):
        parts = cursor.split(":") if cursor else []
        day = date.fromisoformat(parts[0]) if parts else request.since
        offset = int(parts[1]) if len(parts) == 2 else 0
        url = "https://www.boe.es/datosabiertos/api/boe/sumario/" + day.strftime("%Y%m%d")
        try:
            data = json.loads(http.get(url).body)
        except SourceNotFound:
            next_day = day + timedelta(days=1)
            return DiscoveryPage(
                documents=[],
                next_cursor=next_day.isoformat() if next_day <= request.until else None,
            )
        refs = []
        for item in walk_items(data):
            if not any(word in item["titulo"].lower() for word in ("subvenci", "ayudas", "pyme")):
                continue
            if not item.get("url_xml"):
                continue
            metadata = {
                "publication_date": day.isoformat(),
                "html": item.get("url_html", ""),
                "pdf": item.get("url_pdf", {}).get("texto", ""),
            }
            refs.append(
                DocumentRef(
                    external_id=item["identificador"], url=item["url_xml"], metadata=metadata
                )
            )
        next_day = day + timedelta(days=1)
        return DiscoveryPage(
            documents=refs[offset : offset + request.page_size],
            next_cursor=f"{day.isoformat()}:{offset + request.page_size}"
            if offset + request.page_size < len(refs)
            else (next_day.isoformat() if next_day <= request.until else None),
        )

    def normalize(self, response, reference):
        if "<!DOCTYPE" in response.body.upper() or "<!ENTITY" in response.body.upper():
            raise ValueError("DTD is not accepted")
        try:
            root = ElementTree.fromstring(response.body)
        except ElementTree.ParseError:
            raise ValueError("Official XML could not be parsed") from None
        identifier = root.findtext("./metadatos/identificador")
        if identifier != reference.external_id:
            raise ValueError("BOE identifier mismatch")
        title = root.findtext("./metadatos/titulo") or ""
        paragraphs = [" ".join("".join(p.itertext()).split()) for p in root.findall("./texto/p")]
        content = "\n".join(paragraphs)
        code = bdns_reference(content)
        evidence = EvidenceBuilder()
        evidence.add("title", title, "/documento/metadatos/titulo")
        for index, paragraph in enumerate(paragraphs):
            evidence.add(f"official_text.{index}", paragraph, f"/documento/texto/p[{index + 1}]")
        profile = GrantProfile()
        # Narrow labelled absolute dates only. Relative periods stay UNKNOWN.
        for attribute, pattern in (
            (
                "application_deadline",
                r"(?:fin de solicitud|fecha fin solicitud|fecha l[ií]mite de solicitud)\s*:\s*(\d{4}-\d{2}-\d{2})",
            ),
            (
                "application_start",
                r"(?:inicio de solicitud|fecha inicio solicitud)\s*:\s*(\d{4}-\d{2}-\d{2})",
            ),
        ):
            values = set(re.findall(pattern, content, re.I))
            if len(values) == 1:
                setattr(profile, attribute, date.fromisoformat(next(iter(values))))
        publication = root.findtext("./metadatos/fecha_publicacion")
        return NormalizedOpportunity(
            country="ES",
            canonical_external_id=f"ES:BDNS:{code}" if code else f"ES:BOE:{identifier}",
            external_ids={"BOE": str(identifier), **({"BDNS": code} if code else {})},
            title=title,
            issuing_body=root.findtext("./metadatos/departamento") or "BOE",
            publication_date=date.fromisoformat(publication) if publication else None,
            profile=profile,
            linked_material=[v for v in reference.metadata.values() if v.startswith("https://")],
            source_text=evidence.text,
            fields=evidence.facts,
            uncertainty=[
                "Unstructured legal conditions require review; no relative dates or applicant eligibility inferred."
            ],
        )


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "h1", "h2", "h3", "li", "br"):
            self.parts.append("\n")
        if tag == "a":
            self.links.extend(v for k, v in attrs if k == "href" and v)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        return "\n".join(
            " ".join(line.split()) for line in "".join(self.parts).splitlines() if line.strip()
        )


class CamaraConnector:
    version = "camara-1"
    hosts: tuple[str, ...] = ("sede.camara.es",)

    def discover(self, http, request, cursor):
        raise SourcePolicyError(
            "Cámara prohibits automated access; permission is required before live discovery"
        )

    def parse_listing(self, response):
        page = PageText()
        page.feed(response.body)
        refs = {}
        for href in page.links:
            match = re.search(r"/tramites/(TR\d+)$", href)
            if match:
                refs[match[1]] = DocumentRef(external_id=match[1], url=urljoin(response.url, href))
        return DiscoveryPage(documents=list(refs.values()))

    def normalize(self, response, reference):
        page = PageText()
        page.feed(response.body)
        content = page.text()
        title_match = re.search(r"<h3[^>]*>(.*?)</h3>\s*<blockquote", response.body, re.S | re.I)
        heading = PageText()
        heading.feed(title_match[1] if title_match else "")
        title = heading.text()
        if not title:
            raise ValueError("Cámara programme heading is missing")
        evidence = EvidenceBuilder()
        evidence.add("title", title, "h3 preceding blockquote.lead")
        profile = GrantProfile()
        issuer_match = re.search(
            r'<i class="[^"]*fa-university[^"]*"></i>\s*<span>(.*?)</span>', response.body, re.S
        )
        issuer_text = PageText()
        issuer_text.feed(issuer_match[1] if issuer_match else "")
        issuer = issuer_text.text() or "UNKNOWN"
        if issuer != "UNKNOWN":
            evidence.add("issuing_body", issuer, "i.fa-university + span")
        applicant_match = re.search(
            r'<i class="[^"]*fa-user[^\"]*"></i>\s*<span[^>]*>(.*?)</span>', response.body, re.S
        )
        applicant_text = PageText()
        applicant_text.feed(applicant_match[1] if applicant_match else "")
        if applicant_text.text():
            profile.applicant_types = [applicant_text.text()]
            evidence.add(
                "applicant_types", profile.applicant_types, "i.fa-user + span", "Dirigido a: "
            )
        period = re.search(
            r"(\d{1,2})/([a-z]{3})/(\d{4})\s+(\d{2}:\d{2})\s*-\s*(\d{1,2})/([a-z]{3})/(\d{4})\s+(\d{2}:\d{2})",
            content,
            re.I,
        )
        if period:
            months = {
                name: index + 1
                for index, name in enumerate(
                    (
                        "ene",
                        "feb",
                        "mar",
                        "abr",
                        "may",
                        "jun",
                        "jul",
                        "ago",
                        "sep",
                        "oct",
                        "nov",
                        "dic",
                    )
                )
            }
            profile.application_start = date(
                int(period[3]), months[period[2].lower()], int(period[1])
            )
            profile.application_deadline = date(
                int(period[7]), months[period[6].lower()], int(period[5])
            )
            evidence.add(
                "application_window",
                period[0],
                "Periodo de solicitudes / span",
                "Periodo de solicitudes publicado: ",
            )
        # Only explicit numeric dates with an unambiguous field label are normalized.
        for field, label, attribute in [
            ("opening_date", r"(?:Inicio|Desde)", "application_start"),
            ("closing_date", r"(?:Fin|Hasta)", "application_deadline"),
        ]:
            match = re.search(
                label + r"(?: del plazo)?\s*:?\s*(\d{2})/(\d{2})/(\d{4})", content, re.I
            )
            if match:
                value = date(int(match[3]), int(match[2]), int(match[1]))
                setattr(profile, attribute, value)
                evidence.add(field, value.isoformat(), "text:" + match[0], "Fecha publicada: ")
        code = bdns_reference(content)
        return NormalizedOpportunity(
            country="ES",
            opportunity_type="PROGRAMME",
            canonical_external_id=f"ES:BDNS:{code}"
            if code
            else f"ES:CAMARA:{reference.external_id}",
            external_ids={"CAMARA": reference.external_id, **({"BDNS": code} if code else {})},
            title=title,
            issuing_body=issuer,
            application_url=response.url,
            profile=profile,
            linked_material=[urljoin(response.url, x) for x in page.links if "/documento/" in x],
            source_text=evidence.text,
            fields=evidence.facts,
            uncertainty=[
                "Chamber, geography and full programme conditions require review of the retained original page."
            ],
        )


CONNECTORS: dict[str, SourceConnector] = {
    "BDNS": BDNSConnector(),
    "BOE": BOEConnector(),
    "CAMARA": CamaraConnector(),
}
