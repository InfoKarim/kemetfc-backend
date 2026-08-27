from app.data_models import TeamData


def test_valid_team_data():
    team = TeamData(
        team_id="TEAM_U10",
        name="TrainingBuddy U10",
        age_group="U10",
        coach_name="Coach Ahmed",
        season_id="2026-2027",
        active=True,
    )

    assert team.team_id == "TEAM_U10"
    assert team.age_group == "U10"
    assert team.active is True



def test_team_required_fields_cannot_be_empty():
    import pytest

    fields = [
        "team_id",
        "name",
        "age_group",
        "coach_name",
        "season_id",
    ]

    valid_data = {
        "team_id": "TEAM_U10",
        "name": "TrainingBuddy U10",
        "age_group": "U10",
        "coach_name": "Coach Ahmed",
        "season_id": "2026-2027",
        "active": True,
    }

    for field in fields:
        invalid_data = valid_data.copy()
        invalid_data[field] = "   "

        with pytest.raises(ValueError):
            TeamData(**invalid_data)



def test_team_rejects_invalid_age_group():
    import pytest

    with pytest.raises(ValueError):
        TeamData(
            team_id="TEAM_INVALID",
            name="Invalid Team",
            age_group="ten",
            coach_name="Coach Ahmed",
            season_id="2026-2027",
            active=True,
        )
