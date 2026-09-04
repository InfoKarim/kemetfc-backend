from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api_schemas import CreateMessageSchema
from app.database import get_db
from app.db_models import UserDB, VideoDB
from app.dependencies import message_payload, notification_payload
from app.services.message_service import MessageService
from app.services.notification_service import NotificationService
from app.services.player_service import PlayerService
from app.services.team_service import TeamService

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/notifications")
def get_notifications(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    return [
        notification_payload(notification)
        for notification in NotificationService(db=db).list_for_user(user_id)
    ]


@router.get("/notifications/unread-count")
def get_notifications_unread_count(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    return {"unread_count": NotificationService(db=db).unread_count(user_id)}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.state.current_user["user_id"]

    if not NotificationService(db=db).mark_read(notification_id, user_id):
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"message": "Notification marked as read"}


@router.post("/notifications/read-all")
def mark_all_notifications_read(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    NotificationService(db=db).mark_all_read(user_id)
    return {"message": "All notifications marked as read"}


@router.get("/messages/recipients")
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


@router.get("/messages")
def get_messages(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    return [
        message_payload(message)
        for message in MessageService(db=db).inbox_for_user(user_id)
    ]


@router.get("/messages/unread-count")
def get_messages_unread_count(request: Request, db: Session = Depends(get_db)):
    user_id = request.state.current_user["user_id"]
    return {"unread_count": MessageService(db=db).unread_count(user_id)}


@router.post("/messages", status_code=201)
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


@router.post("/messages/{message_id}/read")
def mark_message_read(
    message_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.state.current_user["user_id"]

    if not MessageService(db=db).mark_read(message_id, user_id):
        raise HTTPException(status_code=404, detail="Message not found")

    return {"message": "Message marked as read"}


@router.get("/search")
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


@router.get("/messages-page")
def messages_page():
    return FileResponse(STATIC_DIR / "messages.html")
