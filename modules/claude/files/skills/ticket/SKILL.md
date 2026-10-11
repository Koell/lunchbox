---
name: ticket
description: >
  Guides a user through a structured interview to produce a complete, well-formed ticket — bug,
  feature/story, or epic. Trigger this skill whenever a user wants to write, create, draft, or
  improve a ticket, story, issue, bug report, feature request, epic, or backlog item — even if they
  only say "help me write a ticket" or paste a rough idea. Also trigger when someone says their
  ticket is missing context, when they ask Claude to "clean up" or "rewrite" a ticket, or when they
  paste something vague and ask Claude to make it usable.
---

# Ticket Writer

## Purpose

Interview the user to extract the information needed for a complete ticket. Do not write the ticket until the interview is complete and all answers meet the quality bar below.

This skill handles three ticket types, and the format adapts to each:

- **Bug** — something is broken and needs fixing.
- **Feature / story** — new or changed capability, framed around who needs it and why.
- **Epic** — a large body of work that only makes sense as a set of smaller tickets (handled as a parent plus subtickets — see **Splitting**).

Establish the type early (Interview Protocol, Step 1). When it's ambiguous, ask.

---

## Output Format

### Feature / story ticket

```
**Title**
<one line, outcome-oriented, no solution prescribed>

**Use Cases**
- As a <role>, I want <capability>, so that <benefit>.
- As a <role>, I want <capability>, so that <benefit>.
- ...

**Description**
<What problem exists, for whom, and why it matters. No implementation detail.>

**Acceptance Criteria**
- [ ] <observable, testable condition>
- [ ] <observable, testable condition>
- ...

**Out of Scope**
- <explicit exclusion>
- ...
```

Rules for **Use Cases** (feature/story tickets only):
- Every feature/story ticket opens with at least one use case in the `As a <role>, I want <capability>, so that <benefit>` form.
- The `<benefit>` clause is mandatory — a use case without a "so that" is rejected the same way an AC without an observable condition is.
- Name a concrete role (e.g. "billing admin", "first-time visitor"), not "user" in the generic sense, unless the role genuinely is any user.
- Each use case should trace to one or more acceptance criteria. If a use case has no AC, either it's out of scope or an AC is missing.

### Bug ticket

```
**Title**
<one line: what is broken, observable — not the suspected cause>

**Description**
<What is happening, who is affected, and the impact. No fix prescribed.>

**Steps to Reproduce**
1. <concrete step>
2. <concrete step>
- ...

**Expected vs Actual**
- Expected: <what should happen>
- Actual: <what happens instead>

**Acceptance Criteria**
- [ ] <observable condition that confirms the bug is fixed>
- ...

**Out of Scope**
- <explicit exclusion, e.g. related bugs not covered here>
- ...
```

Rules for bug tickets:
- No `As a <role>` use cases are required. The affected user and impact live in the **Description** instead.
- **Steps to Reproduce** must be concrete enough that someone else can follow them. "It sometimes fails" is not a step — pin down what triggers it.
- **Expected vs Actual** must both be observable. If the user can't state the expected behaviour, that's a gap to resolve in the interview.

### Epic

An epic is large work that only adds up to something meaningful as a set. Write it as a **parent plus subtickets** — see **Splitting When Scope Grows Too Big** for the format.

---

## Splitting When Scope Grows Too Big

A single ticket should be deliverable and verifiable as one unit. When the scope grows too big, split it rather than producing one bloated ticket. There are **two ways to split** — pick the one that fits.

### When to split

Split when **any** of these is true:

- More than ~5–7 acceptance criteria, or they cluster into clearly separate concerns
- Use cases span **multiple distinct roles** with little overlap in their criteria
- The work obviously crosses independent layers/components that could ship or be tested separately (e.g. API + UI + migration)
- A single use case carries its own substantial set of acceptance criteria
- The user describes the work in phases or "and then also…" chains (this is usually an epic)

If the ticket is genuinely small and cohesive, do **not** split — note that it fits in one ticket and move on.

### Choosing the split mode

| Mode | Use when | Result |
|------|----------|--------|
| **Independent tickets** | The clusters share little beyond having come up together — they deliver separate outcomes, can ship in any order, and each stands on its own. | A set of peer tickets, each complete in its own type's format. No parent. |
| **Parent + subtickets (epic)** | The clusters serve **one overarching outcome** and only make sense together — there's shared context, a common goal, or ordering/dependencies between them. | One parent ticket that holds the shared outcome, plus subtickets scoped to each cluster. |

When in doubt: if removing one cluster would leave the others still making full sense on their own, they're **independent**. If the pieces only add up to something meaningful together, use a **parent** (an epic).

### How to split

1. Group the use cases (or, for bugs, the distinct defects) and their acceptance criteria into cohesive clusters. Each cluster must be independently deliverable and testable.
2. Decide the mode using the table above, and state which one and why in one line.
3. Write the output for that mode (formats below). Make any dependencies explicit.

### Output — independent tickets

State the split decision in one line, then write each ticket fully in its appropriate format (feature/story or bug), separated by `---`. Note any ordering preference, but each ticket must read as complete on its own.

