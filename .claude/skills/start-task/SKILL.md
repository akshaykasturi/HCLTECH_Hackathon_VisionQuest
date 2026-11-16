---
description: Start a new piece of work on a fresh branch. Run at the start of every task.
disable-model-invocation: true
argument-hint: <branch-name> <what to build>
---

## Repo state

!`git status --short --branch`

Task: $ARGUMENTS

1. If the repo state above shows uncommitted changes, ask what to do with them (commit them, or `git stash`) before going on.
2. Run `git switch main`, then `git pull`, then `git switch -c <branch-name>` (the first word of the task), one at a time.
3. Read docs/PLAN.md, and the data card (if there is one) if the task touches the data.
4. If the task touches several files, write a short plan: files to create or change, functions, and the tests you will add.
5. Start working straight away; don't wait for approval.
