"""A small in-memory stand-in for the GenVM `genlayer` package.

It is just enough to execute the contracts in this repo with plain CPython:
- storage types behave like dict / list,
- `gl.nondet.web.get` and `gl.nondet.exec_prompt` are served by the test,
- `gl.vm.run_nondet` runs the leader once, then the validator function once per
  simulated validator, and fails like consensus would if any of them disagree,
- `gl.contract.get_at(...).view()` dispatches to contracts registered in `World`.

It does not emulate GenVM itself (sandboxing, gas, calldata encoding). Those are
covered by the Bradbury deployment described in the README.
"""

import dataclasses
import importlib.util
import pathlib
import sys
import types


class World:
    """Mutable test environment shared by the fake modules."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.web = {}  # url -> (status, body bytes)
        self.validator_web = None  # optional url -> (status, body) seen by validators
        self.llm = lambda prompt: '{"verdict": "INSUFFICIENT", "reason": "default"}'
        self.validator_llm = None  # optional override for validators
        self.validators = 4
        self.sender = "0x" + "11" * 20
        self.contracts = {}
        self.calls = {"web": 0, "llm": 0}
        self._in_validator = False


WORLD = World()


class Disagree(Exception):
    """Raised when a simulated validator rejects the leader's result."""


class UserError(Exception):
    pass


@dataclasses.dataclass
class Return:
    calldata: object


@dataclasses.dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class Address:
    def __init__(self, value):
        value = str(value)
        if not value.startswith("0x") or len(value) != 42:
            raise ValueError("bad address")
        self._hex = value

    @property
    def as_hex(self):
        return self._hex

    def __eq__(self, other):
        return isinstance(other, Address) and other._hex.lower() == self._hex.lower()

    def __hash__(self):
        return hash(self._hex.lower())


class u256(int):
    def __new__(cls, value=0):
        value = int(value)
        if value < 0 or value >= 2**256:
            raise OverflowError("u256 out of range")
        return super().__new__(cls, value)


class TreeMap(dict):
    def __class_getitem__(cls, item):
        return cls


class DynArray(list):
    def __class_getitem__(cls, item):
        return cls


def _web_get(url, headers=None):
    WORLD.calls["web"] += 1
    table = WORLD.validator_web if (WORLD._in_validator and WORLD.validator_web is not None) else WORLD.web
    status, body = table.get(url, (404, b"Not Found"))
    return Response(status=status, headers={}, body=body)


def _exec_prompt(prompt, **kwargs):
    WORLD.calls["llm"] += 1
    fn = WORLD.validator_llm if (WORLD._in_validator and WORLD.validator_llm is not None) else WORLD.llm
    return fn(prompt)


def _run_nondet(leader_fn, validator_fn):
    result = leader_fn()
    WORLD._in_validator = True
    try:
        for _ in range(WORLD.validators):
            if not validator_fn(Return(result)):
                raise Disagree("validator disagreed")
    finally:
        WORLD._in_validator = False
    return result


class _ViewProxy:
    def __init__(self, target):
        self._target = target

    def __getattr__(self, name):
        method = getattr(self._target, name)
        return method


def _get_at(address):
    return types.SimpleNamespace(view=lambda **_: _ViewProxy(WORLD.contracts[Address(address.as_hex)]))


class _Message:
    @property
    def sender_address(self):
        return Address(WORLD.sender)


def install():
    """Register fake `genlayer`, `genlayer.storage` and `genlayer.types` modules."""
    gl = types.ModuleType("genlayer")
    storage = types.ModuleType("genlayer.storage")
    types_mod = types.ModuleType("genlayer.types")

    storage.allow = lambda cls: cls
    storage.TreeMap = TreeMap
    storage.DynArray = DynArray

    types_mod.Address = Address
    types_mod.u256 = u256
    types_mod.__all__ = ["Address", "u256"]

    gl.storage = storage
    gl.types = types_mod
    gl.contract = types.SimpleNamespace(Contract=object, get_at=_get_at)
    gl.public = types.SimpleNamespace(view=lambda f: f, write=lambda f: f)
    gl.vm = types.SimpleNamespace(UserError=UserError, Return=Return, run_nondet=_run_nondet)
    gl.nondet = types.SimpleNamespace(web=types.SimpleNamespace(get=_web_get), exec_prompt=_exec_prompt)
    gl.message = _Message()

    sys.modules["genlayer"] = gl
    sys.modules["genlayer.storage"] = storage
    sys.modules["genlayer.types"] = types_mod


def load_contract(filename):
    install()
    path = pathlib.Path(__file__).resolve().parent.parent / "contracts" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def deploy(module_cls, address, *args):
    """Instantiate a contract and register it at `address` for cross-contract calls."""
    instance = module_cls.__new__(module_cls)
    for name, annotation in getattr(module_cls, "__annotations__", {}).items():
        if annotation is TreeMap:
            setattr(instance, name, TreeMap())
        elif annotation is DynArray:
            setattr(instance, name, DynArray())
    instance.__init__(*args)
    WORLD.contracts[Address(address)] = instance
    return instance
