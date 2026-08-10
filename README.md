# UNTP Publisher Service

The UNTP Publisher is a service that helps lines of business publish **UNTP-aligned** verifiable credentials. Issuance runs through **BC Traction** (tenant APIs); this repository is the publisher API and supporting logic.

Publication payloads supply ``template``, ``version``, and ``data``. Entity and cardinality
are resolved from ``data`` via ``x-publisher-pointers`` in
``configs/credentials/{type}/{version}/data.schema.json``.

The goal is for published data to be queryable and usable by BC-registered organizations in their business processes and transactions.

For operational guides, see the [`docs/`](docs/) directory (admin workflows, client integration, and [landing/discovery UI design](docs/ui-design-landing-discovery.md)). Environment Helm overlays live under [`deploy/`](deploy/).

Contributions require a [Developer Certificate of Origin](https://developercertificate.org/) sign-off on each commit; see [CONTRIBUTING.md](CONTRIBUTING.md).
