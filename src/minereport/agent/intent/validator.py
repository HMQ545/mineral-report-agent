"""Pydantic + business validation for slots.

Nothing that identifies a data source may come from the model: company, project,
commodity and PDF references must resolve against the repositories' registries.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ...news import entities
from ...pdf import registry as pdf_registry
from ...price import registry as price_registry
from .schemas import REQUIRED_SLOTS, Intent, SlotResolution, Slots


def _known_companies() -> dict[str, str]:
    return {entity.name.lower(): entity.name for entity in entities._load()}


def _projects_for(company: str | None) -> set[str]:
    if not company:
        return set()
    for entity in entities._load():
        if entity.name.lower() == company.lower():
            return {project.lower() for project in entity.projects}
    return set()


def _pdf_reference_ok(reference: str) -> bool:
    for source in pdf_registry.all_sources():
        if reference in {source.get("key"), source.get("url")}:
            return True
    return False


def validate_slots(intent: Intent, slots: dict[str, Any]) -> SlotResolution:
    errors: list[str] = []
    candidate = {k: v for k, v in slots.items() if k in Slots.model_fields}

    # Reject only the offending field, never the whole batch: a bad ``days`` must
    # not silently discard a perfectly good commodity.
    for _ in range(len(candidate)):
        try:
            model = Slots(**candidate)
            break
        except ValidationError as exc:
            bad = {str(err["loc"][0]) for err in exc.errors() if err.get("loc")}
            if not bad:
                return SlotResolution(
                    slots=Slots(),
                    errors=["; ".join(str(err["msg"]) for err in exc.errors())],
                    missing=list(REQUIRED_SLOTS.get(intent, ())),
                )
            for field in bad:
                errors.append(f"{field}: {next(str(err['msg']) for err in exc.errors() if err['loc'][0] == field)}")
                candidate.pop(field, None)
    else:  # pragma: no cover - defensive
        model = Slots()

    if model.commodity and price_registry.resolve(model.commodity) is None:
        errors.append(f"商品 {model.commodity!r} 不在可信行情注册表中")

    known = _known_companies()
    if model.company and model.company.lower() not in known:
        errors.append(f"公司 {model.company!r} 不在可信实体注册表中")
    elif model.company:
        model.company = known[model.company.lower()]

    if model.project and model.company:
        allowed = _projects_for(model.company)
        if allowed and model.project.lower() not in allowed:
            errors.append(f"项目 {model.project!r} 不属于 {model.company}")
    elif model.project and not model.company:
        owner = next((e.name for e in entities._load() if model.project.lower() in {p.lower() for p in e.projects}), None)
        if owner:
            model.company = owner
        else:
            errors.append(f"项目 {model.project!r} 不在可信实体注册表中")

    if model.pdf_ref and not _pdf_reference_ok(model.pdf_ref):
        errors.append(f"PDF 来源 {model.pdf_ref!r} 不在可信资源注册表中")

    missing = [
        slot
        for slot in REQUIRED_SLOTS.get(intent, ())
        if getattr(model, slot, None) in (None, "")
    ]
    return SlotResolution(slots=model, missing=missing, errors=errors)


__all__ = ["validate_slots"]