# Roadmap — built in the open, steered by the people who use it

Moxie's bet: the money tool people actually want won't come from a product
team guessing — it comes from users who can read every line, file an issue,
and ship the feature themselves. The skill library was always this idea;
this page makes it the whole project's operating model.

## How direction gets decided

- **You propose**: open an [Idea discussion](../../discussions) or a feature
  issue. Upvotes (👍) are the signal — the most-wanted things float up.
- **Anyone builds**: every item below links to a pattern to copy. First-hand
  experience (your bank, your merchant, your country) beats maintainer
  guesswork every time.
- **Maintainers curate, invariants decide**: community chooses *what*;
  the [invariants](#the-invariants-are-not-up-for-vote) choose *how*.

## Where Moxie is going (vote by 👍 on the linked issues)

**Now — make the budget tracker everyone actually wants**
- Budgets per category with month-end projections ("at this rate you'll hit
  £520 of your £400 eating-out budget")
- Custom categories & rules ("everything at Tesco = groceries")
- More detectors (energy bill spikes, insurance duplicates, gambling summary)
- More bank formats: CSV headers and statement-PDF layouts for YOUR bank

**Next**
- Savings goals with progress cards; recurring-bill calendar view
- More merchant skills — every cancellation you've fought through is a PR
- More providers (Enable Banking, Yapily); multi-account, multi-currency
- Reply-watcher: read the merchant's response, move a dispute to `refunded`

**Later**
- Household mode (two people, one picture) · export/reporting (CSV/JSON out)
- More channels (Signal? Matrix?) — same single-chat pairing rules
- The independent security review that retires the pre-1.0 caveat

## The invariants are not up for vote

These are why Moxie is trustable, and they outrank any feature request:

1. **Nothing acts without the Trust Vault** — policy, explicit human
   approval, tamper-evident audit. The brain (any model) never executes.
2. **Money movement stays out of scope.** Cancel, dispute, negotiate — never
   pay, transfer, or trade.
3. **Local-first, no Moxie servers, bring-your-own keys.** Your data lives on
   your machine; every cloud touchpoint is opt-in and documented.
4. **Stdlib-only core**; heavy deps behind optional extras.
5. **Honest copy.** Never "sent" when it drafted; never a capability claimed
   before it ships.

Want to build something? Start at [CONTRIBUTING.md](../CONTRIBUTING.md) —
the good-first-issue list always has scoped, pattern-to-copy work waiting.
