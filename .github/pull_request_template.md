**What does this change?**

**Checklist**

- [ ] `pytest -q` is green (new paths have tests; external calls use injectable fakes)
- [ ] Core stays stdlib-only (new heavy deps go behind an optional extra, imported lazily)
- [ ] Nothing bypasses the Trust Vault (policy → approval → audit)
- [ ] Copy stays honest — never "sent" when it drafted
