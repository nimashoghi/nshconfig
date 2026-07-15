# Trusted executable transport

Normal pickle works by reference for importable Config classes. Install the
transport extra when notebook-local classes or interpolation lambdas must cross a
process boundary:

```bash
pip install --pre 'nshconfig[transport]'
```

```python
import cloudpickle


work = Run.config_draft()
work.seed = 7

payload = cloudpickle.dumps(work)
received = cloudpickle.loads(payload)
run = received.config_finalize()
```

Cloudpickle transports executable Python objects. Never load a pickle from an
untrusted source. Sender and receiver should use the same Python version and
compatible nshconfig, Pydantic, and project dependencies.

Drafts retain their assigned values and interpolation callables. Finals retain
their concrete validated values. A final does not become a draft and cannot
reconstruct the original composition recipe.

Config classes may use eager annotations, quoted forward references, or
`from __future__ import annotations`. During by-value transport, each Config
class defers reconstruction of its own Pydantic core validator and serializer
until cloudpickle has restored the full dynamic class. This avoids partially
materialized cyclic schemas without imposing a special annotation style.

Importing nshconfig does not install global pickle reducers for Pydantic core
types. Unrelated Pydantic models keep their normal pickle behavior.

Cloudpickle is appropriate for trusted, short-lived executable transport. For
durable run metadata, serialize the final's concrete values using the application's
chosen format and versioning policy. nshconfig does not define a record envelope
or stable content fingerprint.
