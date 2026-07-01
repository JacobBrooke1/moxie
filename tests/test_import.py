"""Importer tests against realistic (synthetic) UK bank export formats."""
import textwrap

import pytest

from moxie.connectors import import_csv, normalize_merchant


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------- formats

def test_monzo_style_negative_spend(tmp_path):
    """Monzo-style: 'Name' column, spend negative, ISO-ish dates."""
    path = _write(tmp_path, "monzo.csv", """
        Date,Name,Category,Amount,Currency,Notes
        2026-04-03,Netflix,Entertainment,-9.99,GBP,
        2026-05-03,Netflix,Entertainment,-9.99,GBP,
        2026-05-10,Salary,Income,2200.00,GBP,May salary
    """)
    txns = import_csv(path)
    assert len(txns) == 3
    netflix = [t for t in txns if t.merchant == "Netflix"]
    assert all(t.amount == 9.99 for t in netflix)          # spend flipped positive
    assert [t for t in txns if t.merchant == "Salary"][0].amount == -2200.00
    assert txns[0].currency == "£"


def test_barclays_style_uk_dates_and_memo(tmp_path):
    """Barclays-style: dd/mm/yyyy, single Amount, Memo as merchant text."""
    path = _write(tmp_path, "barclays.csv", """
        Number,Date,Account,Amount,Subcategory,Memo
        1,03/06/2026,20-00-00 123,-40.00,Payment,CLOUDHOST LTD REF 8891
        2,03/06/2026,20-00-00 123,-40.00,Payment,CLOUDHOST LTD REF 8892
        3,04/06/2026,20-00-00 123,-12.50,Payment,TESCO STORES 3412
    """)
    txns = import_csv(path)
    assert txns[0].date == "2026-06-03"
    assert txns[0].merchant == "Cloudhost"
    assert txns[2].merchant == "Tesco Stores"
    assert all(t.amount > 0 for t in txns)


def test_money_out_money_in_columns(tmp_path):
    """HSBC/Nationwide-style: separate Money Out / Money In columns, £ signs."""
    path = _write(tmp_path, "hsbc.csv", """
        Date,Description,Money Out,Money In,Balance
        01/05/2026,CARD PAYMENT TO FITCLUB,£29.99,,"£1,200.00"
        15/05/2026,REFUND FITCLUB,,£29.99,"£1,229.99"
        01/06/2026,CARD PAYMENT TO FITCLUB,£29.99,,"£1,199.99"
    """)
    txns = import_csv(path)
    assert txns[0].amount == 29.99
    assert txns[1].amount == -29.99            # credit is negative
    assert txns[0].merchant == "Fitclub"
    assert txns[0].currency == "£"


def test_missing_columns_helpful_error(tmp_path):
    path = _write(tmp_path, "weird.csv", """
        Foo,Bar
        1,2
    """)
    with pytest.raises(ValueError, match="Couldn't find column"):
        import_csv(path)


def test_skips_blank_and_zero_rows(tmp_path):
    path = _write(tmp_path, "gaps.csv", """
        date,merchant,amount
        2026-06-01,Spotify,9.99
        ,,
        2026-06-02,Active card check,0.00
    """)
    txns = import_csv(path)
    assert len(txns) == 1 and txns[0].merchant == "Spotify"


# ---------------------------------------------------------------- merchants

@pytest.mark.parametrize("raw,expected", [
    ("PAYPAL *NETFLIX 35314369001", "Netflix"),
    ("CARD PAYMENT TO NETFLIX.COM,9.99 GBP, RATE 1.00/GBP ON 03-06-2026", "Netflix.Com"),
    ("SQ *BLUE BOTTLE COFFEE", "Blue Bottle Coffee"),
    ("DIRECT DEBIT PAYMENT TO BRITISH GAS", "British Gas"),
    ("AMZN MKTP *UK 1234567890", "Uk"),
    ("Spotify", "Spotify"),
])
def test_normalize_merchant(raw, expected):
    assert normalize_merchant(raw) == expected
