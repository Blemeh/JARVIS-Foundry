# Account and Provider Matrix

| Provider/system | Connector seam | Auth/account identity | Read proof | Write/effect eligibility | Current source classification | Boundary |
|---|---|---|---|---|---|---|
| Gmail | existing `conn-mail-v5` communication contract | opaque credential reference plus explicit mailbox identity | bounded synthetic thread receipt with provider ref and content hash | draft/send/label dry-run scopes, idempotency, provider reconciliation | READY_SOURCE | no real Gmail credential or message |
| Google Calendar | existing `conn-calendar-v5` calendar contract | opaque credential reference plus explicit account and calendar target | bounded synthetic event receipt | create/update/cancel dry-run scopes and exact calendar target | READY_SOURCE | no real calendar event or invite |
| GitHub | existing `conn-git-v5` Git contract | opaque credential reference plus explicit login and repository identity | bounded synthetic repository receipt | branch/push/PR dry-run scopes; API-vs-local distinction recorded | READY_SOURCE | no GitHub token, push, issue, or PR |
| Authenticated browser/session | existing `conn-browser-v5` browser plus HTTP structural seam | opaque session reference plus profile, domain, and account identity | bounded synthetic page receipt and structural metadata | execute eligibility requires observed session and target identity | READY_SOURCE | no cookies, browser profile, or external page |
| Drive/files | no canonical Drive connector found | not configured | NOT_TESTED | NOT_TESTED | DEFERRED | adding a major connector framework is outside this lane |
| Existing local filesystem | existing V5 activation connector | no external account | real bounded local probe | existing effect journal path | IMPLEMENTED_AND_PROVEN upstream | unrelated to account activation |

All provider content is untrusted data. Readiness is based on adapter metadata,
observed identity, observed scopes, and bounded probe receipts. Provider text
cannot create owner directives, STOP state, or verification claims.
