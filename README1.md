# AutoresearchSystemsHackathon

## Setup

```bash
conda create -n modalhack python=3.11 -y
conda activate modalhack
pip install modal
python3 -m modal setup
```

## Modal Checker

Run the following command to verify Modal is set up correctly:

```bash
modal run modal_initialization/get_started.py
```

If your local folder is named `modal_initalization` (current repo spelling), run:

```bash
modal run modal_initalization/get_started.py
```

## Minimal Workflow (Formalized)

### Objective

Run a multi-agent legal debate workflow that optimizes for one selected side (`defense` or `prosecution`) and produces a final recommendation with risk assessment.

### Agents

1. **Case Builder Agent**
	- Input: raw user context (documents, problem description, incident/history).
	- Output schema:
	  - `case_title`
	  - `case_summary`
	  - `facts`
	  - `prosecution_goals`
	  - `defense_goals`
	  - `defense_penalty_exposure`
	  - `optimize_for` (`defense` | `prosecution`)

2. **Orchestrator Agent**
	- Owns control flow, round management, and routing.
	- Decides what sources to fetch each turn.

3. **Source Agents (N agents)**
	- Each agent is mapped to one source type/provider.
	- Input: orchestrator fetch request.
	- Output: raw retrieved evidence/snippets.

4. **Source Synthesizer Agent**
	- Input: all retrieved source outputs + current conversation state.
	- Output: condensed, relevant evidence package for the current turn.

5. **Prosecution Agent**
	- Uses case context + synthesized evidence to produce argument for prosecution.

6. **Defense Agent**
	- Uses case context + synthesized evidence to produce argument for defense.

7. **Final Strategy Agent**
	- Optimizes final recommendation for `optimize_for` side from Case Builder output.
	- Produces risk assessment and settlement recommendation.

### Conversation State

Maintain a single append-only `conversation` object:

1. Initialize with Case Builder output.
2. Append each turn output (sources used + argument + rebuttal signals).
3. Use this state as context for future retrieval and argument generation.

### Round Protocol (N rounds)

For each round `r` in `1..N`:

1. **Prosecution turn**
	- Orchestrator requests source fetch (optional, based on need).
	- Source Agents retrieve evidence.
	- Source Synthesizer condenses evidence.
	- Prosecution Agent generates argument.
	- Append result to `conversation`.

2. **Defense turn**
	- Orchestrator requests source fetch (optional, based on need).
	- Source Agents retrieve evidence.
	- Source Synthesizer condenses evidence.
	- Defense Agent generates argument.
	- Append result to `conversation`.

### End Condition and Final Output

After `N` rounds, run Final Strategy Agent on full conversation and return:

1. Side-optimized final case strategy (`defense` or `prosecution`).
2. Risk assessment (strengths, weaknesses, uncertainty).
3. Settlement guidance (whether to settle outside court and why).
4. Suggested next actions and evidence gaps.

### Minimal Execution Graph

```text
User Input
  -> Case Builder
  -> Orchestrator
	  -> (per turn) Source Agents -> Source Synthesizer -> Prosecution/Defense
	  -> append to Conversation
  -> Final Strategy Agent
  -> Final Recommendation
```

## Scaffolded Directories

```text
src/
	agents/
		case_builder/
		orchestrator/
		source_agents/
		source_synthesizer/
		prosecution/
		defense/
		final_strategy/

schemas/
	shared/
		common.schema.json
	inputs/
		case_builder_input.schema.json
		orchestrator_input.schema.json
		source_agent_input.schema.json
		source_synthesizer_input.schema.json
		prosecution_input.schema.json
		defense_input.schema.json
		final_strategy_input.schema.json
	outputs/
		case_builder_output.schema.json
		orchestrator_output.schema.json
		source_agent_output.schema.json
		source_synthesizer_output.schema.json
		prosecution_output.schema.json
		defense_output.schema.json
		final_strategy_output.schema.json

data/
	conversation/
```

## Input Schemas

- `schemas/shared/common.schema.json`: Shared types (`side`, `document`, `conversationEntry`, `sourceRequest`, `evidenceSnippet`, `caseBuilderOutput`).
- `schemas/inputs/case_builder_input.schema.json`: Raw user context input for Case Builder.
- `schemas/inputs/orchestrator_input.schema.json`: Case state + conversation + round control input for Orchestrator.
- `schemas/inputs/source_agent_input.schema.json`: Per-source fetch request input for Source Agents.
- `schemas/inputs/source_synthesizer_input.schema.json`: Evidence consolidation input for Source Synthesizer.
- `schemas/inputs/prosecution_input.schema.json`: Argument-generation input for Prosecution.
- `schemas/inputs/defense_input.schema.json`: Argument-generation input for Defense.
- `schemas/inputs/final_strategy_input.schema.json`: Full debate-state input for final strategy and risk output.

## Output Schemas

