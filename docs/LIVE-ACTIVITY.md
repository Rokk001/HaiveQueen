# Recorded agent activity

The selected agent's Live console shows recent recorded command and tool activity above its existing milestone narrative. Each row names the activity and its recorded state. Click it to inspect the command or tool input, output and exit code when available. Details start collapsed, remain expanded across refreshes, and unchanged refreshes preserve the displayed nodes and text selection.

Activity comes exclusively from that agent's existing `events.jsonl`: Codex command, MCP and file-change items, Claude tool calls/results, and OpenCode tool events. No model calls or inferred progress are used. Completed means a completion event was recorded, not that the package passed verification. Nonzero command exits and tool errors show Failed. An unfinished action belonging to an agent that has stopped shows Completion not recorded. Missing or malformed records do not create substitute activity.

Each request reads at most the last 256 KiB and 160 lines and returns up to eight actions. Command/input details are capped at 4,000 characters and output at the last 6,000 characters. A start outside the retained tail may be unavailable; a recorded result can still be shown without inventing its command. Milestones remain independently available below the activity.
