"""Detector tests: real-life noise must not produce false positives."""
from moxie.detect import detect_all, find_duplicate_charges, find_zombie_subscriptions
from moxie.models import Transaction


def T(date, merchant, amount, cur="£"):
    return Transaction(date=date, merchant=merchant, amount=amount, currency=cur)


def test_subscription_survives_price_rise():
    txns = [
        T("2026-04-03", "Netflix", 9.99),
        T("2026-05-03", "Netflix", 9.99),
        T("2026-06-03", "Netflix", 10.99),   # price rise mid-stream
    ]
    subs = find_zombie_subscriptions(txns)
    assert len(subs) == 1
    assert subs[0].merchant == "Netflix"
    assert "£" in subs[0].description


def test_groceries_are_not_a_subscription():
    txns = [T(f"2026-0{m}-{d:02d}", "Tesco", a)
            for m in (5, 6)
            for d, a in ((2, 23.10), (9, 41.75), (16, 18.20), (23, 33.05))]
    assert find_zombie_subscriptions(txns) == []


def test_varying_amounts_are_not_a_subscription():
    txns = [
        T("2026-04-12", "Amazon", 12.50),
        T("2026-05-20", "Amazon", 89.99),
        T("2026-06-05", "Amazon", 7.49),
    ]
    assert find_zombie_subscriptions(txns) == []


def test_two_coffees_are_not_fraud():
    txns = [
        T("2026-06-03", "Pret", 3.50),
        T("2026-06-03", "Pret", 3.50),
    ]
    assert find_duplicate_charges(txns) == []


def test_duplicate_flagged_above_threshold():
    txns = [
        T("2026-06-03", "Cloudhost", 40.00),
        T("2026-06-03", "Cloudhost", 40.00),
    ]
    dupes = find_duplicate_charges(txns)
    assert len(dupes) == 1 and dupes[0].est_savings == 40.00


def test_refunded_duplicate_is_not_chased():
    txns = [
        T("2026-06-03", "Cloudhost", 40.00),
        T("2026-06-03", "Cloudhost", 40.00),
        T("2026-06-05", "Cloudhost", -40.00),   # merchant already fixed it
    ]
    assert find_duplicate_charges(txns) == []


def test_credits_never_flagged():
    txns = [
        T("2026-04-01", "Employer", -2200.00),
        T("2026-05-01", "Employer", -2200.00),
        T("2026-06-01", "Employer", -2200.00),
    ]
    assert detect_all(txns) == []
