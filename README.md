# GitHub Evidence Oracle

A reusable GenLayer Intelligent Contract that answers one question:

> Does **this file**, at **this exact commit**, support **this claim**?

The answer is reached by validator consensus, stored onchain under a
content-addressed id, and readable by any other contract through a view call.
`BountyBoard` is included as an example consumer: it pays out only when the
oracle has confirmed the work.

Live on **GenLayer Bradbury testnet (chain 4221)**:

| Contract | Address |
|---|---|
| `EvidenceOracle` | [`0xAc46eBFC51A34f171AB9E0146a62F4f0df8a541A`](https://scan.bradbury.genlayer.com/address/0xAc46eBFC51A34f171AB9E0146a62F4f0df8a541A) |
| `BountyBoard` (example consumer) | [`0xB7C85027b8D16D3e50413E8F3761D477c7375168`](https://scan.bradbury.genlayer.com/address/0xB7C85027b8D16D3e50413E8F3761D477c7375168) |

## Why commit-pinned evidence

A claim checked against a web page is only as good as the page at the moment
the validators read it. Pages change, render differently for each node, and
cannot be re-checked later. This oracle only accepts evidence addressed by
`owner / repo / 40-char commit SHA / path` and fetches the raw bytes from
`raw.githubusercontent.com`. Content at a commit never changes, so:

- every honest validator downloads **byte-identical** evidence,
- the stored `content_hash` (SHA-256 of those bytes) can be re-checked by
  anyone, at any time, with `curl ... | shasum -a 256`,
- branch names such as `main` are rejected, because they move.

## How consensus is used

`verify` runs its non-deterministic work in `gl.vm.run_nondet` with a custom
validator instead of a generic equivalence prompt.

**Leader:** fetch the pinned file, hash it, and ask the model for
`SUPPORTED`, `REFUTED` or `INSUFFICIENT` with a one-line reason. If the file
cannot be fetched, the result is `UNAVAILABLE` and the model is not called.

**Each validator** (`validator_agrees` in the contract) rejects the proposal if any of these is true:

| Check | Why |
|---|---|
| The result is malformed, has an unknown verdict, or the reason is empty or over 280 chars | Never store unvalidated model output |
| `UNAVAILABLE` is claimed while the HTTP status is 200, or a verdict is claimed for a failed fetch | A leader cannot hide an outage or invent evidence |
| The validator's own HTTP status differs | Different view of the source |
| The validator's own `content_hash` differs | Different bytes, so the judgement is not comparable |
| The validator's own model verdict differs | Only the judgement itself is left to compare |

The cheap, exact checks run first. A validator only pays for a model call
once it has confirmed it is judging the same bytes as the leader. The free-text
`reason` is taken from the leader and is not compared word for word.

Model output is treated as untrusted. The evidence is fenced in `<evidence>`
tags and labelled as untrusted, which guards against prompt injection from
repository content. Anything outside the three allowed verdicts becomes
`INSUFFICIENT`.

## State design

```
checks:    TreeMap[str, Check]    # check_id -> record
check_ids: DynArray[str]          # insertion order, for enumeration
```

`check_id = sha256(owner/repo@commit:path + "\n" + claim)[:24]`. Owner and repo
names are lower-cased, because GitHub treats them case-insensitively. The path
keeps its case, and whitespace in the claim is collapsed. The same question
therefore always maps to the same record:

- asking again returns the stored answer with **no web or model call**,
- another contract can compute the id without trusting the caller
  (`compute_check_id` is a view).

Each `Check` stores requester, owner, repo, commit, path, claim, verdict,
reason, content_hash, http_status and checked_at.

## API

| Method | Kind | Purpose |
|---|---|---|
| `verify(owner, repo, commit, path, claim) -> dict` | write | Check a claim, or return the cached record |
| `compute_check_id(owner, repo, commit, path, claim) -> str` | view | Deterministic id, validates input |
| `has_check(check_id) -> bool` | view | |
| `get_check(check_id) -> dict` | view | Full record |
| `get_check_count() -> int` / `get_check_id_at(i) -> str` | view | Enumerate |

Input rules: owner and repo use only `[A-Za-z0-9-_.]`. The commit must be a
40-char hex SHA. The path has no `..`, `.` or empty segments. The claim is at
most 500 characters.

## Using it from another contract

```python
oracle = gl.contract.get_at(Address(ORACLE_ADDRESS)).view()

check_id = oracle.compute_check_id(owner, repo, commit, path, requirement)
if not oracle.has_check(check_id):
    raise gl.vm.UserError("Call EvidenceOracle.verify first")
if oracle.get_check(check_id)["verdict"] != "SUPPORTED":
    raise gl.vm.UserError("Requirement not met")
```

The consumer makes no model calls and pays no LLM cost. It relies on a verdict
that already went through consensus. `contracts/bounty_board.py` is a complete
example:

1. A sponsor calls `create_task(owner, repo, path, requirement, reward_points)`.
2. A solver lands a commit and calls `EvidenceOracle.verify(...)` with the task's requirement.
3. The solver calls `claim_task(task_id, commit)`. The board recomputes the
   check id through the oracle, requires `SUPPORTED`, marks the task solved,
   and credits the solver's reputation. A task can only be solved once.

## Live run on Bradbury

The evidence below is a real file: `contracts/proofcheck_studio.py` in
`longbds0108/ProofCheck`, checked at two commits.

| Step | Result | GenLayer tx id |
|---|---|---|
| Deploy `EvidenceOracle` | FINISHED_WITH_RETURN | `0xa26f7ff0fed7168a93a2904da7f6f23d76c7ae1888fdc9b36a1ada346ee5acbd` |
| Deploy `BountyBoard(oracle)` | FINISHED_WITH_RETURN | `0xf2cae66a0d3657bf55e38be321467e4701f21740041295d437178d3a70fcaa84` |
| `verify` at `c89294e` | **SUPPORTED**, validators agreed | `0xf526195238d6cc455a782f3a6d68f883658d800d30e38664eeaf857eb48d4c20` |
| `verify` at `471e4c0` (before the feature existed) | **REFUTED**, validators agreed | `0x367e64c79f0ee999c388b3bd2e8fe7849c36d4f2a81f1f43748e9688a30a0204` |
| `create_task` T1 (cross-contract view to the oracle) | FINISHED_WITH_RETURN | `0x751c1fdce6c94ac58d3ccfdb77fab4a927845c225605fcfa9348f17c7d7b7644` |
| `claim_task(T1, c89294e)` | **SOLVED**, +50 reputation | `0xd0593c2af4c0684d5132790a8296cb8fb425becea286e4c6c01e5572f6626b85` |
| `claim_task(T2, 471e4c0)` (simulated) | Rejected: `Oracle verdict is REFUTED, not SUPPORTED` | — |

The claim was: *"The contract stores the wallet address that submitted each
verification."* At `471e4c0` the oracle answered *"The Verification struct has
no wallet address field"*. At `c89294e` it cited the `submitter` field. The
stored hash for `c89294e` is
`2dff36e353bb26f122f0f67cdeca2f18db3b51fdf3a78544768cb40fdf341106`. It matches:

```bash
curl -s https://raw.githubusercontent.com/longbds0108/ProofCheck/c89294e00e2a805be318bac764957cc622610c6f/contracts/proofcheck_studio.py | shasum -a 256
```

To read everything yourself (view calls, no wallet needed):

```bash
npm install
node scripts/inspect.mjs                           # list checks and tasks
node scripts/inspect.mjs 5d0113f584af22a77de788f3  # one check + local hash re-check
```

## Tests

```bash
cd tests && python3 -m unittest -v      # 28 tests, standard library only
```

`tests/fake_genlayer.py` is a small stand-in for the `genlayer` package. It
serves web and model responses from the test, runs `run_nondet` with several
simulated validators, and dispatches cross-contract view calls. The tests cover:

- input validation (branch names, short SHAs, path traversal, oversized claims),
- id stability and sensitivity,
- parsing hostile or malformed model output,
- prompt fencing,
- caching (a repeated question makes no web or model call),
- `UNAVAILABLE` handling,
- every validator rejection rule: different verdict, different bytes, a hidden
  outage, malformed results, and a hash mismatch caught before the model call,
- the full BountyBoard flow.

The fake does not emulate GenVM itself. The Bradbury run above covers that.

## Limits and trade-offs

- **Borderline claims may not reach consensus.** Validators must reach the
  same verdict on their own. When a claim is truly ambiguous, they can split
  and the transaction will not be accepted. That is deliberate: no answer is
  better than a coin flip stored as fact. Phrase claims so a single file can
  settle them.
- Only the first 16,000 characters of a file are shown to the model. The hash
  always covers the full file.
- Only public repositories, and text files that decode as UTF-8.
- The reason is the leader's wording. Only the verdict is consensus-checked.

## Deploying on Bradbury

Two things that are easy to get wrong:

- The runner header must be the **first line and stand alone**. GenVM reads
  the whole leading comment block as the runner JSON, so put a blank line
  before any other comment. These contracts pin
  `py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0`, the
  runner that Bradbury accepted at the time of writing.
- `rpc.testnet-chain.genlayer.com` does not serve `gen_call`. Use
  `https://rpc-bradbury.genlayer.com` for reads and fee simulation.

```bash
npx genlayer@0.40.0-rc.6 network set testnet-bradbury
npx genlayer@0.40.0-rc.6 deploy --contract contracts/evidence_oracle.py --fees '<distribution>' --fee-value <wei>
npx genlayer@0.40.0-rc.6 deploy --contract contracts/bounty_board.py --args <oracle address> --fees '<distribution>' --fee-value <wei>
```

Take `--fees` and `--fee-value` from `genlayer estimate-fees`. A deploy with no
fee is rejected with `FeeValueMustBeNonZero`.

## License

MIT
