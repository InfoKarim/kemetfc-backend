"""Pure, server-side derivation of membership status and training
eligibility from PlayerMembershipDB + SubscriptionDB — never from anything
the client claims, and never fabricated when data is simply missing
(UNASSIGNED / PENDING_PAYMENT are real, honest states, not guesses)."""

from app.db_models import PlayerMembershipDB, SubscriptionDB

UNASSIGNED = "UNASSIGNED"
ACTIVE = "ACTIVE"
PENDING_PAYMENT = "PENDING_PAYMENT"
PAST_DUE = "PAST_DUE"
PAUSED = "PAUSED"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"
COMPLIMENTARY = "COMPLIMENTARY"

TRAINING_ELIGIBLE = "TRAINING_ELIGIBLE"
PAYMENT_ATTENTION_REQUIRED = "PAYMENT_ATTENTION_REQUIRED"
MEMBERSHIP_EXPIRED = "MEMBERSHIP_EXPIRED"
ADMIN_OVERRIDE = "ADMIN_OVERRIDE"

_STRIPE_STATUS_MAP = {
    "active": ACTIVE,
    "trialing": ACTIVE,
    "past_due": PAST_DUE,
    "unpaid": PAST_DUE,
    "canceled": CANCELLED,
    "incomplete": PENDING_PAYMENT,
    "incomplete_expired": EXPIRED,
}


def derive_membership_status(
    membership: PlayerMembershipDB | None,
    subscription: SubscriptionDB | None,
) -> str:
    if membership is None:
        return UNASSIGNED
    if membership.is_complimentary:
        return COMPLIMENTARY
    if subscription is None:
        return PENDING_PAYMENT
    if subscription.is_paused:
        return PAUSED
    return _STRIPE_STATUS_MAP.get(subscription.status, subscription.status.upper())


_ELIGIBLE_STATUSES = {ACTIVE, COMPLIMENTARY}
_EXPIRED_STATUSES = {CANCELLED, EXPIRED}


def derive_eligibility(
    membership: PlayerMembershipDB | None,
    subscription: SubscriptionDB | None,
) -> str:
    """A documented admin override always wins — it's an explicit,
    audited exception (see PlayerMembershipDB.admin_override_eligibility),
    never a silent bypass."""
    if membership is not None and membership.admin_override_eligibility:
        return ADMIN_OVERRIDE

    status = derive_membership_status(membership, subscription)
    if status in _ELIGIBLE_STATUSES:
        return TRAINING_ELIGIBLE
    if status in _EXPIRED_STATUSES:
        return MEMBERSHIP_EXPIRED
    # PAST_DUE, PENDING_PAYMENT, PAUSED, UNASSIGNED all need staff/guardian
    # attention rather than being flatly "expired" or "fine."
    return PAYMENT_ATTENTION_REQUIRED