- `schemas/outputs/case_builder_output.schema.json`: Canonical case object from Case Builder.
- `schemas/outputs/orchestrator_output.schema.json`: Round-side instructions and fetch plan from Orchestrator.
- `schemas/outputs/source_agent_output.schema.json`: Retrieved evidence payload from each Source Agent.
- `schemas/outputs/source_synthesizer_output.schema.json`: Condensed evidence package for the active side/turn.
- `schemas/outputs/prosecution_output.schema.json`: Prosecution argument package (claims, argument, cited evidence, requested outcome).
- `schemas/outputs/defense_output.schema.json`: Defense argument package (claims, argument, cited evidence, requested outcome).
- `schemas/outputs/final_strategy_output.schema.json`: Side-optimized final strategy, risk assessment, settlement recommendation, next actions, and evidence gaps.

## CourtListener Corpus Integration

CourtListener is now wired as a source agent for case background retrieval and evidence support.

### Files Added

- `src/agents/source_agents/courtlistener_client.py`: API client + normalization to shared `evidenceSnippet` shape.
- `src/agents/source_agents/courtlistener_source_agent.py`: Source-agent entry function that returns `source_agent_output`-compatible payloads.
- `src/agents/source_agents/run_courtlistener_agent.py`: CLI runner for quick local testing.
- `src/agents/source_agents/statutes_source_agent.py`: Local-corpus statutes source stub.
- `src/agents/source_agents/dockets_source_agent.py`: Local-corpus dockets source stub.
- `src/agents/source_agents/source_router.py`: Source router for `courtlistener`, `statutes`, and `dockets`.
- `src/agents/source_agents/run_source_agent.py`: Generic source CLI runner.
- `data/sources/statutes_corpus.json`: Starter statutes/guidance corpus.
- `data/sources/dockets_corpus.json`: Starter dockets corpus.

### Environment Variable

Set your API token before running:

```bash
export COURTLISTENER_API_TOKEN="<your_token_here>"
```

### Quick Local Test

```bash
python src/agents/source_agents/run_courtlistener_agent.py \
	--query "Brown v. Board of Education" \
	--case-title "Education Equality Case" \
	--round 1 \
	--side prosecution \
	--top-k 5
```

### Generic Multi-Source Tests

```bash
python src/agents/source_agents/run_source_agent.py \
	--source statutes \
	--query "employment minimum wage damages" \
	--case-title "Employment Contract Dispute" \
	--round 1 \
	--side defense \
	--top-k 3 \
	--jurisdiction federal
```

```bash
python src/agents/source_agents/run_source_agent.py \
	--source dockets \
	--query "severance confidentiality settlement" \
	--case-title "Employment Contract Dispute" \
	--round 1 \
	--side prosecution \
	--top-k 3
```

```bash
python src/agents/source_agents/run_source_agent.py \
	--source courtlistener \
	--query "employment contract severance confidentiality" \
	--case-title "Employment Contract Dispute" \
	--round 1 \
	--side prosecution \
	--top-k 3
```

### How It Fits the Workflow

1. Orchestrator emits one or more `source_agent_input` requests with `source_name` in `courtlistener`, `statutes`, `dockets`.
2. Each source agent returns normalized evidence snippets using the shared schema.
3. Source Synthesizer deduplicates and reranks across all retrieved snippets.
4. Synthesized evidence is then consumed by Prosecution/Defense agents.

## One-Command Round Demo

Run a full single-round pipeline in one command:

```bash
python src/agents/run_multi_source_round_demo.py \
	--input data/conversation/orchestrator_input.sample.json \
	--output data/conversation/round_demo_trace.json
```

This executes:

1. Orchestrator (builds multi-source fetch plan)
2. Source Router (runs all sources in fetch plan)
3. Source Synthesizer (dedupe + rerank + summary)

The complete round trace is saved to:

- `data/conversation/round_demo_trace.json`

## Multi-Round Demo (N Rounds)

Run a full N-round simulation (each round runs prosecution then defense):

```bash
python src/agents/run_multi_round_demo.py \
	--input data/conversation/orchestrator_input.sample.json \
	--rounds 2 \
	--output data/conversation/multi_round_demo_trace.json \
	--conversation-output data/conversation/multi_round_conversation.json
```

Artifacts produced:

- `data/conversation/multi_round_demo_trace.json`: Full round-by-round orchestrator/source/synthesizer trace.
- `data/conversation/multi_round_conversation.json`: Final append-only conversation history after all turns.

## Evidence Metadata (Optional, Backward-Compatible)

The shared `evidenceSnippet` now supports additional optional metadata fields:

- `source_type`: `case_law | statute | docket | guidance | internal_doc`
- `jurisdiction`
- `court_level`
- `decision_date`
- `precedential_status`
- `citation_count`
- `treatment_signal`: `positive | neutral | negative`

Existing snippets with only `source_name`, `citation`, `snippet`, and `relevance` remain valid.