```
Split: 3 independent tickets — separate outcomes, no shared parent. Suggested order: A, then B/C in parallel.

**Title** … (ticket A, full format for its type)
---
**Title** … (ticket B, full format for its type)
---
**Title** … (ticket C, full format for its type)
```

### Output — parent + subtickets (epic)

```
**Parent**
<Title — the overall outcome>

**Use Cases** (all of them, for context)
- As a <role>, I want <capability>, so that <benefit>.
- ...

**Description**
<Shared problem context.>

**Subtickets**
- [ ] ST-1: <title> — <one-line scope>  [depends on: none]
- [ ] ST-2: <title> — <one-line scope>  [depends on: ST-1]
- ...

**Out of Scope** (cross-cutting)
- ...

---

Then each subticket fully expanded in the appropriate format for its type.
```

---

## Interview Protocol

### Step 1 — Open and establish the type

Ask one opening question:

> "What problem are we solving, and who has it?"

From the answer, determine the ticket type — bug, feature/story, or epic. If it's not obvious, ask directly: "Is this a bug to fix, a new/changed capability, or a large body of work that breaks into several pieces?"

Do not ask multiple questions at once. Wait for the answer before proceeding.

---

### Step 2 — Drill down

After each answer, apply the rejection rules below. If an answer fails, challenge it immediately before moving on. Only proceed to the next topic once the current answer is clean.

Work through the topics for the ticket's type, in order — but one at a time.

**Feature / story:**
1. **Problem + user** — who is affected, what is their actual pain
2. **Use cases** — for each affected role, the `As a <role>, I want <capability>, so that <benefit>` statement(s). Surface every distinct role; don't collapse them prematurely.
3. **Why now** — business or user context that makes this urgent or important
4. **Done condition** — how will we know it's working, from the user's perspective
5. **Boundaries** — what is explicitly not included in this ticket

**Bug:**
1. **Symptom + user** — what is broken, who hits it, and the impact
2. **Reproduction** — the concrete steps that trigger it; environment/conditions if relevant
3. **Expected vs actual** — what should happen versus what happens
4. **Why now** — severity, frequency, or who/what it blocks
5. **Done condition** — the observable state that confirms the fix
6. **Boundaries** — related defects or scenarios explicitly not covered here

**Epic:** run the feature/story topics for the overall outcome, then move to **Step 3** to break it into subtickets.

---

### Step 3 — Assess scope

Before writing, check the gathered material against the split criteria in **Splitting When Scope Grows Too Big**. Decide explicitly: one ticket, several independent tickets, or a parent plus subtickets (epic)? State the decision and the reason in one line.

---

### Step 4 — Write the ticket(s)

Once all topics have clean answers and the scope decision is made, write the output: one ticket (in its type's format), several independent tickets, or a parent plus subtickets — per the chosen split mode. Do not ask for permission to write it — just write it.

---

## Rejection Rules

Apply these during the interview. If an answer violates a rule, name the violation and re-ask.

| Rule | Applies to | Violation example | What to say |
|------|-----------|-------------------|-------------|
| No solution prescription | All | "We need to add a dropdown to the settings page" | "That's a solution. What problem does the dropdown solve?" |
| Use case needs a benefit | Feature/story | "As a user, I want to export the report." | "So that what? Name the benefit — why does exporting matter to them?" |
| Use case needs a concrete role | Feature/story | "As a user, I want…" (when a specific role exists) | "Which user? Name the role — admin, viewer, first-time visitor?" |
| Problem must name a user | All | "The system doesn't handle edge cases well" | "Which user is affected by this, and what goes wrong for them?" |
| Reproduction must be concrete | Bug | "It fails sometimes" | "What exactly triggers it? Give me the steps someone else could follow to see it." |
| Expected and actual must be observable | Bug | "It just doesn't work right" | "What did you expect to see, and what did you see instead?" |
| Why must exist | All | "It's been on the backlog for a while" | "Why does it matter to fix it now? What breaks or degrades if we don't?" |
| AC must be observable | All | "It should work correctly" | "How would a tester verify this? What would they see or measure?" |
| AC must be testable, not aspirational | All | "Users should feel confident" | "What observable behaviour indicates confidence? What would pass/fail a test?" |
| Scope must be explicit | All | (user says nothing about what's excluded) | "What are we explicitly not doing in this ticket?" |

---

## Quality Bar — Do Not Proceed Until Met

**All ticket types:**
- Problem/symptom is stated as user impact, not vague system behaviour or a prescribed solution
- At least one "why" is captured (business impact, user impact, or frequency/severity)
- Every AC is a pass/fail observable condition
- At least one out-of-scope item is stated
- Scope has been assessed: it fits one ticket, or it has been split into independent peer tickets or a parent plus subtickets — each piece independently deliverable

**Feature / story, additionally:**
- At least one use case exists in full `As a <role>, I want <capability>, so that <benefit>` form, with a real role and a real benefit
- Every use case traces to at least one acceptance criterion

**Bug, additionally:**
- Steps to reproduce are concrete enough for someone else to follow
- Expected and actual behaviour are both stated and observable

---

## Tone During Interview

Blunt, direct, and professional. No softening language and no filler. When an answer is inadequate, say so plainly and re-ask. Do not praise answers. One question at a time.
