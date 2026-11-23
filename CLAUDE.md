# subito-scanner

Standalone Subito.it scanner: a Python library and a `subito-scanner` command line tool, with no dependency on Personal Collection Hub, pch-marketplace-kit or Opportunity Hunter (a test enforces it). Decisions live in the hub repo (`docs/adr/`): ADR-0011 (read-only access, clean-room rewrite) and ADR-0013 (four-repository architecture).

Source code, identifiers, commit messages and `README.md` are in English. Talk to the user in Italian.

Never copy code from the original SubitoScanner: it has no licence. Fixtures are real responses with ids, names and image URLs replaced by fake values.

Acquisition behaviour (endpoint, params, headers, User-Agent, rate limit, backoff, Retry-After, paging) is frozen: changing it is a separate decision, not part of a refactor.
