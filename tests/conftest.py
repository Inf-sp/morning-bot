"""Проставляем dummy-env ДО импорта модулей, чтобы config не падал на os.environ,
и добавляем корень проекта в sys.path (чтобы import verify/skills работал)."""
import os
import sys

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("CHAT_ID", "1")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
