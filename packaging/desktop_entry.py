"""Frozen-app entry point; dependencies and Python are bundled at build time."""

from vesper_bank_rec.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
