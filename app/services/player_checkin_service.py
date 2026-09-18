"""Player check-in QR tokens — a coach mints an opaque token for a player
(embedded in a printed/displayed QR code), and the iOS app's QR Scan
screen resolves it back to a Player Profile before starting an
assessment. The QR itself never carries the player's internal database
ID (spec: "Do not expose internal database IDs in the QR") — only this
opaque, unguessable token, which only this backend can map back to a
player. Only the token's hash is ever stored, matching AuthSessionDB's
own token_hash pattern (see hash_session_token) — a database read alone
can never yield a working check-in code.

Minting a new token for a player revokes any previously active token for
that player, so a player never has more than one currently-valid QR code
outstanding (the scenario "duplicate" tokens would otherwise create,
e.g. after a lost badge is reissued)."""

import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.db_models import PlayerCheckInTokenDB, PlayerDB
from app.services.auth_service import hash_session_token, utcnow
from app.services.id_service import next_entity_id

TOKEN_PREFIX = "KEMETCHK"
# A player's printed/displayed QR badge is meant to last a full season,
# not require reprinting — reissued on demand (mint_token) if a badge is
# lost, which immediately revokes the old one rather than waiting for
# this TTL.
TOKEN_TTL = timedelta(days=365)


class CheckInTokenError(Exception):
    """Raised with a `reason` the router maps to a specific HTTP response
    and the iOS app maps to a specific coach-facing message — never a
    single generic failure, since invalid/expired/revoked need different
    guidance (e.g. "ask the office to reissue this player's badge")."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        self.message = message
        super().__init__(message)


class PlayerCheckInService:
    def __init__(self, db: Session):
        self.db = db

    def mint_token(self, player_id: str) -> str:
        """Issue a fresh check-in token for a player, revoking any
        previous one first so at most one stays valid at a time."""
        now = utcnow()
        self.db.query(PlayerCheckInTokenDB).filter(
            PlayerCheckInTokenDB.player_id == player_id,
            PlayerCheckInTokenDB.revoked_at.is_(None),
        ).update({"revoked_at": now})

        raw_token = f"{TOKEN_PREFIX}-{secrets.token_urlsafe(32)}"
        token_id = next_entity_id(self.db, "player_checkin_token")
        self.db.add(PlayerCheckInTokenDB(
            token_id=token_id,
            player_id=player_id,
            token_hash=hash_session_token(raw_token),
            created_at=now,
            expires_at=now + TOKEN_TTL,
            revoked_at=None,
            last_used_at=None,
        ))
        self.db.commit()
        return raw_token

    def resolve_token(self, raw_token: str) -> PlayerDB:
        """Validate an opaque check-in token and return the player it
        belongs to, or raise CheckInTokenError with a specific reason."""
        token_hash = hash_session_token(raw_token)
        record = (
            self.db.query(PlayerCheckInTokenDB)
            .filter(PlayerCheckInTokenDB.token_hash == token_hash)
            .one_or_none()
        )
        if record is None:
            raise CheckInTokenError(
                "invalid",
                "This QR code is not a recognized KEMET player check-in code.",
            )

        now = utcnow()
        if record.revoked_at is not None:
            raise CheckInTokenError(
                "revoked",
                "This player's check-in code has been reissued and is no longer valid.",
            )
        if record.expires_at <= now:
            raise CheckInTokenError(
                "expired",
                "This player's check-in code has expired — ask the office to reissue it.",
            )

        player = self.db.get(PlayerDB, record.player_id)
        if player is None:
            raise CheckInTokenError(
                "invalid",
                "The player for this check-in code no longer exists.",
            )

        record.last_used_at = now
        self.db.commit()
        return player
