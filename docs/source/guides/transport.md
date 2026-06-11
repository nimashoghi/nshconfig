# Transport and run records

The flagship flow: compose a draft in a notebook, ship it to a cluster, finalize there.

```python
import cloudpickle

# notebook side: classes may live in the notebook itself (__main__)
cfg = TrainConfig.config_draft()
cfg.model.dim = 2048
cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)  # still pending!
payload = cloudpickle.dumps(cfg)

# cluster side: only nshconfig + cloudpickle needed
d = cloudpickle.loads(payload)
assert C.is_draft(d)
final = C.finalize(d)        # interpolation resolves on the far side
```

Pending drafts round-trip with their interpolations, provenance, and source sites intact, so a
finalize error on the cluster cites the original notebook line.

## Which pickle, when

| Object | plain `pickle` | `cloudpickle` |
|---|---|---|
| Finals | yes | yes |
| Drafts without markers | yes | yes |
| Drafts with `interp(lambda ...)` | no (lambdas) | yes |
| Drafts with `interp(named_module_fn)` | yes | yes |
| Notebook-defined config classes | no | yes (shipped by value) |

cloudpickle is the supported notebook-to-cluster channel; pin compatible versions on both
ends. nshconfig ships two pieces of transport hardening internally: the resolution validator
is a module-level function (so by-value class pickles never capture interpreter-local state),
and pydantic-core validator/serializer objects are wrapped in a lazy stand-in during pickling
(rebuilding them mid-stream can otherwise fail on cyclic, partially-materialized schemas).

## Run records: pickles are transport, JSON is the record

```python
(run_dir / "config.json").write_text(final.model_dump_json(indent=2))
wandb.config.update(final.model_dump(mode="json"))
```

Finals contain concrete values only (nothing symbolic can survive `finalize`), so the record
is dead data: reproducible by `TrainConfig.model_validate_json(...)` at the recorded git SHA,
with no code re-execution. A config `.py` is a program, not a record; keep it in git and point
at it from the run's provenance metadata. Never archive a pickle as the only copy of a run's
config.
