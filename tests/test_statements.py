"""Statement-PDF text parser tests (synthetic NatWest-style layout)."""
import pytest

from moxie.statements import parse_statement_text

NATWEST_STYLE = """
                                                      From                       To
  EXAMPLE AN                                          20/04/2026                 29/06/2026
  Select Account

Your transactions

Date           Description             Type                        Paid in (£)       Paid out (£)
29 Jun         OMAZE                   Debit Card Transaction                          -£15.00

29 Jun         From A/C 12345678       Mobile/Online Transaction     £300.00

26 Jun         Pret A Manger           Debit Card Transaction                          -£13.45

01 May         Kaboodle                Debit Card Transaction                        -£1,282.84
"""


def test_parses_natwest_layout():
    txns = parse_statement_text(NATWEST_STYLE)
    assert len(txns) == 4
    omaze = txns[0]
    assert (omaze.date, omaze.merchant, omaze.amount) == ("2026-06-29", "Omaze", 15.00)
    assert omaze.currency == "£"


def test_paid_in_becomes_negative_credit():
    txns = parse_statement_text(NATWEST_STYLE)
    credit = [t for t in txns if t.amount < 0]
    assert len(credit) == 1 and credit[0].amount == -300.00


def test_thousands_commas():
    txns = parse_statement_text(NATWEST_STYLE)
    assert any(t.amount == 1282.84 for t in txns)


def test_year_comes_from_header():
    assert all(t.date.startswith("2026-") for t in parse_statement_text(NATWEST_STYLE))


def test_year_wrap_december_january():
    text = """
  From                       To
  20/12/2026                 10/01/2027

Date           Description             Type                        Paid in (£)       Paid out (£)
28 Dec         Netflix                 Debit Card Transaction                           -£9.99
05 Jan         Netflix                 Debit Card Transaction                           -£9.99
"""
    txns = parse_statement_text(text)
    assert txns[0].date == "2026-12-28"
    assert txns[1].date == "2027-01-05"


def test_no_transactions_returns_empty():
    assert parse_statement_text("Dear customer, thank you for banking with us.") == []


def test_missing_year_raises():
    with pytest.raises(ValueError, match="infer the statement's year"):
        parse_statement_text(
            "29 Jun         OMAZE      Debit Card Transaction        -£15.00"
        )
