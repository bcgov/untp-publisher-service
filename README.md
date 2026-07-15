# UNTP Publisher Service

The UNTP Publisher is a service that helps lines of business publish **UNTP-aligned** verifiable credentials. Issuance runs through **BC Traction** (tenant APIs); this repository is the publisher API and supporting logic.

Publication payloads supply ``template``, ``version``, and ``data``. Entity and cardinality
are resolved from ``data`` via JSON Pointers declared on the credential type in
``configs/issuers.yaml`` (``pointers.entity``, ``pointers.cardinality``).

The goal is for published data to be queryable and usable by BC-registered organizations in their business processes and transactions.

For operational guides, see the [`docs/`](docs/) directory (admin workflows and client integration). Environment Helm overlays live under [`deploy/`](deploy/).

Contributions require a [Developer Certificate of Origin](https://developercertificate.org/) sign-off on each commit; see [CONTRIBUTING.md](CONTRIBUTING.md).
