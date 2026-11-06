"""Window to answer the open owner questions (the inbox questions) without writing an inbox file.

An answer is stored through db.answer_question, the same function the inbox and `bzz answer` use, as answered by the owner. For an owner check
question the window also lists the steps (packages) of the plan: the ones ticked there are marked rejected, so the Queen reworks exactly those.
"""
import re

from . import bees, db, gui_data, hives, runtime
from .i18n import tr

REFRESH_MS = 5000


# Describe one question as a list line.
def line_text(q) -> str:
    """One list line of a question: id, Apiary, Hive and item."""
    return "#%s  %s | %s | %s" % (q["id"], q["project"], gui_data.hive_name(q["plan_path"] or ""), q["item"] or "")


# Read the questions that wait for an answer.
def open_questions(conn) -> list:
    """The open questions of all Apiaries, oldest first."""
    return runtime.list_questions(conn, status="open")


# Read the steps of a plan.
def plan_packages(conn, project, plan_path) -> list:
    """Ids of the packages of a plan in file order (empty when the plan has none)."""
    return [r[0] for r in conn.execute("SELECT id FROM packages WHERE project=? AND plan_path=? ORDER BY rowid", (project, plan_path))]


# Store the owner's answer and mark the rejected steps.
def answer(conn, qid, text, rejected=(), apiary=False) -> None:
    """Answer question `qid` as the owner and mark the packages in `rejected` as rejected.

    Raises ValueError when the text is empty or says ok while steps are marked rejected; db.answer_question raises KeyError for a question that is already answered
    (nothing is marked then)."""
    text = (text or "").strip()
    if not text:
        raise ValueError(tr("Write an answer first."))
    if rejected and hives.owner_check_ok(text):
        raise ValueError(tr("The answer says ok, but steps are marked as rejected."))
    row = conn.execute("SELECT project, plan_path FROM questions WHERE id=?", (qid,)).fetchone()
    db.answer_question(conn, qid, text, "owner", apiary=bool(apiary))
    for package in rejected:
        bees.set_package_state(conn, row["project"], row["plan_path"], package, "rejected", "owner")


# The text of a refused answer.
def error_text(exc) -> str:
    """The message of a refused answer in the language; the database functions raise it in English."""
    text = str(exc).strip("'\"")
    found = re.fullmatch(r"(?:question (\S+) is already answered)|(?:unknown question (\S+))", text)
    if not found:
        return text
    return tr("question %s is already answered", found.group(1)) if found.group(1) else tr("unknown question %s", found.group(2))


