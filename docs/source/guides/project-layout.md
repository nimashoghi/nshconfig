# Project layout

Use ordinary Python modules for schemas, preset helpers, experiments, and execution:

```text
project/
    schemas.py
    presets.py
    experiments/
        wide.py
    train.py
```

An experiment module can expose `config = make_config()` or a typed builder function. The entry point imports it and calls `train(config.finalize())`. Apply command-line values with ordinary Python argument parsing and typed assignments. Sweeps can loop over builder calls or `.copy()` drafts.

The repository's `examples/protenix.py` is a runnable reduced case study. It uses one root `c_z` for Pairformer and diffusion, plus an aligned dataset/weight check. It reflects `boltz-bio/protenix` commit `c28e67887dceb144c219cea5bc594fbbcbd27822`; it is not a complete migration of that application.
