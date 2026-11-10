"""Tests of the questions window: answering as the owner, rejected steps, and the window itself (fully transparent windows)."""
import time
import unittest
from unittest import mock

from bzztest import REL, RoundCase
from pathlib import Path

from haivequeen import bees, db, gui_questions, runtime, triage


class AnswerTest(RoundCase):
    """gui_questions.answer stores the owner's answer and marks the rejected steps."""

    # A plan with three steps and an owner check question.
    def setUp(self):
        """Plan with packages P1 to P3 and one open owner check question."""
        super().setUp()
        self.plan('IN_PROGRESS', [{'id': 'P1'}, {'id': 'P2'}, {'id': 'P3'}])
        self.qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Please check the menu.', 'Check it in the game.')

    # The answer is stored like an inbox answer, and the ticked steps become rejected.
    def test_answer_marks_rejected_steps(self):
        """The question is answered by the owner, a decision is logged, and exactly the ticked steps are rejected."""
        gui_questions.answer(self.conn, self.qid, 'Menu and mana ring are wrong.', ['P2', 'P3'])
        row = self.conn.execute('SELECT status, answer, answered_by FROM questions WHERE id=?', (self.qid,)).fetchone()
        self.assertEqual((row['status'], row['answered_by']), ('open', 'owner'))
        self.assertEqual(row['answer'], 'Menu and mana ring are wrong.')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions WHERE text LIKE '%Menu and mana ring%'").fetchone()[0], 0)
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), [])

    # Wrong input changes nothing.
    def test_refusals_change_nothing(self):
        """An empty answer, ok together with rejected steps, and a second answer are refused and mark nothing."""
        with self.assertRaises(ValueError):
            gui_questions.answer(self.conn, self.qid, '   ', [])
        gui_questions.answer(self.conn, self.qid, 'ok', ['P1'])
        gui_questions.answer(self.conn, self.qid, 'ok', [])
        gui_questions.answer(self.conn, self.qid, 'No, wrong.', ['P1'])
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), [])


class WindowTest(RoundCase):
    """The window lists the open questions, shows the steps of an owner check and answers."""

    # A transparent Tk root and two open questions.
    def setUp(self):
        """Plan with two steps, an owner check question and a plain question, a transparent root."""
        super().setUp()
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")

        class Quiet(tk.Toplevel):
            """A Toplevel that is transparent from the start."""

            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.attributes("-alpha", 0.0)

        quiet = mock.patch("tkinter.Toplevel", Quiet)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.plan('IN_PROGRESS', [{'id': 'P1'}, {'id': 'P2'}])
        self.owner_q = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Please check the menu.', 'Check it.')
        self.plain_q = db.add_question(self.conn, 'testproj', REL, 'colour', 'Which colour?', 'Red.')

    def settle(self, win):
        """Run the event loop until the background reads of the window are shown."""
        for _ in range(6000):
            self.root.update()
            if not getattr(win, "loading", False) and not win.package_reads:
                return
            time.sleep(0.01)

    def tearDown(self):
        """Close the window root."""
        self.root.destroy()
        super().tearDown()

    # Answer a plain question and an owner check question through the window.
    def test_answer_through_the_window(self):
        """Both questions are listed; the plain one has no step boxes; ticking P2 on the owner check marks it rejected; answered questions leave the list."""
        done = []
        win = gui_questions.QuestionsWindow(self.root, self.conn, on_answered=lambda: done.append(1))
        self.settle(win)
        self.assertEqual(win.list.size(), 2)
        win.list.selection_clear(0, "end")
        win.list.selection_set(1)
        win.show()
        self.settle(win)
        self.assertEqual(win.checks, {})
        win.use_recommendation()
        self.assertEqual(win.reply.get("1.0", "end").strip(), 'Red.')
        win.send()
        self.settle(win)
        self.assertEqual(win.list.size(), 1)
        win.list.selection_clear(0, "end")
        win.list.selection_set(0)
        win.show()
        self.settle(win)
        self.assertEqual(sorted(win.checks), ['P1', 'P2'])
        win.checks['P2'].set(True)
        win.reply.insert("1.0", 'The menu is still wrong.')
        win.send()
        self.settle(win)
        self.assertEqual(win.list.size(), 1)
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), [])
        self.assertEqual(done, [1, 1])

    # An empty answer shows a message and keeps the question open.
    def test_empty_answer_shows_a_message(self):
        """Pressing Answer without text leaves the question open and fills the status line."""
        win = gui_questions.QuestionsWindow(self.root, self.conn)
        self.settle(win)
        win.send()
        self.settle(win)
        self.assertIn("answer", win.status.cget("text").lower())
        self.assertEqual(win.list.size(), 2)

    def integrated_owner_check(self):
        self.conn.execute("INSERT INTO queue(project, branch, tip, plan_path, status, updated_at) VALUES (?,?,?,?,?,?)", ('testproj', 'plan-branch', 'abcdef012345', REL, 'integrated', db.now()))
        ready = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute("INSERT INTO hives(project, plan_path, provider, colour, state, ready_id) VALUES (?,?,?,?,?,?)", ('testproj', REL, 'test', 'blue', 'owner_check', ready))

    def test_explicit_owner_buttons_and_required_rejection_text(self):
        runtime.set_plan_status(self.conn, 'testproj', REL, 'REVIEW')
        self.integrated_owner_check()
        win = gui_questions.QuestionsWindow(self.root, self.conn)
        self.settle(win)
        self.assertEqual(win.accept_button.cget('text'), 'Accept')
        self.assertEqual(win.reject_button.cget('text'), 'Not ok')
        self.assertTrue(win.accept_button.winfo_manager())
        win.reject_button.invoke()
        self.settle(win)
        self.assertIn('text', win.status.cget('text').lower())
        self.assertEqual(win.list.size(), 2)
        win.reply.insert('1.0', 'Fix the second package')
        win.checks['P2'].set(True)
        win.reject_button.invoke()
        self.settle(win)
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), ['P2'])
        self.assertEqual(db.owner_check_result(self.conn, self.owner_q), 'rejected')

    def test_accept_button_completes_plan(self):
        runtime.set_plan_status(self.conn, 'testproj', REL, 'REVIEW')
        self.integrated_owner_check()
        db.answer_question(self.conn, self.plain_q, 'Red', 'owner')
        win = gui_questions.QuestionsWindow(self.root, self.conn)
        self.settle(win)
        win.accept_button.invoke()
        self.settle(win)
        self.assertEqual(win.list.size(), 0)
        self.assertEqual(runtime.list_plans(self.conn, 'testproj')[0]['status'], 'DONE')

    # The checkbox sits below the answer field, off by default, and is read when answering.
    def test_apiary_checkbox(self):
        """The checkbox is off, packed right after the answer field, resets after an answer and stores the Project decision when set."""
        win = gui_questions.QuestionsWindow(self.root, self.conn)
        self.settle(win)
        self.assertFalse(win.apiary_var.get())
        self.assertIn('whole Project', win.apiary_box.cget('text'))
        slaves = win.reply.master.pack_slaves()
        self.assertIs(slaves[slaves.index(win.reply) + 1], win.apiary_box)
        win.list.selection_clear(0, "end")
        win.list.selection_set(1)
        win.show()
        self.settle(win)
        win.apiary_var.set(True)
        win.reply.insert("1.0", 'Blue.')
        win.send()
        self.settle(win)
        self.assertFalse(win.apiary_var.get())
        self.assertEqual([d['plan_path'] for d in runtime.list_decisions(self.conn, 'testproj', item='colour')], [None])


