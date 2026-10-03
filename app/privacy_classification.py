"""Privacy classification of every database table that holds player-linked data.

This is the list that stops a new child-data table from being forgotten by the
privacy export and deletion code. ``tests/test_privacy_classification.py`` finds
every table that references a player (directly, through another player-linked
table, or by having a ``player_id`` column, or by being named ``v1_*``) and fails
when one is missing here, or when an entry here no longer matches a real table.

Adding a table: add one ``TableClassification`` below, and make the export and
deletion code in ``app/services/privacy_service.py`` agree with it.

``export``: how the table appears in the guardian data export.
``erase``: what an approved privacy deletion does to the table's rows.
``status``: IMPLEMENTED (the code does what the entry says), POLICY_PENDING (a
policy or legal decision is outstanding, so the code deliberately does not act
yet) or RETAINED_BY_DESIGN (kept on purpose, with the reason given).

This file records behaviour; it does not change it. The registry never means a
retained table was deleted.
"""
from dataclasses import dataclass

EXPORT_VALUES = {"EXPORTED", "SEPARATE_EXPORT", "NOT_EXPORTED", "NOT_APPLICABLE"}
ERASE_VALUES = {"DELETED", "ANONYMIZED", "REVOKED", "RETAINED_WITH_REASON", "NOT_APPLICABLE"}
STATUS_VALUES = {"IMPLEMENTED", "POLICY_PENDING", "RETAINED_BY_DESIGN"}


@dataclass(frozen=True)
class TableClassification:
    export: str
    erase: str
    status: str
    reason: str
    # Set only on legacy tables whose normal (non-privacy) delete path hard-deletes
    # history. V1 tables must never copy that pattern.
    legacy_not_for_v1: bool = False
    # Required True for every ``v1_`` table: rows are append-only in normal
    # operation and are removed only by the privileged privacy workflow.
    privileged_deletion_only: bool = False


def _entry(export, erase, status, reason, **flags) -> TableClassification:
    return TableClassification(export, erase, status, reason, **flags)


_DONE = "IMPLEMENTED"
_PENDING = "POLICY_PENDING"
_KEPT = "RETAINED_BY_DESIGN"

