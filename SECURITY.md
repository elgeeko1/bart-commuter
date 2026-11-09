# Security

## Reporting a vulnerability

Report vulnerabilities privately through this repository's Security tab, under Report a vulnerability. Do not open a public issue.

## How updates reach your iPhone

The script you paste into Scriptable is a loader. It downloads the widget's engine, `bart-commuter.js`, from this repository's latest GitHub release over HTTPS, checks it against the SHA-256 in that release's manifest, and runs it. Releases are not signed and the manifest ships in the same release, so whoever can publish or edit a release here can change what every installed widget runs. Releases are published only by the [Release workflow](.github/workflows/release.yml), from a version tag. [Updates](doc/DEVELOPMENT.md#updates) describes the loader in detail.

The widget sends requests only to BART's servers at `api.bart.gov` and to GitHub for updates, and stores only its installed engine and update state on the device.