class ApiaryDecisionTest(RoundCase):
    """The checkbox stores the answer as a decision of the whole Project."""

    def test_apiary_answer_is_found_again_in_another_plan(self):
        """Checked: decision without plan, found for the same item in another plan (worded differently); not seen in another Project."""
        qid = db.add_question(self.conn, 'testproj', REL, 'colour', 'Which colour?', 'Red.')
        gui_questions.answer(self.conn, qid, 'Blue.', apiary=True)
        decisions = runtime.list_decisions(self.conn, 'testproj')
        self.assertEqual([d['plan_path'] for d in decisions], [None])
        known = triage._known_answer(self.conn, 'testproj', 'docs/plans/other/OTHER.md', 'colour')
        self.assertEqual(known[0], 'decision')
        self.assertIn('Blue.', known[2])
        self.assertIsNone(triage._known_answer(self.conn, 'otherproj', 'docs/plans/other/OTHER.md', 'colour'))
        self.assertEqual(runtime.list_decisions(self.conn, 'otherproj'), [])

    def test_without_checkbox_only_the_plan_decision(self):
        """Not checked: the decision keeps its plan, another plan of the Project is not answered."""
        qid = db.add_question(self.conn, 'testproj', REL, 'colour', 'Which colour?', 'Red.')
        gui_questions.answer(self.conn, qid, 'Blue.')
        self.assertEqual([d['plan_path'] for d in runtime.list_decisions(self.conn, 'testproj')], [REL])
        self.assertIsNone(triage._known_answer(self.conn, 'testproj', 'docs/plans/other/OTHER.md', 'colour'))

    def test_queen_instructions_name_both_rules(self):
        """roles/queen.md tells the Queen to check Project decisions first and to ask once on contradicting settings and rules."""
        text = (Path(__file__).resolve().parent.parent / 'roles' / 'queen.md').read_text(encoding='utf-8')
        self.assertIn('check the decisions of your Project (bzz decisions list)', text)
        self.assertIn('name the decision', text)
        self.assertIn('settings of the Project and its project rules contradict each other', text)
        self.assertIn('his answer decides', text)
        self.assertIn('asked again in another plan', text)


if __name__ == "__main__":
    unittest.main()
