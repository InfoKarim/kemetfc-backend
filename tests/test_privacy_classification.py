"""Privacy table-classification guard.

Fails when a table that references a player is not classified in
app/privacy_classification.py, so a new child-data table cannot be added
without a decision about its export and deletion.
"""
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table

import app.db_models  # noqa: F401  (registers every table on Base.metadata)
from app.database import Base
from app.privacy_classification import (
    REVIEWED_NOT_PLAYER_LINKED,
    TABLE_CLASSIFICATIONS,
    TableClassification,
    player_linked_tables,
    unclassified_tables,
    validate_classification,
)
from main import app


def synthetic_metadata() -> MetaData:
    """A tiny schema shaped like the real one, for proving the guard fails."""
    meta = MetaData()
    Table("players", meta, Column("player_id", String, primary_key=True))
    Table("users", meta, Column("user_id", String, primary_key=True))
    return meta


# ---- TEST 12 ------------------------------------------------------------------

def test_every_player_linked_table_is_classified_and_nothing_is_stale():
    assert unclassified_tables(Base.metadata) == []
    stale = sorted(set(TABLE_CLASSIFICATIONS) - set(Base.metadata.tables))
    assert stale == [], f"classified tables that no longer exist: {stale}"


def test_every_classification_is_well_formed():
    problems = [p for name, entry in TABLE_CLASSIFICATIONS.items()
                for p in validate_classification(name, entry)]
    assert problems == []


def test_the_guard_finds_direct_and_indirect_links_but_not_unrelated_tables():
    linked = player_linked_tables(Base.metadata)
    assert {"player_assessments", "tracking_sessions", "videos"} <= linked
    # Indirect: no player_id, reached only through a player-linked table.
    assert {"tracking_samples", "tracking_events", "video_analysis_jobs", "refunds"} <= linked
    # Tables that only reference users or teams are not swept in.
    assert not ({"users", "teams", "auth_sessions", "notifications", "audit_events"} & linked)


def test_reviewed_non_player_linked_tables_still_exist():
    assert set(REVIEWED_NOT_PLAYER_LINKED) <= set(Base.metadata.tables)
    assert not set(REVIEWED_NOT_PLAYER_LINKED) & set(TABLE_CLASSIFICATIONS)


# ---- TEST 13 ------------------------------------------------------------------

def test_a_new_unclassified_player_table_fails_the_guard():
    meta = synthetic_metadata()
    assert unclassified_tables(meta, {"players": TABLE_CLASSIFICATIONS["players"]}) == []

    Table("v1_manual_measurements", meta,
          Column("id", Integer, primary_key=True),
          Column("player_id", String, ForeignKey("players.player_id")))
    assert unclassified_tables(meta, {"players": TABLE_CLASSIFICATIONS["players"]}) == [
        "v1_manual_measurements"
    ]


def test_an_indirect_only_unclassified_table_fails_the_guard():
    meta = synthetic_metadata()
    Table("assessments_x", meta, Column("id", Integer, primary_key=True),
          Column("player_id", String, ForeignKey("players.player_id")))
    Table("trial_rows_x", meta, Column("id", Integer, primary_key=True),
          Column("assessment_id", Integer, ForeignKey("assessments_x.id")))
    known = {"players": TABLE_CLASSIFICATIONS["players"],
             "assessments_x": TABLE_CLASSIFICATIONS["player_metrics"]}
    assert unclassified_tables(meta, known) == ["trial_rows_x"]


def test_any_v1_table_must_be_classified_even_without_a_player_column():
    meta = synthetic_metadata()
    Table("v1_station_definitions", meta, Column("id", Integer, primary_key=True))
    assert unclassified_tables(meta, {"players": TABLE_CLASSIFICATIONS["players"]}) == [
        "v1_station_definitions"
    ]


def test_a_table_that_only_references_users_does_not_fail_the_guard():
    meta = synthetic_metadata()
    Table("staff_notes_x", meta, Column("id", Integer, primary_key=True),
          Column("user_id", String, ForeignKey("users.user_id")))
    assert unclassified_tables(meta, {"players": TABLE_CLASSIFICATIONS["players"]}) == []


# ---- TESTS 14, 15: policy-pending tables are explicit, not silently ignored ----

def test_registration_table_is_explicitly_policy_pending():
    entry = TABLE_CLASSIFICATIONS["assessment_registrations"]
    assert (entry.erase, entry.status) == ("RETAINED_WITH_REASON", "POLICY_PENDING")
    assert entry.export == "NOT_EXPORTED"
    assert "field handling" in entry.reason


def test_billing_tables_are_explicitly_policy_pending():
    billing = ["subscriptions", "payments", "manual_payments", "refunds",
               "player_memberships", "promo_code_redemptions"]
    for name in billing:
        entry = TABLE_CLASSIFICATIONS[name]
        assert (entry.erase, entry.status) == ("RETAINED_WITH_REASON", "POLICY_PENDING"), name
        assert entry.reason.strip(), name
    assert "not cancelled" in TABLE_CLASSIFICATIONS["subscriptions"].reason


def test_dataset_entries_are_flagged_as_pending_and_external_copies_are_not_claimed():
    entry = TABLE_CLASSIFICATIONS["ml_dataset_entries"]
    assert entry.status == "POLICY_PENDING"
    assert "never deleted" in entry.reason


# ---- TEST 16: the legacy hard-delete pattern is not carried into V1 ------------

def test_legacy_assessment_delete_is_marked_and_not_for_v1():
    paths = app.openapi()["paths"]
    # Still the legacy staff hard-delete route; S0.6 does not change it.
    assert "delete" in paths["/player-assessments/{assessment_id}"]
    for name in ("player_assessments", "player_metrics"):
        entry = TABLE_CLASSIFICATIONS[name]
        assert entry.legacy_not_for_v1 and "LEGACY - NOT FOR V1" in entry.reason
    assert not any(n.startswith("v1_") for n in TABLE_CLASSIFICATIONS)


def test_a_v1_entry_must_be_privilege_only_and_not_legacy():
    good = TableClassification("EXPORTED", "DELETED", "IMPLEMENTED", "",
                               privileged_deletion_only=True)
    assert validate_classification("v1_manual_measurements", good) == []
    legacy_style = TableClassification("EXPORTED", "DELETED", "IMPLEMENTED", "",
                                       legacy_not_for_v1=True)
    problems = validate_classification("v1_manual_measurements", legacy_style)
    assert any("legacy" in p for p in problems)
    assert any("privacy workflow" in p for p in problems)
    bad_value = TableClassification("MAYBE", "DELETED", "IMPLEMENTED", "x",
                                    privileged_deletion_only=True)
    assert validate_classification("v1_x", bad_value)


def test_v1_tables_have_no_delete_route_yet():
    delete_paths = [p for p, ops in app.openapi()["paths"].items() if "delete" in ops]
    assert delete_paths  # the schema is really being read
    assert not any("v1" in p.lower() for p in delete_paths)
