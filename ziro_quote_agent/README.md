# Term Insurance Quote Comparer: Ziro Agentic AI Hackathon

An agent that collects a customer profile, retrieves term-insurance quotes from insurer pages, compares them, and fills the quote form **only after a human approves it**. Every premium is stored with its date and source, and the agent never invents data it doesn't have.

- Pure Python 3.10+ for offline mode. The optional Gemini planner uses Google's `google-genai` package.
- Runs fully offline against mock insurer pages. An optional LLM tool-calling planner is included.

---

## Quick start

Run from the directory that *contains* the `ziro_quote_agent/` folder.

```bash
# Run the four test cases (TC1-TC4); results are saved to results/TCx.json
python -m ziro_quote_agent.run_scenarios

# Run the agent with a real human approval prompt
python -m ziro_quote_agent.main --age 30 --cover 10000000 --term 30

# Leave a health answer blank (the TC4 behaviour)
python -m ziro_quote_agent.main --smoker ?

# Install the optional Gemini SDK, then add your key to the project-root .env file
python -m pip install google-genai
# GEMINI_API_KEY=your-gemini-api-key
# Use Gemini tool-calling instead of the rule-based planner
python -m ziro_quote_agent.main --planner llm
```

### CLI options

| Flag | Meaning | Default |
|---|---|---|
| `--name` | Applicant name | `Asha Rao` |
| `--age` / `--cover` / `--term` | Age, cover in ₹, term in years | `30` / `10000000` / `30` |
| `--smoker`, `--diabetes`, `--hypertension`, `--heart-condition`, `--prior-surgery` | `yes`, `no` or `?` (unanswered) | `no` |
| `--planner` | `rule` (offline) or `llm` (Gemini API) | `rule` |
| `--approval` | `interactive`, `approve` or `deny` (the last two are simulated approvers for testing) | `interactive` |

---

## How it works

The agent follows the 7-step blueprint from the brief.

```
 Inputs ──► Sources ──► Decision rules ──► Action + Approval ──► Verification ──► Result view
 validate    fetch +      explicit output     human gate +         read back        comparison
 + stable    extract +    + saved reasons     idempotency key      actual vs        table +
 app ID      ledger                                                intended         evidence trail
```

| Step | Implementation |
|---|---|
| 1. Inputs | `Profile.validate()` checks age 18-65, cover ₹25L-₹10Cr, term 5-40, age+term ≤ 85 and the health fields. `Profile.app_id` is a stable ID derived from the content. Validation failure halts the run before any tool is called. |
| 2. Sources | `fetch_quote` extracts premium, claim ratio, exclusions and riders. Each premium is written to a ledger with source ID, source URL and UTC retrieval time. Pages are cached with a TTL. |
| 3. Decision rules | `compare_quotes` returns structured JSON: the rule applied, the recommendation (or `null`), the evidence and the reasons. |
| 4. Action and approval | `propose_form` builds the payload. `submit_form` shows the destination and fields to a human, stores the approved payload hash, and uses an idempotency key so retries can't submit twice. |
| 5. Verification | `verify_submission` reads the form back and diffs it against the intended payload. Pending or failed states stay visible. |
| 6. Result view | `render_table` prints the comparison table, exclusions and riders, and an evidence-trail table. |
| 7. Scenarios | `run_scenarios.py` runs TC1-TC4 and saves input, expected result, actual result, checks and the full log. |

### Agent design

- **Toolbox:** seven tools (`validate_inputs`, `fetch_quote`, `compare_quotes`, `propose_form`, `submit_form`, `verify_submission`, `render_table`), each with a JSON schema.
- **Two planners share the same tools:**
  - `RulePlanner` chooses the next tool from session state and logs its reasoning. It works offline and is deterministic.
  - `LLMPlanner` does Gemini function calling over the same tools. It uses `GEMINI_API_KEY` and defaults to `gemini-2.5-flash`; override the model with `ZIRO_MODEL`.
- **Safety lives in the tools, not the prompt.** Even a misbehaving LLM cannot submit a form without approval or fill an unanswered health field.
- **Failure handling:** transient errors are retried (3 attempts, same idempotency key). Extraction failures become visible `FAILED` rows instead of guesses.

Logs are tagged `THINK` (reasoning), `ACT` (tool call), `OBS` (observation), `WARN` (failure or uncertainty) and `GATE` (approval).

---

## Test cases

| ID | Scenario | Expected | Checks |
|---|---|---|---|
| **TC1** | Same age and cover | Premiums compared across all insurers. Form filled once despite a transient portal failure. | 3 insurers compared; same app ID on every record; cheapest chosen; form executed exactly once; verified |
| **TC2** | Monthly teaser | Monthly price converted to annual (×12) and labelled as converted | Basis says "converted"; annual equals monthly × 12 |
| **TC3** | Claim ratio | Each ratio labelled with its year. An undisclosed year is flagged, never guessed. | Years present on two insurers; "year not disclosed" shown for the third |
| **TC4** | Health question unanswered | Uncertain state, no recommendation, nothing invented | All quotes `UNCERTAIN`; recommendation is `null`; field stays `null`; flagged as pending for the human |

Current status: all four pass (`python -m ziro_quote_agent.run_scenarios`).

---

## Safety guarantees

1. **Forms only.** The agent only fills quote forms. It never purchases or binds a policy.
2. **Human approval is mandatory.** `submit_form` is blocked until the gate returns an approval for the exact payload. A payload changed after approval is rejected.
3. **No duplicate executions.** The idempotency key is a hash of application ID, insurer and fields.
4. **No hallucination.** Unanswered health questions stay `null`. They are shown as "PENDING HUMAN ANSWER", and the recommendation is withheld.
5. **Traceability.** Every premium has a source ID, URL and retrieval time.

---

## Project structure

```
ziro_quote_agent/
├── core.py           # Profile + validation, extraction, cache, approval gate
├── mock_web.py       # Mock insurer pages + mock quote-form portal (with fault injection)
├── agent.py          # Session, ToolBox, retry executor, planners, table renderer
├── main.py           # CLI
└── run_scenarios.py  # TC1-TC4 executable scenarios
```

---

## Limitations

- Insurer pages and the quote-form portal are **mocks**. Premiums come from an invented formula and have no real-world meaning.
- To use real pages, replace `page()` in `mock_web.py` with an HTTP fetch and adjust the regexes in `extract()`.
- `LLMPlanner` requires the optional `google-genai` package and a `GEMINI_API_KEY`; it has not been tested against a live API.
- Scenarios use a simulated approver, labelled as such in the logs. The `main` CLI uses a real interactive prompt by default.
- The mock portal never corrupts fields, so the `FAILED` verification path exists in code but isn't exercised by the scenarios.
- Cache entries are keyed by insurer and application ID, with a 15-minute TTL.
