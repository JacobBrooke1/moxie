"""Receipt capture — email + photo. Stubbed in the scaffold.

Design intent (see SECURITY.md): support LOCAL OCR (Tesseract) and a local/offline
model so receipt images and their contents never have to leave the machine.
"""
from __future__ import annotations


def ocr_receipt(image_path: str):
    """Turn a photo of a paper receipt into a Receipt via OCR.

    TODO: local OCR (pytesseract) or an offline model, then parse merchant/date/amount.
    Not implemented in the scaffold.
    """
    raise NotImplementedError(
        "OCR not implemented yet — planned: local Tesseract / offline model so images stay on-device."
    )


def ingest_email_receipts(*args, **kwargs):
    """Scan a mailbox (read-only) and extract receipts / invoices / bills.

    TODO: read-only IMAP/Gmail scan + parser. Not implemented in the scaffold.
    """
    raise NotImplementedError("Email receipt ingestion not implemented yet.")
