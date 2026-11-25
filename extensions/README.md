# Extension sources

Each extension lives in a directory named after its immutable reverse-DNS ID:

```text
extensions/<id>/
├── manifest.toml
├── main.rhai
├── README.md
└── LICENSE
```

The first validated example, `org.example.bus-departures`, is tracked separately in kiosk issue #141. This scaffold intentionally contains no provider URL, stop ID, entity ID, or personal data.

A source directory must have a matching entry in the signed `index.json` before release. CI validates the manifest, permissions, required files, safe paths, and package metadata. Extensions are untrusted third-party code: a catalog signature proves provenance, not safety.
