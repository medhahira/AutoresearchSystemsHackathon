# AutoresearchSystemsHackathon

## Setup

```bash
conda create -n modalhack python=3.11 -y
conda activate modalhack
pip install modal
python3 -m modal setup
```

## Runtime Budget Policy

- Run legal reasoning through the OpenAI API.
- Use a mini model for iteration and reserve the strongest model for final showcase runs.
- Use Modal primarily for orchestration and sandbox isolation.
- GPU demo mode is limited to A10 only.
- Do not use H100 or H100:8 for this project.

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
	- Highlights strong evidence and arguments for the optimized side, weak points
	  in the opposing side, and weak points the optimized side must repair.

5. **Prosecution Agent**
	- Uses case context + synthesized evidence to produce argument for prosecution.

6. **Defense Agent**
	- Uses case context + synthesized evidence to produce argument for defense.

7. **Judge Agent**
	- Scores every prosecution and defense turn out of 100.
	- Uses a fixed rubric: argument validity, evidence groundedness,
	  counter-attack/defense, legal specificity, and strategic strength.

8. **Final Strategy Agent**
	- Optimizes final recommendation for `optimize_for` side from Case Builder output.
	- Produces risk assessment and settlement recommendation.

### Conversation State

Maintain a single append-only `conversation` object:

1. Initialize with Case Builder output.
2. Append each turn output (sources used + argument + rebuttal signals).
3. Use this state as context for future retrieval and argument generation.

### Round Protocol (N rounds)

Default `N` is 2 rounds. The configured hard maximum is 10 rounds.
After each prosecution or defense turn, the Judge Agent scores the turn out of
100 using the fixed rubric above. Early convergence is checked only after round 2
and stops the debate when the optimized side scores at least 85/100 and leads the
other side by at least 15 points.

Round 1 uses independent async openings by default: prosecution and defense both
prepare from the case record at the same time, and the judge scores both opening
turns after they are appended. Later rounds are sequential so defense can respond
to prosecution's latest attack. Use `--sequential-opening` to make round 1 follow
the same prosecution-then-defense progression.

For each round `r` in `1..N`:

1. **Prosecution turn**
	- Orchestrator requests source fetch (optional, based on need).
	- Source Agents retrieve evidence.
	- Source Synthesizer condenses evidence.
	- Prosecution Agent generates argument.
	- Judge Agent scores the prosecution turn.
	- Append result to `conversation`.

2. **Defense turn**
	- Orchestrator requests source fetch (optional, based on need).
	- Source Agents retrieve evidence.
	- Source Synthesizer condenses evidence.
	- Defense Agent generates argument.
	- Judge Agent scores the defense turn.
	- Append result to `conversation`.

### End Condition and Final Output

After `N` rounds, run Final Strategy Agent on full conversation and return:

1. Side-optimized final case strategy (`defense` or `prosecution`).
2. Risk assessment (strengths, weaknesses, uncertainty).
3. Settlement guidance (whether to settle outside court and why).
4. Suggested next actions and evidence gaps.

## Offline Dummy Evaluation

The repository includes five synthetic cases with mocked source packets. This
benchmark does not call OpenAI or Modal; it validates schema fit, source coverage,
theme coverage, citation coverage, risk-band sanity, and early-convergence behavior.
Its dummy early-stop check uses the same judge-score threshold as the live pipeline.

```bash
python -m legal_arena.evals.evaluator --pretty
```

Add `--output data/evals/offline_dummy.json` to save the report.

To run the same dummy cases through the live OpenAI/Modal pipeline:

```bash
export OPENAI_API_KEY="sk-..."
python -m legal_arena.evals.live_runner --model gpt-5.4-mini --rounds 2 --pretty
```

Add `--output data/evals/live_dummy_gpt54mini.json` to save the live report.

Add `--modal-gpu A10` only for the optional GPU demo path.

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
