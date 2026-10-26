# Pinned API schema

[`openapi-0.2.0-2026-10-26.json`](openapi-0.2.0-2026-10-26.json) is a verbatim snapshot of the
service's OpenAPI description, retrieved on **26 October 2026**, with `info.version` **0.2.0**.
It records the endpoints and wire shapes checked for the 0.1.0 release. The previous snapshot,
retrieved on 20 September 2026 and used while implementing the SDK, remains in Git history
(`openapi-0.2.0-2026-09-20.json`, SHA-256 `a191f8a7…`).

SHA-256: `3ca93b6e6b9aa8d7820531b78c66ed7a9798b11e6e163ad96b140fb3d75fbcf4`.

The `info.version` did not change between the two snapshots although the contract did (Jev 1.14
added image assets, box questions and answers, danger-zone headers, a multipart request body and
a documented 400 error shape), so the SHA-256 is the only drift signal the maintenance monitor has.

Use the [SDK compatibility guide](../PARITY.md) for Java-specific validation and observed
service differences. This snapshot is not a promise that the live service is unchanged;
the service publishes its current description at `https://api.typesafe.ai/openapi.json`.