TABLE_CLASSIFICATIONS: dict[str, TableClassification] = {
    # --- identity and access --------------------------------------------------
    "players": _entry("EXPORTED", "ANONYMIZED", _DONE,
        "Row kept so legal-request and audit rows stay valid; names, date of birth, "
        "sex, profiles and the photo reference are cleared."),
    "guardian_player_links": _entry("NOT_APPLICABLE", "DELETED", _DONE,
        "The link is the access grant itself, not child data."),
    "guardian_consents": _entry("EXPORTED", "DELETED", _DONE,
        "Exported without guardian name or email. Whether consent evidence must be kept "
        "after deletion is a legal question (see S0.5 record)."),
    "player_checkin_tokens": _entry("NOT_APPLICABLE", "REVOKED", _DONE,
        "Opaque hash, not child data. Every token is revoked on completed deletion; the "
        "row holds only a hash and dates."),
    "privacy_requests": _entry("NOT_APPLICABLE", "RETAINED_WITH_REASON", _KEPT,
        "The legal request record must stay. Its free-text reason and review notes are "
        "retained; retention period is a legal question."),
    # --- video and analysis ---------------------------------------------------
    "data_records": _entry("EXPORTED", "DELETED", _DONE, "File path is not exported."),
    "videos": _entry("EXPORTED", "DELETED", _DONE,
        "Database rows and local files under /uploads/videos/ only. Cross-copy deletion "
        "(object storage, devices, clips, thumbnails, backups) is deferred."),
    "video_analysis_jobs": _entry("NOT_APPLICABLE", "DELETED", _DONE,
        "Internal processing job; its result is the analysis, which is exported."),
    "analyses": _entry("EXPORTED", "DELETED", _DONE, "Raw output path is not exported."),
    "training_plans": _entry("EXPORTED", "DELETED", _DONE, ""),
    # --- legacy assessment and forecasts -------------------------------------
    "player_assessments": _entry("EXPORTED", "DELETED", _DONE,
        "LEGACY - NOT FOR V1. Its ordinary staff route DELETE /player-assessments/{id} "
        "hard-deletes history without an audit event. V1 corrections are append-only.",
        legacy_not_for_v1=True),
    "player_metrics": _entry("EXPORTED", "DELETED", _DONE,
        "LEGACY - NOT FOR V1 (deleted together with its assessment by that same route).",
        legacy_not_for_v1=True),
    "ml_predictions": _entry("EXPORTED", "DELETED", _DONE, ""),
    "monte_carlo_simulations": _entry("EXPORTED", "DELETED", _DONE, ""),
    "prediction_outcomes": _entry("EXPORTED", "DELETED", _DONE, ""),
    "coach_messages": _entry("EXPORTED", "DELETED", _DONE, ""),
    # --- tracking --------------------------------------------------------------
    "tracking_sessions": _entry("EXPORTED", "DELETED", _DONE, ""),
    "tracking_events": _entry("EXPORTED", "DELETED", _DONE, ""),
    "tracking_feature_sets": _entry("EXPORTED", "DELETED", _DONE, ""),
    "coach_validation_labels": _entry("EXPORTED", "DELETED", _DONE, ""),
    "tracking_samples": _entry("SEPARATE_EXPORT", "DELETED", _DONE,
        "Raw per-frame movement and pose data. Deleted on completed deletion. The export "
        "lists only a sample count; the separate download is not built yet."),
    # --- V1 assessment (all removable only by the approved privacy workflow) -------
    "v1_metric_definitions": _entry("NOT_APPLICABLE", "NOT_APPLICABLE", _DONE,
        "Versioned reference data: no child data, so nothing to export or erase. Never "
        "deleted by a privacy request; protected from normal edits by being versioned.",
        privileged_deletion_only=True),
    "v1_assessments": _entry("EXPORTED", "DELETED", _DONE,
        "One assessment occasion per player. Append-only in normal use (no delete route); "
        "removed in dependency order by completed privacy deletion.",
        privileged_deletion_only=True),
    "v1_station_runs": _entry("EXPORTED", "DELETED", _DONE,
        "Station inside an assessment. Exported inside its assessment; removed by completed "
        "privacy deletion.", privileged_deletion_only=True),
    "v1_trials": _entry("EXPORTED", "DELETED", _DONE,
        "Attempts with validity and invalid reason. Immutable through the ORM; removed by "
        "completed privacy deletion.", privileged_deletion_only=True),
    "v1_manual_measurements": _entry("EXPORTED", "DELETED", _DONE,
        "Human-entered stopwatch times (exact centiseconds) with original values. Immutable "
        "through the ORM; removed by completed privacy deletion.", privileged_deletion_only=True),
    "v1_manual_measurement_corrections": _entry("EXPORTED", "DELETED", _DONE,
        "Append-only correction history. Exported with its measurement and removed with it by "
        "completed privacy deletion.", privileged_deletion_only=True),
    # --- policy pending --------------------------------------------------------
    "assessment_registrations": _entry("NOT_EXPORTED", "RETAINED_WITH_REASON", _PENDING,
        "Holds child and guardian names, contact details and date of birth. The principle "
        "that identifying fields must not stay attached is frozen, but the exact field "
        "handling and the retention of the consent answers need a policy decision."),
    "ml_dataset_entries": _entry("NOT_EXPORTED", "DELETED", _PENDING,
        "The row is deleted, which also removes the only in-app record of which videos "
        "may have fed an external extract. An opaque tombstone and a dataset-export "
        "register are not designed yet. Copies outside the platform are never deleted "
        "or tracked by this code."),
    "subscriptions": _entry("NOT_EXPORTED", "RETAINED_WITH_REASON", _PENDING,
        "Financial record. Retention period requires policy/legal review. A live Stripe "
        "subscription is not cancelled by deletion."),
    "payments": _entry("NOT_EXPORTED", "RETAINED_WITH_REASON", _PENDING,
        "Financial record. Retention period requires policy/legal review."),
    "manual_payments": _entry("NOT_EXPORTED", "RETAINED_WITH_REASON", _PENDING,
        "Financial record with a free-text note that may name the child. Retention and "
        "redaction require policy/legal review."),
    "refunds": _entry("NOT_EXPORTED", "RETAINED_WITH_REASON", _PENDING,
        "Financial record with free-text reason and internal note. Retention and "
        "redaction require policy/legal review."),
    "player_memberships": _entry("NOT_EXPORTED", "RETAINED_WITH_REASON", _PENDING,
        "Plan assignment. Whether it is deleted or retained is not decided."),
    "promo_code_redemptions": _entry("NOT_EXPORTED", "RETAINED_WITH_REASON", _PENDING,
        "Needed to enforce per-family promo limits. Retention requires policy review."),
}

