from pathlib import Path
from datetime import date, datetime
import hmac
import json
import logging
import re
import time
import uuid
from dataclasses import asdict
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api_schemas import (
    AnalysisDrillRecommendationSchema,
    AnalysisSchema,
    ChangeOwnPasswordSchema,
    ChildDeletionRequestSchema,
    ConfirmPasswordResetSchema,
    CreateMessageSchema,
    CreateSeasonSchema,
    CreateUserSchema,
    CreateVideoAnalysisJobSchema,
    CreateTrainingPlanSchema,
    DevelopmentForecastSchema,
    DrillRecommendationSchema,
    DrillSchema,
    GuardianConsentSchema,
    GuardianPlayerLinkSchema,
    LoginSchema,
    MatchSchema,
    PlayerSchema,
    PublicRegistrationSchema,
    RequestPasswordResetSchema,
    ReviewPrivacyRequestSchema,
    ReviewVideoAnalysisJobSchema,
    TeamSchema,
    PlayerVideoUploadMetadataSchema,
    UpdateTrainingPlanStatusSchema,
    UpdateUserSchema,
    UpdateVideoAnalysisJobSchema,
    VideoSchema,
)
from app.analysis_result_storage import get_analysis_result_storage
from app.avatar_upload import AvatarUploadError, avatar_path, delete_avatar, save_avatar
from app.config import (
    get_app_environment,
    get_auth_cookie_secure,
    get_public_site_origins,
)
from app.database import SessionLocal, get_db
from app.email_service import EmailSendError, send_password_reset_email
from app.data_models import (
    AIAnalysisRecord,
    DataRecord,
    DrillData,
    MatchData,
    TrainingPlanData,
    TeamData,
    VideoData,
    VideoAnalysisJobData,
)
from app.db_models import PlayerDB, UserDB, VideoDB
from app.development_plan import create_development_plan
from app.development_forecast import forecast_development
from app.development_snapshot import build_development_snapshot, calculate_player_age
from app.services.smart_recommendation_service import (
    RecommendationError,
    get_smart_recommendations,
    is_configured as is_smart_recommendations_configured,
)
from app.drill_recommendations import build_drill_recommendations
from app.drill_ranking import rank_drills
from app.drill_upload import DrillUploadError, save_drill_video
from app.match_performance import MatchPerformance
from app.mental_profile import MentalProfile
from app.migration_health import (
    DatabaseSchemaNotCurrent,
    require_database_at_head,
)
from app.physical_profile import PhysicalProfile
from app.player_video_upload import (
    delete_player_video,
    PlayerVideoUploadError,
    save_player_video,
)
from app.player import Player
from app.services.analysis_service import AnalysisService
from app.services.auth_service import (
    AuthService,
    effective_feature_permissions,
    normalize_username,
    utcnow,
    verify_password,
)
from app.services.drill_service import DrillService
from app.services.id_service import next_entity_id
from app.services.data_record_service import DataRecordService
from app.services.match_service import MatchService
from app.services.message_service import MessageService
from app.services.notification_service import NotificationService
from app.services.player_service import PlayerService
from app.services.privacy_service import PrivacyService
from app.services.registration_service import RegistrationService
from app.services.season_service import SeasonService
from app.services.training_plan_service import TrainingPlanService
from app.services.team_service import TeamService
from app.services.video_service import VideoDeletionError, VideoService
from app.services.video_analysis_job_service import VideoAnalysisJobService
from app.technical_profile import TechnicalProfile
from app.video_storage import (
    VideoStorageError,
    get_drill_video_storage,
    get_video_storage,
)
from app.video_analysis_publication import VideoAnalysisPublisher

app = FastAPI(title="Kemet FC")
app.state.auth_session_factory = SessionLocal
logger = logging.getLogger("trainingbuddy.http")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_public_site_origins(),
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)

SESSION_COOKIE_NAME = "trainingbuddy_pilot2_session"
CSRF_COOKIE_NAME = "trainingbuddy_pilot2_csrf"

