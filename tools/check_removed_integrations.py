import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import ai
import config
import leisure_concerts
import research


REMOVED_PROVIDERS = {"cl" + "aude"}
REMOVED_CONFIG = {
    "ANTHROPIC" + "_API_KEY",
    "ANTHROPIC" + "_MODEL",
    "GRAMMAR" + "_MODEL",
    "WARDROBE" + "_MODEL",
    "EVENT" + "BRITE_API_KEY",
    "SERP" + "API_API_KEY",
}


def main():
    for name in REMOVED_CONFIG:
        assert not hasattr(config, name), name

    assert not (set(ai.DEFAULT_ORDER) & REMOVED_PROVIDERS)
    assert not (set(ai.CHAT_ORDER) & REMOVED_PROVIDERS)
    assert not (set(ai.GRAMMAR_ORDER) & REMOVED_PROVIDERS)
    assert not (set(ai.LEISURE_ORDER) & REMOVED_PROVIDERS)
    removed_provider = "cl" + "aude"
    assert removed_provider not in ai.PROVIDER_ORDER
    assert removed_provider not in ai._resolve("smart", None, route=removed_provider)

    event_client = "_event" + "brite_events"
    assert not hasattr(leisure_concerts, event_client)
    assert not hasattr(leisure_concerts, event_client + "_many")

    calls = []

    def fake_tavily(query, max_results=5, **_kwargs):
        calls.append(("tavily", query, max_results))
        return [{"title": "x", "url": "https://ticketmaster.example/x", "content": "x"}]

    with patch.object(research, "firecrawl_search", lambda *_args, **_kwargs: []), \
         patch.object(research, "tavily_search", fake_tavily):
        assert research.web_search("test", max_results=1) == []
        result = research.web_search(
            "test", max_results=1, scenario="explicit_research",
            allow_tavily=True, search_priority="tavily",
        )
    assert result and calls == [("tavily", "test", 1)]
    assert not hasattr(research, "serp" + "api_search")

    print("ok")


if __name__ == "__main__":
    main()
