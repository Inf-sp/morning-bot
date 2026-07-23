"""Хранение словарных записей и атомарное обновление SRS."""

import config
import srs
from dictionary_model import (
    PHRASE_CORRECTIONS,
    entry_language,
    entry_term,
    entry_translation,
    language_code,
    normalize_key,
    normalize_entry,
)
from repositories import UserListRepository


class DictionaryRepository:
    def __init__(self, cid):
        self.cid = str(cid)
        self.records = UserListRepository(config.DICT_KEY, self.cid)

    def all(self):
        entries = self.records.all()
        normalized = []
        seen = set()
        for entry in entries:
            item = normalize_entry(entry)
            lang = "en" if entry_language(item) == "en" else "nl"
            item["lang"] = lang
            key = (lang, normalize_key(entry_term(item)), normalize_key(entry_translation(item)))
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        if normalized != entries:
            self.records.save(normalized)
        return normalized

    def save_all(self, entries):
        self.records.save(entries)

    def training_entries(self, language):
        code = language_code(language)
        return [entry for entry in self.all()
                if entry_language(entry) == code and entry_term(entry) and entry_translation(entry)]

    def repair_training_state(self, language):
        """Локально чинит контракт тренажёра без сети и AI-вызова."""
        code = language_code(language)

        def update(entries):
            changed = False
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict) or entry_language(entry) != code:
                    continue
                normalized = {**entry, **srs.normalize_state(entry)}
                examples = entry.get("examples")
                normalized["examples"] = (
                    [dict(example) for example in examples if isinstance(example, dict)]
                    if isinstance(examples, list) else []
                )
                for key in ("forms", "alt_translations"):
                    value = entry.get(key)
                    normalized[key] = list(value) if isinstance(value, list) else []
                if normalized != entry:
                    entries[index] = normalized
                    changed = True
            return entries, changed

        return self.records.mutate(update)

    def correction_for(self, entry):
        return PHRASE_CORRECTIONS.get(normalize_key(entry_term(entry)))

    def apply_known_corrections(self, language):
        code = language_code(language)
        entries = self.all()
        changed = False
        for index, entry in enumerate(entries):
            if entry_language(entry) != code:
                continue
            correction = self.correction_for(entry)
            if not correction:
                continue
            updated = dict(entry)
            if entry_translation(updated) != correction["translation"]:
                updated["translation"] = correction["translation"]
                changed = True
            examples = []
            for example in updated.get("examples") or []:
                example = dict(example)
                if normalize_key(example.get("text")) == normalize_key(entry_term(updated)):
                    if example.get("translation") != correction["translation"]:
                        example["translation"] = correction["translation"]
                        changed = True
                examples.append(example)
            if examples:
                updated["examples"] = examples
            entries[index] = updated
        if changed:
            self.save_all(entries)
        return changed

    def record_answer(self, language, term, exercise_type, quality, form_focus=""):
        def update(entries):
            for index, entry in enumerate(entries):
                if entry_language(entry) != language or entry_term(entry) != term:
                    continue
                state = srs.normalize_state(entry)
                updated = {**entry, **srs.record_answer(state, exercise_type, quality)}
                if exercise_type == "verb_form" and form_focus in ("past", "participle"):
                    progress = entry.get("verb_forms_progress") or {}
                    progress = dict(progress) if isinstance(progress, dict) else {}
                    try:
                        current = max(0, min(5, int(progress.get(form_focus) or 0)))
                    except (TypeError, ValueError):
                        current = 0
                    if quality == srs.NOT_REMEMBERED:
                        current = max(0, current - 1)
                    else:
                        current = min(5, current + 1)
                    progress.setdefault("infinitive", 5)
                    progress[form_focus] = current
                    updated["verb_forms_progress"] = progress
                entries[index] = updated
                return entries, updated
            return entries, None

        return self.records.mutate(update)

    def delete_training_entry(self, language, term):
        """Удаляет ровно текущую учебную запись и возвращает её при успехе."""
        def update(entries):
            kept = []
            removed = None
            for entry in entries:
                if (removed is None and entry_language(entry) == language
                        and entry_term(entry).casefold() == str(term).casefold()):
                    removed = entry
                    continue
                kept.append(entry)
            return kept, removed

        return self.records.mutate(update)