# --- Login gate ---------------------------------------------------------
# Real accounts (login screen, sessions, CSRF, per-user roles/permissions)
# are enforced below. Set AUTH_DISABLED = True only for a throwaway local
# demo on a fully trusted, single-user machine — never on a shared or
# internet-reachable deployment.
AUTH_DISABLED = False
DISABLED_AUTH_USER = {
    "user_id": "LOCAL_ADMIN",
    "username": "coach",
    "role": "admin",
    "active": True,
    "feature_permissions": sorted(
        {
            "dashboard", "players", "teams", "assessments", "training",
            "videos", "matches", "reports", "calendar",
        }
    ),
    "csrf_token": "",
    "avatar_url": None,
}
# -----------------------------------------------------------------------------
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.on_event("startup")
def ensure_local_admin_user_exists() -> None:
    """When AUTH_DISABLED is on, every request is treated as this synthetic
    admin - but nothing had ever inserted a matching row into the real
    `users` table. Any code path that foreign-key-references the acting
    user (e.g. audit logging) then fails. This creates that row once, at
    startup, so it exists for those references without being a usable
    login (the login route stays bypassed either way)."""
    if not AUTH_DISABLED:
        return

    db = SessionLocal()
    try:
        existing = db.get(UserDB, DISABLED_AUTH_USER["user_id"])
        if existing is not None:
            return

        existing_by_username = (
            db.query(UserDB)
            .filter(UserDB.username == DISABLED_AUTH_USER["username"])
            .first()
        )
        if existing_by_username is not None:
            return

        now = datetime.now()
        db.add(UserDB(
            user_id=DISABLED_AUTH_USER["user_id"],
            username=DISABLED_AUTH_USER["username"],
            password_hash="!disabled-local-admin-not-a-real-login!",
            role=DISABLED_AUTH_USER["role"],
            active=True,
            feature_permissions=DISABLED_AUTH_USER["feature_permissions"],
            failed_login_attempts=0,
            locked_until=None,
            created_at=now,
            updated_at=now,
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("ensure_local_admin_user_exists_failed")
    finally:
        db.close()
PUBLIC_PATHS = {
    "/health",
    "/health/ready",
    "/login",
    "/auth/login",
    "/forgot-password",
    "/auth/password-reset/request",
    "/auth/password-reset/confirm",
    "/auth-client.js",
    "/design-system.css",
    "/logo.png",
    "/favicon.png",
    "/public/registrations",
    "/_debug/jobs",
}
HTML_PAGE_PATHS = {
    "/",
    "/dashboard",
    "/players-dashboard",
    "/player-details",
    "/development-snapshot",
    "/add-player",
    "/teams-dashboard",
    "/team-details",
    "/add-team",
    "/assessments-dashboard",
    "/assessment-details",
    "/add-assessment",
    "/training-plans-dashboard",
    "/training-plan-details",
    "/drill-library",
    "/videos-dashboard",
    "/video-analysis-details",
    "/body-analysis-3d",
    "/add-video",
    "/upload-player-video",
    "/matches-dashboard",
    "/add-match",
    "/reports-dashboard",
    "/calendar-dashboard",
    "/admin/users",
    "/messages-page",
    "/registrations-dashboard",
}

FEATURE_PAGE_PATHS = {
    "/dashboard": "dashboard",
    "/players-dashboard": "players",
    "/player-details": "players",
    "/add-player": "players",
    "/teams-dashboard": "teams",
    "/team-details": "teams",
    "/add-team": "teams",
    "/assessments-dashboard": "assessments",
    "/assessment-details": "assessments",
    "/add-assessment": "assessments",
    "/development-snapshot": "assessments",
    "/training-plans-dashboard": "training",
    "/training-plan-details": "training",
    "/drill-library": "training",
    "/videos-dashboard": "videos",
    "/video-analysis-details": "videos",
    "/body-analysis-3d": "videos",
    "/add-video": "videos",
    "/upload-player-video": "videos",
    "/matches-dashboard": "matches",
    "/add-match": "matches",
    "/reports-dashboard": "reports",
    "/calendar-dashboard": "calendar",
    "/messages-page": "messaging",
    "/registrations-dashboard": "assessments",
    "/registrations": "assessments",
}


def required_feature_for_path(path: str) -> str | None:
    if path in FEATURE_PAGE_PATHS:
        return FEATURE_PAGE_PATHS[path]

    if path.startswith("/analyses"):
        return "assessments"
    if path.startswith("/registrations"):
        return "assessments"
    if path.startswith("/training-plans") or path.startswith("/drills"):
        return "training"
    if path.startswith("/analysis-jobs") or path.startswith("/videos"):
        return "videos"
    if path.startswith("/uploads/avatars"):
        return None
    if path.startswith("/uploads/drills"):
        return "training"
    if path.startswith("/uploads/"):
        return "videos"
    if path.startswith("/matches"):
        return "matches"
    if path.startswith("/teams"):
        return "teams"
    if path.startswith("/messages") or path.startswith("/notifications"):
        return "messaging"
    if path.startswith("/players/") and (
        path.endswith("/analyses")
        or path.endswith("/development-plan")
        or path.endswith("/development-snapshot")
    ):
        return "assessments"
    if path.startswith("/players"):
        return "players"

    return None


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


@app.middleware("http")
async def enforce_authentication(request: Request, call_next):
    path = request.url.path

    if AUTH_DISABLED:
        if path == "/login":
            return RedirectResponse(url="/dashboard", status_code=307)
        request.state.current_user = DISABLED_AUTH_USER
        return await call_next(request)

    if path in PUBLIC_PATHS:
        return await call_next(request)

    token = request.cookies.get(SESSION_COOKIE_NAME)
    authenticated = None

    if token:
        db = request.app.state.auth_session_factory()

        try:
            result = AuthService(db=db).get_session_user(token)

            if result is not None:
                user, session = result
                authenticated = {
                    "user_id": user.user_id,
                    "username": user.username,
                    "role": user.role,
                    "active": user.active,
                    "feature_permissions": effective_feature_permissions(user),
                    "csrf_token": session.csrf_token,
                    "avatar_url": (
                        f"/uploads/avatars/{user.avatar_filename}"
                        if user.avatar_filename
                        else None
                    ),
                }
        finally:
            db.close()

    if authenticated is None:
        if request.method == "GET" and path in HTML_PAGE_PATHS:
            target = quote(str(request.url.path), safe="/")
            return RedirectResponse(
                url=f"/login?next={target}",
                status_code=303,
            )

        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
        )

    request.state.current_user = authenticated

    if authenticated["role"] == "guardian":
        guardian_path_allowed = (
            path in {"/auth/me", "/auth/logout", "/auth/me/password", "/auth/me/avatar"}
            or path.startswith("/uploads/avatars/")
            or path == "/guardian/children"
            or path.startswith("/guardian/children/")
            or path == "/messages-page"
            or path.startswith("/messages")
            or path.startswith("/notifications")
            or (request.method == "GET" and path == "/upload-player-video")
            or (request.method == "POST" and path == "/videos/upload")
            or (
                request.method == "POST"
                and path.startswith("/videos/")
                and path.endswith("/analysis-jobs")
            )
            or (
                request.method in {"POST", "DELETE"}
                and path.startswith("/players/")
                and path.endswith("/photo")
            )
        )

        if not guardian_path_allowed:
            return JSONResponse(
                status_code=403,
                content={"detail": "Guardian access is limited to linked children"},
            )

    if path.startswith("/auth/users") and authenticated["role"] != "admin":
        return JSONResponse(
            status_code=403,
            content={"detail": "Admin access required"},
        )

    if path == "/admin/users" and authenticated["role"] != "admin":
        return JSONResponse(
            status_code=403,
            content={"detail": "Admin access required"},
        )

    if (
        request.method == "PATCH"
        and path.startswith("/analysis-jobs/")
        and authenticated["role"] != "admin"
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "Admin access required"},
        )

    if (
        request.method == "PUT"
        and path.startswith("/analysis-jobs/")
        and path.endswith("/review")
        and authenticated["role"] not in {"admin", "reviewer"}
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "Reviewer access required"},
        )

    if (
        request.method == "DELETE"
        and path.startswith("/analyses/")
        and authenticated["role"] != "admin"
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "Admin access required"},
        )

    if request.method in UNSAFE_METHODS:
        review_request = (
            request.method == "PUT"
            and path.startswith("/analysis-jobs/")
            and path.endswith("/review")
        )
        role = authenticated["role"]

        if (
            role == "reviewer"
            and not review_request
            and path not in {"/auth/logout", "/auth/me/password", "/auth/me/avatar"}
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Write access required"},
            )

        csrf_token = request.headers.get("X-CSRF-Token", "")

        if not hmac.compare_digest(
            csrf_token,
            authenticated["csrf_token"],
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid CSRF token"},
            )

    required_feature = required_feature_for_path(path)

    if (
        required_feature is not None
        and authenticated["role"] != "admin"
        and required_feature not in authenticated["feature_permissions"]
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": f"{required_feature.replace('_', ' ').title()} "
                "access is not enabled for this account"
            },
        )

    return await call_next(request)


@app.middleware("http")
async def add_operational_headers(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_failed request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        raise

    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://i.ytimg.com; "
        "media-src 'self' https:; connect-src 'self'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
    )
    response.headers["Cache-Control"] = "no-store"

    if get_app_environment() == "production":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    logger.info(
        "request_complete request_id=%s method=%s path=%s "
        "status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.post("/auth/login")
def login(login_data: LoginSchema, db: Session = Depends(get_db)):
    service = AuthService(db=db)
    user = service.authenticate(login_data.username, login_data.password)

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password",
        )

    session = service.create_session(user)
    response = JSONResponse(
        content={"user": user_payload(user)},
    )
    max_age = max(
        int((session.expires_at - utcnow()).total_seconds()),
        1,
    )
    secure = get_auth_cookie_secure()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=session.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE_NAME)

    if token:
        AuthService(db=db).delete_session(token)

    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return response


@app.get("/auth/me")
def get_current_user(request: Request):
    user = request.state.current_user
    return {
        "user": {
            "user_id": user["user_id"],
            "username": user["username"],
            "role": user["role"],
            "active": user["active"],
            "feature_permissions": user["feature_permissions"],
            "avatar_url": user.get("avatar_url"),
        }
    }


@app.post("/auth/me/password")
def change_own_password(
    password_data: ChangeOwnPasswordSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.state.current_user["user_id"]
    user = db.get(UserDB, user_id)

    if user is None or not verify_password(
        password_data.current_password, user.password_hash
    ):
        raise HTTPException(
            status_code=400,
            detail="Current password is incorrect",
        )

    try:
        AuthService(db=db).update_user(
            user_id=user_id,
            password=password_data.new_password,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return {"message": "Password updated. Please sign in again."}


@app.post("/auth/me/avatar")
def upload_own_avatar(
    request: Request,
    db: Session = Depends(get_db),
    avatar: UploadFile = File(...),
):
    user_id = request.state.current_user["user_id"]
    user = db.get(UserDB, user_id)

    if user is None:
        raise HTTPException(status_code=404, detail="Account not found")

    try:
        filename = save_avatar(user_id, avatar)
    except AvatarUploadError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error))

    user.avatar_filename = filename
    db.commit()

    return {"avatar_url": f"/uploads/avatars/{filename}"}


