"""Phase 4 detectors: each one explainable, each one conservative."""
from moxie.detect import (detect_all, find_bank_fees, find_duplicate_services,
                          find_fx_fees, find_new_subscriptions,
                          find_partial_refunds, find_price_hikes,
                          find_zombie_subscriptions)
from moxie.models import Transaction


def T(date, merchant, amount, desc="", cur="£"):
    return Transaction(date=date, merchant=merchant, amount=amount,
                       description=desc, currency=cur)


# ------------------------------------------------------------ new subs ------
def test_trial_that_stuck_is_flagged_with_honest_copy():
    txns = [
        T("2026-05-14", "StreamMax", 15.99),
        T("2026-06-14", "StreamMax", 15.99),
        T("2026-06-14", "Corner Grocery", 40.00),   # anchors 'now'
    ]
    new = find_new_subscriptions(txns)
    assert len(new) == 1
    assert "trial that stuck" in new[0].description
    # …and the zombie detector leaves it alone (no double-fire)
    assert find_zombie_subscriptions(txns) == []


def test_old_subscription_is_zombie_not_new():
    txns = [T(f"2026-0{m}-02", "FitClub", 29.99) for m in (3, 4, 5, 6)]
    assert find_new_subscriptions(txns) == []
    assert len(find_zombie_subscriptions(txns)) == 1


# ------------------------------------------------------------ price hikes ---
def test_price_hike_flagged_with_old_and_new_price():
    txns = [
        T("2026-03-03", "Netflix", 9.99),
        T("2026-04-03", "Netflix", 9.99),
        T("2026-05-03", "Netflix", 9.99),
        T("2026-06-03", "Netflix", 15.99),   # +60% — breaks the ±20% sub filter
    ]
    hikes = find_price_hikes(txns)
    assert len(hikes) == 1
    h = hikes[0]
    assert h.kind == "negotiate" and "9.99" in h.description and "15.99" in h.description
    assert h.est_savings == round((15.99 - 9.99) * 12, 2)


def test_small_drift_is_not_a_hike():
    txns = [
        T("2026-04-03", "Netflix", 9.99),
        T("2026-05-03", "Netflix", 9.99),
        T("2026-06-03", "Netflix", 10.49),   # +50p: annoying, not actionable
    ]
    assert find_price_hikes(txns) == []


def test_varying_spend_is_not_a_hike():
    txns = [
        T("2026-04-03", "Amazon", 12.00),
        T("2026-05-03", "Amazon", 80.00),
        T("2026-06-03", "Amazon", 95.00),
    ]
    assert find_price_hikes(txns) == []


# ------------------------------------------------------------ two of a kind -
def test_two_streaming_subs_flagged_once():
    txns = []
    for m in (3, 4, 5, 6):
        txns.append(T(f"2026-0{m}-03", "Netflix", 9.99))
    for m in (5, 6):
        txns.append(T(f"2026-0{m}-14", "Disney Plus", 7.99))
    dupes = find_duplicate_services(txns)
    assert len(dupes) == 1
    d = dupes[0]
    assert d.kind == "duplicate_service"
    assert d.merchant == "Disney Plus"           # the newer one goes
    assert "Netflix" in d.description and "rotating" in d.rationale.lower()


def test_one_streaming_sub_is_fine():
    txns = [T(f"2026-0{m}-03", "Netflix", 9.99) for m in (4, 5, 6)]
    assert find_duplicate_services(txns) == []


# ------------------------------------------------------------ bank fees -----
def test_overdraft_fees_produce_a_waiver_ask():
    txns = [
        T("2026-05-02", "Unarranged Overdraft Fee", 8.00),
        T("2026-06-02", "Unarranged Overdraft Fee", 8.00),
        T("2026-06-09", "Corner Grocery", 63.20),
    ]
    fees = find_bank_fees(txns)
    assert len(fees) == 1
    f = fees[0]
    assert f.est_savings == 16.00 and "waive" in f.description
    assert "goodwill" in f.draft


