# Persona grading rubrics, side by side

5 delivery-loop personas, 1 architect and 1 product owner: 7 grading rubrics, 28 dimensions. They do not overlap by accident: each persona reviews a different artifact at a different point of the loop, so each needs different dimensions. This page is the cross-reference: which grade comes from whom, what it measures, and where the gaps are. The roles that use these personas are in [roles/](../roles/); the mapping is [config/personas.example.toml](../config/personas.example.toml). The review rule for agent builds is in [process/checks-by-tier.md](../process/checks-by-tier.md).

## Summary matrix

| Persona | Role | Artifact reviewed | Dimension 1 | Dimension 2 | Dimension 3 | Dimension 4 |
|---|---|---|---|---|---|---|
| **[Frink](frink.md)** (architect) | architect | System or component structure | Structural Integrity | Data Sovereignty | Evolutionary Fitness | Conceptual Compressibility |
| **[Ned](ned.md)** (analyst) | spec-checker | Story or spec | Clarity | Testability | Completeness | Business-Tech Fit |
| **[Martin](martin.md)** (agentic) | agentic-reviewer | Agent, prompt, AI angle of a PR | Agent Efficiency | Tool Design | Context Hygiene | Evaluability |
| **[Marge](marge.md)** (backend) | builder | Own diff, pre-commit | Fit | Craft | Observability | Debt Delta |
| **[Lisa](lisa.md)** (code review) | reviewer | Finished code | Testability | Reliability | Scalability | Annoyance Factor |
| **[Milhouse](milhouse.md)** (SE2) | rework | Any artifact (teachability lens) | Learnability | Question Surface | Growth Value | Implicit Knowledge Load |
| **[Apu](apu.md)** (product owner) | product-owner | The running change, live | Goal Movement | Caller Experience | Handoff Fidelity | Value per Cost |

Each grade is A to F with plus and minus. A grade block tells you which persona graded it without a header: the dimension set is the fingerprint.

## Where each grade lives in the loop

```
Frink reviews structure       ->  Structural Integrity / Data Sovereignty / Evolutionary Fitness / Conceptual Compressibility
Ned writes the story          ->  Clarity / Testability / Completeness / Business-Tech Fit
Martin augments (if AI)       ->  Agent Efficiency / Tool Design / Context Hygiene / Evaluability
Marge builds                  ->  Fit / Craft / Observability / Debt Delta
Lisa reviews code             ->  Testability / Reliability / Scalability / Annoyance Factor
Martin converts findings      ->  eval cases (no new grade; feeds Agent Efficiency / Evaluability)
Ned checks the ACs            ->  Clarity / Testability / Completeness / Business-Tech Fit again
Apu runs the change           ->  Goal Movement / Caller Experience / Handoff Fidelity / Value per Cost
Milhouse shadows (any stage)  ->  Learnability / Question Surface / Growth Value / Implicit Knowledge Load
```

Frink's grades measure *structural fitness before anyone builds*. Ned's and Martin's augmentation grades measure *intent quality*. Marge's measure *construction quality*. Lisa's measure *shipped quality*. Apu's measure *delivered value*, observed by running the change for the agent and the human at the end. Milhouse's measure *teachability* at any stage.

## Per-persona detail

### Frink: architectural quality

| Dimension | A anchor | F anchor |
|---|---|---|
| **Structural Integrity** | Boundaries real and enforced; 1 responsibility per component; a new engineer orients in 30 minutes. | A monolith wearing a distributed costume. Changing anything moves everything. |
| **Data Sovereignty** | 1 master per entity, mutation flows through it, the path is traceable. | No ownership model. Any component can write anything. |
| **Evolutionary Fitness** | The next 3 requirements are cheap under the current shape. | The next decision requires undoing this one first. |
| **Conceptual Compressibility** | A new engineer has a working mental model in 15 minutes from a whiteboard. | Nobody can explain it in under an hour. |

Graded at: the architectural review before the work, system description review, decision records.

### Ned: story quality

| Dimension | A anchor | F anchor |
|---|---|---|
| **Clarity** | Reads like a contract; 1 interpretation. | Even the analyst is not sure what it means. |
| **Testability** | Every AC has a scriptable pass and fail boundary. | It is done when the author nods. |
| **Completeness** | Edge cases covered; Out of Scope explicit. | Happy path only, *and* it misses a step. |
| **Business-Tech Fit** | The intent is obvious; the implied approach has the right shape. | Intent and solution have nothing to do with each other. |

Graded at: the spec check before the build, and the check against the ACs after it.

### Martin: agentic and AI-tooling quality

| Dimension | A anchor | F anchor |
|---|---|---|
| **Agent Efficiency** | Tight prompts, cached, retrieval-based, no redundant rounds. | Unbounded. Every call is a gamble on cost. |
| **Tool Design** | Verbs first, 1 job per tool, consistent shapes. | Kitchen-sink tools; agents fall back to free-form. |
| **Context Hygiene** | Pull-based retrieval dominant; a deliberate hot and cold split. | Copy-paste culture; no retrieval layer. |
| **Evaluability** | Regression suite, numeric metric, adversarial cases, monitored. | No way to tell if it works. |

Graded at: agent, prompt and tool-server design review, token audit, the AI angle of a PR review.

### Marge: build quality

