from datetime import datetime, UTC

from sqlalchemy.orm import Session

from app.db_models import AuditEventDB, PromoCodeDB, PromoCodeRedemptionDB
from app.services.id_service import next_entity_id


class PromoCodeError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PromoCodeService:
    def __init__(self, db: Session):
        self.db = db

    def create_promo_code(
        self,
        actor_user_id: str,
        code: str,
        discount_type: str,
        discount_value: int,
        starts_at: datetime | None,
        expires_at: datetime | None,
        max_uses: int | None,
        per_family_limit: int | None,
        eligible_plan_ids: list[str] | None,
    ) -> PromoCodeDB:
        normalized_code = code.strip().upper()
        if not normalized_code:
            raise PromoCodeError("Promo code cannot be empty")

        existing = (
            self.db.query(PromoCodeDB)
            .filter(PromoCodeDB.code == normalized_code)
            .first()
        )
        if existing is not None:
            raise PromoCodeError("A promo code with this code already exists")

        now = _now()
        promo = PromoCodeDB(
            promo_code_id=next_entity_id(self.db, "promo_code"),
            code=normalized_code,
            discount_type=discount_type,
            discount_value=discount_value,
            starts_at=starts_at,
            expires_at=expires_at,
            max_uses=max_uses,
            per_family_limit=per_family_limit,
            eligible_plan_ids=eligible_plan_ids,
            active=True,
            created_by_user_id=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        self.db.add(promo)
        self._audit(
            actor_user_id=actor_user_id,
            action="promo_code_created",
            resource_id=promo.promo_code_id,
            details={"code": normalized_code, "discount_type": discount_type, "discount_value": discount_value},
        )
        self.db.commit()
        self.db.refresh(promo)
        return promo

    def list_promo_codes(self) -> list[PromoCodeDB]:
        return (
            self.db.query(PromoCodeDB)
            .order_by(PromoCodeDB.created_at.desc())
            .all()
        )

    def update_promo_code(
        self,
        promo_code_id: str,
        actor_user_id: str,
        active: bool | None = None,
    ) -> PromoCodeDB:
        promo = self.db.get(PromoCodeDB, promo_code_id)
        if promo is None:
            raise PromoCodeError("Promo code not found")

        previous_active = promo.active
        if active is not None:
            promo.active = active
        promo.updated_at = _now()

        self._audit(
            actor_user_id=actor_user_id,
            action="promo_code_updated",
            resource_id=promo_code_id,
            details={"previous_active": previous_active, "new_active": promo.active},
        )
        self.db.commit()
        self.db.refresh(promo)
        return promo

    def validate_promo_code(
        self,
        code: str,
        guardian_user_id: str,
        plan_id: str | None,
    ) -> PromoCodeDB:
        """Raises PromoCodeError with a specific, guardian-facing reason if
        the code can't be used right now. Every check is server-side —
        the client only ever submits the code string."""
        normalized = code.strip().upper()
        promo = (
            self.db.query(PromoCodeDB)
            .filter(PromoCodeDB.code == normalized)
            .first()
        )
        if promo is None:
            raise PromoCodeError("Invalid promo code")
        if not promo.active:
            raise PromoCodeError("This promo code is no longer active")

        now = _now()
        if promo.starts_at is not None and now < promo.starts_at:
            raise PromoCodeError("This promo code is not active yet")
        if promo.expires_at is not None and now > promo.expires_at:
            raise PromoCodeError("This promo code has expired")
        if promo.eligible_plan_ids and plan_id not in promo.eligible_plan_ids:
            raise PromoCodeError("This promo code is not valid for the selected plan")

        if promo.max_uses is not None:
            total_uses = (
                self.db.query(PromoCodeRedemptionDB)
                .filter(PromoCodeRedemptionDB.promo_code_id == promo.promo_code_id)
                .count()
            )
            if total_uses >= promo.max_uses:
                raise PromoCodeError("This promo code has reached its usage limit")

        if promo.per_family_limit is not None:
            family_uses = (
                self.db.query(PromoCodeRedemptionDB)
                .filter(
                    PromoCodeRedemptionDB.promo_code_id == promo.promo_code_id,
                    PromoCodeRedemptionDB.guardian_user_id == guardian_user_id,
                )
                .count()
            )
            if family_uses >= promo.per_family_limit:
                raise PromoCodeError("This promo code has already been used by your family")

        return promo

    def redeem_promo_code(
        self,
        promo_code_id: str,
        guardian_user_id: str,
        player_id: str,
    ) -> PromoCodeRedemptionDB:
        redemption = PromoCodeRedemptionDB(
            redemption_id=next_entity_id(self.db, "promo_code_redemption"),
            promo_code_id=promo_code_id,
            guardian_user_id=guardian_user_id,
            player_id=player_id,
            redeemed_at=_now(),
        )
        self.db.add(redemption)
        self.db.commit()
        self.db.refresh(redemption)
        return redemption

    def _audit(
        self,
        actor_user_id: str,
        action: str,
        resource_id: str,
        details: dict,
    ) -> None:
        from uuid import uuid4

        self.db.add(
            AuditEventDB(
                event_id=str(uuid4()),
                occurred_at=_now(),
                actor_user_id=actor_user_id,
                action=action,
                resource_type="promo_code",
                resource_id=resource_id,
                details=details,
            )
        )
