# Vendored front-end libraries

Served locally so the demo UI works offline and nothing executes from a CDN.

| File | Library | Version | Source | SHA-384 |
|---|---|---|---|---|
| `marked.min.js` | marked (Markdown parser) | 12.0.2 | https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js | `fd341bb4b0807ab0b78e06a29be37bf116520d857baf27a804256a4eecd1ac579cd9a91f0641d2ec0090dcf421bcc562` |
| `purify.min.js` | DOMPurify (HTML sanitizer) | 3.1.6 | https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js | `f957d43c46f43ddb4284cc2605c066ca444c0ddfafe83fe8166077ad933fa6e08c0d8708bc5f7af0e8a6461e0a418f5a` |

Every Markdown render goes through `DOMPurify.sanitize(marked.parse(text))` in `app.js`.
To upgrade: download the new file, verify its hash with `sha384sum`, update the version and hash here.
