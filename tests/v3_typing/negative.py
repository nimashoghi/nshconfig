import nshconfig as C


class Run(C.Config):
    name: str
    c_z: int = 128


Run()  # error: missing required argument
Run(name=1)  # error: argument type
Run(name="x", typo=1)  # error: unknown argument
r = Run.draft()
r.c_z = "wrong"  # error: assignment
r.typo = 1  # error: unknown attribute
r.c_z = C.interp(lambda c: c.root(Run).name)  # error: interpolation type
r.name = C.interp(lambda c: c.root(Run).typo)  # error: callback attribute
Run.draft(name="x")  # error: draft accepts no arguments
