# Binged refresh validation

The manual audit signs in through the provider's normal PIN flow on each configured origin and checks approved sample HLS manifests, encryption keys, and media segments. The complete-lineup workflow validates exact current catalog matches in the privately preserved selected lineup. Both workflows stop on provider authorization or rate-limit denials and never publish a playlist.

Inputs are encrypted Actions secrets: BINGED_HOUSE_PIN, BINGED_LINEUP_GZIP_1, BINGED_LINEUP_GZIP_2, DROPBOX_APP_KEY, and DROPBOX_REFRESH_TOKEN. The two lineup parts contain one gzip/base64 payload with original stream session values removed. Do not commit selected playlists, provider sessions, PINs, refresh credentials, or generated HTML. Public report artifacts omit selected lineup names and session URLs.

The Dropbox app is restricted to its own App Folder. Publishing and scheduling remain separate approval-gated steps. A valid manifest is not proof of decoded video or channel identity.

Offline checks: `python -m unittest discover -s tests -v`.