@app.delete("/auth/me/avatar")
def delete_own_avatar(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    user = db.get(UserDB, user_id)

    if user is None:
        raise HTTPException(status_code=404, detail="Account not found")

    if user.avatar_filename:
        delete_avatar(user.avatar_filename)
        user.avatar_filename = None
        db.commit()

    return {"message": "Profile picture removed"}


@app.get("/uploads/avatars/{filename}")
def get_uploaded_avatar(filename: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.(jpg|png|webp)", filename):
        raise HTTPException(status_code=404, detail="Avatar not found")

    path = avatar_path(filename)

    if not path.is_file():
        raise HTTPException(status_code=404, detail="Avatar not found")

    return FileResponse(path)


@app.get("/seasons")
def get_seasons(db: Session = Depends(get_db)):
    return [season_payload(season) for season in SeasonService(db=db).list_seasons()]


@app.post("/seasons", status_code=201)
def create_season(
    season_data: CreateSeasonSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    season = SeasonService(db=db).create_season(
        name=season_data.name,
        start_date=season_data.start_date,
        end_date=season_data.end_date,
        make_active=season_data.make_active,
    )
    return season_payload(season)


@app.post("/seasons/{season_id}/activate")
def activate_season(
    season_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    season = SeasonService(db=db).set_active(season_id)

    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")

    return season_payload(season)


@app.get("/notifications")
def get_notifications(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    return [
        notification_payload(notification)
        for notification in NotificationService(db=db).list_for_user(user_id)
    ]


@app.get("/notifications/unread-count")
def get_notifications_unread_count(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    return {"unread_count": NotificationService(db=db).unread_count(user_id)}


@app.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.state.current_user["user_id"]

    if not NotificationService(db=db).mark_read(notification_id, user_id):
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"message": "Notification marked as read"}


@app.post("/notifications/read-all")
def mark_all_notifications_read(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    NotificationService(db=db).mark_all_read(user_id)
    return {"message": "All notifications marked as read"}


@app.get("/messages/recipients")
def get_message_recipients(request: Request, db: Session = Depends(get_db)):
    current_user_id = request.state.current_user["user_id"]
    users = (
        db.query(UserDB)
        .filter(UserDB.active.is_(True), UserDB.user_id != current_user_id)
        .order_by(UserDB.username)
        .all()
    )
    return [
        {"user_id": user.user_id, "username": user.username, "role": user.role}
        for user in users
    ]


@app.get("/messages")
def get_messages(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    return [
        message_payload(message)
        for message in MessageService(db=db).inbox_for_user(user_id)
    ]


@app.get("/messages/unread-count")
def get_messages_unread_count(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    return {"unread_count": MessageService(db=db).unread_count(user_id)}


@app.post("/messages", status_code=201)
def send_message(
    message_data: CreateMessageSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    sender_id = request.state.current_user["user_id"]

    try:
        message = MessageService(db=db).send_message(
            sender_id=sender_id,
            recipient_id=message_data.recipient_id,
            subject=message_data.subject,
            body=message_data.body,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return message_payload(message)


@app.post("/messages/{message_id}/read")
def mark_message_read(
    message_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.state.current_user["user_id"]

    if not MessageService(db=db).mark_read(message_id, user_id):
        raise HTTPException(status_code=404, detail="Message not found")

    return {"message": "Message marked as read"}


@app.get("/search")
def search(q: str = "", db: Session = Depends(get_db)):
    query = q.strip().lower()

    if len(query) < 2:
        return {"players": [], "teams": [], "videos": []}

    players = (
        PlayerService(db=db).get_all_players()
    )
    matching_players = [
        {
            "player_id": p.player_id,
            "name": f"{p.first_name_en} {p.last_name_en}",
            "link": f"/player-details?player_id={p.player_id}",
        }
        for p in players
        if query in f"{p.first_name_en} {p.last_name_en} {p.player_id}".lower()
    ][:5]

    teams = TeamService(db=db).get_all_teams()
    matching_teams = [
        {
            "team_id": t.team_id,
            "name": t.name,
            "link": f"/team-details?team_id={t.team_id}",
        }
        for t in teams
        if query in f"{t.name} {t.team_id}".lower()
    ][:5]

    videos = db.query(VideoDB).all()
    matching_videos = [
        {
            "video_id": v.video_id,
            "name": v.video_id,
            "link": f"/video-analysis-details?job_id={v.video_id}",
        }
        for v in videos
        if query in v.video_id.lower()
    ][:5]

    return {
        "players": matching_players,
        "teams": matching_teams,
        "videos": matching_videos,
    }


@app.get("/messages-page")
def messages_page():
    page = Path(__file__).parent / "app" / "static" / "messages.html"
    return FileResponse(page)


@app.get("/auth/users")
def get_users(db: Session = Depends(get_db)):
    return [user_payload(user) for user in AuthService(db=db).list_users()]


@app.post("/auth/users", status_code=201)
def create_user(
    user_data: CreateUserSchema,
    db: Session = Depends(get_db),
):
    try:
        user = AuthService(db=db).create_user(
            username=user_data.username,
            password=user_data.password,
            role=user_data.role,
            feature_permissions=user_data.feature_permissions,
            email=user_data.email,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return user_payload(user)


@app.patch("/auth/users/{user_id}")
def update_user(
    user_id: str,
    user_data: UpdateUserSchema,
    db: Session = Depends(get_db),
):
    try:
        user = AuthService(db=db).update_user(
            user_id=user_id,
            role=user_data.role,
            active=user_data.active,
            password=user_data.password,
            feature_permissions=user_data.feature_permissions,
            email=user_data.email,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return user_payload(user)


@app.delete("/auth/users/{user_id}")
def delete_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if request.state.current_user["user_id"] == user_id:
        raise HTTPException(
            status_code=400,
            detail="You cannot delete the account you are currently using",
        )

    try:
        deleted = AuthService(db=db).delete_user(user_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    return {"message": "Account deleted"}


@app.get("/login")
def login_page():
    return FileResponse(Path(__file__).parent / "app" / "static" / "login.html")


@app.get("/forgot-password")
def forgot_password_page():
    return FileResponse(
        Path(__file__).parent / "app" / "static" / "forgot_password.html"
    )


@app.post("/auth/password-reset/request")
def request_password_reset(
    reset_data: RequestPasswordResetSchema,
    db: Session = Depends(get_db),
):
    generic_response = {
        "message": (
            "If that account exists and has an email on file, "
            "a 6-digit code has been sent to it."
        ),
    }

    try:
        normalized = normalize_username(reset_data.username)
    except ValueError:
        return generic_response

    user = db.query(UserDB).filter(UserDB.username == normalized).first()

    if user is None or not user.active or not user.email:
        return generic_response

    code = AuthService(db=db).create_password_reset_code(user.user_id)

    try:
        send_password_reset_email(user.email, code)
    except EmailSendError as error:
        logger.error("password_reset_email_failed: %s", error)
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not send the reset email. Contact your "
                "administrator for help."
            ),
        )

    return generic_response


@app.post("/auth/password-reset/confirm")
def confirm_password_reset(
    reset_data: ConfirmPasswordResetSchema,
    db: Session = Depends(get_db),
):
    try:
        normalized = normalize_username(reset_data.username)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    user = db.query(UserDB).filter(UserDB.username == normalized).first()

    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    service = AuthService(db=db)
    valid = service.consume_password_reset_code(
        user.user_id, reset_data.code
    )

    if not valid:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    service.update_user(user_id=user.user_id, password=reset_data.new_password)

    return {"message": "Password updated. You can now sign in."}


@app.get("/admin/users")
def user_management_page(request: Request):
    require_admin(request)
    return FileResponse(
        Path(__file__).parent / "app" / "static" / "user_management.html"
    )


@app.get("/players/{player_id}/guardian-consents")
def get_guardian_consents(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    return [
        consent_payload(consent)
        for consent in PrivacyService(db=db).list_player_consents(player_id)
    ]


@app.post("/players/{player_id}/guardian-consents", status_code=201)
def grant_guardian_consent(
    player_id: str,
    consent_data: GuardianConsentSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        consent = PrivacyService(db=db).grant_consent(
            player_id=player_id,
            recorded_by_user_id=request.state.current_user["user_id"],
            **{
                **consent_data.model_dump(),
                "consent_id": consent_data.consent_id
                or next_entity_id(db, "consent"),
            },
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return consent_payload(consent)


@app.delete("/guardian-consents/{consent_id}")
def withdraw_guardian_consent(
    consent_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    consent = PrivacyService(db=db).withdraw_consent(
        consent_id=consent_id,
        actor_user_id=request.state.current_user["user_id"],
    )

    if consent is None:
        raise HTTPException(status_code=404, detail="Consent not found")

    return consent_payload(consent)


@app.post("/guardian-player-links", status_code=201)
def create_guardian_player_link(
    link_data: GuardianPlayerLinkSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)

    try:
        link = PrivacyService(db=db).link_guardian_to_player(
            guardian_user_id=link_data.guardian_user_id,
            player_id=link_data.player_id,
            actor_user_id=request.state.current_user["user_id"],
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return {
        "guardian_user_id": link.guardian_user_id,
        "player_id": link.player_id,
        "created_at": link.created_at,
        "created_by_user_id": link.created_by_user_id,
    }


@app.get("/guardian/children")
def get_guardian_children(
    request: Request,
    db: Session = Depends(get_db),
):
    players = PrivacyService(db=db).list_guardian_players(
        request.state.current_user["user_id"]
    )
    return [
        {
            "player_id": player.player_id,
            "name": f"{player.first_name_en} {player.last_name_en}",
            "date_of_birth": player.date_of_birth,
            "team_id": player.team_id,
        }
        for player in players
    ]


@app.get("/guardian/children/{player_id}/development-snapshot")
def get_guardian_child_snapshot(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    privacy = PrivacyService(db=db)
    guardian_user_id = request.state.current_user["user_id"]

    if not privacy.guardian_can_access_player(guardian_user_id, player_id):
        raise HTTPException(status_code=404, detail="Linked child not found")

    privacy.record_event(
        actor_user_id=guardian_user_id,
        action="guardian_snapshot_viewed",
        resource_type="player",
        resource_id=player_id,
    )
    return get_player_development_snapshot(player_id=player_id, db=db)


@app.get("/guardian/children/{player_id}/data-export")
def export_guardian_child_data(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return PrivacyService(db=db).build_child_export(
            guardian_user_id=request.state.current_user["user_id"],
            player_id=player_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))


def privacy_request_payload(privacy_request) -> dict:
    return {
        "request_id": privacy_request.request_id,
        "guardian_user_id": privacy_request.guardian_user_id,
        "player_id": privacy_request.player_id,
        "request_type": privacy_request.request_type,
        "status": privacy_request.status,
        "reason": privacy_request.reason,
        "created_at": privacy_request.created_at,
        "reviewed_at": privacy_request.reviewed_at,
        "reviewed_by_user_id": privacy_request.reviewed_by_user_id,
        "review_notes": privacy_request.review_notes,
    }


@app.post(
    "/guardian/children/{player_id}/deletion-requests",
    status_code=201,
)
def request_guardian_child_deletion(
    player_id: str,
    request_data: ChildDeletionRequestSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        privacy_request = PrivacyService(db=db).create_deletion_request(
            request_id=request_data.request_id
            or next_entity_id(db, "privacy_request"),
            guardian_user_id=request.state.current_user["user_id"],
            player_id=player_id,
            reason=request_data.reason,
        )
    except ValueError as error:
        status_code = (
            404 if str(error) == "Linked child not found" else 409
        )
        raise HTTPException(status_code=status_code, detail=str(error))

    return privacy_request_payload(privacy_request)


@app.get("/privacy-requests")
def get_privacy_requests(
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    return [
        privacy_request_payload(item)
        for item in PrivacyService(db=db).list_privacy_requests()
    ]


@app.patch("/privacy-requests/{request_id}")
def review_privacy_request(
    request_id: str,
    review_data: ReviewPrivacyRequestSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    try:
        service = PrivacyService(db=db)

        if review_data.status == "completed":
            privacy_request = service.complete_deletion_request(
                request_id=request_id,
                reviewer_user_id=request.state.current_user["user_id"],
                review_notes=review_data.review_notes,
            )
        else:
            privacy_request = service.review_privacy_request(
                request_id=request_id,
                status=review_data.status,
                reviewer_user_id=request.state.current_user["user_id"],
                review_notes=review_data.review_notes,
            )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))

    if privacy_request is None:
        raise HTTPException(status_code=404, detail="Privacy request not found")

    return privacy_request_payload(privacy_request)


@app.get("/auth-client.js")
def auth_client_script():
    return FileResponse(
        Path(__file__).parent / "app" / "static" / "auth_client.js",
        media_type="application/javascript",
    )


@app.get("/design-system.css")
def design_system_stylesheet():
    return FileResponse(
        Path(__file__).parent / "app" / "static" / "design-system.css",
        media_type="text/css",
    )


@app.get("/logo.png")
def app_logo():
    return FileResponse(
        Path(__file__).parent / "app" / "static" / "logo.png",
        media_type="image/png",
    )


@app.get("/favicon.png")
def app_favicon():
    return FileResponse(
        Path(__file__).parent / "app" / "static" / "favicon.png",
        media_type="image/png",
    )


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/ready")
def readiness_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )

    schema = "not_checked"

    if get_app_environment() == "production":
        try:
            require_database_at_head(db.connection())
        except DatabaseSchemaNotCurrent:
            logger.exception("readiness_schema_mismatch")
            raise HTTPException(
                status_code=503,
                detail="Database schema is not current",
            )

        schema = "current"

    return {"status": "ready", "database": "ok", "schema": schema}



@app.post("/videos", status_code=201)
def create_video(
    video_data: VideoSchema,
    db: Session = Depends(get_db),
):
    service = VideoService(db=db)
    data = video_data.model_dump()
    data["video_id"] = data["video_id"] or next_entity_id(db, "video")
    video = VideoData(**data)
    service.add_video(video)
    return video






@app.post("/videos/upload", status_code=201)
def upload_player_video(
    request: Request,
    metadata: str = Form(...),
    video: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        upload_data = (
            PlayerVideoUploadMetadataSchema.model_validate(
                json.loads(metadata)
            )
        )
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid video metadata",
        )

    player_service = PlayerService(db=db)

    player = player_service.get_player(upload_data.player_id)

    if player is None:
        raise HTTPException(
            status_code=404,
            detail="Player not found",
        )

    require_guardian_player_access(request, db, player.player_id)

    record_service = DataRecordService(db=db)
    video_service = VideoService(db=db)

    upload_data.video_id = (
        upload_data.video_id or next_entity_id(db, "video")
    )
    upload_data.record_id = (
        upload_data.record_id or next_entity_id(db, "record")
    )

    if record_service.get_record(upload_data.record_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Data record already exists",
        )

    if video_service.get_video(upload_data.video_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Video already exists",
        )

    try:
        saved = save_player_video(
            video=video,
            video_id=upload_data.video_id,
        )
    except PlayerVideoUploadError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        )

    created_at = datetime.now()

    record = DataRecord(
        record_id=upload_data.record_id,
        player_id=upload_data.player_id,
        source_type="video",
        created_at=created_at,
        data_type="player_video",
        status="completed",
        original_file_path=saved.public_path,
        analysis_id=f"PENDING_{upload_data.video_id}",
        schema_version=upload_data.schema_version,
        created_by=upload_data.created_by,
    )

    player_video = VideoData(
        video_id=upload_data.video_id,
        record_id=upload_data.record_id,
        video_type=upload_data.video_type,
        duration_seconds=upload_data.duration_seconds,
        recorded_at=created_at,
        session_id=upload_data.session_id,
        location_id=upload_data.location_id,
        capture_device=upload_data.capture_device,
        resolution=upload_data.resolution,
        frame_rate_fps=upload_data.frame_rate_fps,
        file_size_mb=saved.file_size_mb,
        file_format=saved.file_format,
        file_path=saved.public_path,
        checksum=saved.checksum,
        original_preserved=True,
        ai_processing_status="pending",
        ai_processed_at=None,
        ai_model_version=None,
        ai_confidence_score=None,
        requires_human_review=False,
        review_reason="",
        human_review_status="not_required",
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        analysis_approved=False,
        approved_by=None,
        approved_at=None,
    )

    try:
        record_service.add_record(record)
        video_service.add_video(player_video)
    except Exception:
        db.rollback()
        record_service.delete_record(upload_data.record_id)
        delete_player_video(saved.filename)
        raise

    PrivacyService(db=db).record_event(
        actor_user_id=request.state.current_user["user_id"],
        action="minor_video_uploaded" if is_minor(player.date_of_birth) else "video_uploaded",
        resource_type="video",
        resource_id=player_video.video_id,
        details={"player_id": player.player_id},
    )

    return player_video


@app.get("/uploads/videos/{filename}")
def get_uploaded_player_video(
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        video_id = Path(filename).stem
        video = db.get(VideoDB, video_id)

        if video is None or Path(video.file_path).name != filename:
            raise VideoStorageError("Video not found", status_code=404)

        storage = get_video_storage()
        path = storage.local_path(filename)

        PrivacyService(db=db).record_event(
            actor_user_id=request.state.current_user["user_id"],
            action="video_accessed",
            resource_type="video",
            resource_id=video_id,
            details={},
        )

        if path is not None:
            return FileResponse(path, filename=path.name)

        download_url = storage.create_download_url(filename)

        if download_url is None:
            raise VideoStorageError("Video not found", status_code=404)

        return RedirectResponse(download_url, status_code=307)
    except (PlayerVideoUploadError, VideoStorageError) as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        )


@app.get("/videos")
def get_all_videos(
    db: Session = Depends(get_db),
):
    service = VideoService(db=db)
    return service.get_all_videos()


@app.get("/_debug/jobs")
def _debug_jobs(
    token: str, requeue: str = "", db: Session = Depends(get_db)
):
    if token != "kemet-debug-2026":
        raise HTTPException(status_code=404)

    from app.db_models import VideoAnalysisJobDB

    requeue_log = []
    if requeue:
        service = VideoAnalysisJobService(db=db)
        for job_id in [j.strip() for j in requeue.split(",") if j.strip()]:
            existing = db.get(VideoAnalysisJobDB, job_id)
            if existing is None:
                requeue_log.append(f"{job_id}: not found")
                continue
            try:
                if existing.status == "processing":
                    service.transition_job(
                        job_id=job_id,
                        status="failed",
                        error_message="Cancelled: orphaned by worker restart",
                    )
                result = service.transition_job(
                    job_id=job_id, status="queued"
                )
                requeue_log.append(
                    f"{job_id}: {'requeued' if result else 'not found'}"
                )
            except ValueError as error:
                requeue_log.append(f"{job_id}: error: {error}")

    jobs = (
        db.query(VideoAnalysisJobDB)
        .order_by(VideoAnalysisJobDB.created_at.desc())
        .limit(10)
        .all()
    )

    return {"requeue_log": requeue_log, "jobs": [
        {
            "job_id": j.job_id,
            "video_id": j.video_id,
            "status": j.status,
            "attempt_count": j.attempt_count,
            "max_attempts": j.max_attempts,
            "error_message": j.error_message,
            "created_at": str(j.created_at),
            "started_at": str(j.started_at) if j.started_at else None,
            "completed_at": str(j.completed_at) if j.completed_at else None,
        }
        for j in jobs
    ]}


@app.post("/videos/{video_id}/analysis-jobs", status_code=201)
def create_video_analysis_job(
    video_id: str,
    job_data: CreateVideoAnalysisJobSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    video_service = VideoService(db=db)

    if video_service.get_video(video_id) is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_guardian_video_access(request, db, video_id)

    job_service = VideoAnalysisJobService(db=db)

    job_id = job_data.job_id or next_entity_id(db, "analysis_job")

    if job_service.get_job(job_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Analysis job already exists",
        )

    job = VideoAnalysisJobData(
        job_id=job_id,
        video_id=video_id,
        analysis_type=job_data.analysis_type,
        status="queued",
        created_at=datetime.now(),
        started_at=None,
        completed_at=None,
        progress_percent=0.0,
        attempt_count=0,
        max_attempts=job_data.max_attempts,
        model_name=None,
        model_version=None,
        result_path=None,
        error_message=None,
        target_track_id=job_data.target_track_id,
    )
    job_service.add_job(job)
    return job


@app.get("/videos/{video_id}/analysis-jobs")
def get_video_analysis_jobs(
    video_id: str,
    db: Session = Depends(get_db),
):
    if VideoService(db=db).get_video(video_id) is None:
        raise HTTPException(status_code=404, detail="Video not found")

    return VideoAnalysisJobService(db=db).get_video_jobs(video_id)


@app.get("/analysis-jobs/{job_id}")
def get_video_analysis_job(
    job_id: str,
    db: Session = Depends(get_db),
):
    job = VideoAnalysisJobService(db=db).get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    return job


@app.get("/analysis-jobs/{job_id}/result")
def get_video_analysis_result(
    job_id: str,
    db: Session = Depends(get_db),
):
    job = VideoAnalysisJobService(db=db).get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Analysis job is not completed",
        )

    try:
        payload = get_analysis_result_storage().read(
            job_id,
            job.result_path,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return Response(content=payload, media_type="application/json")


@app.put("/analysis-jobs/{job_id}/review")
def review_video_analysis_job(
    job_id: str,
    review_data: ReviewVideoAnalysisJobSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    service = VideoAnalysisJobService(db=db)

    try:
        job = service.review_job(
            job_id=job_id,
            reviewed_by=request.state.current_user["user_id"],
            **review_data.model_dump(),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))

    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    VideoAnalysisPublisher(db=db).apply_human_review(
        job_id=job.job_id,
        video_id=job.video_id,
        review_status=job.review_status,
        reviewed_by=job.reviewed_by,
        review_notes=job.review_notes,
    )

    return job


@app.patch("/analysis-jobs/{job_id}")
def update_video_analysis_job(
    job_id: str,
    update_data: UpdateVideoAnalysisJobSchema,
    db: Session = Depends(get_db),
):
    service = VideoAnalysisJobService(db=db)

    try:
        job = service.transition_job(
            job_id=job_id,
            **update_data.model_dump(),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error))

    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    return job


@app.get("/videos/{video_id}")
def get_video(
    video_id: str,
    db: Session = Depends(get_db),
):
    service = VideoService(db=db)
    video = service.get_video(video_id)

    if video is None:
        raise HTTPException(
            status_code=404,
            detail="Video not found",
        )

    return video


@app.put("/videos/{video_id}")
def update_video(
    video_id: str,
    video_data: VideoSchema,
    db: Session = Depends(get_db),
):
    if video_data.video_id is not None and video_id != video_data.video_id:
        raise HTTPException(
            status_code=400,
            detail="Video ID cannot be changed",
        )

    service = VideoService(db=db)
    data = video_data.model_dump()
    data["video_id"] = video_id
    video = VideoData(**data)
    updated = service.update_video(video)

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Video not found",
        )

    return video


@app.delete("/videos/{video_id}")
def delete_video(
    video_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    service = VideoService(db=db)
    try:
        deleted = service.delete_video(video_id)
    except VideoDeletionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Video not found",
        )

    return {"message": "Video deleted"}


@app.post("/players", status_code=201)
def create_player(
    player_data: PlayerSchema,
    db: Session = Depends(get_db),
):
    service = PlayerService(db=db)

    if player_data.team_id is not None:
        team_service = TeamService(db=db)

        if team_service.get_team(player_data.team_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Team not found",
            )

    player_id = player_data.player_id or next_entity_id(db, "player")
    player = Player(
        player_id=player_id,
        first_name_ar=player_data.first_name_ar,
        last_name_ar=player_data.last_name_ar,
        first_name_en=player_data.first_name_en,
        last_name_en=player_data.last_name_en,
        date_of_birth=player_data.date_of_birth,
        sex=player_data.sex,
        team_id=player_data.team_id,
        physical_profile=PhysicalProfile(
            **player_data.physical_profile.model_dump()
        ),
        technical_profile=TechnicalProfile(
            **player_data.technical_profile.model_dump()
        ),
        mental_profile=MentalProfile(
            **player_data.mental_profile.model_dump()
        ),
        match_performance=MatchPerformance(
            **player_data.match_performance.model_dump()
        ),
        created_at=utcnow(),
    )

    service.add_player(player)

    return player


@app.post("/public/registrations", status_code=201)
def submit_public_registration(
    payload: PublicRegistrationSchema,
    db: Session = Depends(get_db),
):
    consents = payload.consents

    if not all([
        consents.parent_consent,
        consents.liability_waiver,
        consents.emergency_medical,
        consents.photo_video,
        consents.privacy_policy,
        consents.terms,
        consents.technology_ai_consent,
    ]):
        raise HTTPException(
            status_code=422,
            detail="All consents are required to register",
        )

    registration = RegistrationService(db=db).create_registration(payload)

    return {"registration_id": registration.registration_id}


@app.get("/registrations")
def list_registrations(db: Session = Depends(get_db)):
    return [
        registration_payload(registration)
        for registration in RegistrationService(db=db).list_registrations()
    ]


@app.delete("/registrations/{registration_id}")
def delete_registration(
    registration_id: str,
    db: Session = Depends(get_db),
):
    deleted = RegistrationService(db=db).delete_registration(registration_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Registration not found",
        )

    return {"message": "Registration deleted"}


@app.get("/players/{player_id}")
def get_player(
    player_id: str,
    db: Session = Depends(get_db),
):
    service = PlayerService(db=db)
    player = service.get_player(player_id)

    if player is None:
        raise HTTPException(
            status_code=404,
            detail="Player not found",
        )

    return player


@app.post("/players/{player_id}/photo")
def upload_player_photo(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
    photo: UploadFile = File(...),
):
    require_guardian_player_access(request, db, player_id)
    player_db = db.get(PlayerDB, player_id)

    if player_db is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        filename = save_avatar(player_id, photo)
    except AvatarUploadError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error))

    player_db.photo_filename = filename
    db.commit()

    return {"photo_url": f"/uploads/avatars/{filename}"}


@app.delete("/players/{player_id}/photo")
def delete_player_photo(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_guardian_player_access(request, db, player_id)
    player_db = db.get(PlayerDB, player_id)

    if player_db is None:
        raise HTTPException(status_code=404, detail="Player not found")

    if player_db.photo_filename:
        delete_avatar(player_db.photo_filename)
        player_db.photo_filename = None
        db.commit()

    return {"message": "Player photo removed"}


@app.get("/players")
def get_all_players(
    db: Session = Depends(get_db),
):
    service = PlayerService(db=db)
    return service.get_all_players()

@app.put("/players/{player_id}")
def update_player(
    player_id: str,
    player_data: PlayerSchema,
    db: Session = Depends(get_db),
):
    service = PlayerService(db=db)
    existing_player = service.get_player(player_id)

    if existing_player is None:
        raise HTTPException(
            status_code=404,
            detail="Player not found",
        )

    if player_data.team_id is not None:
        team_service = TeamService(db=db)

        if team_service.get_team(player_data.team_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Team not found",
            )

    updated_player = Player(
        player_id=player_id,
        first_name_ar=player_data.first_name_ar,
        last_name_ar=player_data.last_name_ar,
        first_name_en=player_data.first_name_en,
        last_name_en=player_data.last_name_en,
        date_of_birth=player_data.date_of_birth,
        sex=player_data.sex,
        team_id=player_data.team_id,
        physical_profile=PhysicalProfile(
            **player_data.physical_profile.model_dump()
        ),
        technical_profile=TechnicalProfile(
            **player_data.technical_profile.model_dump()
        ),
        mental_profile=MentalProfile(
            **player_data.mental_profile.model_dump()
        ),
        match_performance=MatchPerformance(
            **player_data.match_performance.model_dump()
        ),
        created_at=existing_player.created_at,
        photo_filename=existing_player.photo_filename,
    )

    service.update_player(updated_player)

    return updated_player

@app.delete("/players/{player_id}")
def delete_player(
    player_id: str,
    db: Session = Depends(get_db),
):
    service = PlayerService(db=db)
    deleted = service.delete_player(player_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Player not found",
        )

    return {"message": "Player deleted"}
@app.post("/matches", status_code=201)
def create_match(
    match_data: MatchSchema,
    db: Session = Depends(get_db),
):
    team_service = TeamService(db=db)

    for team_id in (
        match_data.home_team_id,
        match_data.away_team_id,
    ):
        if team_service.get_team(team_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Team not found",
            )

    service = MatchService(db=db)
    data = match_data.model_dump()
    data["match_id"] = data["match_id"] or next_entity_id(db, "match")
    match = MatchData(**data)
    service.add_match(match)
    return match


@app.get("/matches")
def get_all_matches(
    db: Session = Depends(get_db),
):
    service = MatchService(db=db)
    return service.get_all_matches()


@app.get("/matches/{match_id}")
def get_match(
    match_id: str,
    db: Session = Depends(get_db),
):
    service = MatchService(db=db)
    match = service.get_match(match_id)

    if match is None:
        raise HTTPException(
            status_code=404,
            detail="Match not found",
        )

    return match


@app.put("/matches/{match_id}")
def update_match(
    match_id: str,
    match_data: MatchSchema,
    db: Session = Depends(get_db),
):
    service = MatchService(db=db)
    existing_match = service.get_match(match_id)

    if existing_match is None:
        raise HTTPException(
            status_code=404,
            detail="Match not found",
        )

    team_service = TeamService(db=db)

    for team_id in (
        match_data.home_team_id,
        match_data.away_team_id,
    ):
        if team_service.get_team(team_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Team not found",
            )

    updated_data = match_data.model_dump()
    updated_data["match_id"] = match_id

    updated_match = MatchData(**updated_data)
    service.update_match(updated_match)

    return updated_match


@app.delete("/matches/{match_id}")
def delete_match(
    match_id: str,
    db: Session = Depends(get_db),
):
    service = MatchService(db=db)
    deleted = service.delete_match(match_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Match not found",
        )

    return {"message": "Match deleted"}


@app.get("/analyses")
def get_all_analyses(
    db: Session = Depends(get_db),
):
    service = AnalysisService(db=db)
    return service.get_all_analyses()

@app.get("/analyses/{analysis_id}")
def get_analysis(
    analysis_id: str,
    db: Session = Depends(get_db),
):
    service = AnalysisService(db=db)
    analysis = service.get_analysis(analysis_id)

    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found",
        )

    return analysis


@app.get("/analyses/{analysis_id}/smart-recommendations")
def get_analysis_smart_recommendations(
    analysis_id: str,
    db: Session = Depends(get_db),
):
    analysis = AnalysisService(db=db).get_analysis(analysis_id)

    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    if not is_smart_recommendations_configured():
        raise HTTPException(
            status_code=404,
            detail="Smart recommendations are not configured",
        )

    player = PlayerService(db=db).get_player(analysis.player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        focus_areas = get_smart_recommendations(
            player_name=f"{player.first_name_en} {player.last_name_en}",
            age=calculate_player_age(player.date_of_birth),
            weaknesses=analysis.weaknesses,
            strengths=analysis.strengths,
        )
    except RecommendationError as error:
        raise HTTPException(status_code=502, detail=str(error))

    return {"focus_areas": focus_areas}


@app.post("/analyses/{analysis_id}/development-forecast")
def create_analysis_development_forecast(
    analysis_id: str,
    criteria: DevelopmentForecastSchema,
    db: Session = Depends(get_db),
):
    analysis = AnalysisService(db=db).get_analysis(analysis_id)

    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if analysis.requires_human_review and not analysis.approved:
        raise HTTPException(
            status_code=409,
            detail="Analysis must be approved before forecasting",
        )
    if not analysis.weaknesses:
        raise HTTPException(
            status_code=409,
            detail="Analysis has no weaknesses to forecast",
        )

    return forecast_development(
        weaknesses=analysis.weaknesses,
        confidence_score=analysis.confidence_score,
        **criteria.model_dump(),
    )

@app.post("/analyses", status_code=201)
def create_analysis(
    analysis_data: AnalysisSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    player_service = PlayerService(db=db)

    if player_service.get_player(analysis_data.player_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Player not found",
        )

    service = AnalysisService(db=db)
    data = analysis_data.model_dump()
    data["analysis_id"] = data["analysis_id"] or next_entity_id(
        db, "analysis"
    )
    if request.state.current_user["role"] != "admin":
        data.update({
            "human_review_status": (
                "pending" if data["requires_human_review"] else "not_required"
            ),
            "reviewed_by": None,
            "reviewed_at": None,
            "review_notes": None,
            "approved": False,
            "approved_by": None,
            "approved_at": None,
        })
    analysis = AIAnalysisRecord(**data)
    service.add_analysis(analysis)
    notify_coaching_staff(
        db,
        exclude_user_id=request.state.current_user["user_id"],
        type="assessment",
        title="New assessment recorded",
        body=f"Assessment {analysis.analysis_id} was added for player {analysis.player_id}.",
        link=f"/assessment-details?analysis_id={analysis.analysis_id}",
    )

    return analysis

@app.put("/analyses/{analysis_id}")
def update_analysis(
    analysis_id: str,
    analysis_data: AnalysisSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    service = AnalysisService(db=db)
    existing_analysis = service.get_analysis(analysis_id)

    if existing_analysis is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found",
        )

    updated_data = analysis_data.model_dump()
    updated_data["analysis_id"] = analysis_id

    if request.state.current_user["role"] != "admin":
        for field in (
            "requires_human_review",
            "human_review_status",
            "reviewed_by",
            "reviewed_at",
            "review_notes",
            "approved",
            "approved_by",
            "approved_at",
        ):
            updated_data[field] = getattr(existing_analysis, field)

    updated_analysis = AIAnalysisRecord(**updated_data)
    service.update_analysis(updated_analysis)

    return updated_analysis

@app.delete("/analyses/{analysis_id}")
def delete_analysis(
    analysis_id: str,
    db: Session = Depends(get_db),
):
    service = AnalysisService(db=db)
    deleted = service.delete_analysis(analysis_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found",
        )

    return {"message": "Analysis deleted"}

@app.post("/analyses/{analysis_id}/drill-recommendations")
def recommend_drills_for_analysis(
    analysis_id: str,
    criteria: AnalysisDrillRecommendationSchema,
    db: Session = Depends(get_db),
):
    analysis_service = AnalysisService(db=db)
    analysis = analysis_service.get_analysis(analysis_id)

    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found",
        )

    require_approved_analysis(analysis)

    age = criteria.age

    if age is None:
        player_service = PlayerService(db=db)
        player = player_service.get_player(analysis.player_id)

        if player is None:
            raise HTTPException(
                status_code=404,
                detail="Player not found",
            )

        today = date.today()
        age = today.year - player.date_of_birth.year

        if (today.month, today.day) < (
            player.date_of_birth.month,
            player.date_of_birth.day,
        ):
            age -= 1

    drill_service = DrillService(db=db)
    drills = drill_service.get_all_drills()

    return build_drill_recommendations(
        weaknesses=analysis.weaknesses,
        drills=drills,
        age=age,
        player_difficulty=criteria.player_difficulty,
        target_duration=criteria.target_duration,
        available_equipment=criteria.available_equipment,
    )


@app.post(
    "/analyses/{analysis_id}/training-plans",
    status_code=201,
)
def create_training_plan_from_analysis(
    analysis_id: str,
    plan_data: CreateTrainingPlanSchema,
    db: Session = Depends(get_db),
):
    analysis_service = AnalysisService(db=db)
    analysis = analysis_service.get_analysis(analysis_id)

    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found",
        )

    require_approved_analysis(analysis)

    plan_service = TrainingPlanService(db=db)

    plan_id = plan_data.plan_id or next_entity_id(db, "training_plan")

    if plan_service.get_plan(plan_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Training plan already exists",
        )

    recommendation_criteria = AnalysisDrillRecommendationSchema(
        player_difficulty=plan_data.player_difficulty,
        target_duration=plan_data.target_duration,
        available_equipment=plan_data.available_equipment,
    )

    recommendations = recommend_drills_for_analysis(
        analysis_id=analysis_id,
        criteria=recommendation_criteria,
        db=db,
    )

    plan = TrainingPlanData(
        plan_id=plan_id,
        player_id=analysis.player_id,
        analysis_id=analysis.analysis_id,
        created_at=datetime.now(),
        status="draft",
        player_difficulty=plan_data.player_difficulty,
        target_duration=plan_data.target_duration,
        available_equipment=plan_data.available_equipment,
        recommendations=recommendations,
    )

    plan_service.add_plan(plan)
    return plan


@app.get("/players/{player_id}/analyses")
def get_analyses_by_player(
    player_id: str,
    db: Session = Depends(get_db),
):
    service = AnalysisService(db=db)
    return service.get_analyses_by_player(player_id)
@app.get("/players/{player_id}/development-plan")
def get_development_plan(
    player_id: str,
    db: Session = Depends(get_db),
):
    service = PlayerService(db=db)
    player = service.get_player(player_id)

    if player is None:
        raise HTTPException(
            status_code=404,
            detail="Player not found",
        )

    return create_development_plan(player)


@app.get("/players/{player_id}/development-snapshot")
def get_player_development_snapshot(
    player_id: str,
    player_difficulty: str | None = None,
    target_duration: int | None = None,
    available_equipment: str | None = None,
    db: Session = Depends(get_db),
):
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(
            status_code=404,
            detail="Player not found",
        )

    equipment = None

    if available_equipment is not None:
        equipment = [
            item.strip()
            for item in available_equipment.split(",")
            if item.strip()
        ]

    return build_development_snapshot(
        player=player,
        analyses=AnalysisService(db=db).get_analyses_by_player(player_id),
        drills=DrillService(db=db).get_all_drills(),
        training_plans=TrainingPlanService(db=db).get_plans_by_player(
            player_id
        ),
        player_difficulty=player_difficulty,
        target_duration=target_duration,
        available_equipment=equipment,
    )


@app.get("/players/{player_id}/smart-recommendations")
def get_player_smart_recommendations(
    player_id: str,
    db: Session = Depends(get_db),
):
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    if not is_smart_recommendations_configured():
        raise HTTPException(
            status_code=404,
            detail="Smart recommendations are not configured",
        )

    analyses = AnalysisService(db=db).get_analyses_by_player(player_id)
    latest_analysis = max(
        analyses, key=lambda analysis: analysis.created_at, default=None
    )

    if latest_analysis is not None:
        weaknesses = latest_analysis.weaknesses
        strengths = latest_analysis.strengths
        source = "analysis"
    else:
        profile_scores = [
            ("Speed", player.physical_profile.speed),
            ("Stamina", player.physical_profile.stamina),
            ("Passing", player.technical_profile.passing),
            ("Dribbling", player.technical_profile.dribbling),
            ("Ball Control", player.technical_profile.ball_control),
            ("Game IQ", player.mental_profile.decision_making),
        ]
        ranked = sorted(profile_scores, key=lambda item: item[1])
        weaknesses = [
            {"attribute": name, "score": score}
            for name, score in ranked[:3]
        ]
        strengths = [
            {"attribute": name, "score": score}
            for name, score in ranked[-3:]
        ]
        source = "profile"

    try:
        focus_areas = get_smart_recommendations(
            player_name=f"{player.first_name_en} {player.last_name_en}",
            age=calculate_player_age(player.date_of_birth),
            weaknesses=weaknesses,
            strengths=strengths,
        )
    except RecommendationError as error:
        raise HTTPException(status_code=502, detail=str(error))

    return {"focus_areas": focus_areas, "source": source}



@app.get("/training-plans")
def get_all_training_plans(
    db: Session = Depends(get_db),
):
    service = TrainingPlanService(db=db)
    return service.get_all_plans()


@app.get("/training-plans/{plan_id}")
def get_training_plan(
    plan_id: str,
    db: Session = Depends(get_db),
):
    service = TrainingPlanService(db=db)
    plan = service.get_plan(plan_id)

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Training plan not found",
        )

    return plan


@app.patch("/training-plans/{plan_id}/status")
def update_training_plan_status(
    plan_id: str,
    status_data: UpdateTrainingPlanStatusSchema,
    db: Session = Depends(get_db),
):
    service = TrainingPlanService(db=db)
    plan = service.get_plan(plan_id)

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Training plan not found",
        )

    plan.status = status_data.status
    service.update_plan(plan)
    return plan


@app.post("/drills", status_code=201)
def create_drill(
    drill_data: DrillSchema,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)
    data = drill_data.model_dump()
    data["drill_id"] = data["drill_id"] or next_entity_id(db, "drill")
    drill = DrillData(**data)
    service.add_drill(drill)
    return drill


@app.get("/drills/{drill_id}")
def get_drill(
    drill_id: str,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)
    drill = service.get_drill(drill_id)

    if drill is None:
        raise HTTPException(
            status_code=404,
            detail="Drill not found",
        )

    return drill


@app.get("/drills")
def get_all_drills(
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)
    return service.get_all_drills()


@app.put("/drills/{drill_id}")
def update_drill(
    drill_id: str,
    drill_data: DrillSchema,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)

    existing_drill = service.get_drill(drill_id)

    if existing_drill is None:
        raise HTTPException(
            status_code=404,
            detail="Drill not found",
        )

    updated_data = drill_data.model_dump()
    updated_data["drill_id"] = drill_id

    drill = DrillData(**updated_data)
    service.update_drill(drill)

    if (
        existing_drill.video_url.startswith("/uploads/drills/")
        and existing_drill.video_url != drill.video_url
    ):
        try:
            get_drill_video_storage().delete(
                Path(existing_drill.video_url).name
            )
        except VideoStorageError:
            logger.warning(
                "Could not remove replaced drill video %s",
                existing_drill.video_url,
            )

    return drill


@app.delete("/drills/{drill_id}")
def delete_drill(
    drill_id: str,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)
    drill = service.get_drill(drill_id)

    if drill is None:
        raise HTTPException(
            status_code=404,
            detail="Drill not found",
        )

    if drill.video_url.startswith("/uploads/drills/"):
        filename = Path(drill.video_url).name
        try:
            get_drill_video_storage().delete(filename)
        except VideoStorageError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=str(error),
            ) from error

    deleted = service.delete_drill(drill_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Drill not found",
        )

    return {"message": "Drill deleted"}


@app.post("/drills/recommendations")
def recommend_drills(
    criteria: DrillRecommendationSchema,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)

    eligible_drills = [
        asdict(drill)
        for drill in service.get_drills_for_age(criteria.age)
        if drill.active
    ]

    return rank_drills(
        drills=eligible_drills,
        weakness=criteria.weakness,
        weakness_score=criteria.weakness_score,
        player_difficulty=criteria.player_difficulty,
        target_duration=criteria.target_duration,
        available_equipment=criteria.available_equipment,
    )


@app.post("/drills/upload", status_code=201)
def upload_drill_video(
    metadata: str = Form(...),
    video: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        payload = json.loads(metadata)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid drill metadata JSON",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Drill metadata must be an object",
        )

    validation_payload = dict(payload)
    validation_payload["drill_id"] = (
        validation_payload.get("drill_id")
        or next_entity_id(db, "drill")
    )
    validation_payload["video_url"] = "/pending.mp4"

    try:
        drill = DrillData(**validation_payload)
        video_url = save_drill_video(video, drill.drill_id)
    except DrillUploadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        )

    drill.video_url = video_url

    service = DrillService(db=db)
    service.add_drill(drill)

    return drill


@app.get("/uploads/drills/{filename}")
def get_uploaded_drill_video(filename: str):
    try:
        storage = get_drill_video_storage()
        video_path = storage.local_path(filename)

        if video_path is not None:
            return FileResponse(video_path, filename=video_path.name)

        download_url = storage.create_download_url(filename)

        if download_url is None:
            raise VideoStorageError("Video not found", status_code=404)

        return RedirectResponse(download_url, status_code=307)
    except (DrillUploadError, VideoStorageError) as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        )


























@app.get("/calendar-dashboard")
def calendar_dashboard():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "calendar.html"
    )
    return FileResponse(page)


@app.get("/reports-dashboard")
def reports_dashboard():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "reports.html"
    )
    return FileResponse(page)


@app.get("/add-match")
def add_match_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "add_match.html"
    )
    return FileResponse(page)


@app.get("/matches-dashboard")
def matches_dashboard():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "matches.html"
    )
    return FileResponse(page)


@app.get("/add-video")
def add_video_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "add_training_video.html"
    )
    return FileResponse(page)


@app.get("/upload-player-video")
def upload_player_video_page():
    page = Path(__file__).parent / "app" / "static" / "add_video.html"
    return FileResponse(page)


@app.get("/videos-dashboard")
def videos_dashboard():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "videos.html"
    )
    return FileResponse(page)


@app.get("/video-analysis-details")
def video_analysis_details_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "video_analysis_details.html"
    )
    return FileResponse(page)


@app.get("/body-analysis-3d")
def body_analysis_3d_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "body_analysis_3d.html"
    )
    return FileResponse(page)


@app.get("/add-player")
def add_player_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "add_player.html"
    )
    return FileResponse(page)


