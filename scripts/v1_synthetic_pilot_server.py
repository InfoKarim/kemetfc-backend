"""Start a throwaway server for a SYNTHETIC field test of the V1 sprint screen.

It never touches the real database or the real upload folders: it builds a brand-new
SQLite database and upload folders in a temporary directory, migrates it, seeds only
synthetic people, prints the sign-in details once, and serves until you press Ctrl+C
(the temporary directory is then deleted).

    python scripts/v1_synthetic_pilot_server.py                 # this computer only
    python scripts/v1_synthetic_pilot_server.py --host <your-LAN-IP>   # phone on the same Wi-Fi

Serving to a phone over Wi-Fi uses plain http on your local network: use a network you
trust, only for this synthetic data, and stop the server when the test is over. Do not
put this behind a public tunnel, and do not use it with real children.
"""
import argparse
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def seed(tmp: Path) -> dict[str, str]:
    """Create the synthetic people. Imports happen here, after the scratch environment is set."""
    from app.database import SessionLocal
    from app.db_models import PlayerDB, UserDB
    from app.services.auth_service import hash_password, utcnow
    from app.services.privacy_service import PrivacyService
    from app.v1.definitions import ensure_metric_definitions

    db_url = os.environ["DATABASE_URL"]
    if str(tmp) not in db_url:                      # belt and braces: never seed anything else
        raise SystemExit("Refusing to seed: the database is not the temporary one.")
    passwords = {name: secrets.token_urlsafe(12) for name in ("coach1", "coach2", "reviewer")}
    db = SessionLocal()
    now = utcnow()
    passwords["pilot.admin"] = secrets.token_urlsafe(24)       # not printed: an admin is not needed to test
    for user_id, name, role in (("PILOT_COACH1", "coach1", "coach"), ("PILOT_COACH2", "coach2", "coach"),
                                ("PILOT_REVIEWER", "reviewer", "reviewer"), ("PILOT_ADMIN", "pilot.admin", "admin")):
        db.add(UserDB(user_id=user_id, username=f"t.{name}" if name != "pilot.admin" else name,
                      password_hash=hash_password(passwords[name] + "-Pilot1!"),
                      role=role, active=True, feature_permissions=None, created_at=now, updated_at=now))
    db.commit()
    profile = dict(
        physical_profile={"height_cm": 140.0, "weight_kg": 35.0, "dominant_foot": "right", "speed": 70.0,
                          "acceleration": 70.0, "agility": 70.0, "stamina": 70.0, "strength": 60.0},
        technical_profile={"ball_control": 70.0, "dribbling": 70.0, "passing": 70.0, "shooting": 70.0, "finishing": 70.0},
        mental_profile={"decision_making": 70.0, "concentration": 70.0, "composure": 70.0, "positioning": 70.0, "vision": 70.0},
        match_performance={"minutes_played": 0, "goals": 0, "assists": 0, "shots": 0, "shots_on_target": 0,
                           "passes_attempted": 0, "passes_completed": 0, "tackles": 0, "interceptions": 0, "rating": 0.0})
    for player_id, last in (("PILOT_PLAYER_A", "Player A"), ("PILOT_PLAYER_B", "Player B")):
        db.add(PlayerDB(player_id=player_id, first_name_ar="x", last_name_ar="y", first_name_en="Test",
                        last_name_en=last, date_of_birth=date(2016, 1, 1), sex="male", **profile))
    db.commit()
    ensure_metric_definitions(db)
    # Only Test Player A has the consent the screen requires; Test Player B shows the refusal.
    PrivacyService(db).grant_consent("PILOT_CONSENT_A", "PILOT_PLAYER_A", "Synthetic Guardian",
                                     "synthetic@example.invalid", "in_person", ["performance_tracking"], "PILOT_ADMIN")
    db.close()
    return {f"t.{name}": password + "-Pilot1!" for name, password in passwords.items() if name != "pilot.admin"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this computer only)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, signal.default_int_handler)   # a plain "kill" also cleans up
    tmp = Path(tempfile.mkdtemp(prefix="kemet-v1-pilot-"))
    env = {**os.environ, "APP_ENV": "test", "AUTH_COOKIE_SECURE": "false",
           "DATABASE_URL": f"sqlite:///{tmp / 'pilot.db'}",
           "PLAYER_VIDEO_UPLOAD_DIR": str(tmp / "videos"), "DRILL_UPLOAD_DIR": str(tmp / "drills"),
           "VIDEO_ANALYSIS_OUTPUT_DIR": str(tmp / "analysis")}
    try:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PROJECT_ROOT, env=env,
                       check=True, capture_output=True)
        os.environ.update(env)
        sys.path.insert(0, str(PROJECT_ROOT))
        os.chdir(PROJECT_ROOT)
        logins = seed(tmp)
        print("\nSYNTHETIC FIELD TEST SERVER (temporary data, deleted when you stop it)")
        print(f"  Open:  http://{args.host}:{args.port}/v1/sprint")
        print("  Sign-in (username / password), shown once:")
        for name, password in logins.items():
            print(f"    {name:12s} {password}")
        print("  Players: Test Player A (consent recorded), Test Player B (no consent)")
        print("  Stop with Ctrl+C.\n", flush=True)
        subprocess.run([sys.executable, "-m", "uvicorn", "main:app", "--host", args.host, "--port", str(args.port)],
                       cwd=PROJECT_ROOT, env=env, check=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
