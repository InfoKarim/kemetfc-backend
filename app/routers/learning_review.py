"""Backend review of Learning Engine items by an authenticated coach or admin. ADDITIVE, ISOLATED and LOCKED BY DEFAULT.

Locked: every route answers 404 unless the environment variable LEARNING_ENGINE_REVIEW_API_ENABLED is exactly "1" AND
LEARNING_ENGINE_STATE_DIR names a private research-state directory. Isolated: it opens only the research database
(learning_engine package), never the application database, players, videos, consents or uploads, and it imports the package
only when a request arrives, so the application starts and runs identically without it. It publishes nothing and changes no
player's programme: approval just records a coach decision in the research database.
"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/learning-review", tags=["learning-review"])

ENABLED_ENV = "LEARNING_ENGINE_REVIEW_API_ENABLED"
STATE_ENV = "LEARNING_ENGINE_STATE_DIR"
ROLES = {"coach", "admin"}


class ContentBody(BaseModel):
    content: dict


class DecisionBody(BaseModel):
    version: int = Field(ge=1)
    decision: str
    reasons: str = Field(min_length=1, max_length=2000)
    sources_checked: list[str] = Field(default_factory=list, max_length=20)


def _gate(request: Request):
    if os.environ.get(ENABLED_ENV) != "1":
        raise HTTPException(status_code=404, detail="Not Found")
    user = request.state.current_user
    if user["role"] not in ROLES:
        raise HTTPException(status_code=403, detail="Not allowed for this role")
    return user


def _open():
    state = os.environ.get(STATE_ENV)
    if not state:
        raise HTTPException(status_code=503, detail="The learning review is not configured")
    from learning_engine.store import ResearchStore, StoreError          # imported only when the feature is enabled and used
    try:
        return ResearchStore(Path(state))
    except (StoreError, OSError):
        raise HTTPException(status_code=503, detail="The learning review is not available") from None


def _reviewer(user):
    from learning_engine.review import AUTH_BACKEND, Reviewer
    return Reviewer(user["user_id"], AUTH_BACKEND, user["role"])


def _fail(exc):
    text = str(exc)
    status = 409 if ("only a pending" in text or "changed since" in text or "not confirmed available" in text) else 422
    raise HTTPException(status_code=status, detail=text)


@router.get("/items")
def list_items(request: Request, status: str | None = None, needs_source_review: bool = False):
    _gate(request)
    from learning_engine.review import ReviewError, ReviewService
    store = _open()
    try:
        return ReviewService(store).list_items(status, needs_source_review)
    except ReviewError as exc:
        _fail(exc)
    finally:
        store.close()


@router.get("/items/{item_id}")
def get_item(item_id: str, request: Request):
    _gate(request)
    from learning_engine.review import ReviewError, ReviewService
    store = _open()
    try:
        service = ReviewService(store)
        return {**service.get(item_id), "history": service.history(item_id)}
    except ReviewError:
        raise HTTPException(status_code=404, detail="Item not found") from None
    finally:
        store.close()


@router.post("/items/{item_id}/content")
def save_content(item_id: str, body: ContentBody, request: Request):
    user = _gate(request)
    from learning_engine.review import ReviewError, ReviewService
    store = _open()
    try:
        return ReviewService(store).save_content(item_id, body.content, _reviewer(user))
    except ReviewError as exc:
        _fail(exc)
    finally:
        store.close()


@router.post("/items/{item_id}/decision")
def decide(item_id: str, body: DecisionBody, request: Request):
    user = _gate(request)
    from learning_engine.review import ReviewError, ReviewService
    store = _open()
    try:
        return ReviewService(store).decide(item_id, body.version, body.decision, _reviewer(user), body.reasons, body.sources_checked)
    except ReviewError as exc:
        _fail(exc)
    finally:
        store.close()
