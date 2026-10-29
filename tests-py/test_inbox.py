"""Tests of the inbox folder: valid file, invalid file (all or nothing), .tmp file, two rounds without taking a file over twice, oldest first."""
import os
import subprocess
import unittest

from bzztest import REL, RoundCase, db, runtime
from haivequeen import inbox


class InboxTest(RoundCase):
    """Files in <workspace>/.haivequeen/inbox/ are taken over by a round through the same functions as `bzz answer` and `bzz decisions add`."""

    def setUp(self):
        """One open question in the dummy project and the inbox folder of its workspace."""
        super().setUp()
        self.qid = db.add_question(self.conn, "testproj", REL, "P1", "Which value?", "5")
        self.box = inbox.inbox_dir(self.repo)
        self.box.mkdir(parents=True)

    def put(self, name, text, age=0):
        """Write an inbox file; `age` seconds make it older (modification time) so the order is fixed."""
        path = self.box / name
        path.write_text(text, encoding="utf-8")
        if age:
            stamp = path.stat().st_mtime - age
            os.utime(path, (stamp, stamp))
        return path

    def decisions(self):
        """Texts of all decisions of the dummy project."""
        return [d["text"] for d in runtime.list_decisions(self.conn, "testproj", active_only=False)]

    def test_valid_file_is_taken_over(self):
        """The answer closes the question, the decision is stored, the file moves to done/ with a time stamp in front."""
        self.put("a.toml", '[[answer]]\nquestion_id = %d\ntext = "seven"\nby = "owner"\n\n'
                           '[[decision]]\ntext = "Use seven"\nsource = "owner"\nplan = "%s"\nitem = "P1"\n' % (self.qid, REL))
        self.round()
        q = runtime.list_questions(self.conn, "testproj", "answered")
        self.assertEqual((q[0]["id"], q[0]["answer"], q[0]["answered_by"]), (self.qid, "seven", "owner"))
        self.assertIn("Use seven", self.decisions())
        self.assertFalse((self.box / "a.toml").exists())
        done = [p.name for p in (self.box / "done").iterdir()]
        self.assertEqual(len(done), 1)
        self.assertRegex(done[0], r"^\d{8}-\d{6}-a\.toml$")
        self.assertTrue(any(e["kind"] == "answer" for e in runtime.list_events(self.conn, "testproj")))

    def test_invalid_file_changes_nothing(self):
        """A file with a good decision and an unknown question moves to failed/ with a reason and line; the decision is not stored."""
        self.put("bad.toml", '[[decision]]\ntext = "Must not appear"\nsource = "owner"\n\n'
                             '[[answer]]\nquestion_id = 999\ntext = "x"\nby = "owner"\n')
        self.round()
        self.assertNotIn("Must not appear", self.decisions())
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open")[0]["id"], self.qid)
        self.assertTrue((self.box / "failed" / "bad.toml").exists())
        reason = (self.box / "failed" / "bad.error.txt").read_text(encoding="utf-8")
        self.assertIn("unknown question 999", reason)
        self.assertIn("line 5", reason)
        self.assertTrue(any(e["kind"] == "events" and "bad.toml" in e["text"] for e in runtime.list_events(self.conn, "testproj")))

    def test_parse_error_and_wrong_value(self):
        """A TOML parse error and a wrong value (by = robot) both end in failed/ with an error file."""
        self.put("broken.toml", '[[answer]\nquestion_id = 1\n')
        self.put("wrong.toml", '[[answer]]\nquestion_id = %d\ntext = "x"\nby = "robot"\n' % self.qid)
        self.round()
        for name in ("broken", "wrong"):
            self.assertTrue((self.box / "failed" / (name + ".toml")).exists(), name)
            self.assertTrue((self.box / "failed" / (name + ".error.txt")).read_text(encoding="utf-8").strip(), name)
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open")[0]["id"], self.qid)

    def test_tmp_file_is_ignored(self):
        """A .tmp file stays where it is until it is renamed to .toml; then the next round takes it over."""
        tmp = self.put("w.toml.tmp", '[[decision]]\ntext = "Renamed later"\nsource = "coordinator"\n')
        self.round()
        self.assertTrue(tmp.exists())
        self.assertNotIn("Renamed later", self.decisions())
        tmp.rename(self.box / "w.toml")
        self.round()
        self.assertIn("Renamed later", self.decisions())

    def test_two_rounds_take_over_once(self):
        """The second round finds nothing left: the decision exists exactly once."""
        self.put("once.toml", '[[decision]]\ntext = "Only once"\nsource = "owner"\n')
        self.round()
        self.round()
        self.assertEqual(self.decisions().count("Only once"), 1)
        self.assertEqual(len(list((self.box / "done").iterdir())), 1)

    def test_oldest_file_first(self):
        """Two files answer the same question: the older one wins, the newer one fails because the question is already answered."""
        self.put("new.toml", '[[answer]]\nquestion_id = %d\ntext = "newer"\nby = "owner"\n' % self.qid)
        self.put("old.toml", '[[answer]]\nquestion_id = %d\ntext = "older"\nby = "coordinator"\n' % self.qid, age=60)
        self.round()
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "answered")[0]["answer"], "older")
        self.assertTrue((self.box / "failed" / "new.toml").exists())
        self.assertIn("already answered", (self.box / "failed" / "new.error.txt").read_text(encoding="utf-8"))