@app.get("/add-assessment")
def add_assessment_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "add_assessment.html"
    )
    return FileResponse(page)


@app.get("/assessment-details")
def assessment_details_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "assessment_details.html"
    )
    return FileResponse(page)


@app.get("/assessments-dashboard")
def assessments_dashboard():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "assessments.html"
    )
    return FileResponse(page)


@app.get("/player-details")
def player_details_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "player_details.html"
    )
    return FileResponse(page)


@app.get("/development-snapshot")
def development_snapshot_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "development_snapshot.html"
    )
    return FileResponse(page)


@app.get("/players-dashboard")
def players_dashboard():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "players.html"
    )
    return FileResponse(page)


@app.get("/registrations-dashboard")
def registrations_dashboard():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "registrations.html"
    )
    return FileResponse(page)


@app.get("/dashboard")
def training_buddy_dashboard():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "dashboard.html"
    )
    return FileResponse(page)


@app.get("/")
def home_page():
    return RedirectResponse("/dashboard", status_code=307)


@app.get("/training-plan-details")
def training_plan_details_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "training_plan_details.html"
    )
    return FileResponse(page)


@app.get("/training-plans-dashboard")
def training_plans_dashboard():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "training_plans.html"
    )
    return FileResponse(page)


@app.get("/drill-library")
def get_drill_library_page():
    return FileResponse(
        Path(__file__).parent / "app/static/drill_library.html",
        media_type="text/html",
    )



