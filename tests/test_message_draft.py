import asyncio

import config
import rich_delivery
from ui import assistant as assistant_ui


class DraftBot:
    def __init__(self):
        self.drafts = []

    async def send_message_draft(self, **kwargs):
        self.drafts.append(kwargs)
        return True


def test_classic_draft_streams_without_rich_messages(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_RICH_MESSAGES", False)
    bot = DraftBot()

    async def flow():
        draft = await rich_delivery.start_draft(bot, "42")
        await draft.text("Первая строка")
        return draft

    draft = asyncio.run(flow())

    assert isinstance(draft, rich_delivery.ClassicDraft)
    assert [call.get("text", "") for call in bot.drafts] == ["", "Первая строка"]
    assert len({call["draft_id"] for call in bot.drafts}) == 1


def test_preview_text_drops_model_markdown_like_final_card():
    assert assistant_ui.preview_text("## **Итог**\n\nСовет __важный__") == "Итог\nСовет важный"
