"""Idempotent, order-preserving tracking telemetry ingest (samples + events).

Written BEFORE the implementation (tests-first). A failure here means the
behavior is missing, not that the test is wrong. Several tests pass today
on purpose: they pin behavior that must be PRESERVED (legacy payloads,
authorization, ingest after completion).

CONTRACT UNDER TEST
-------------------
Samples  POST /tracking/sessions/{id}/samples
  request : each sample may carry `sequence_number` (int >= 1, monotonic
            per session, assigned once by the client, unchanged on retry).
            Samples without it are LEGACY and keep today's behavior.
  identity: UNIQUE(session_id, sequence_number), enforced by the database.
  content : "same content" = the client-authored telemetry fields
            (t_seconds, player_/ball_ bbox/center/confidence/track_id,
            pose_keypoints, gimbal_state, tracking_mode, tracking_status)
            after applying the schema defaults. Server-generated fields
            (sample_id) and the identity itself are not compared.
  ATOMIC  : a batch is all-or-nothing with respect to identity conflicts.
  201     : every submitted sequence is either new (inserted) or already
            stored with IDENTICAL content (duplicate). Body:
            {"ingested": n_new_rows (legacy samples included),
             "duplicates": n_identical_already_stored,
             "acknowledged_sequences": [every submitted sequence, sorted]}
            `acknowledged_sequences` lists exactly the submitted sequences
            that are now durably accounted for. It never contains a
            sequence that was not in the request, so it can never imply an
            unverified sequence is durable because a higher one is.
  409     : ANY submitted sequence already stored with DIFFERENT content
            (or two different contents for one sequence inside the batch).
            NOTHING from the request is inserted (legacy samples
            included). Body: {"detail", "mismatched_sequences": [...],
            "conflicts": [{"sequence_number", "differing_fields": [...]}]}
            and NO `acknowledged_sequences`.

Events   POST /tracking/sessions/{id}/events
  request : optional `client_event_id` (str, stable across retries) and
            `client_occurred_at` (ISO-8601, device time).
  identity: UNIQUE(session_id, client_event_id).
  201     : first receipt. Body keeps `occurred_at` (SERVER receipt time,
            unchanged semantics) and adds `client_event_id`,
            `client_occurred_at` and `duplicate: false`.
  200     : identical retry. Same `event_id`, `duplicate: true`.
  409     : same client_event_id, different content; stored event unchanged.
  no client_event_id -> legacy behavior (always inserts).

Late telemetry: samples are still accepted after /complete; when a late
batch inserts at least one new row, the cached feature set is invalidated.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import (
    TrackingEventDB,
    TrackingFeatureSetDB,
    TrackingSampleDB,
    UserDB,
)
from app.services.auth_service import hash_password, utcnow
from main import CSRF_COOKIE_NAME, app
from tests.test_tracking import create_session, create_test_player, make_sample


ingest_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(ingest_engine, "connect")
def _enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


IngestSessionLocal = sessionmaker(bind=ingest_engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=ingest_engine)


def _override_get_db():
    db = IngestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_user(user_id, username, password, role):
    db = IngestSessionLocal()
    now = utcnow()
    db.add(UserDB(
        user_id=user_id,
        username=username,
        password_hash=hash_password(password),
        role=role,
        active=True,
        email=f"{username}@example.com" if role == "guardian" else None,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.close()


def _login(username, password):
    logged_in = TestClient(app)
    response = logged_in.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    logged_in.headers.update({"X-CSRF-Token": logged_in.cookies.get(CSRF_COOKIE_NAME)})
    return logged_in


@pytest.fixture(scope="module")
def client():
    _add_user("ING_COACH", "ing.coach", "IngCoachPassword123!", "coach")
    _add_user("ING_OTHER", "ing.other", "IngOtherPassword123!", "coach")
    _add_user("ING_GUARD", "ing.guardian", "IngGuardianPassword123!", "guardian")

    previous_override = app.dependency_overrides.get(get_db)
    previous_session_factory = app.state.auth_session_factory
    app.dependency_overrides[get_db] = _override_get_db
    app.state.auth_session_factory = IngestSessionLocal

    coach = _login("ing.coach", "IngCoachPassword123!")
    create_test_player(coach, "ING_PLAYER")
    yield coach

    if previous_override is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous_override
    app.state.auth_session_factory = previous_session_factory


@pytest.fixture()
def session_id(client):
    return create_session(client, "ING_PLAYER")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def seq_sample(seq, **overrides):
    """A deterministic sample whose content is a pure function of `seq`."""
    sample = make_sample(seq * 0.1)
    sample["sequence_number"] = seq
    sample.update(overrides)
    return sample


def post_samples(client, session_id, samples):
    return client.post(f"/tracking/sessions/{session_id}/samples", json={"samples": samples})


def sample_rows(session_id):
    db = IngestSessionLocal()
    try:
        return (
            db.query(TrackingSampleDB)
            .filter(TrackingSampleDB.session_id == session_id)
            .order_by(TrackingSampleDB.sample_id.asc())
            .all()
        )
    finally:
        db.close()


def stored_sequences(session_id):
    return sorted(row.sequence_number for row in sample_rows(session_id) if row.sequence_number is not None)


def event_rows(session_id):
    db = IngestSessionLocal()
    try:
        return db.query(TrackingEventDB).filter(TrackingEventDB.session_id == session_id).all()
    finally:
        db.close()


def parse_utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# 1-3. Idempotent sample ingest
# ---------------------------------------------------------------------------

def test_first_sequenced_batch_inserts_normally(client, session_id):
    response = post_samples(client, session_id, [seq_sample(n) for n in range(1, 6)])

    assert response.status_code == 201
    body = response.json()
    assert body["ingested"] == 5
    assert body["duplicates"] == 0
    assert body["acknowledged_sequences"] == [1, 2, 3, 4, 5]
    assert stored_sequences(session_id) == [1, 2, 3, 4, 5]


def test_exact_same_batch_twice_is_stored_once(client, session_id):
    batch = [seq_sample(n) for n in range(100, 151)]

    first = post_samples(client, session_id, batch)
    retry = post_samples(client, session_id, batch)   # the "response was lost" retry

    assert first.status_code == 201 and first.json()["ingested"] == 51
    assert retry.status_code == 201, "a retry of a committed batch is a success, not an error"
    assert retry.json()["ingested"] == 0
    assert retry.json()["duplicates"] == 51
    assert retry.json()["acknowledged_sequences"] == list(range(100, 151))
    assert stored_sequences(session_id) == list(range(100, 151))
    assert len(sample_rows(session_id)) == 51


def test_partially_overlapping_batch_inserts_only_missing_sequences(client, session_id):
    post_samples(client, session_id, [seq_sample(n) for n in range(1, 6)])

    response = post_samples(client, session_id, [seq_sample(n) for n in range(4, 9)])

    assert response.status_code == 201
    body = response.json()
    assert body["ingested"] == 3          # 6, 7, 8
    assert body["duplicates"] == 2        # 4, 5
    assert body["acknowledged_sequences"] == [4, 5, 6, 7, 8]   # exactly the submitted ones
    assert stored_sequences(session_id) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_same_sequence_within_one_batch_is_stored_once(client, session_id):
    response = post_samples(client, session_id, [seq_sample(1), seq_sample(1), seq_sample(2)])

    assert response.status_code == 201
    assert response.json()["ingested"] == 2
    assert response.json()["duplicates"] == 1
    assert stored_sequences(session_id) == [1, 2]


# ---------------------------------------------------------------------------
# 4-5. Session scoping and ordering
# ---------------------------------------------------------------------------

def test_same_sequence_in_different_sessions_is_allowed(client):
    first_session = create_session(client, "ING_PLAYER")
    second_session = create_session(client, "ING_PLAYER")

    assert post_samples(client, first_session, [seq_sample(n) for n in range(1, 4)]).status_code == 201
    second = post_samples(client, second_session, [seq_sample(n) for n in range(1, 4)])

    assert second.status_code == 201
    assert second.json()["ingested"] == 3
    assert stored_sequences(first_session) == [1, 2, 3]
    assert stored_sequences(second_session) == [1, 2, 3]


def test_sequence_numbers_and_their_content_are_preserved_in_order(client, session_id):
    # Arrival order differs from sequence order (a retry can deliver an earlier
    # sequence later). Every stored row must keep its own sequence and content.
    post_samples(client, session_id, [seq_sample(n) for n in (4, 5, 6)])
    post_samples(client, session_id, [seq_sample(n) for n in (1, 2, 3)])

    rows = sorted(sample_rows(session_id), key=lambda row: row.sequence_number)

    assert [row.sequence_number for row in rows] == [1, 2, 3, 4, 5, 6]
    for row in rows:
        assert row.t_seconds == pytest.approx(row.sequence_number * 0.1)
        assert row.player_center == [pytest.approx(0.1 + 0.01 * row.sequence_number * 0.1), 0.5]


# ---------------------------------------------------------------------------
# 6. Legacy compatibility
# ---------------------------------------------------------------------------

def test_legacy_payload_without_sequence_number_remains_compatible(client, session_id):
    legacy = [make_sample(float(n)) for n in range(3)]

    first = post_samples(client, session_id, legacy)
    second = post_samples(client, session_id, legacy)

    assert first.status_code == 201 and first.json()["ingested"] == 3
    # Legacy clients have no identity to dedupe on: behavior is unchanged.
    assert second.status_code == 201 and second.json()["ingested"] == 3
    assert len(sample_rows(session_id)) == 6
    assert first.json()["acknowledged_sequences"] == []   # nothing carried an identity to acknowledge


def test_mixed_legacy_and_sequenced_batch_is_handled_per_sample(client, session_id):
    response = post_samples(client, session_id, [make_sample(0.0), seq_sample(1), seq_sample(2)])

    assert response.status_code == 201
    assert response.json()["ingested"] == 3
    assert response.json()["acknowledged_sequences"] == [1, 2]
    assert len(sample_rows(session_id)) == 3


def test_invalid_sequence_numbers_are_rejected(client, session_id):
    assert post_samples(client, session_id, [seq_sample(0)]).status_code == 422
    assert post_samples(client, session_id, [seq_sample(-4)]).status_code == 422
    assert post_samples(client, session_id, [seq_sample(1, sequence_number="abc")]).status_code == 422
    assert sample_rows(session_id) == []


# ---------------------------------------------------------------------------
# 7. Content mismatch: atomic, detectable, diagnosable
# ---------------------------------------------------------------------------

def row_snapshot(session_id):
    """Every stored column of every sample row, keyed by sample_id."""
    return {
        row.sample_id: {column.name: getattr(row, column.name) for column in TrackingSampleDB.__table__.columns}
        for row in sample_rows(session_id)
    }


def test_batch_with_one_mismatched_sequence_inserts_zero_rows(client, session_id):
    # The retry-loop scenario: 100-110 are stored; the client re-sends 100-120
    # and its copy of 110 differs. Nothing from the request may be committed,
    # or the client would re-send the same conflicting batch forever.
    post_samples(client, session_id, [seq_sample(n) for n in range(100, 111)])
    before = row_snapshot(session_id)

    response = post_samples(
        client, session_id,
        [seq_sample(n, t_seconds=999.0) if n == 110 else seq_sample(n) for n in range(100, 121)],
    )

    assert response.status_code == 409
    assert response.json()["mismatched_sequences"] == [110]
    assert stored_sequences(session_id) == list(range(100, 111)), "111-120 must NOT have been inserted"
    assert row_snapshot(session_id) == before, "existing rows are byte-for-byte unchanged"


def test_mismatch_response_never_acknowledges_anything(client, session_id):
    post_samples(client, session_id, [seq_sample(1), seq_sample(2)])

    response = post_samples(client, session_id, [seq_sample(1), seq_sample(2, t_seconds=42.0), seq_sample(3)])

    assert response.status_code == 409
    assert "acknowledged_sequences" not in response.json()
    assert response.json()["mismatched_sequences"] == [2]


def test_mismatch_details_identify_the_conflicting_fields(client, session_id):
    post_samples(client, session_id, [seq_sample(7)])

    response = post_samples(client, session_id, [seq_sample(7, t_seconds=1.234, player_center=[0.9, 0.9])])

    assert response.status_code == 409
    conflicts = response.json()["conflicts"]
    assert [conflict["sequence_number"] for conflict in conflicts] == [7]
    assert set(conflicts[0]["differing_fields"]) == {"t_seconds", "player_center"}
    assert response.json()["detail"]


def test_mismatch_rolls_back_legacy_samples_in_the_same_batch(client, session_id):
    post_samples(client, session_id, [seq_sample(1), seq_sample(2), seq_sample(3)])

    response = post_samples(
        client, session_id, [make_sample(50.0), seq_sample(1), seq_sample(2, t_seconds=7.0), seq_sample(9)]
    )

    assert response.status_code == 409
    assert len(sample_rows(session_id)) == 3, "not even the legacy sample or the new sequence 9 was committed"


def test_conflicting_duplicates_inside_one_batch_are_a_mismatch(client, session_id):
    response = post_samples(client, session_id, [seq_sample(1), seq_sample(1, t_seconds=5.0), seq_sample(2)])

    assert response.status_code == 409
    assert response.json()["mismatched_sequences"] == [1]
    assert sample_rows(session_id) == []


def test_identical_content_in_a_different_representation_is_not_a_mismatch(client, session_id):
    original = make_sample(1.0, ball=False)
    original["sequence_number"] = 1
    post_samples(client, session_id, [original])

    equivalent = dict(reversed(list(original.items())))          # different key order
    equivalent.update({"ball_bbox": None, "ball_center": None, "ball_confidence": None})   # explicit nulls
    response = post_samples(client, session_id, [equivalent])

    assert response.status_code == 201
    assert response.json()["duplicates"] == 1
    assert len(sample_rows(session_id)) == 1


def test_partial_overlap_with_identical_existing_rows_succeeds_and_acknowledges_all(client, session_id):
    post_samples(client, session_id, [seq_sample(n) for n in range(100, 111)])

    response = post_samples(client, session_id, [seq_sample(n) for n in range(105, 121)])

    assert response.status_code == 201
    body = response.json()
    assert body["duplicates"] == 6                      # 105-110
    assert body["ingested"] == 10                       # 111-120
    assert body["acknowledged_sequences"] == list(range(105, 121))
    assert stored_sequences(session_id) == list(range(100, 121))


def test_acknowledgment_never_claims_an_unsubmitted_sequence(client, session_id):
    post_samples(client, session_id, [seq_sample(1), seq_sample(2), seq_sample(3)])

    gap = post_samples(client, session_id, [seq_sample(1), seq_sample(2), seq_sample(5)])
    far = post_samples(client, session_id, [seq_sample(10)])

    assert gap.json()["acknowledged_sequences"] == [1, 2, 5], "3 was not in the request; 4 is not stored — neither is claimed"
    assert far.json()["acknowledged_sequences"] == [10], "10 does not vouch for 4-9"
    assert stored_sequences(session_id) == [1, 2, 3, 5, 10]


# ---------------------------------------------------------------------------
# 8-9. Events
# ---------------------------------------------------------------------------

CLIENT_TIME = "2026-01-01T10:00:00Z"


def event_payload(event_id="evt-0001", event_type="player_locked", **overrides):
    payload = {
        "event_type": event_type,
        "details": {"target_track_id": 1},
        "client_event_id": event_id,
        "client_occurred_at": CLIENT_TIME,
    }
    payload.update(overrides)
    return payload


def post_event(client, session_id, payload):
    return client.post(f"/tracking/sessions/{session_id}/events", json=payload)


def test_event_keeps_server_receipt_time_and_client_time_distinct(client, session_id):
    response = post_event(client, session_id, event_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["client_event_id"] == "evt-0001"
    assert body["duplicate"] is False
    assert parse_utc(body["client_occurred_at"]) == parse_utc(CLIENT_TIME)
    # `occurred_at` keeps its established SERVER-time meaning.
    assert abs((datetime.now(timezone.utc) - parse_utc(body["occurred_at"])).total_seconds()) < 120
    assert parse_utc(body["occurred_at"]) != parse_utc(CLIENT_TIME)


def test_exact_event_retry_is_stored_once(client, session_id):
    first = post_event(client, session_id, event_payload())
    retry = post_event(client, session_id, event_payload())

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert retry.json()["event_id"] == first.json()["event_id"]
    assert len(event_rows(session_id)) == 1


def test_event_id_reused_with_different_content_is_a_mismatch(client, session_id):
    post_event(client, session_id, event_payload())

    response = post_event(client, session_id, event_payload(event_type="player_lost"))

    assert response.status_code == 409
    stored = event_rows(session_id)
    assert len(stored) == 1 and stored[0].event_type == "player_locked"

    different_details = post_event(client, session_id, event_payload(details={"target_track_id": 2}))
    assert different_details.status_code == 409


def test_same_client_event_id_in_different_sessions_is_allowed(client):
    first_session = create_session(client, "ING_PLAYER")
    second_session = create_session(client, "ING_PLAYER")

    assert post_event(client, first_session, event_payload()).status_code == 201
    assert post_event(client, second_session, event_payload()).status_code == 201


def test_legacy_event_without_client_event_id_is_unchanged(client, session_id):
    legacy = {"event_type": "mode_switch", "details": {"mode": "smart_soccer"}}

    first = post_event(client, session_id, legacy)
    second = post_event(client, session_id, legacy)

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["event_id"] != second.json()["event_id"]
    assert len(event_rows(session_id)) == 2


# ---------------------------------------------------------------------------
# 10-11. Late telemetry
# ---------------------------------------------------------------------------

def test_late_sequenced_telemetry_after_completion_is_accepted(client, session_id):
    post_samples(client, session_id, [seq_sample(n) for n in range(1, 11)])
    assert client.post(f"/tracking/sessions/{session_id}/complete", json={}).status_code == 200

    late = post_samples(client, session_id, [seq_sample(n) for n in range(11, 16)])

    assert late.status_code == 201
    assert late.json()["ingested"] == 5
    assert stored_sequences(session_id) == list(range(1, 16))


def test_late_telemetry_invalidates_the_cached_feature_set(client, session_id):
    post_samples(client, session_id, [seq_sample(n) for n in range(1, 11)])
    client.post(f"/tracking/sessions/{session_id}/complete", json={})
    assert client.get(f"/tracking/sessions/{session_id}/features").json()["sample_count"] == 10

    post_samples(client, session_id, [seq_sample(n) for n in range(11, 16)])

    features = client.get(f"/tracking/sessions/{session_id}/features").json()
    assert features["sample_count"] == 15, "stale features must not stay published as current"


def test_late_legacy_telemetry_also_invalidates_the_cached_feature_set(client, session_id):
    post_samples(client, session_id, [make_sample(float(n)) for n in range(10)])
    client.post(f"/tracking/sessions/{session_id}/complete", json={})
    assert client.get(f"/tracking/sessions/{session_id}/features").json()["sample_count"] == 10

    post_samples(client, session_id, [make_sample(float(n)) for n in range(10, 14)])

    assert client.get(f"/tracking/sessions/{session_id}/features").json()["sample_count"] == 14


def test_duplicate_only_late_retry_does_not_recompute_features(client, session_id):
    batch = [seq_sample(n) for n in range(1, 11)]
    post_samples(client, session_id, batch)
    client.post(f"/tracking/sessions/{session_id}/complete", json={})
    db = IngestSessionLocal()
    computed_before = db.get(TrackingFeatureSetDB, session_id).computed_at
    db.close()

    retry = post_samples(client, session_id, batch)

    assert retry.json()["ingested"] == 0
    db = IngestSessionLocal()
    cached = db.get(TrackingFeatureSetDB, session_id)
    db.close()
    assert cached is not None and cached.computed_at == computed_before


# ---------------------------------------------------------------------------
# 12. Authorization is unchanged
# ---------------------------------------------------------------------------

def test_unknown_session_still_returns_404(client):
    assert post_samples(client, "TRK_DOES_NOT_EXIST", [seq_sample(1)]).status_code == 404
    assert post_event(client, "TRK_DOES_NOT_EXIST", event_payload()).status_code == 404


def test_another_coachs_session_still_returns_403(client, session_id):
    other = _login("ing.other", "IngOtherPassword123!")

    assert post_samples(other, session_id, [seq_sample(1)]).status_code == 403
    assert post_event(other, session_id, event_payload()).status_code == 403
    assert sample_rows(session_id) == []
    assert event_rows(session_id) == []


def test_guardian_and_anonymous_callers_are_still_rejected(client, session_id):
    guardian = _login("ing.guardian", "IngGuardianPassword123!")
    anonymous = TestClient(app)

    assert post_samples(guardian, session_id, [seq_sample(1)]).status_code == 403
    assert post_samples(anonymous, session_id, [seq_sample(1)]).status_code in (401, 403)
    assert sample_rows(session_id) == []


# ---------------------------------------------------------------------------
# Database uniqueness is the final authority (not a check-then-insert)
# ---------------------------------------------------------------------------

def _sample_row(session_id, sequence_number):
    return TrackingSampleDB(
        session_id=session_id,
        sequence_number=sequence_number,
        t_seconds=0.0,
        gimbal_state="tracking_active",
        tracking_mode="smart_soccer",
        tracking_status="locked",
    )


def test_database_rejects_duplicate_session_sequence_without_any_service_check(client, session_id):
    db = IngestSessionLocal()
    db.add(_sample_row(session_id, 1))
    db.commit()

    db.add(_sample_row(session_id, 1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()


def test_database_allows_many_legacy_rows_without_a_sequence(client, session_id):
    db = IngestSessionLocal()
    db.add_all([_sample_row(session_id, None), _sample_row(session_id, None)])
    db.commit()
    db.close()
    assert len(sample_rows(session_id)) == 2


def test_database_rejects_duplicate_session_client_event_id(client, session_id):
    def event_row(event_id):
        return TrackingEventDB(
            event_id=event_id, session_id=session_id, occurred_at=utcnow(), event_type="mode_switch",
            details={}, client_event_id="dup-evt",
        )

    db = IngestSessionLocal()
    db.add(event_row("TEV_A"))
    db.commit()
    db.add(event_row("TEV_B"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()


def test_models_declare_the_unique_indexes():
    def unique_column_sets(table):
        return {
            tuple(column.name for column in index.columns)
            for index in table.indexes
            if index.unique
        } | {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }

    assert ("session_id", "sequence_number") in unique_column_sets(TrackingSampleDB.__table__)
    assert ("session_id", "client_event_id") in unique_column_sets(TrackingEventDB.__table__)


def test_identity_ambiguous_is_stored_and_is_part_of_the_sample_content(client, session_id):
    post_samples(client, session_id, [seq_sample(1, identity_ambiguous=True)])
    assert sample_rows(session_id)[0].identity_ambiguous is True

    same = post_samples(client, session_id, [seq_sample(1, identity_ambiguous=True)])
    assert same.status_code == 201 and same.json()["duplicates"] == 1

    changed = post_samples(client, session_id, [seq_sample(1, identity_ambiguous=False)])
    assert changed.status_code == 409
    assert changed.json()["conflicts"][0]["differing_fields"] == ["identity_ambiguous"]


def test_legacy_samples_have_unknown_identity_ambiguity(client, session_id):
    post_samples(client, session_id, [make_sample(0.0)])
    assert sample_rows(session_id)[0].identity_ambiguous is None
