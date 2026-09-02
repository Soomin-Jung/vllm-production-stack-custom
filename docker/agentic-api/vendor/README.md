# Offline source bundle

Run `../scripts/prepare-offline-inputs.sh` in a connected staging environment. Transfer the generated
`agentic-api-offline_0.5.0.tar.gz` and `SHA256SUMS` files into this directory before the closed-network image build.

The generated archive is intentionally not committed. It contains the exact Agentic API v0.5.0 Git tree, the locked
Cargo dependency sources created by `cargo vendor --locked --versioned-dirs`, and a source manifest.
