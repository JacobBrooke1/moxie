---
name: Dispute a card charge via NatWest (UK)
merchant: "*"
action_type: dispute_charge
channel: deeplink
url: https://www.natwest.com/support-centre/report-a-problem/dispute-a-card-transaction.html
---

# Dispute a card transaction via NatWest

The bank-route skill: when a merchant won't fix a duplicate/wrong charge,
your card issuer can claw it back (chargeback under the card scheme rules;
Section 75 for credit-card purchases over £100).

## Steps

1. First give the merchant a chance: send the dispute email with the receipt
   attached and allow 14 days.
2. No fix? Open the NatWest dispute page (this skill's link) or the app:
   Help → Dispute a transaction.
3. Have ready: the transaction date/amount, the merchant's reply (or silence),
   and the receipt — Moxie keeps both in the audit log.
4. Chargebacks have scheme deadlines (typically 120 days from the charge) —
   don't sit on it.

## Notes

- Debit card → chargeback. Credit card and over £100 → also Section 75,
  which is a legal right, not a goodwill gesture. Say the words.
- Keep everything in writing; phone summaries evaporate.
