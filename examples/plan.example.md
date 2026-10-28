+++
status = "READY"

[[packages]]
id = "P1"
title = "Add the parser"
class = "medium"
agent = "claude"        # claude, codex or any provider name
depends_on = []

[[packages]]
id = "P2"
title = "Draw the icons"
class = "light"
agent = "claude"
capability = "images"   # only providers with this capability may run it
depends_on = ["P1"]

[[packages]]
id = "P3"
title = "Final check of the whole plan"
class = "heavy"
agent = "codex"
depends_on = ["all"]    # waits for every other package of this plan
+++
# Example plan

Everything below the header is free Markdown that the agents read.
The first heading is used as the plan title.

## Goal
Describe what the plan delivers and how the result is checked.

## Package notes
- P1: details for the first package.
- P2: details for the second package.
- P3: verify that all requirements are met.
