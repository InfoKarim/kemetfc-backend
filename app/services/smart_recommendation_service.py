import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from app.config import get_anthropic_api_key, get_youtube_api_key


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
REQUEST_TIMEOUT_SECONDS = 15


class RecommendationError(Exception):
    pass


def get_anthropic_model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5").strip()


def _describe(items: list) -> str:
    labels = []

    for item in items or []:
        if isinstance(item, dict):
            labels.append(str(item.get("attribute") or item))
        else:
            labels.append(str(item))

    return "; ".join(labels) if labels else "None recorded"


def _call_anthropic(prompt: str) -> str:
    api_key = get_anthropic_api_key()
    body = json.dumps({
        "model": get_anthropic_model(),
        "max_tokens": 700,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    request = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=body,
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")
        raise RecommendationError(
            f"Anthropic API error ({error.code}): {detail[:200]}"
        ) from error
    except urllib.error.URLError as error:
        raise RecommendationError(str(error.reason)) from error

    try:
        text_blocks = [
            block["text"]
            for block in payload["content"]
            if block.get("type") == "text"
        ]
    except (KeyError, TypeError) as error:
        raise RecommendationError(
            "Unexpected response from Anthropic API"
        ) from error

    if not text_blocks:
        raise RecommendationError("Unexpected response from Anthropic API")

    return text_blocks[0]


def generate_focus_areas(
    player_name: str,
    age: int,
    weaknesses: list,
    strengths: list,
    max_items: int = 3,
) -> list[dict]:
    prompt = (
        "You are an assistant to a youth football (soccer) coach. "
        f"Player: {player_name}, age {age}.\n"
        f"Weaknesses from video analysis: {_describe(weaknesses)}\n"
        f"Strengths from video analysis: {_describe(strengths)}\n\n"
        f"Suggest exactly {max_items} specific training focus areas that "
        "would most improve this player, prioritizing the weaknesses. "
        "Reply with ONLY a JSON array (no prose, no markdown fences), where "
        "each item has this shape: "
        '{"title": "short focus area name", '
        '"reason": "one sentence explaining why, referencing the player\'s '
        'own data", '
        '"search_keywords": "a short youth-football training video search '
        'query for this focus area"}'
    )

    raw_text = _call_anthropic(prompt)
    match = re.search(r"\[.*\]", raw_text, re.DOTALL)

    if match is None:
        raise RecommendationError("Could not parse AI recommendations")

    try:
        focus_areas = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise RecommendationError("Could not parse AI recommendations") from error

    if not isinstance(focus_areas, list):
        raise RecommendationError("Could not parse AI recommendations")

    return focus_areas[:max_items]


def generate_sports_medicine_notes(
    player_name: str,
    age: int,
    weaknesses: list,
    strengths: list,
    max_items: int = 3,
) -> list[dict]:
    prompt = (
        "You are a youth sports medicine physician giving a coach general, "
        "educational background on a young athlete's movement measurements. "
        "This is NOT a diagnosis and NOT a treatment plan — it is "
        "informational context only, and a licensed professional must "
        "evaluate the athlete in person before any medical or training "
        "decisions are made.\n"
        f"Player: {player_name}, age {age}.\n"
        f"Measured weaknesses: {_describe(weaknesses)}\n"
        f"Measured strengths: {_describe(strengths)}\n\n"
        f"Give exactly {max_items} general, non-diagnostic notes a coach "
        "should keep in mind (e.g. mobility work, load management, warm-up "
        "focus, or when it's worth suggesting the family see a sports "
        "medicine professional). Do not name or imply any specific medical "
        "condition, injury, or diagnosis. Reply with ONLY a JSON array (no "
        "prose, no markdown fences), where each item has this shape: "
        '{"title": "short note name", '
        '"note": "one or two sentences of general, non-diagnostic guidance, '
        'referencing the player\'s own measurements"}'
    )

    raw_text = _call_anthropic(prompt)
    match = re.search(r"\[.*\]", raw_text, re.DOTALL)

    if match is None:
        raise RecommendationError("Could not parse AI recommendations")

    try:
        notes = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise RecommendationError("Could not parse AI recommendations") from error

    if not isinstance(notes, list):
        raise RecommendationError("Could not parse AI recommendations")

    return notes[:max_items]


def generate_coaching_insights(
    player_name: str,
    age: int,
    weaknesses: list,
    strengths: list,
    max_items: int = 3,
) -> list[dict]:
    prompt = (
        "You are one of Europe's top youth football development coaches, "
        "trained in modern sports-science-based methodology (technical, "
        "tactical, and physical development for young players).\n"
        f"Player: {player_name}, age {age}.\n"
        f"Measured weaknesses: {_describe(weaknesses)}\n"
        f"Measured strengths: {_describe(strengths)}\n\n"
        f"Give exactly {max_items} theoretical, principle-based coaching "
        "insights for this player, grounded in established youth "
        "development science (e.g. long-term athletic development, motor "
        "learning, periodization). For each, explain the coaching "
        "principle behind it, not just a drill — this is meant to build "
        "the coach's understanding, not replace the video "
        "recommendations. Reply with ONLY a JSON array (no prose, no "
        "markdown fences), where each item has this shape: "
        '{"title": "short principle name", '
        '"insight": "two to three sentences explaining the coaching '
        'science behind this, referencing the player\'s own '
        'measurements"}'
    )

    raw_text = _call_anthropic(prompt)
    match = re.search(r"\[.*\]", raw_text, re.DOTALL)

    if match is None:
        raise RecommendationError("Could not parse AI recommendations")

    try:
        insights = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise RecommendationError("Could not parse AI recommendations") from error

    if not isinstance(insights, list):
        raise RecommendationError("Could not parse AI recommendations")

    return insights[:max_items]


def search_training_videos(query: str, max_results: int = 3) -> list[dict]:
    api_key = get_youtube_api_key()

    if not api_key:
        return []

    params = urllib.parse.urlencode({
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "safeSearch": "strict",
        "videoEmbeddable": "true",
        "key": api_key,
    })

    try:
        with urllib.request.urlopen(
            f"{YOUTUBE_SEARCH_URL}?{params}",
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")
        raise RecommendationError(
            f"YouTube API error ({error.code}): {detail[:200]}"
        ) from error
    except urllib.error.URLError as error:
        raise RecommendationError(str(error.reason)) from error

    videos = []

    for item in payload.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        snippet = item.get("snippet", {})

        if not video_id:
            continue

        videos.append({
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail_url": (
                snippet.get("thumbnails", {}).get("medium", {}).get("url")
            ),
        })

    return videos


def is_configured() -> bool:
    return bool(get_anthropic_api_key())


def get_smart_recommendations(
    player_name: str,
    age: int,
    weaknesses: list,
    strengths: list,
) -> list[dict]:
    if not is_configured():
        raise RecommendationError(
            "Smart recommendations are not configured "
            "(ANTHROPIC_API_KEY is missing)"
        )

    focus_areas = generate_focus_areas(player_name, age, weaknesses, strengths)

    for area in focus_areas:
        keywords = area.get("search_keywords") or area.get("title", "")
        try:
            area["videos"] = search_training_videos(keywords)
        except RecommendationError:
            area["videos"] = []

    return focus_areas


def get_sports_medicine_notes(
    player_name: str,
    age: int,
    weaknesses: list,
    strengths: list,
) -> list[dict]:
    if not is_configured():
        raise RecommendationError(
            "Smart recommendations are not configured "
            "(ANTHROPIC_API_KEY is missing)"
        )

    return generate_sports_medicine_notes(
        player_name, age, weaknesses, strengths
    )


def get_coaching_insights(
    player_name: str,
    age: int,
    weaknesses: list,
    strengths: list,
) -> list[dict]:
    if not is_configured():
        raise RecommendationError(
            "Smart recommendations are not configured "
            "(ANTHROPIC_API_KEY is missing)"
        )

    return generate_coaching_insights(
        player_name, age, weaknesses, strengths
    )
