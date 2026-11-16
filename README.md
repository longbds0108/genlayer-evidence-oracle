# GitHub Evidence Oracle

A reusable GenLayer Intelligent Contract that answers one question:

> Does **this file**, at **this exact commit**, support **this claim**?

The answer is reached by validator consensus, stored onchain under a
content-addressed id, and readable by any other contract through a view call.
`BountyBoard` is included as an example consumer: it pays out only to the
assigned solver, and only after the oracle has confirmed the work.

Live on **GenLayer Bradbury testnet (chain 4221)**. The deployed code is
byte-identical to the source in this repository (v1.1.0):

| Contract | Address | Deployed source sha256 |
|---|---|---|
| `EvidenceOracle` v1.1.0 | [`0x597D7D61532D83385Aa112cD80B7f5BD7bA164c0`](https://scan.bradbury.genlayer.com/address/0x597D7D61532D83385Aa112cD80B7f5BD7bA164c0) | `b7a8bb9f6c8eb97b…` = `contracts/evidence_oracle.py` |
| `BountyBoard` v1.1.0 (example consumer) | [`0x9895025f21fD990F81671908750cCfCb17562B75`](https://scan.bradbury.genlayer.com/address/0x9895025f21fD990F81671908750cCfCb17562B75) | `99789b933506c188…` = `contracts/bounty_board.py` |

Check it yourself: `npm install && node scripts/inspect.mjs --source` reads the
deployed code from the chain and compares it with `contracts/`.

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

## Outcomes

| Outcome | Meaning | Stored? |
|---|---|---|
| `SUPPORTED` / `REFUTED` / `INSUFFICIENT` | Validators judged the **whole** file and agreed | Yes, permanently |
| `UNAVAILABLE` | The file could not be fetched (404, rate limit, outage) | **No.** Returned with `stored: "false"` so the same check can be retried later |
| `TOO_LARGE` | The file is longer than the 16,000-character evidence window | **No.** `verify` fails with an error, so no whole-file verdict is ever issued from part of a file |

The oracle never truncates evidence. A file either fits completely in the
model's evidence window and is judged as a whole, or it is rejected. To check
something in a large file, point the check at a smaller file.

## How consensus is used

`verify` runs its non-deterministic work in `gl.vm.run_nondet` with a custom
validator instead of a generic equivalence prompt.

**Leader:** fetch the pinned file and hash it. If it is unavailable or too
large, say so without calling the model. Otherwise, give the model the entire
file and ask for `SUPPORTED`, `REFUTED` or `INSUFFICIENT` with a one-line reason.

**Each validator** (`validator_agrees` in the contract) fetches the file
itself and rejects the proposal if any of these is true:

| Check | Why |
|---|---|
| The result is malformed, has an unknown verdict, or the reason is empty or over 280 chars | Never store unvalidated model output |
| `UNAVAILABLE` is claimed while the HTTP status is 200, or a verdict is claimed for a failed fetch | A leader cannot hide an outage or invent evidence |
| The validator's own HTTP status differs | Different view of the source |
| The validator's own `content_hash` differs | Different bytes, so the judgement is not comparable |
| The file is too large but the leader issued a verdict, or it fits but the leader said `TOO_LARGE` | Size is a fact of the bytes, so a leader can neither judge a partial file nor dodge a real one |
| The validator's own model verdict differs | Only the judgement itself is left to compare |

The cheap, exact checks run first. A validator only pays for a model call
once it has confirmed it is judging the same, complete bytes as the leader.
The free-text `reason` is taken from the leader and is not compared word for word.

Model output is treated as untrusted. The evidence is fenced in `<evidence>`
tags and labelled as untrusted, which guards against prompt injection from
repository content. Anything outside the three allowed verdicts becomes
`INSUFFICIENT`.

## State design

```
checks:    TreeMap[str, Check]    # check_id -> record (judged outcomes only)
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
| `verify(owner, repo, commit, path, claim) -> dict` | write | Check a claim, or return the cached record. The result includes `stored` |
| `compute_check_id(owner, repo, commit, path, claim) -> str` | view | Deterministic id, validates input |
| `has_check(check_id) -> bool` | view | |
| `get_check(check_id) -> dict` | view | Full record |
| `get_check_count() -> int` / `get_check_id_at(i) -> str` | view | Enumerate |
| `get_evidence_limit() -> int` | view | Maximum file size in characters (16,000) |

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
that already went through consensus. Because only judged outcomes are stored,
a temporary fetch failure can never be mistaken for a final answer.

### BountyBoard: credit goes to an authorized solver

A SUPPORTED check on a public commit is public. Anyone can see it and anyone
can pay for it. On its own, it proves nothing about **who** did the work.
`BountyBoard` binds the reward to a wallet the sponsor authorized:

1. The sponsor calls `create_task(owner, repo, path, requirement, reward_points, assignee)`.
   `assignee` may be `""` and set later with `assign_task(task_id, solver)`.
   Only the sponsor can assign, and only while the task is open.
2. The solver lands a commit, and anyone calls `EvidenceOracle.verify(...)` with
   the task's requirement.
3. The solver calls `claim_task(task_id, commit)`. The board rejects the call
   unless the sender **is the assigned solver**. It then recomputes the check
   id through the oracle, requires `SUPPORTED`, marks the task solved, and
   credits that wallet's reputation. A task can only be solved once.

Wallets are accepted as hex strings (SDK and web clients) or as calldata
`Address` values (the GenLayer CLI turns `0x…` arguments into addresses).

## Live run on Bradbury (v1.1.0)

The evidence is a real file: `contracts/proofcheck_studio.py` in
`longbds0108/ProofCheck`, checked at two commits, plus a missing path and an
oversized file.

| Step | Result | GenLayer tx id |
|---|---|---|
| Deploy `EvidenceOracle` | FINISHED_WITH_RETURN | `0x31406f909111ebf53078734bf56864665f0578332ca5f684ac8081dd6cc8ae5f` |
| Deploy `BountyBoard(oracle)` | FINISHED_WITH_RETURN | `0x9c734cec116f9d6478e3b2bdea8367d9c07d8ff713a6e2942f2ebe6c0a90a2d8` |
| `verify` at `c89294e` | **SUPPORTED**, stored | `0x1cf7b598354cc9c3ed39e1aea8baf7f1f76e678a6a94a84fbc2e18a84d053f73` |
| `verify` at `471e4c0` (before the feature existed) | **REFUTED**, stored | `0x654615b3cfc4c2debc7d196a9bededcc3bb79b514b2d0c34d3a9b269819f75c4` |
| `verify` of a missing path | **UNAVAILABLE**, not stored (`has_check` = false) | `0xbd0953c8feb13b6d5a72e64cce320a711dc4ee34e751b4512aad3294bdbd4a98` |
| `verify` of `dist/app.js` (43,554 chars) | **Rejected**: `Evidence file is larger than 16000 characters`, nothing stored | `0x37dc7aa31b119d1430b43266f0399203c2909c0fd3bc875e746af0b72ce1322a` |
| `create_task` T1, assigned to `0xc480…37ba` | FINISHED_WITH_RETURN | `0xf129ffdd584753c0ed7b14279f7db84e93e93ac7204a6f17d023a87038443970` |
| `claim_task(T1)` from another wallet (simulated) | **Rejected**: `Only the assigned solver can claim this task` | — |
| `claim_task(T1, c89294e)` by the assigned solver | **SOLVED**, +50 reputation | `0xcceb566d7307b4664d7f69ad9a11c166fcac144d70b2079166aec77e06cacede` |

After this run the oracle holds exactly two checks (SUPPORTED and REFUTED). The
unavailable and oversized attempts left nothing behind.

The claim was: *"The contract stores the wallet address that submitted each
verification."* At `471e4c0` the oracle answered that the struct has no wallet
field. At `c89294e` it cited the `submitter` field. The stored hash for
`c89294e` is `2dff36e353bb26f122f0f67cdeca2f18db3b51fdf3a78544768cb40fdf341106`.
It matches:

```bash
curl -s https://raw.githubusercontent.com/longbds0108/ProofCheck/c89294e00e2a805be318bac764957cc622610c6f/contracts/proofcheck_studio.py | shasum -a 256
```

To read everything yourself (view calls, no wallet needed):

```bash
npm install
node scripts/inspect.mjs                           # list checks and tasks
node scripts/inspect.mjs 5d0113f584af22a77de788f3  # one check + local hash re-check
```

An earlier v1.0.0 deployment (`0xAc46…541A` / `0xB7C8…5168`) is superseded. It
truncated large files, cached `UNAVAILABLE` and let any wallet claim a task.

## Tests

```bash
cd tests && python3 -m unittest -v      # 36 tests, standard library only
```

`tests/fake_genlayer.py` is a small stand-in for the `genlayer` package. It
serves web and model responses from the test, runs `run_nondet` with several
simulated validators, and dispatches cross-contract view calls. The tests cover:

- input validation (branch names, short SHAs, path traversal, oversized claims),
- id stability and sensitivity,
- parsing hostile or malformed model output,
- prompt fencing,
- caching (a repeated question makes no web or model call),
- `UNAVAILABLE` being returned but not stored, then succeeding on retry,
- oversized files being rejected without a model call, and files at the limit
  being judged whole,
- every validator rejection rule: different verdict, different bytes, a hidden
  outage, a verdict on an oversized file, a false `TOO_LARGE`, malformed
  results, and a hash mismatch caught before the model call,
- the BountyBoard flow: assignment, sponsor-only `assign_task`, another wallet
  failing to claim a public SUPPORTED commit, and double claims.

The fake does not emulate GenVM itself. The Bradbury run above covers that.

## Limits and trade-offs

- **Borderline claims may not reach consensus.** Validators must reach the
  same verdict on their own. When a claim is truly ambiguous, they can split
  and the transaction will not be accepted. That is deliberate: no answer is
  better than a coin flip stored as fact. Phrase claims so a single file can
  settle them.
- Files over 16,000 characters are rejected, not paginated.
- Only public repositories, and text files that decode as UTF-8.
- The reason is the leader's wording. Only the verdict is consensus-checked.
- BountyBoard reputation is points, not tokens.

## Deploying on Bradbury

Three things that are easy to get wrong:

- The runner header must be the **first line and stand alone**. GenVM reads
  the whole leading comment block as the runner JSON, so put a blank line
  before any other comment. These contracts pin
  `py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0`, the
  runner that Bradbury accepted at the time of writing.
- `rpc.testnet-chain.genlayer.com` does not serve `gen_call`. Use
  `https://rpc-bradbury.genlayer.com` for reads and fee simulation.
- The CLI turns any `0x…40 hex` argument into an `Address`, so contracts that
  take wallets as `str` must also accept `Address` values.

```bash
npx genlayer@0.40.0-rc.6 network set testnet-bradbury
npx genlayer@0.40.0-rc.6 deploy --contract contracts/evidence_oracle.py --fees '<distribution>' --fee-value <wei>
npx genlayer@0.40.0-rc.6 deploy --contract contracts/bounty_board.py --args <oracle address> --fees '<distribution>' --fee-value <wei>
```

Take `--fees` and `--fee-value` from `genlayer estimate-fees`. A deploy with no
fee is rejected with `FeeValueMustBeNonZero`.

## License

MIT
