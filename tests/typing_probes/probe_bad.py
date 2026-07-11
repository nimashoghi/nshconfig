"""Golden typing probe: every BAD line must produce a basedpyright error."""

import nshconfig as C


class LayerNorm(C.Config):
    dim: int = 32


class Model(C.Config):
    dim: int = 768
    norm: LayerNorm


bad_default: int = C.interp(lambda context: "oops")  # BAD[reportAssignmentType]

work = C.draft(Model)
work.norm.dim = C.interp(lambda context: "oops")  # BAD[reportAttributeAccessIssue]
work.norm.dim = C.interp(
    lambda context: context.current(
        LayerNorm
    ).missing  # BAD[reportAttributeAccessIssue]
)
work.norm.dim = C.interp(  # BAD[reportAttributeAccessIssue]
    lambda context: context.parent(Model).norm
)
work.norm.dmi = 3  # BAD[reportAttributeAccessIssue]
work.dim = "1024"  # BAD[reportAttributeAccessIssue]
bad_result: str = C.finalize(work)  # BAD[reportAssignmentType]

C.Field(default=1)  # BAD[reportAttributeAccessIssue]
work.config_finalize()  # BAD[reportAttributeAccessIssue]
