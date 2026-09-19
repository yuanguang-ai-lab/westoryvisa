# Supplemental unchanged production frontend assets

These files supplement the existing 39-file public frontend snapshot. They were
copied from the static root captured from `deploy-frontend-1` on 2026-09-20,
without changing the running container. The corresponding public URLs and local
language work had already identified the extension page, account script and
intake dictionary. The extension download is preserved byte-for-byte.

Source image: `westoryvisa-frontend:navigation-20260914`.

Included: `extension.html`, `extension-account.js`, `intake-i18n.js`,
`downloads/WestoryVisa-Chrome-1.0.5.zip`, `index.html`, `50x.html`.
The last two are the existing Nginx default pages, not the product homepage.

No backend code, customer data, environment files, private keys, or macOS
resource-fork metadata are included here. Combine this directory (excluding
this README) with `../public-frontend/` to inspect the previous static content.