class QuestionsWindow:
    """The window: list of open questions on the left, the question, the answer field and the rejected steps on the right."""

    def __init__(self, root, conn, mono="Consolas", on_answered=None):
        """Open the window below `root`; `on_answered()` is called after every stored answer."""
        import tkinter as tk
        from .gui import COL
        self.tk, self.conn, self.mono, self.on_answered = tk, conn, mono, on_answered
        self.win = tk.Toplevel(root)
        self.win.title(tr("HaiveQueen questions"))
        self.win.geometry("1040x680")
        self.questions, self.checks = [], {}
        side = tk.Frame(self.win)
        side.pack(side="left", fill="y", padx=6, pady=6)
        self.list = tk.Listbox(side, width=44, exportselection=False, font=(mono, 10))
        self.list.pack(fill="y", expand=True)
        self.list.bind("<<ListboxSelect>>", lambda _e: self.show())
        main = tk.Frame(self.win)
        main.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self.meta = tk.Label(main, anchor="w", justify="left", font=(mono, 10, "bold"))
        self.meta.pack(fill="x")
        self.body = tk.Text(main, height=14, wrap="word", font=(mono, 10), state="disabled")
        self.body.pack(fill="both", expand=True, pady=(4, 6))
        tk.Label(main, text=tr("Your answer"), anchor="w", font=(mono, 10)).pack(fill="x")
        self.reply = tk.Text(main, height=7, wrap="word", font=(mono, 10))
        self.reply.pack(fill="x", pady=(2, 0))
        self.apiary_var = tk.BooleanVar(value=False)
        self.apiary_box = tk.Checkbutton(main, text=tr("This answer applies to the whole Apiary"), variable=self.apiary_var, anchor="w", font=(mono, 10))
        self.apiary_box.pack(fill="x", pady=(0, 6))
        self.steps = tk.LabelFrame(main, text=tr("Not accepted steps (marked rejected, the Queen reworks them)"), font=(mono, 10))
        self.steps.pack(fill="x", pady=(0, 6))
        buttons = tk.Frame(main)
        buttons.pack(fill="x")
        self.status = tk.Label(buttons, anchor="w", fg=COL["red"], font=(mono, 10))
        self.status.pack(side="left", fill="x", expand=True)
        for label, action in ((tr("Use recommendation"), self.use_recommendation), (tr("Answer"), self.send), (tr("Close"), self.win.destroy)):
            tk.Button(buttons, text=label, command=action, font=(mono, 10)).pack(side="right", padx=3)
        self.win.bind("<Control-Return>", lambda _e: self.send())
        self.refresh()
        self.win.after(REFRESH_MS, self.tick)

    # Reload the list every few seconds.
    def tick(self):
        """Refresh the list now and again after REFRESH_MS while the window exists."""
        if self.win.winfo_exists():
            self.refresh()
            self.win.after(REFRESH_MS, self.tick)

    # The question that is selected.
    def current(self):
        """The selected question row, or None."""
        pick = self.list.curselection()
        return self.questions[pick[0]] if pick and pick[0] < len(self.questions) else None

    # Reload the open questions and keep the selection.
    def refresh(self):
        """Reload the open questions; the selected one stays selected, and the form is rebuilt only when the list changed."""
        old = self.current()
        fresh = open_questions(self.conn)
        if [q["id"] for q in fresh] == [q["id"] for q in self.questions] and self.questions:
            return
        self.questions = fresh
        self.list.delete(0, "end")
        for q in fresh:
            self.list.insert("end", line_text(q))
        index = next((i for i, q in enumerate(fresh) if old and q["id"] == old["id"]), 0)
        if fresh:
            self.list.selection_set(index)
        self.show()

    # Fill the right side for the selected question.
    def show(self):
        """Show the selected question, its recommendation and (for an owner check) the steps of its plan."""
        tk = self.tk
        q = self.current()
        self.status.config(text="")
        for child in self.steps.winfo_children():
            child.destroy()
        self.checks = {}
        self.body.config(state="normal")
        self.body.delete("1.0", "end")
        if q is None:
            self.meta.config(text=tr("No open questions."))
            self.body.config(state="disabled")
            self.steps.pack_forget()
            return
        self.meta.config(text="#%s  %s | %s | %s | %s" % (q["id"], q["project"], q["plan_path"], q["item"], gui_data.fmt_stamp(q["created_at"])))
        self.body.insert("1.0", tr("%s\n\nRecommendation:\n%s", q["question"], q["recommendation"] or "-"))
        self.body.config(state="disabled")
        packages = plan_packages(self.conn, q["project"], q["plan_path"]) if str(q["item"] or "").startswith("owner-check") else []
        if packages:
            self.steps.pack(fill="x", pady=(0, 6))
            for n, package in enumerate(packages):
                var = tk.BooleanVar(value=False)
                self.checks[package] = var
                tk.Checkbutton(self.steps, text=package, variable=var, font=(self.mono, 10)).grid(row=n // 4, column=n % 4, sticky="w", padx=6)
        else:
            self.steps.pack_forget()

    # Put the recommendation into the answer field.
    def use_recommendation(self):
        """Copy the recommendation of the selected question into the answer field."""
        q = self.current()
        if q is not None:
            self.reply.delete("1.0", "end")
            self.reply.insert("1.0", q["recommendation"] or "")

    # Store the answer.
    def send(self):
        """Answer the selected question with the typed text and mark the ticked steps rejected; problems are shown in the status line."""
        q = self.current()
        if q is None:
            return
        try:
            answer(self.conn, q["id"], self.reply.get("1.0", "end"), [p for p, var in self.checks.items() if var.get()], self.apiary_var.get())
        except (ValueError, KeyError) as exc:
            self.status.config(text=error_text(exc))
            return
        self.reply.delete("1.0", "end")
        self.apiary_var.set(False)
        self.questions = []
        self.refresh()
        if self.on_answered:
            self.on_answered()
