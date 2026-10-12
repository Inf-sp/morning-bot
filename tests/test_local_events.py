from datetime import date

import local_events

TODAY = date(2026, 7, 20)
PAGE = {"url": "https://pride.amsterdam/program", "title": "Pride Amsterdam 2026",
        "content": "Pride Amsterdam takes place from 25 July to 2 August 2026 with the Canal Parade on 1 August."}


def _ai(monkeypatch, events):
    monkeypatch.setattr(local_events.ai, "llm_json", lambda *_a, **_k: {"events": events})


def test_only_events_with_date_quoted_from_the_page_are_kept(monkeypatch):
    _ai(monkeypatch, [
        {"name": "Pride Amsterdam", "about": "прайд-фестиваль на каналах", "start": "2026-07-25",
         "end": "2026-08-02", "url": PAGE["url"], "evidence": "from 25 July to 2 August 2026"},
        # Дата придумана: цитаты нет на странице.
        {"name": "Kermis", "about": "ярмарка", "start": "2026-07-22", "end": "2026-07-22",
         "url": PAGE["url"], "evidence": "Kermis on 22 July"},
        # Страницы нет среди найденных.
        {"name": "Jazz Fest", "start": "2026-07-23", "end": "2026-07-23", "url": "https://x.example",
         "evidence": "23 July"},
        # За горизонтом двух недель.
        {"name": "Uitmarkt", "start": "2026-08-28", "end": "2026-08-30", "url": PAGE["url"],
         "evidence": "Canal Parade on 1 August"},
    ])

    found = local_events._extract("Amsterdam", [PAGE], TODAY)

    assert found == [{"name": "Pride Amsterdam", "about": "прайд-фестиваль на каналах",
                      "start": "2026-07-25", "end": "2026-08-02", "url": PAGE["url"]}]


def test_my_day_and_eve_lines_from_cache(monkeypatch):
    event = {"name": "Pride Amsterdam", "about": "прайд-фестиваль", "start": "2026-07-25",
             "end": "2026-08-02", "url": PAGE["url"]}
    monkeypatch.setattr(local_events, "events", lambda _cid, fetch=True: [event])

    assert local_events.today_lines("42", date(2026, 7, 28)) == ["Сегодня: Pride Amsterdam — прайд-фестиваль"]
    assert local_events.today_lines("42", date(2026, 8, 3)) == []
    assert local_events.starting_on("42", date(2026, 7, 25)) == ["Pride Amsterdam — прайд-фестиваль"]
    assert local_events.starting_on("42", date(2026, 7, 26)) == []
