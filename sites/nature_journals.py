"""Nature Portfolio journal alias and filter helpers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NatureJournal:
    key: str
    name: str
    aliases: tuple[str, ...] = ()
    family: str = ""


NATURE_JOURNALS: dict[str, NatureJournal] = {
    "ncomms": NatureJournal(
        key="ncomms",
        name="Nature Communications",
        aliases=("nc", "nature communications"),
        family="nature",
    ),
    "npjcompumats": NatureJournal(
        key="npjcompumats",
        name="npj Computational Materials",
        aliases=("npj computational materials",),
        family="npj",
    ),
    "npj2dmaterials": NatureJournal(
        key="npj2dmaterials",
        name="npj 2D Materials and Applications",
        aliases=("npj 2d materials", "npj 2d materials and applications"),
        family="npj",
    ),
    "npjquantmats": NatureJournal(
        key="npjquantmats",
        name="npj Quantum Materials",
        aliases=("npj quantum materials",),
        family="npj",
    ),
    "npjcleanwater": NatureJournal(
        key="npjcleanwater",
        name="npj Clean Water",
        aliases=("npj clean water",),
        family="npj",
    ),
    "npjdigmed": NatureJournal(
        key="npjdigmed",
        name="npj Digital Medicine",
        aliases=("npj digital medicine",),
        family="npj",
    ),
}


def normalize_journal_token(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("_", " ").split())


def resolve_journals(values: list[str] | tuple[str, ...] | None, family: str = "") -> list[NatureJournal]:
    requested = [normalize_journal_token(value) for value in (values or []) if normalize_journal_token(value)]
    family_key = normalize_journal_token(family)
    resolved: list[NatureJournal] = []

    if family_key:
        for journal in NATURE_JOURNALS.values():
            if normalize_journal_token(journal.family) == family_key:
                resolved.append(journal)

    for token in requested:
        for journal in NATURE_JOURNALS.values():
            aliases = {normalize_journal_token(journal.key), normalize_journal_token(journal.name)}
            aliases.update(normalize_journal_token(alias) for alias in journal.aliases)
            if token in aliases and journal not in resolved:
                resolved.append(journal)
                break

    return resolved