| Dimension | A anchor | F anchor |
|---|---|---|
| **Fit** | Addresses the intent; the ACs are satisfied by design. | Built the wrong thing; the ACs pass; everyone loses. |
| **Craft** | Clean seams, no anti-patterns, fits the module's style. | Would fail her own review. |
| **Observability** | Correlation ids everywhere, structured logs, 3am-proof. | Silent failure possible; a customer will tell us. |
| **Debt Delta** | Surrounding code left better; follow-ups filed. | The garden is visibly worse. |

Graded at: the pre-commit self-review.

### Lisa: code review after the build

| Dimension | A anchor | F anchor |
|---|---|---|
| **Testability** | Clean injection, pure functions, mockable boundaries. | Cannot be tested without a rewrite. |
| **Reliability** | Retries, timeouts, idempotent writes, errors with context. | 1 network blip from corruption; hostile to operators. |
| **Scalability** | Near-constant scaling; profile-driven; proper batching. | Cannot scale; the shape of the code forbids it. |
| **Annoyance Factor** | Clean, consistent, nothing surprising. | Lisa is updating her resume. |

Graded at: the pull-request review. Lisa is stingy with A's.

### Milhouse: teachability

| Dimension | A anchor | F anchor |
|---|---|---|
| **Learnability** | A motivated SE2 could reproduce the reasoning from the artifact alone. | Even an experienced engineer could not reconstruct why. |
| **Question Surface** | Every non-obvious decision invites a question he can ask. | Decisions are invisible: correct but unexplained. |
| **Growth Value** | Engaging with it teaches a transferable principle. | Correct, and teaches nothing. |
| **Implicit Knowledge Load** | Self-contained; the prior context needed is minimal and named. | Needs extensive tribal knowledge. |

Graded at: any stage where teachability is in question; in the workbench, at the end of a rework round.

### Apu: delivered value

| Dimension | A anchor | F anchor |
|---|---|---|
| **Goal Movement** | The spec's goal is visibly met in use. | The goal is untouched, or moved backwards. |
| **Caller Experience** | An agent uses it correctly on the first call, and errors say what to do next. | The agent cannot use it. |
| **Handoff Fidelity** | The human's picture is accurate, including what is missing or stale. | The human is confidently misled by correct data. |
| **Value per Cost** | High value at proportionate cost; the remainder correctly deferred or dropped. | Significant cost for no user-visible value. |

Graded at: the acceptance after the build, by running the change live. A low Handoff Fidelity grade is never deferred to wait for real-world impact: a D ships with a mandatory follow-up, an F blocks the merge.

## Overlaps, and what makes them different

- **"Testability" appears for Ned and Lisa.** Ned asks: *can QA verify "done" from the ACs alone?* Lisa asks: *can this code be tested; are the seams there?* Same word, different artifact.
- **Fit (Marge) and Business-Tech Fit (Ned)** both ask whether the work matches intent: Ned before the build, Marge during it. Frink's **Structural Integrity** is a layer above both: does the *system shape* match the requirements?
- **Evolutionary Fitness (Frink) and Scalability (Lisa)** are adjacent but distinct: Scalability is performance under load; Evolutionary Fitness is whether the architecture absorbs the *next requirement*.
- **Observability is Marge's alone.** Lisa may flag its consequence (Reliability drops) but does not grade it directly.
- **Data Sovereignty is Frink's alone.** Lisa catches mutation bugs after they are written; Frink catches the missing ownership model before.
- **Debt Delta is Marge's alone.** Lisa grades what is there; Marge grades the *change*.
- **Agent Efficiency, Context Hygiene and Evaluability are Martin's alone.**
- **Annoyance Factor is Lisa's alone,** and deliberately subjective.
- **Conceptual Compressibility is Frink's alone.** Annoyance Factor is about line-by-line readability; Compressibility is about whether the *system* can be explained.
- **Milhouse's 4 dimensions are all unique.**
- **Caller Experience (Apu) and Tool Design (Martin)** both concern how an agent meets a tool: Martin grades the design at PR time; Apu grades actual use, live. A low Caller Experience on a tool Martin passed is a candidate eval case.
- **Goal Movement (Apu), Business-Tech Fit (Ned) and Fit (Marge)** all ask whether the work matches intent: before, during and after the build.
- **Handoff Fidelity and Value per Cost are Apu's alone.**

## Known gaps (not graded by anyone today)

Each is a candidate reason to write a new persona.

- **Accessibility and UX:** screen readers, keyboard-only use, low-bandwidth contexts.
- **Security posture:** Frink and Lisa touch security (boundary correctness; hard-coded secrets, injection), but nobody grades threat-model completeness or data-classification handling end to end.
- **Data correctness at scale:** nobody grades whether a pipeline produces *correct* data at load (dedup, late events, schema evolution).
- **Operational readiness:** runbooks, paging, service-level objectives, on-call ergonomics.
- **Documentation and knowledge transfer:** Milhouse grades artifacts; nobody grades whether the *repo as a whole* can be onboarded.
- **Product and UX research alignment:** nobody grades whether the stakeholder ask itself was informed by real users.

## Adding or changing a rubric

A change to a dimension or an anchor needs:

1. a case made by 1 of the personas (or a new persona) for the change;
2. a cross-check by the others that the dimension does not collapse into an existing one;
3. the update of the owning persona's page *first*, then this page;
4. the update of [roles/](../roles/) and the persona mapping if a new persona lands.

A rubric that is not applied in a review does not exist. If a dimension never shows up in a grade after 1 sprint, it is retired.
