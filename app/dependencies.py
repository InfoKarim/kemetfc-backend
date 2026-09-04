"""Cross-cutting helpers shared across main.py and app/routers/*.

Kept in one place, independent of any single router, so both main.py
(auth middleware, startup wiring) and the domain routers can import them
without circular imports.
"""

from datetime import date

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.data_models import AIAnalysisRecord
from app.db_models import UserDB
from app.services.auth_service import effective_feature_permissions
from app.services.data_record_service import DataRecordService
from app.services.notification_service import NotificationService
from app.services.privacy_service import PrivacyService
from app.services.video_service import VideoService


def user_payload(user) -> dict:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "active": user.active,
        "feature_permissions": effective_feature_permissions(user),
        "avatar_url": (
            f"/uploads/avatars/{user.avatar_filename}"
            if user.avatar_filename
            else None
        ),
    }


def consent_payload(consent) -> dict:
    return {
        "consent_id": consent.consent_id,
        "player_id": consent.player_id,
        "guardian_name": consent.guardian_name,
        "guardian_email": consent.guardian_email,
        "verification_method": consent.verification_method,
        "purposes": consent.purposes,
        "granted_at": consent.granted_at,
        "expires_at": consent.expires_at,
        "withdrawn_at": consent.withdrawn_at,
        "recorded_by_user_id": consent.recorded_by_user_id,
    }


def ml_dataset_entry_payload(entry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "video_id": entry.video_id,
        "team_id": entry.team_id,
        "age_band": entry.age_band,
        "sex_cohort": entry.sex_cohort,
        "camera_id": entry.camera_id,
        "lighting": entry.lighting,
        "consent_id": entry.consent_id,
        "status": entry.status,
        "notes": entry.notes,
        "flagged_by_user_id": entry.flagged_by_user_id,
        "flagged_at": entry.flagged_at,
        "reviewed_by_user_id": entry.reviewed_by_user_id,
        "reviewed_at": entry.reviewed_at,
    }


def season_payload(season) -> dict:
    return {
        "season_id": season.season_id,
        "name": season.name,
        "start_date": season.start_date,
        "end_date": season.end_date,
        "is_active": season.is_active,
    }


def registration_payload(registration) -> dict:
    return {
        "registration_id": registration.registration_id,
        "parent_name": registration.parent_name,
        "parent_email": registration.parent_email,
        "parent_phone": registration.parent_phone,
        "emergency_contact": registration.emergency_contact,
        "player_name": registration.player_name,
        "player_date_of_birth": registration.player_date_of_birth,
        "player_age": registration.player_age,
        "preferred_position": registration.preferred_position,
        "experience_level": registration.experience_level,
        "current_team": registration.current_team,
        "consents": registration.consents,
        "submitted_at": registration.submitted_at,
    }


def contact_message_payload(message) -> dict:
    return {
        "message_id": message.message_id,
        "name": message.name,
        "email": message.email,
        "topic": message.topic,
        "message": message.message,
        "submitted_at": message.submitted_at,
    }


def notification_payload(notification) -> dict:
    return {
        "notification_id": notification.notification_id,
        "type": notification.type,
        "title": notification.title,
        "body": notification.body,
        "link": notification.link,
        "read": notification.read,
        "created_at": notification.created_at,
    }


def message_payload(message) -> dict:
    return {
        "message_id": message.message_id,
        "sender_id": message.sender_id,
        "recipient_id": message.recipient_id,
        "subject": message.subject,
        "body": message.body,
        "read": message.read,
        "created_at": message.created_at,
    }


def notify_coaching_staff(
    db: Session,
    *,
    exclude_user_id: str | None,
    type: str,
    title: str,
    body: str,
    link: str | None = None,
) -> None:
    """Create a notification for every active admin/coach except the actor."""
    recipients = (
        db.query(UserDB)
        .filter(UserDB.role.in_(["admin", "coach"]), UserDB.active.is_(True))
        .all()
    )
    service = NotificationService(db=db)

    for user in recipients:
        if user.user_id == exclude_user_id:
            continue
        service.create_notification(
            user_id=user.user_id,
            type=type,
            title=title,
            body=body,
            link=link,
        )


def is_minor(date_of_birth: date, today: date | None = None) -> bool:
    current = today or date.today()
    age = current.year - date_of_birth.year - (
        (current.month, current.day)
        < (date_of_birth.month, date_of_birth.day)
    )
    return age < 18


def resolve_ai_provider(request: Request) -> str:
    provider = request.query_params.get("provider", "claude")
    if provider not in {"claude", "chatgpt"}:
        raise HTTPException(status_code=400, detail="Invalid AI provider")
    return provider


def require_admin(request: Request) -> None:
    if request.state.current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


def require_guardian_player_access(
    request: Request,
    db: Session,
    player_id: str,
) -> None:
    if request.state.current_user["role"] != "guardian":
        return

    if not PrivacyService(db=db).guardian_can_access_player(
        request.state.current_user["user_id"],
        player_id,
    ):
        raise HTTPException(status_code=404, detail="Linked child not found")


def require_guardian_video_access(
    request: Request,
    db: Session,
    video_id: str,
) -> None:
    if request.state.current_user["role"] != "guardian":
        return

    video = VideoService(db=db).get_video(video_id)
    record = (
        DataRecordService(db=db).get_record(video.record_id)
        if video is not None
        else None
    )

    if record is None:
        raise HTTPException(status_code=404, detail="Linked child video not found")

    require_guardian_player_access(request, db, record.player_id)


def require_approved_analysis(analysis: AIAnalysisRecord) -> None:
    if analysis.requires_human_review and not analysis.approved:
        raise HTTPException(
            status_code=409,
            detail="Analysis must be approved before generating training guidance",
        )