def test_fx_fees_summed_and_explained():
    txns = [
        T("2026-06-03", "Non-Sterling Transaction Fee", 2.75,
          desc="Non-sterling transaction fee"),
        T("2026-06-10", "Non-Sterling Transaction Fee", 3.10,
          desc="Non-sterling transaction fee"),
    ]
    fx = find_fx_fees(txns)
    assert len(fx) == 1 and fx[0].est_savings == 5.85
    assert "no-FX-fee card" in fx[0].description


def test_normal_spending_is_not_a_fee():
    txns = [T("2026-06-09", "Corner Grocery", 63.20),
            T("2026-06-11", "Daily Coffee", 4.75)]
    assert find_bank_fees(txns) == [] and find_fx_fees(txns) == []


# ------------------------------------------------------------ partial refunds
def test_partial_refund_worth_chasing():
    txns = [
        T("2026-06-01", "BigShop", 89.99),
        T("2026-06-08", "BigShop", -30.00),   # £59.99 short
    ]
    out = find_partial_refunds(txns)
    assert len(out) == 1
    assert out[0].kind == "chase_refund" and out[0].est_savings == 59.99
    assert "skip me" in out[0].rationale      # honest about uncertainty


def test_full_refund_is_not_chased():
    txns = [
        T("2026-06-01", "BigShop", 89.99),
        T("2026-06-08", "BigShop", -89.99),
    ]
    assert find_partial_refunds(txns) == []


def test_small_shortfall_is_ignored():
    txns = [
        T("2026-06-01", "BigShop", 25.00),
        T("2026-06-05", "BigShop", -20.00),   # £5 gap: below the floor
    ]
    assert find_partial_refunds(txns) == []


# ------------------------------------------------------------ the whole sweep
def test_detect_all_composes_and_credits_still_never_flagged():
    txns = [
        T("2026-04-01", "Employer", -2200.00),
        T("2026-05-01", "Employer", -2200.00),
        T("2026-06-01", "Employer", -2200.00),
    ]
    assert detect_all(txns) == []


def test_detect_all_actually_runs_every_detector():
    """Guards against detect_all silently dropping a detector (regression:
    a shadowed detect_all once hid everything but dups + zombies)."""
    txns = [
        # duplicate charge
        T("2026-06-03", "CloudHost", 40.00), T("2026-06-03", "CloudHost", 40.00),
        # established zombie sub
        T("2026-03-02", "FitClub", 29.99), T("2026-04-02", "FitClub", 29.99),
        T("2026-05-02", "FitClub", 29.99), T("2026-06-02", "FitClub", 29.99),
        # brand-new sub (trial that stuck)
        T("2026-05-14", "StreamMax", 15.99), T("2026-06-14", "StreamMax", 15.99),
        # second stable streaming sub -> duplicate_service pairs it with StreamMax
        T("2026-03-20", "Disney Plus", 7.99), T("2026-04-20", "Disney Plus", 7.99),
        T("2026-05-20", "Disney Plus", 7.99), T("2026-06-20", "Disney Plus", 7.99),
        # price hike
        T("2026-03-03", "Netflix", 9.99), T("2026-04-03", "Netflix", 9.99),
        T("2026-05-03", "Netflix", 9.99), T("2026-06-03", "Netflix", 15.99),
        # bank fee
        T("2026-06-02", "Unarranged Overdraft Fee", 8.00),
        # fx fee
        T("2026-06-05", "Non-Sterling Transaction Fee", 2.75),
        # partial refund
        T("2026-06-01", "BigShop", 89.99), T("2026-06-08", "BigShop", -30.00),
    ]
    kinds = {a.kind for a in detect_all(txns)}
    assert {"dispute_charge", "cancel_subscription", "negotiate",
            "duplicate_service", "chase_refund"} <= kinds
    merchants = {a.merchant for a in detect_all(txns)}
    assert {"CloudHost", "FitClub", "StreamMax", "Netflix",
            "Bank fees", "Non-sterling fees", "BigShop"} <= merchants
