"""Player check-in QR codes — consumed by the native iOS KemetFCTracker
app's QR Scan screen (spec section 5: identify a player before any
tracking begins). A coach mints an opaque token for a player (embedded
in that player's printed/displayed QR badge); the app scans it and
resolves it back to a Player Profile via this same API. Coach/admin only,
same as every other tracking-adjacent endpoint in this app — a guardian
has no reason to check a player in for an assessment."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api_schemas import ResolveCheckInTokenSchema
from app.database import get_db
from app.db_models import PlayerDB, TeamDB
from app.services.player_checkin_service import CheckInTokenError, PlayerCheckInService

router = APIRouter()

# HTTP status per failure reason — distinct enough that the iOS app can
# show the coach a specific, actionable message rather than one generic
# "scan failed" (spec: "Handle invalid, expired, revoked, or duplicate QR
# tokens gracefully").
_REASON_STATUS = {
    "invalid": 404,
    "expired": 410,
    "revoked": 410,
}


def _require_coach_or_admin(request: Request) -> None:
    if request.state.current_user["role"] not in {"admin", "coach"}:
        raise HTTPException(status_code=403, detail="Coach or admin access required")


def _player_summary_payload(player: PlayerDB, db: Session) -> dict:
    team_name = None
    age_group = None
    if player.team_id is not None:
        team = db.get(TeamDB, player.team_id)
        if team is not None:
            team_name = team.name
            age_group = team.age_group

    return {
        "player_id": player.player_id,
        "first_name_en": player.first_name_en,
        "last_name_en": player.last_name_en,
        "team_name": team_name,
        "age_group": age_group,
        "photo_url": (
            f"/uploads/avatars/{player.photo_filename}"
            if player.photo_filename
            else None
        ),
    }


@router.post("/players/{player_id}/checkin-token")
def mint_player_checkin_token(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Issue (or reissue) a player's QR check-in token — the raw token
    is returned exactly once, here, and never stored or logged in
    plaintext; whoever calls this is responsible for embedding it into
    the QR the player carries."""
    _require_coach_or_admin(request)

    player = db.get(PlayerDB, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    token = PlayerCheckInService(db=db).mint_token(player_id)
    return {"player_id": player_id, "token": token}


@router.post("/players/checkin-token/resolve")
def resolve_player_checkin_token(
    payload: ResolveCheckInTokenSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    """Validate a scanned QR token and resolve it to a Player Profile.
    The token itself never contains a player_id or any other internal
    identifier — only this lookup does."""
    _require_coach_or_admin(request)

    try:
        player = PlayerCheckInService(db=db).resolve_token(payload.token)
    except CheckInTokenError as error:
        status_code = _REASON_STATUS.get(error.reason, 400)
        return JSONResponse(
            status_code=status_code,
            content={"detail": error.message, "reason": error.reason},
        )

    return _player_summary_payload(player, db)
