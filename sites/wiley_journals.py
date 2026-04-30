"""Wiley Online Library journal alias and DOI-code filter helpers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WileyJournal:
    key: str
    name: str
    doi_codes: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    family: str = ""


WILEY_JOURNALS: dict[str, WileyJournal] = {
    "am": WileyJournal(
        key="am",
        name="Advanced Materials",
        doi_codes=("adma",),
        aliases=("advanced materials", "adv mater", "advanced material"),
        family="advanced",
    ),
    "afm": WileyJournal(
        key="afm",
        name="Advanced Functional Materials",
        doi_codes=("adfm",),
        aliases=("advanced functional materials", "adv funct mater"),
        family="advanced",
    ),
    "aem": WileyJournal(
        key="aem",
        name="Advanced Energy Materials",
        doi_codes=("aenm",),
        aliases=("advanced energy materials", "adv energy mater"),
        family="advanced",
    ),
    "small": WileyJournal(
        key="small",
        name="Small",
        doi_codes=("smll",),
        aliases=("small",),
        family="advanced",
    ),
}


def normalize_journal_token(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("_", " ").split())


def resolve_journals(values: list[str] | tuple[str, ...] | None, family: str = "") -> list[WileyJournal]:
    requested = [normalize_journal_token(value) for value in (values or []) if normalize_journal_token(value)]
    family_key = normalize_journal_token(family)
    resolved: list[WileyJournal] = []

    if family_key:
        for journal in WILEY_JOURNALS.values():
            if normalize_journal_token(journal.family) == family_key:
                resolved.append(journal)

    for token in requested:
        matched = False
        for journal in WILEY_JOURNALS.values():
            aliases = {normalize_journal_token(journal.key), normalize_journal_token(journal.name)}
            aliases.update(normalize_journal_token(alias) for alias in journal.aliases)
            aliases.update(normalize_journal_token(code) for code in journal.doi_codes)
            if token in aliases and journal not in resolved:
                resolved.append(journal)
                matched = True
                break
        if not matched:
            raise ValueError(f"未知 Wiley 期刊: {token}")

    return resolved
