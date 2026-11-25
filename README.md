# Home Assistant Kiosk Extensions

Public companion catalog for the post-v1 Home Assistant Kiosk extension shop.

The kiosk fetches `index.json` and `index.json.minisig` over HTTPS. It never clones this repository. Each package is a sandboxed Rhai data script with declared HTTP-host and Home Assistant-entity permissions. A signature proves provenance, not safety.

## Status

This repository is an intentionally empty scaffold. The initial `index.json` has no extensions; the validated bus-departures example is tracked in kiosk issue #141.

`index.json.minisig` is signed with an **ephemeral bootstrap key**. Its public key is in `catalog.pub`, and its secret key was destroyed after signing. Do not configure the bootstrap key as a production trust anchor. Before the first real release, `ABOYrbx` must generate a production Minisign key pair on encrypted offline storage, publish the new public key, sign the reviewed index, and rotate kiosk configuration. The production private key never enters CI or either repository.

## Layout

```text
.
├── index.json
├── index.json.minisig
├── catalog.pub
├── schema/
│   ├── index.schema.json
│   └── manifest.schema.json
├── extensions/
├── scripts/
│   ├── validate.py
│   ├── validate.sh
│   ├── package.py
│   └── package.sh
├── .github/
│   ├── CODEOWNERS
│   └── workflows/validate.yml
└── tests/
```

## Validate locally

Requirements: Python 3.11+, `minisign`, and `requirements-dev.txt`.

```bash
python3 -m pip install --requirement requirements-dev.txt
python3 -m unittest discover -s tests -p 'test_*.py'
scripts/validate.sh
```

Validation rejects unknown fields, duplicate JSON keys, duplicate extension IDs, insecure package URLs, manifest/index permission drift, unsafe entry paths, missing required files, symlinks, and secret defaults. The signature check covers the exact bytes of `index.json`.

## Package an extension

After its source is reviewed and present under `extensions/<id>/`:

```bash
scripts/package.sh org.example.bus-departures 1.0.0
```

The deterministic `.hkext` archive is written to `dist/`. The command prints JSON containing the package path, SHA-256, and byte size. Publish that package as an HTTPS release asset, add its metadata to `index.json`, validate again, and sign the final index offline.

## Security and contributions

- No native code, WASM, arbitrary UI, filesystem/process/environment access, or Home Assistant service calls.
- No secrets, personal stop IDs, real provider URLs, home addresses, or tokens.
- Permissions must be minimal and must match between manifest and signed index.
- Every package includes `manifest.toml`, its entry script, `README.md`, and `LICENSE`.
- CI has read-only permissions and never receives a signing key.

See the kiosk [extension author guide](https://github.com/ABOYrbx/home-assistant-kiosk/blob/main/docs/extensions.md) and [catalog specification](https://github.com/ABOYrbx/home-assistant-kiosk/blob/main/docs/extension-catalog.md) for the full contract.
