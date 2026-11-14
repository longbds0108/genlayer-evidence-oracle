import json
import unittest

from fake_genlayer import WORLD, UserError, deploy, load_contract

oracle_mod = load_contract("evidence_oracle.py")
board_mod = load_contract("bounty_board.py")

ORACLE_ADDR = "0x" + "aa" * 20
BOARD_ADDR = "0x" + "bb" * 20
SPONSOR = "0x" + "51" * 20
SOLVER = "0x" + "5A" * 20
OWNER, REPO, PATH = "acme", "widgets", "README.md"
REQUIREMENT = "The README explains how to run the tests."
GOOD = "1" * 40
BAD = "2" * 40


def url(commit):
    return f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{commit}/{PATH}"


class BountyBoardTest(unittest.TestCase):
    def setUp(self):
        WORLD.reset()
        WORLD.web[url(GOOD)] = (200, b"Run tests with `make test`.")
        WORLD.web[url(BAD)] = (200, b"Nothing about tests here.")
        WORLD.llm = lambda prompt: json.dumps(
            {"verdict": "SUPPORTED" if "make test" in prompt else "INSUFFICIENT", "reason": "From README."}
        )
        self.oracle = deploy(oracle_mod.EvidenceOracle, ORACLE_ADDR)
        self.board = deploy(board_mod.BountyBoard, BOARD_ADDR, ORACLE_ADDR)
        WORLD.sender = SPONSOR
        self.task_id = self.board.create_task(OWNER, REPO, PATH, REQUIREMENT, 50)

    def solve(self, commit):
        WORLD.sender = SOLVER
        self.oracle.verify(OWNER, REPO, commit, PATH, REQUIREMENT)
        return self.board.claim_task(self.task_id, commit)

    def test_create_task_records_sponsor_and_rejects_bad_input(self):
        task = self.board.get_task(self.task_id)
        self.assertEqual((task["status"], task["sponsor"], task["reward_points"]), ("OPEN", SPONSOR, "50"))
        self.assertEqual(self.board.get_oracle(), ORACLE_ADDR)
        with self.assertRaises(UserError):
            self.board.create_task(OWNER, REPO, "../x", REQUIREMENT, 10)
        with self.assertRaises(UserError):
            self.board.create_task(OWNER, REPO, PATH, REQUIREMENT, 0)

    def test_supported_check_solves_task_and_pays_reputation(self):
        task = self.solve(GOOD)
        self.assertEqual((task["status"], task["solver"], task["commit"]), ("SOLVED", SOLVER, GOOD))
        self.assertEqual(self.board.get_reputation(SOLVER.lower()), 50)
        self.assertEqual(self.board.get_reputation(SOLVER.upper().replace("0X", "0x")), 50)

    def test_claim_requires_an_oracle_check_first(self):
        WORLD.sender = SOLVER
        with self.assertRaisesRegex(UserError, "verify first"):
            self.board.claim_task(self.task_id, GOOD)

    def test_claim_rejects_non_supported_verdicts(self):
        with self.assertRaisesRegex(UserError, "INSUFFICIENT"):
            self.solve(BAD)
        self.assertEqual(self.board.get_task(self.task_id)["status"], "OPEN")
        self.assertEqual(self.board.get_reputation(SOLVER), 0)

    def test_task_cannot_be_claimed_twice(self):
        self.solve(GOOD)
        with self.assertRaisesRegex(UserError, "already solved"):
            self.board.claim_task(self.task_id, GOOD)
        self.assertEqual(self.board.get_reputation(SOLVER), 50)

    def test_checks_for_other_requirements_do_not_count(self):
        WORLD.sender = SOLVER
        self.oracle.verify(OWNER, REPO, GOOD, PATH, "Run tests with make test.")  # different wording
        with self.assertRaisesRegex(UserError, "verify first"):
            self.board.claim_task(self.task_id, GOOD)

    def test_unknown_task(self):
        with self.assertRaises(UserError):
            self.board.claim_task("T999", GOOD)


if __name__ == "__main__":
    unittest.main()
