"""Tests of the questions window: answering as the owner, rejected steps, and the window itself (fully transparent windows)."""
import unittest
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, db, gui_questions


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
        self.assertEqual((row['status'], row['answered_by']), ('answered', 'owner'))
        self.assertEqual(row['answer'], 'Menu and mana ring are wrong.')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions WHERE text LIKE '%Menu and mana ring%'").fetchone()[0], 1)
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), ['P2', 'P3'])

    # Wrong input changes nothing.
    def test_refusals_change_nothing(self):
        """An empty answer, ok together with rejected steps, and a second answer are refused and mark nothing."""
        with self.assertRaises(ValueError):
            gui_questions.answer(self.conn, self.qid, '   ', [])
        with self.assertRaises(ValueError):
            gui_questions.answer(self.conn, self.qid, 'ok', ['P1'])
        gui_questions.answer(self.conn, self.qid, 'ok', [])
        with self.assertRaises(KeyError):
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

    def tearDown(self):
        """Close the window root."""
        self.root.destroy()
        super().tearDown()

    # Answer a plain question and an owner check question through the window.
    def test_answer_through_the_window(self):
        """Both questions are listed; the plain one has no step boxes; ticking P2 on the owner check marks it rejected; answered questions leave the list."""
        done = []
        win = gui_questions.QuestionsWindow(self.root, self.conn, on_answered=lambda: done.append(1))
        self.assertEqual(win.list.size(), 2)
        win.list.selection_clear(0, "end")
        win.list.selection_set(1)
        win.show()
        self.assertEqual(win.checks, {})
        win.use_recommendation()
        self.assertEqual(win.reply.get("1.0", "end").strip(), 'Red.')
        win.send()
        self.assertEqual(win.list.size(), 1)
        win.list.selection_clear(0, "end")
        win.list.selection_set(0)
        win.show()
        self.assertEqual(sorted(win.checks), ['P1', 'P2'])
        win.checks['P2'].set(True)
        win.reply.insert("1.0", 'The menu is still wrong.')
        win.send()
        self.assertEqual(win.list.size(), 0)
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), ['P2'])
        self.assertEqual(done, [1, 1])

    # An empty answer shows a message and keeps the question open.
    def test_empty_answer_shows_a_message(self):
        """Pressing Answer without text leaves the question open and fills the status line."""
        win = gui_questions.QuestionsWindow(self.root, self.conn)
        win.send()
        self.assertIn("answer", win.status.cget("text").lower())
        self.assertEqual(win.list.size(), 2)


if __name__ == "__main__":
    unittest.main()
