# Query evidence-first welcome workspace design (2026-09-24)

## Context

The current Query page opens with a centered icon, a generic paragraph, three
feature cards, and sample-question chips. The existing screenshot
`frontend/output/shots/after4/light/1440/query.png` shows that the content is
readable, but its centered stack leaves the page's large work area visually
undifferentiated. The main product distinction—answers can be traced to
retrieved knowledge and its source passages—is present only as explanatory
copy.

The existing product direction in `docs/research/tencent-adp-knowledge-ui-2026-08-25.md`
calls for calm neutral surfaces, one primary action color, visible focus, and
RAG4C-specific evidence lineage. The general redesign plan also requires
behavior-preserving UI work and keeping reduced-motion and theme support.

## Goal

Make the first-use Query page explain how to start and what makes an answer
verifiable, while making better use of the available desktop canvas. Preserve
the chat and answer experience after the first question.

## Design

Use an asymmetric, two-column “evidence workbench” in the empty state:

```text
┌───────────────────────────────┬────────────────────────────┐
│ Knowledge Q&A                 │ 可追溯的回答路径           │
│ 让每个答案都从可核对的资料…   │ 01 检索资料                │
│ Authorized scope / no guess   │ 02 回链出处                │
│                               │ 03 生成回答                │
│ 常见问题                      │                            │
│ [问题示例] [问题示例]         │                            │
└───────────────────────────────┴────────────────────────────┘
```

The left side states the user action, explains the authorization/evidence
boundary in plain language, and presents existing sample questions as
accessible buttons. The right side is a compact ordered “evidence trail”:
retrieval, source citation, answer. This is static product guidance, not a
live status indicator or a claim that any particular query has completed.

The signature element is the connected three-step citation trail. Its
numbering is meaningful because it represents a real ordered process. Keep
color and typography grounded in existing theme tokens: neutral page and
surface colors, the theme's primary blue, semantic text/border tokens, and
monospace only for the step indices. Do not add external fonts, imagery,
fake metrics, or a new design-token system.

At tablet/mobile widths, stack the introduction, suggested questions, and
evidence trail in reading order. Keep question buttons keyboard-operable with
visible focus; use reduced-motion-safe transitions. Existing light, dark, and
anime themes must remain legible.

## Alternatives

1. **Keep the centered stack and tune spacing:** lowest risk, but does not
   connect the page's visual hierarchy to evidence tracing.
2. **Recommended — evidence workbench empty state:** improves the first-use
   explanation using only behavior the Query page already promises; keeps the
   change isolated from active conversations.
3. **Redesign the full chat and answer experience:** offers more surface area
   but mixes visual work with streaming, citation, and run-state behavior.
   Deferred to a separately scoped slice.

## Scope and invariants

- Change only the Query page's empty-state presentation and its responsive
  styling.
- Preserve sample prompts and their `ask()` behavior, ACL selection, send and
  stop behavior, streaming, answer cards, citations, navigation, and API calls.
- Do not imply that static guidance is live telemetry.
- Keep the existing top bar and docked input region.
- Do not change the navigation shell, backend contracts, or page routing.

## Files and verification

Expected implementation files:

- `frontend/src/pages/QueryPage.tsx`
- `frontend/src/pages/QueryWelcome.tsx`
- `frontend/src/pages/query-welcome.css`
- `frontend/src/pages/QueryWelcome.test.tsx`
- `frontend/src/styles.css` (remove superseded welcome-only rules)
- `frontend/src/theme/anime.css` (remove superseded welcome-only overrides if
  no longer applicable)

Run the focused welcome test, TypeScript/Vite production build, focused ESLint,
and `git diff --check`. Inspect a fresh desktop and mobile screenshot if the
local browser runtime is available. Request an independent sub-agent review
before treating the slice as handed off.

## Rollback

Revert only the implementation files listed above and remove this slice's
handoff/design documents. No data, schema, or persisted state is changed.
