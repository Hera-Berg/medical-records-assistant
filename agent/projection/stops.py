"""When a source says a medication is over, and what that is allowed to do.

Rule 4 in ``CLAUDE.md`` is enforced in two places that must not drift. The
projection's reconciliation raises a *stop proposal* as its own review kind, so
the inbox can render it as a distinct action rather than a generic accept in a
tap-through queue. The entity builder decides whether an endorsed stop actually
transitions the status or only annotates the page. Both need the same two facts —
what counts as a statement that something stopped, and which evidence tiers may
act on one — so both read them from here.

Keeping the vocabulary in one module is not tidiness. If the inbox's idea of a
stop were wider than the entity builder's, a claim would be presented with a
"take it off the list" button and then quietly fail to take it off the list; if
it were narrower, a stop would reach the queue as a generic confirmation and a
careless tap could drop a medication. Both failures are invisible from either
module alone.
"""

from __future__ import annotations

from .claims import Claim

#: Values of a ``status`` predicate that mean the thing is over. Matched against
#: the normalised value, so "Stopped" and "stopped" are one word.
STOPPED_WORDS = frozenset(
    {"stopped", "ceased", "discontinued", "stop", "resolved", "inactive"}
)

#: Evidence tiers a *proposed* stop may carry and still transition the status.
#:
#: A confirmed proposal is one tap, and a tap is cheaper than a typed
#: correction, so the document behind the proposal has to be one that can
#: actually cease a medication. ``device-recorded``, ``patient-reported`` and
#: ``inferred`` can never transition a status however emphatically they are
#: confirmed — the user saying "I think I stopped that" is a correction to type,
#: not a proposal to accept. A ``claim.corrected`` is exempt everywhere this is
#: consulted: the user authored the value itself, so there is no extractor's
#: reading to vouch for.
STOP_TIERS = frozenset({"prescriber-issued", "lab-issued"})

#: The predicate a stop is stated on. Anything else saying "stopped" is about
#: something other than a medication's lifecycle.
STATUS = "status"


def stated_stop(claim: Claim) -> bool:
    """Whether this ``status`` claim says the thing is over."""
    stated = claim.value.fields.get(STATUS) or claim.value.literal.strip().lower()
    return stated in STOPPED_WORDS


def is_stop_claim(claim: Claim) -> bool:
    """Whether this claim proposes that a medication has stopped.

    Restricted to ``med`` on purpose. A problem whose status is ``resolved`` is
    a different thing with different consequences, and it does not get the
    medication list's guard rail or its wording.
    """
    return (
        claim.subject.kind == "med"
        and claim.predicate == STATUS
        and stated_stop(claim)
    )


def transitions(claim: Claim) -> bool:
    """Whether confirming this stop would take the medication off the list.

    ``False`` means confirming is still worth doing — the stop is recorded on
    the page, cited, and raised as a discrepancy — but the medication stays.
    The inbox says which of the two will happen *before* the tap, because a
    button that reads "take it off the list" and then does not is worse than no
    button.
    """
    return claim.is_correction or claim.evidence_tier in STOP_TIERS
