from datetime import datetime

from app.db_models import PlayerMembershipDB, SubscriptionDB
from app.services.eligibility_service import (
    ADMIN_OVERRIDE,
    COMPLIMENTARY,
    MEMBERSHIP_EXPIRED,
    PAST_DUE,
    PAUSED,
    PAYMENT_ATTENTION_REQUIRED,
    PENDING_PAYMENT,
    TRAINING_ELIGIBLE,
    UNASSIGNED,
    derive_eligibility,
    derive_membership_status,
)


def _membership(**overrides):
    defaults = dict(
        player_id="P1",
        plan_id="MPLAN1",
        assigned_by_user_id="U1",
        assigned_at=datetime(2026, 1, 1),
        discount_percent_off=None,
        is_complimentary=False,
        admin_override_eligibility=None,
        promo_code_id=None,
        updated_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    return PlayerMembershipDB(**defaults)


def _subscription(**overrides):
    defaults = dict(
        stripe_subscription_id="sub_1",
        player_id="P1",
        paying_user_id="U1",
        stripe_customer_id="cus_1",
        stripe_price_id="price_1",
        status="active",
        current_period_end=None,
        cancel_at_period_end=False,
        discount_percent_off=None,
        plan_id="MPLAN1",
        is_paused=False,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    return SubscriptionDB(**defaults)


def test_no_membership_is_unassigned():
    assert derive_membership_status(None, None) == UNASSIGNED
    assert derive_eligibility(None, None) == PAYMENT_ATTENTION_REQUIRED


def test_complimentary_membership_overrides_everything_else():
    membership = _membership(is_complimentary=True)
    assert derive_membership_status(membership, None) == COMPLIMENTARY
    assert derive_eligibility(membership, None) == TRAINING_ELIGIBLE


def test_membership_assigned_but_never_paid_is_pending_payment():
    membership = _membership()
    assert derive_membership_status(membership, None) == PENDING_PAYMENT
    assert derive_eligibility(membership, None) == PAYMENT_ATTENTION_REQUIRED


def test_active_subscription_is_active_and_eligible():
    membership = _membership()
    subscription = _subscription(status="active")
    assert derive_membership_status(membership, subscription) == "ACTIVE"
    assert derive_eligibility(membership, subscription) == TRAINING_ELIGIBLE


def test_past_due_subscription_needs_payment_attention():
    membership = _membership()
    subscription = _subscription(status="past_due")
    assert derive_membership_status(membership, subscription) == PAST_DUE
    assert derive_eligibility(membership, subscription) == PAYMENT_ATTENTION_REQUIRED


def test_unpaid_subscription_maps_to_past_due():
    membership = _membership()
    subscription = _subscription(status="unpaid")
    assert derive_membership_status(membership, subscription) == PAST_DUE


def test_cancelled_subscription_is_membership_expired():
    membership = _membership()
    subscription = _subscription(status="canceled")
    assert derive_membership_status(membership, subscription) == "CANCELLED"
    assert derive_eligibility(membership, subscription) == MEMBERSHIP_EXPIRED


def test_incomplete_expired_subscription_is_expired():
    membership = _membership()
    subscription = _subscription(status="incomplete_expired")
    assert derive_membership_status(membership, subscription) == "EXPIRED"
    assert derive_eligibility(membership, subscription) == MEMBERSHIP_EXPIRED


def test_paused_subscription_is_paused_not_active():
    # Stripe's pause_collection does NOT change `status` — it stays
    # "active" while paused, which is exactly why is_paused is tracked
    # separately and must be checked BEFORE looking at raw status.
    membership = _membership()
    subscription = _subscription(status="active", is_paused=True)
    assert derive_membership_status(membership, subscription) == PAUSED
    assert derive_eligibility(membership, subscription) == PAYMENT_ATTENTION_REQUIRED


def test_admin_override_wins_regardless_of_underlying_status():
    membership = _membership(admin_override_eligibility="Injured, keep training with team")
    subscription = _subscription(status="past_due")
    # Status reporting still reflects the real payment state...
    assert derive_membership_status(membership, subscription) == PAST_DUE
    # ...but eligibility respects the documented, audited exception.
    assert derive_eligibility(membership, subscription) == ADMIN_OVERRIDE


def test_admin_override_does_not_apply_once_cleared():
    membership = _membership(admin_override_eligibility=None)
    subscription = _subscription(status="past_due")
    assert derive_eligibility(membership, subscription) == PAYMENT_ATTENTION_REQUIRED
