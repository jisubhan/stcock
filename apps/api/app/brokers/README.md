# Read-only broker boundary

Strategy code never calls a broker. The current primary client is
`toss.documented.OfficialTossClient` (`DocumentedTossClient` alias), driven by the
checked-in official OpenAPI 1.2.19 read-only response contract. It accepts injected
client ID/secret bindings, sends documented form OAuth, and keeps the token only
in memory. It hardlocks the HTTPS origin, preserves TLS checking, refuses redirects
and order/account routes, validates decimal strings and nullable official schemas,
and uses bounded retries with rate headers. Token renewal is centralized because
Toss supports only one valid token per client.

`run_probe` is a bounded discovery path: it does not pretend venue verification
already exists. It verifies quote/candle schemas and an NXT-supported stock against
actual calendar sessions. Venue proof requires nonzero KRW minute candles inside
the documented extended sessions and explicitly records that the API has no
individual venue discriminator. Probe output contains only enumerated status,
HTTP/error codes, counts, timestamps, and nonsecret evidence references.

The actual environment's one OAuth call returned 403 `access_denied`, with the exact
fixed IP-not-allowed condition. No token or authenticated market sample was obtained.
The service latches this refusal until explicit refresh after the external IP setting
changes; it does not repeat the rejected request on restart or in background loops.
See the API README for the precise retry workflow and `TOSS_DATA_MODE=demo` override.

The older `TossMarketData` adapter remains available as an optional generic,
user-verified manifest interface (`TOSS_API_MANIFEST`). It is separate from the
current official ingestion pipeline. Its small schema vocabulary, field mappings,
and legacy `TOSS_ACCESS_TOKEN` injection do not replace the verified current-client
contract or the complete normalized snapshot quality gate. No adapter exposes
real-order methods, and frontend code receives neither secret bindings nor tokens.
