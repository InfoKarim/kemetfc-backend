import json
from types import SimpleNamespace

import pytest

from app.services import smart_recommendation_service as service


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_is_configured_reflects_anthropic_key(monkeypatch):
    monkeypatch.setattr(service, "get_anthropic_api_key", lambda: "")
    assert service.is_configured() is False

    monkeypatch.setattr(service, "get_anthropic_api_key", lambda: "sk-ant-test")
    assert service.is_configured() is True


def test_generate_focus_areas_parses_json_from_model_reply(monkeypatch):
    monkeypatch.setattr(service, "get_anthropic_api_key", lambda: "sk-ant-test")

    anthropic_payload = {
        "content": [{
            "type": "text",
            "text": (
                "Here you go:\n"
                '[{"title": "Weak-foot passing", '
                '"reason": "Passing was flagged as a weakness.", '
                '"search_keywords": "youth soccer weak foot passing drills"}]'
            ),
        }]
    }

    def fake_urlopen(request, timeout=None):
        assert request.full_url == service.ANTHROPIC_API_URL
        assert request.get_header("X-api-key") == "sk-ant-test"
        return FakeHTTPResponse(anthropic_payload)

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)

    focus_areas = service.generate_focus_areas(
        player_name="Test Player",
        age=12,
        weaknesses=[{"attribute": "Passing", "score": 60}],
        strengths=[{"attribute": "Speed", "score": 90}],
    )

    assert focus_areas == [{
        "title": "Weak-foot passing",
        "reason": "Passing was flagged as a weakness.",
        "search_keywords": "youth soccer weak foot passing drills",
    }]


def test_generate_focus_areas_skips_leading_thinking_block(monkeypatch):
    # Extended-thinking models return a "thinking" content block before the
    # actual "text" block — content[0] is not reliably the answer.
    monkeypatch.setattr(service, "get_anthropic_api_key", lambda: "sk-ant-test")

    anthropic_payload = {
        "content": [
            {"type": "thinking", "thinking": "reasoning about the player..."},
            {
                "type": "text",
                "text": (
                    '[{"title": "Passing", "reason": "...", '
                    '"search_keywords": "passing drills"}]'
                ),
            },
        ]
    }

    def fake_urlopen(request, timeout=None):
        return FakeHTTPResponse(anthropic_payload)

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)

    focus_areas = service.generate_focus_areas("Test Player", 12, [], [])

    assert focus_areas[0]["title"] == "Passing"


def test_generate_focus_areas_raises_on_unparseable_reply(monkeypatch):
    monkeypatch.setattr(service, "get_anthropic_api_key", lambda: "sk-ant-test")

    def fake_urlopen(request, timeout=None):
        return FakeHTTPResponse({"content": [{"text": "no json here"}]})

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(service.RecommendationError):
        service.generate_focus_areas("Test Player", 12, [], [])


def test_search_training_videos_returns_empty_without_key(monkeypatch):
    monkeypatch.setattr(service, "get_youtube_api_key", lambda: "")
    assert service.search_training_videos("passing drills") == []


def test_search_training_videos_parses_results(monkeypatch):
    monkeypatch.setattr(service, "get_youtube_api_key", lambda: "yt-test-key")

    youtube_payload = {
        "items": [{
            "id": {"videoId": "abc123"},
            "snippet": {
                "title": "Passing Drills",
                "channelTitle": "Coach Example",
                "thumbnails": {"medium": {"url": "https://img.example/abc123.jpg"}},
            },
        }]
    }

    def fake_urlopen(url, timeout=None):
        assert url.startswith(service.YOUTUBE_SEARCH_URL)
        return FakeHTTPResponse(youtube_payload)

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)

    videos = service.search_training_videos("passing drills")

    assert videos == [{
        "title": "Passing Drills",
        "channel": "Coach Example",
        "url": "https://www.youtube.com/watch?v=abc123",
        "thumbnail_url": "https://img.example/abc123.jpg",
    }]


def test_get_smart_recommendations_requires_anthropic_key(monkeypatch):
    monkeypatch.setattr(service, "get_anthropic_api_key", lambda: "")

    with pytest.raises(service.RecommendationError):
        service.get_smart_recommendations("Test Player", 12, [], [])


def test_get_smart_recommendations_attaches_videos_per_area(monkeypatch):
    monkeypatch.setattr(service, "get_anthropic_api_key", lambda: "sk-ant-test")
    monkeypatch.setattr(
        service,
        "generate_focus_areas",
        lambda *a, **k: [{"title": "Passing", "search_keywords": "passing drills"}],
    )
    monkeypatch.setattr(
        service,
        "search_training_videos",
        lambda query, max_results=3: [{"title": "A video", "url": "https://x"}],
    )

    result = service.get_smart_recommendations("Test Player", 12, [], [])

    assert result[0]["videos"] == [{"title": "A video", "url": "https://x"}]
