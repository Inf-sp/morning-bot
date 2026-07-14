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
)
from repositories import UserListRepository


class DictionaryRepository:
    def __init__(self, cid):
        self.cid = str(cid)
        self.records = UserListRepository(config.DICT_KEY, self.cid)

    def all(self):
        return self.records.all()

    def save_all(self, entries):
        self.records.save(entries)

    def training_entries(self, language):
        code = language_code(language)
        return [entry for entry in self.all()
                if entry_language(entry) == code and entry_term(entry) and entry_translation(entry)]

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

    def record_answer(self, language, term, exercise_type, quality):
        def update(entries):
            for index, entry in enumerate(entries):
                if entry_language(entry) != language or entry_term(entry) != term:
                    continue
                state = ({key: entry.get(key) for key in (
                    "srs_level", "srs_easiness", "srs_interval_days", "srs_due_at",
                    "srs_history", "srs_last_exercise_type")}
                    if "srs_due_at" in entry else srs.default_srs_state())
                updated = {**entry, **srs.record_answer(state, exercise_type, quality)}
                entries[index] = updated
                return entries, updated
            return entries, None

        return self.records.mutate(update)
