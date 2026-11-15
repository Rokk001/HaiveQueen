# Answer wait recovery

Answering an ordinary question clears its plan's `answer` wait in the same transaction. As before, one answered referenced question lets the Queen process that answer while independent questions may remain open. This only changes the internal wait to `waiting`; it does not start an agent, resume a paused plan or bypass global stop/update gates.

Each scheduling round also reconciles historical answer waits before checking global start gates. With no pending questions, obsolete question references are cleared even while starts are blocked. Explicit owner acceptance is separate: adding text to an owner-check question does not accept it or release its acceptance wait.

For an older dashboard snapshot with state `answer` but no pending questions, the display uses the actual start blocker or “Waiting to start”, rather than falsely asking the owner for another answer.
