from app.data_models import DrillData


def test_valid_drill_data():
    drill = DrillData(
        drill_id="DRILL001",
        name="Scanning Before Receiving",
        category="Vision",
        description="Player scans before receiving the ball.",
        min_age=7,
        max_age=13,
        difficulty="beginner",
        duration_minutes=10,
        equipment=["ball", "cones"],
        video_url="/drills/scanning_before_receiving.mp4",
        active=True,
    )

    assert drill.drill_id == "DRILL001"
    assert drill.category == "Vision"
    assert drill.min_age == 7
    assert drill.max_age == 13
    assert drill.active is True
import pytest


def test_drill_id_cannot_be_empty():
    with pytest.raises(ValueError):
        DrillData(
            drill_id="",
            name="Scanning Before Receiving",
            category="Vision",
            description="Player scans before receiving the ball.",
            min_age=7,
            max_age=13,
            difficulty="beginner",
            duration_minutes=10,
            equipment=["ball", "cones"],
            video_url="/drills/scanning_before_receiving.mp4",
            active=True,
        )

def test_drill_name_cannot_be_empty():
    with pytest.raises(ValueError):
        DrillData(
            drill_id="DRILL001",
            name="",
            category="Vision",
            description="Player scans before receiving the ball.",
            min_age=7,
            max_age=13,
            difficulty="beginner",
            duration_minutes=10,
            equipment=["ball", "cones"],
            video_url="/drills/scanning_before_receiving.mp4",
            active=True,
        )
def test_drill_min_age_cannot_be_greater_than_max_age():
    with pytest.raises(ValueError):
        DrillData(
            drill_id="DRILL001",
            name="Scanning Before Receiving",
            category="Vision",
            description="Player scans before receiving the ball.",
            min_age=14,
            max_age=13,
            difficulty="beginner",
            duration_minutes=10,
            equipment=["ball", "cones"],
            video_url="/drills/scanning_before_receiving.mp4",
            active=True,
        )

def test_drill_invalid_difficulty():
    with pytest.raises(ValueError):
        DrillData(
            drill_id="DRILL001",
            name="Scanning Before Receiving",
            category="Vision",
            description="Player scans before receiving the ball.",
            min_age=7,
            max_age=13,
            difficulty="impossible",
            duration_minutes=10,
            equipment=["ball", "cones"],
            video_url="/drills/scanning_before_receiving.mp4",
            active=True,
        )

def test_drill_duration_must_be_positive():
    with pytest.raises(ValueError):
        DrillData(
            drill_id="DRILL001",
            name="Scanning Before Receiving",
            category="Vision",
            description="Player scans before receiving the ball.",
            min_age=7,
            max_age=13,
            difficulty="beginner",
            duration_minutes=0,
            equipment=["ball", "cones"],
            video_url="/drills/scanning_before_receiving.mp4",
            active=True,
        )

def test_drill_category_cannot_be_empty():
    with pytest.raises(ValueError):
        DrillData(
            drill_id="DRILL001",
            name="Scanning Before Receiving",
            category="",
            description="Player scans before receiving the ball.",
            min_age=7,
            max_age=13,
            difficulty="beginner",
            duration_minutes=10,
            equipment=["ball", "cones"],
            video_url="/drills/scanning_before_receiving.mp4",
            active=True,
        )


def test_drill_video_url_cannot_be_empty():
    with pytest.raises(ValueError):
        DrillData(
            drill_id="DRILL001",
            name="Scanning Before Receiving",
            category="Vision",
            description="Player scans before receiving the ball.",
            min_age=7,
            max_age=13,
            difficulty="beginner",
            duration_minutes=10,
            equipment=["ball", "cones"],
            video_url="",
            active=True,
        )


def test_drill_age_must_be_positive():
    with pytest.raises(ValueError):
        DrillData(
            drill_id="DRILL001",
            name="Scanning Before Receiving",
            category="Vision",
            description="Player scans before receiving the ball.",
            min_age=0,
            max_age=13,
            difficulty="beginner",
            duration_minutes=10,
            equipment=["ball", "cones"],
            video_url="/drills/scanning_before_receiving.mp4",
            active=True,
        )




def test_drill_video_url_rejects_unsupported_scheme():
    with pytest.raises(ValueError):
        DrillData(
            drill_id="DRILL_UNSAFE_URL",
            name="Unsafe URL Drill",
            category="Vision",
            description="Unsafe URL test.",
            min_age=7,
            max_age=13,
            difficulty="beginner",
            duration_minutes=10,
            equipment=["ball"],
            video_url="javascript:alert(1)",
            active=True,
        )


def test_drill_accepts_facebook_reel_url():
    drill = DrillData(
        drill_id="DRILL_FACEBOOK_REEL",
        name="Facebook Reel Drill",
        category="Vision",
        description="Training hosted on Facebook.",
        min_age=7,
        max_age=13,
        difficulty="beginner",
        duration_minutes=10,
        equipment=["ball"],
        video_url="https://www.facebook.com/reel/123456789",
        active=True,
    )

    assert drill.video_url == (
        "https://www.facebook.com/reel/123456789"
    )
