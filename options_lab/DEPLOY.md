# Share the interactive desk

## Current state

Deployment files are prepared. A public application URL has **not** been created
or verified. GitHub contains the source code; the existing GitHub Pages site
continues to serve the original inventory research report.

## Free public demo: Streamlit Community Cloud

Sign in at <https://share.streamlit.io/> and create an app from the public GitHub
repository using these exact settings:

| Field | Value |
|---|---|
| Repository | `yz3639-gif/cushing-wti-research` |
| Branch | `main` |
| Main file | `options_lab/cloud_app.py` |
| Python, under Advanced settings | `3.12` |
| Suggested app subdomain, subject to availability | `antony-zuo-wti-options` |
| Secrets | None |

The entrypoint-adjacent `options_lab/requirements.txt` supplies the five direct
runtime dependencies. The root `.streamlit/config.toml` supplies the theme.
The public demo requires no data-provider account or paid feed.

The public entrypoint retains volatility editing, Preview / Apply, indicative
quotes, integer hedges, stress analysis and replay. It accepts only the bundled,
explicitly synthetic demonstration. Snapshot, saved-session and volatility-file
uploads are not exposed, and it does not initiate provider connections. The
local `options_lab/app.py` continues to support authorized local files.

The page title and author credit remain **WTI Options Desk | Antony Zuo** and
**Built by Antony Zuo**. The hosting service may expose its own platform controls
or branding independently of the application.

## Before sharing the URL with John

1. Wait for the cloud build to succeed and record the actual assigned URL.
2. Set the app's viewing access to public. Open that URL in a separate unsigned-in
   browser and confirm it does not request the developer's account.
3. Load the synthetic demo. Shift CSO volatility, Preview, Apply, and inspect the
   updated quote, hedge ticket and residual risk. Confirm the synthetic label
   remains visible.
4. Confirm the page remains usable when the developer's local server is stopped.
5. Share the verified application URL, with the GitHub repository as a source link.

Local tests do not establish that a cloud deployment or anonymous-access check
has completed. Do not publish an intended subdomain as a working application URL.

Official references: [Deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy),
[dependency discovery](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies),
[app sharing](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app).
