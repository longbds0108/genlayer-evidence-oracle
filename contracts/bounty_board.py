# { "Depends": "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0" }

# Bounty Board v1.1.0 - example consumer of the GitHub Evidence Oracle.
#
# A sponsor posts a task as a claim about a file in a repository ("README.md
# documents how to run the tests") and assigns it to a solver's wallet. The
# solver lands the change, asks the oracle to verify the claim at their commit,
# then claims the task here. Only the assigned wallet can claim, so nobody can
# take the credit by pointing at someone else's public commit. The board never
# calls an LLM itself: it trusts only the oracle's stored, consensus-backed
# verdict, read through a cross-contract view call.

from dataclasses import dataclass

import genlayer as gl
from genlayer.storage import allow as allow_storage
from genlayer.types import *


OPEN = "OPEN"
SOLVED = "SOLVED"


def _wallet(value, allow_empty: bool = False) -> str:
    """Normalize a wallet given as a hex string (SDK / web) or an Address (CLI)."""
    if isinstance(value, Address):
        return value.as_hex
    if allow_empty and (value is None or (isinstance(value, str) and not value.strip())):
        return ""
    try:
        return Address(str(value).strip()).as_hex
    except Exception:
        raise gl.vm.UserError("Invalid wallet address")


@allow_storage
@dataclass
class Task:
    task_id: str
    sponsor: str
    owner: str
    repo: str
    path: str
    requirement: str
    reward_points: u256
    status: str
    assignee: str
    solver: str
    commit: str
    check_id: str


class BountyBoard(gl.contract.Contract):
    oracle: Address
    tasks: gl.storage.TreeMap[str, Task]
    task_ids: gl.storage.DynArray[str]
    reputation: gl.storage.TreeMap[str, u256]

    def __init__(self, oracle_address: str) -> None:
        self.oracle = Address(oracle_address)

    @gl.public.write
    def create_task(
        self, owner: str, repo: str, path: str, requirement: str, reward_points: int, assignee: str
    ) -> str:
        """Create a task. assignee may be "" and set later with assign_task."""
        if reward_points <= 0 or reward_points > 1_000_000:
            raise gl.vm.UserError("Reward must be between 1 and 1000000 points")
        # Validate the shape now with an all-zero commit, so a task can never be
        # created that the oracle would later refuse to check.
        self._oracle().compute_check_id(owner, repo, "0" * 40, path, requirement)
        assignee_hex = _wallet(assignee, allow_empty=True)
        task_id = "T" + str(len(self.task_ids) + 1)
        self.tasks[task_id] = Task(
            task_id=task_id,
            sponsor=gl.message.sender_address.as_hex,
            owner=owner.strip(),
            repo=repo.strip(),
            path=path.strip().strip("/"),
            requirement=" ".join(requirement.split()),
            reward_points=u256(reward_points),
            status=OPEN,
            assignee=assignee_hex,
            solver="",
            commit="",
            check_id="",
        )
        self.task_ids.append(task_id)
        return task_id

    @gl.public.write
    def assign_task(self, task_id: str, solver: str) -> dict[str, str]:
        """Sponsor-only: authorize the one wallet that may claim this task."""
        task = self._open_task(task_id)
        if gl.message.sender_address.as_hex.lower() != task.sponsor.lower():
            raise gl.vm.UserError("Only the sponsor can assign this task")
        task.assignee = _wallet(solver)
        return self._to_dict(task)

    @gl.public.write
    def claim_task(self, task_id: str, commit: str) -> dict[str, str]:
        task = self._open_task(task_id)
        solver = gl.message.sender_address.as_hex
        if not task.assignee:
            raise gl.vm.UserError("Task has no assigned solver yet")
        if solver.lower() != task.assignee.lower():
            raise gl.vm.UserError("Only the assigned solver can claim this task")

        oracle = self._oracle()
        check_id = oracle.compute_check_id(task.owner, task.repo, commit, task.path, task.requirement)
        if not oracle.has_check(check_id):
            raise gl.vm.UserError("No oracle check for this commit yet. Call EvidenceOracle.verify first")
        check = oracle.get_check(check_id)
        if check["verdict"] != "SUPPORTED":
            raise gl.vm.UserError("Oracle verdict is " + check["verdict"] + ", not SUPPORTED")

        task.status = SOLVED
        task.solver = solver
        task.commit = check["commit"]
        task.check_id = check_id
        current = self.reputation.get(solver.lower())
        self.reputation[solver.lower()] = u256(int(current or 0) + int(task.reward_points))
        return self._to_dict(task)

    @gl.public.view
    def get_task(self, task_id: str) -> dict[str, str]:
        task = self.tasks.get(task_id)
        if task is None:
            raise gl.vm.UserError("Unknown task")
        return self._to_dict(task)

    @gl.public.view
    def get_task_count(self) -> int:
        return len(self.task_ids)

    @gl.public.view
    def get_reputation(self, solver: str) -> int:
        return int(self.reputation.get(solver.strip().lower()) or 0)

    @gl.public.view
    def get_oracle(self) -> str:
        return self.oracle.as_hex

    def _open_task(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise gl.vm.UserError("Unknown task")
        if task.status != OPEN:
            raise gl.vm.UserError("Task is already solved")
        return task

    def _oracle(self):
        return gl.contract.get_at(self.oracle).view()

    def _to_dict(self, task: Task) -> dict[str, str]:
        return {
            "task_id": task.task_id,
            "sponsor": task.sponsor,
            "owner": task.owner,
            "repo": task.repo,
            "path": task.path,
            "requirement": task.requirement,
            "reward_points": str(int(task.reward_points)),
            "status": task.status,
            "assignee": task.assignee,
            "solver": task.solver,
            "commit": task.commit,
            "check_id": task.check_id,
        }