class QueueOrderTest(InboxTest):
    """[[queue]] orders: withdraw only for a tip that is in the integration ref (or the reason "ueberholt"), mark_cleanup writes a mark, requeue sets ready."""

    def git(self, *args):
        """Run git in the workspace and return the output."""
        res = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(self.repo)] + list(args), capture_output=True, text=True, check=True)
        return res.stdout.strip()

    def setUp(self):
        """A git workspace with one commit inside origin/integration/all (self.inside) and one commit outside it (self.outside)."""
        super().setUp()
        with open(self.repo / ".haivequeen" / "project.toml", "a", encoding="utf-8") as fh:
            fh.write('integrationBranch = "integration/all"' + chr(10))
        self.git("init", "-q")
        (self.repo / "f.txt").write_text("a", encoding="utf-8")
        self.git("add", "f.txt")
        self.git("commit", "-q", "-m", "a")
        self.inside = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/integration/all", self.inside)
        (self.repo / "f.txt").write_text("b", encoding="utf-8")
        self.git("commit", "-q", "-am", "b")
        self.outside = self.git("rev-parse", "HEAD")

    def row(self, branch, tip, status="blocked"):
        """Add a queue row of the dummy project with the given status and return its id."""
        return runtime.queue_upsert(self.conn, "testproj", branch, tip=tip, status=status, note="was " + status)["id"]

    def status(self, qid):
        """Status and note of a queue row."""
        r = self.conn.execute("SELECT status, note FROM queue WHERE id=?", (qid,)).fetchone()
        return r["status"], r["note"]

    def order(self, branch, action, reason, tip=None):
        """Text of one [[queue]] entry."""
        return '[[queue]]\nbranch = "%s"\naction = "%s"\nreason = "%s"\n%s\n' % (branch, action, reason, 'tip = "%s"' % tip if tip else "")

    def test_withdraw_needs_tip_in_integration(self):
        """A tip inside origin/integration/all is withdrawn with the old status word and the reason in the note."""
        qid = self.row("fix/a", self.inside)
        self.put("w.toml", self.order("fix/a", "withdraw", "bereits integriert"))
        self.round()
        self.assertEqual(self.status(qid), ("withdrawn", "blocked (bereits integriert)"))
        self.assertTrue((self.box / "done").is_dir())

    def test_withdraw_outside_fails_unless_ueberholt(self):
        """A tip outside the integration ref keeps its row and moves the file to failed/ with a reason; the reason ueberholt allows it."""
        qid = self.row("fix/b", self.outside)
        self.put("w1.toml", '[[decision]]\ntext = "Must not appear"\nsource = "owner"\n\n' + self.order("fix/b", "withdraw", "bereits integriert"))
        self.round()
        self.assertEqual(self.status(qid)[0], "blocked")
        self.assertNotIn("Must not appear", self.decisions())
        self.assertIn("not in origin/integration/all", (self.box / "failed" / "w1.error.txt").read_text(encoding="utf-8"))
        self.put("w2.toml", self.order("fix/b", "withdraw", "ueberholt", tip=self.outside[:9]))
        self.round()
        self.assertEqual(self.status(qid), ("withdrawn", "blocked (ueberholt)"))

    def test_mark_cleanup_and_requeue(self):
        """mark_cleanup writes a mark and keeps the row; requeue sets the newest open row of the branch to ready; a branch without row fails."""
        qid = self.row("fix/c", self.inside)
        qid2 = self.row("chore/d", self.outside)
        self.put("m.toml", self.order("fix/c", "mark_cleanup", "integriert") + "\n" + self.order("chore/d", "requeue", "nur Umgebung"))
        self.put("n.toml", self.order("fix/none", "requeue", "x"), age=-60)
        self.round()
        self.assertEqual(self.status(qid)[0], "blocked")
        self.assertEqual(self.status(qid2), ("ready", "requeued: nur Umgebung"))
        self.assertEqual(runtime.kv_get(self.conn, "testproj", "branch-cleanup:fix/c"), "erledigt, Aufraeumen erlaubt: integriert")
        self.assertIn("no queue row of branch fix/none", (self.box / "failed" / "n.error.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