@app.post("/teams", status_code=201)
def create_team(
    team_data: TeamSchema,
    db: Session = Depends(get_db),
):
    data = team_data.model_dump()
    data["team_id"] = data["team_id"] or next_entity_id(db, "team")
    data["created_at"] = utcnow()
    team = TeamData(**data)
    service = TeamService(db=db)
    service.add_team(team)
    return team



@app.get("/teams/{team_id}")
def get_team(
    team_id: str,
    db: Session = Depends(get_db),
):
    service = TeamService(db=db)
    team = service.get_team(team_id)

    if team is None:
        raise HTTPException(
            status_code=404,
            detail="Team not found",
        )

    return team


@app.get("/teams")
def get_all_teams(
    db: Session = Depends(get_db),
):
    service = TeamService(db=db)
    return service.get_all_teams()


@app.put("/teams/{team_id}")
def update_team(
    team_id: str,
    team_data: TeamSchema,
    db: Session = Depends(get_db),
):
    if team_data.team_id is not None and team_id != team_data.team_id:
        raise HTTPException(
            status_code=400,
            detail="Team ID mismatch",
        )

    service = TeamService(db=db)
    existing_team = service.get_team(team_id)

    if existing_team is None:
        raise HTTPException(
            status_code=404,
            detail="Team not found",
        )

    data = team_data.model_dump()
    data["team_id"] = team_id
    data["created_at"] = existing_team.created_at
    team = TeamData(**data)

    if not service.update_team(team):
        raise HTTPException(
            status_code=404,
            detail="Team not found",
        )

    return team


@app.delete("/teams/{team_id}")
def delete_team(
    team_id: str,
    db: Session = Depends(get_db),
):
    service = TeamService(db=db)
    player_service = PlayerService(db=db)

    if player_service.get_players_by_team(team_id):
        raise HTTPException(
            status_code=409,
            detail="Team still has assigned players",
        )

    if not service.delete_team(team_id):
        raise HTTPException(
            status_code=404,
            detail="Team not found",
        )

    return {"message": "Team deleted"}



@app.get("/teams-dashboard")
def teams_dashboard_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "teams.html"
    )
    return FileResponse(page)



@app.get("/add-team")
def add_team_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "add_team.html"
    )
    return FileResponse(page)



@app.get("/team-details")
def team_details_page():
    page = (
        Path(__file__).parent
        / "app"
        / "static"
        / "team_details.html"
    )
    return FileResponse(page)
