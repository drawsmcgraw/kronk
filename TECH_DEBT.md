# Kronk Tech Debt

## Open Issues

### [LITELLM-01] LiteLLM `async_pre_call_hook` not firing in proxy mode

**Status:** Workaround in place  
**Workaround:** Permissive Jinja chat template override at `/opt/models/mistralai/devstral-template-permissive.jinja`, referenced by `--chat-template-file` in both devstral systemd units.

**Problem:**  
When switching between devstral Q8 and Q4 in Zed without clearing conversation history, the devstral Jinja template raises an exception because prior messages don't strictly alternate user/assistant roles.

The correct fix is a LiteLLM pre-call hook (`litellm/hooks.py`) that normalizes the message array before it reaches llama.cpp — merging consecutive same-role messages and appending a trailing user turn if the conversation ends on an assistant message. The hook is implemented and mounted into the container, but `async_pre_call_hook` is never invoked.

**Root cause (suspected):**  
`litellm.callbacks` is empty inside the running container despite `callbacks: ["hooks.proxy_handler_instance"]` in `litellm_settings`. The `initialize_callbacks_on_proxy()` startup path does not appear to wire custom `CustomLogger` subclasses into `proxy_logging_obj`'s pre-call pipeline.

**To investigate:**  
- Check LiteLLM proxy source for how `general_settings` vs `litellm_settings` callbacks differ
- Try registering via `custom_logger` key or another config path
- Check if `proxy_logging_obj` has a separate registration mechanism from `litellm.callbacks`

**Risk of workaround:**  
External template file is a snapshot. If a devstral model update changes the embedded chat template, the override file won't auto-update and must be re-extracted and re-patched.

---

### [ROUTING-01] Gate-based router can't handle queries that are partially research-flavored

**Status:** Open — no workaround  

**Problem:**  
The current router classifies at the gate before any LLM reasoning occurs. For queries that are geopolitical, medical, or otherwise factual-but-ambiguous ("Why is the US at war with Iran?", "What does X policy mean for Y?"), the router can't reliably distinguish "needs live data" from "answerable from training knowledge." These tend to route to research even when a direct coordinator answer would be more useful.

**Better approach:**  
A two-phase design where the coordinator sees the query first and decides whether a tool call is actually warranted, rather than routing at the gate. The coordinator would attempt an answer and only invoke research if it determines current data is genuinely needed. This is more expensive per request but eliminates the gate classification problem entirely for ambiguous queries.

**To investigate:**  
- Evaluate whether the coordinator model (gemma-4-e4b) is capable enough to make this tool-use decision reliably
- Assess latency impact of coordinator-first vs. current router-first approach
- Consider a hybrid: keep gate routing for clear-cut cases (health, home, finance, coding, devops) and use coordinator-first only for the research/direct ambiguity