# Tables that hold personal data but are not linked to a player by any foreign key.
# They are reviewed here so they are not forgotten; the test only checks that they
# still exist. They are NOT covered by a child's privacy request.
REVIEWED_NOT_PLAYER_LINKED: dict[str, str] = {
    "users": "Guardian and staff accounts (email, phone, address, national id). Account-level "
             "erasure needs its own decision; no child privacy request touches it.",
    "audit_events": "Operational audit trail: ids, actions and counts only, never content. "
                    "Retention period requires policy/legal review.",
    "stripe_customer_mappings": "Maps a user to a Stripe customer id; payer data, not child data.",
}


def validate_classification(name: str, entry: TableClassification) -> list[str]:
    """Return what is wrong with one entry (empty list when it is valid)."""
    problems = []
    if entry.export not in EXPORT_VALUES:
        problems.append(f"{name}: unknown export value {entry.export!r}")
    if entry.erase not in ERASE_VALUES:
        problems.append(f"{name}: unknown erase value {entry.erase!r}")
    if entry.status not in STATUS_VALUES:
        problems.append(f"{name}: unknown status {entry.status!r}")
    if entry.erase == "RETAINED_WITH_REASON" and not entry.reason.strip():
        problems.append(f"{name}: a retained table needs a reason")
    if entry.status == "POLICY_PENDING" and not entry.reason.strip():
        problems.append(f"{name}: a policy-pending table needs a reason")
    if name.startswith("v1_"):
        if entry.legacy_not_for_v1:
            problems.append(f"{name}: a v1_ table must not carry the legacy delete pattern")
        if not entry.privileged_deletion_only:
            problems.append(f"{name}: a v1_ table must be removable only by the privacy workflow")
    return problems


def player_linked_tables(metadata) -> set[str]:
    """Tables that reference a player: by a ``player_id`` column or a foreign key to
    ``players``, through a foreign-key chain to such a table, or named ``v1_*``.
    Only foreign keys into already player-linked tables are followed, so tables that
    merely reference users or teams are not swept in."""
    tables = metadata.tables
    linked = {
        name for name, table in tables.items()
        if name != "players" and (
            "player_id" in table.c
            or any(fk.column.table.name == "players" for fk in table.foreign_keys)
        )
    }
    reachable = linked | {"players"}
    grew = True
    while grew:
        grew = False
        for name, table in tables.items():
            if name not in reachable and any(
                fk.column.table.name in reachable for fk in table.foreign_keys
            ):
                reachable.add(name)
                linked.add(name)
                grew = True
    linked |= {name for name in tables if name.startswith("v1_")}
    return linked


def unclassified_tables(metadata, classifications=None) -> list[str]:
    classified = TABLE_CLASSIFICATIONS if classifications is None else classifications
    expected = player_linked_tables(metadata) | ({"players"} if "players" in metadata.tables else set())
    return sorted(expected - set(classified))
