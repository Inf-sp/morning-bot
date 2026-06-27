"""Очистка БД: удаляет всех пользователей кроме CHAT_ID.

Запуск:
    DATABASE_URL=... CHAT_ID=... python tools/clean_other_users.py

Что делает:
  - Per-user ключи (dict {str(cid): data}): удаляет записи всех cid кроме CHAT_ID
  - wardrobe_user_* ключи для чужих cid: удаляет строку целиком
  - Нетронутые: cost_log.json, wardrobe.json, food_tip_cache.json и прочие не-per-user ключи
"""
import json
import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

if not DATABASE_URL:
    sys.exit("❌ DATABASE_URL не задан")
if not CHAT_ID:
    sys.exit("❌ CHAT_ID не задан")

# Ключи, которые хранят per-user данные как {str(cid): ...}
PER_USER_KEYS = {
    "settings.json",
    "profile.json",
    "levels.json",
    "artists.json",
    "watchlist.json",
    "readlist.json",
    "mycountries.json",
    "mybooks.json",
    "favorites.json",
    "favcountries.json",
    "movie_blacklist.json",
    "book_blacklist.json",
    "music_dislike.json",
    "travel_dislike.json",
    "worries.json",
    "notes.json",
    "dict.json",
    "topics_nl.json",
    "topics_en.json",
    "lagom.json",
    "diary.json",
    "city_facts_seen.json",
    "lifehacks_seen.json",
    "fridge.json",
    "my_recipes.json",
    "quote_authors_seen.json",
    "motiv_lagom_seen.json",
    "micro_topics.json",
    "micro_lessons.json",
    "micro_progress.json",
    "allowed_cids.json",
}

# Ключи, которые НЕ трогаем (глобальные / системные)
SKIP_KEYS = {
    "cost_log.json",
    "food_tip_cache.json",
    "wardrobe.json",
    "city_facts_db",
    "pending_invites.json",
}

import psycopg2

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

# Получаем все строки
cur.execute("SELECT key, value FROM kv")
rows = cur.fetchall()

cleaned = 0
deleted = 0
skipped = 0

print(f"\n🔍 Найдено строк в БД: {len(rows)}")
print(f"👤 Оставляем только CHAT_ID: {CHAT_ID}\n")

for key, value in rows:
    if key in SKIP_KEYS:
        print(f"  ⏭  {key} — пропущен (системный)")
        skipped += 1
        continue

    # wardrobe_user_{cid} — per-user ключ вида строки
    if key.startswith("wardrobe_user_"):
        cid = key[len("wardrobe_user_"):]
        if cid != str(CHAT_ID):
            cur.execute("DELETE FROM kv WHERE key = %s", (key,))
            print(f"  🗑  {key} — удалён (чужой шкаф)")
            deleted += 1
        else:
            print(f"  ✅ {key} — оставлен (ваш шкаф)")
            skipped += 1
        continue

    if key in PER_USER_KEYS:
        if not isinstance(value, dict):
            print(f"  ⚠️  {key} — не dict ({type(value).__name__}), пропускаем")
            skipped += 1
            continue
        other_cids = [c for c in value if c != str(CHAT_ID)]
        if not other_cids:
            print(f"  ✅ {key} — чужих нет")
            skipped += 1
            continue
        for cid in other_cids:
            del value[cid]
        cur.execute("UPDATE kv SET value = %s WHERE key = %s",
                    (json.dumps(value, ensure_ascii=False), key))
        print(f"  🧹 {key} — удалены cid: {other_cids}")
        cleaned += 1
        continue

    # Неизвестный ключ — смотрим, похож ли на per-user dict
    if isinstance(value, dict):
        other_cids = [c for c in value if c != str(CHAT_ID) and c.lstrip("-").isdigit()]
        if other_cids:
            for cid in other_cids:
                del value[cid]
            cur.execute("UPDATE kv SET value = %s WHERE key = %s",
                        (json.dumps(value, ensure_ascii=False), key))
            print(f"  🧹 {key} (unknown) — удалены cid: {other_cids}")
            cleaned += 1
        else:
            print(f"  ⏭  {key} — не per-user, пропущен")
            skipped += 1
    else:
        print(f"  ⏭  {key} — не dict, пропущен")
        skipped += 1

cur.close()
conn.close()

print(f"\n✅ Готово:")
print(f"   Очищено (убраны чужие cid): {cleaned}")
print(f"   Удалено строк целиком: {deleted}")
print(f"   Пропущено: {skipped}")
