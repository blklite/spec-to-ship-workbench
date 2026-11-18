# The prompt cache


A runner with a prompt cache charges much less for the part of a call that it read from the cache. At the prices of [cost/prices.json](../cost/prices.json), a cache read costs less than 10 percent of a cache write. A cache write costs 1.25 times the input price for a 5-minute TTL and 2 times for a 1-hour TTL. A cache entry ends when no call reads it for its TTL. The next call then writes the whole context again. In a long agent the context is most of each call, so an expired cache is the largest cost that the workbench can avoid.

An invented example, at list price: subagents with a 5-minute TTL spend $1,000 in all. If many tool calls wait 5 to 10 minutes on 1 shell command (a sleep loop or a long test run), each wait ends the cache, and the rewrites of the whole context can be a third of that spend. A main session with a 1-hour TTL pays the same rewrite after each pause of more than 1 hour, and each call with a very large context costs more than the work of that call needs.

3 rules follow:

1. **An agent waits in steps shorter than its cache TTL.** With a 5-minute TTL, no tool call blocks for more than 4 minutes. The agent starts a longer job in the background and checks it with commands that each return within 4 minutes. Each check costs a cache read, which is less than 10 percent of the rewrite that it prevents.
2. **A rework round or a second review starts a fresh agent.** Do not resume an agent that was idle for longer than its TTL, because the resume writes its whole context again. The brief of the fresh agent carries the findings and the branch.
3. **The main session stays small.** It hands off to a new session when its context passes about 400,000 tokens, and before a pause longer than its TTL, such as a night or a long wait for the owner. The state of each item is in the tracker, so the new session reads it there.
