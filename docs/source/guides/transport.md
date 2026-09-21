# Transport

Standard pickle supports importable config schemas. Install the optional `transport` extra to use cloudpickle with notebook-defined classes and callbacks:

```python
import cloudpickle

payload = cloudpickle.dumps((draft, draft.finalize()))
restored_draft, restored_final = cloudpickle.loads(payload)
```

Transport preserves the editable/final distinction, interpolation rules, and child ownership. Compiled field validators are process-local; concrete resolved annotations travel with dynamic classes. Subprocess tests exercise eager and postponed annotations across the Python/Pydantic support matrix.

Pickle and cloudpickle execute trusted Python. These payloads are for short-lived transport between compatible environments, not stable archival formats. For a record of completed values, use a final's `.to_dict()` or `.to_json()`.
