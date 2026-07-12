"""Реестр Skills-контрактов бота (ECC: skills как primary workflow surface).

Лёгкий декларативный слой: НЕ переписывает диспетчер. Описывает для каждой фичи
явный контракт - вход (entrypoints), поверхность (surface -> набор грейдеров в
verify.py), память (ключи store/config) и fallback при сбое. Используется как
документация контракта и для аудита callback'ов.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    name: str
    title: str
    surface: str                       # chat | health | card | weather (см. verify.SURFACES)
    entrypoints: tuple = ()            # callback_data / pending-ключи, запускающие скилл
    memory: tuple = ()                 # ключи store/config, которые скилл читает/пишет
    fallback: str = "⚠️ Не получилось. Попробуй ещё раз через минуту."


SKILLS = {s.name: s for s in (
    Skill("morning_brief", "Мой день", "weather",
          entrypoints=("плановый запуск", "☀️ Мой день"),
          memory=("SETTINGS_FILE", "WARDROBE_FILE", "DICT_KEY", "LAGOM_KEY", "PROFILE_KEY"),
          fallback="⚠️ Не удалось собрать сводку дня. Попробуй ещё раз."),
    Skill("adhd_unstuck", "Личная мотивация / состояние", "card",
          entrypoints=("as_motiv", "role_state", "chat_retry", "ans_short", "ans_deep"),
          memory=("last_action", "last_surface"),
          fallback="⚠️ Не получилось сгенерировать. Попробуй ещё раз."),
    Skill("wardrobe_feedback", "Гардероб", "card",
          entrypoints=("w_look", "w_improve", "w_check", "wardrobe_check", "w_fb_worn",
                       "w_fb_nostyle"),
          memory=("WARDROBE_FILE", "SETTINGS_FILE", "recent_looks", "last_look", "PROFILE_KEY"),
          fallback="⚠️ Не удалось разобрать гардероб. Попробуй ещё раз."),
    Skill("language_micro_lesson", "Грамматика и тренажёр слов", "card",
          entrypoints=("a_gram_nl", "a_gram_en", "a_train_nl", "a_train_en"),
          memory=("DICT_KEY", "LEVELS_FILE", "grammar_state", "train_state"),
          fallback="⚠️ Не удалось подготовить задание. Попробуй ещё раз."),
    Skill("evening_review", "Вечерний разбор тревог", "card",
          entrypoints=("плановый запуск",),
          memory=("WORRIES_KEY", "PROFILE_KEY"),
          fallback="⚠️ Не удалось собрать вечерний разбор. Попробуй ещё раз."),
    Skill("health_triage_safe", "Вопрос врачу", "health",
          entrypoints=("as_doctor", "role_doctor"),
          memory=("last_action",),
          fallback="⚠️ Не удалось подготовить разбор. При тревожных симптомах обратись к врачу."),
    Skill("travel_recommender", "Поездки", "card",
          entrypoints=("a_trav_go", "a_trav_plan"),
          memory=("FAVCOUNTRIES_KEY", "TRAVEL_DISLIKE_KEY", "NOTES_KEY", "suggested_countries"),
          fallback="⚠️ Не удалось подобрать направление. Попробуй ещё раз."),
    Skill("free_chat", "Свободный чат", "chat",
          entrypoints=("текстовое сообщение",),
          memory=("chat_history", "last_answer"),
          fallback="⚠️ Ассистент сейчас недоступен. Попробуй ещё раз через минуту."),
)}


def get(name):
    return SKILLS.get(name)
