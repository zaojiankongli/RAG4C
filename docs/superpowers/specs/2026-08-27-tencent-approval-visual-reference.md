# Tencent-style Enterprise Approval Visual Reference for RAG4C

**Observed:** 2026-08-27 (Asia/Shanghai)  
**Local evidence:** `output/playwright/tencent-approval-reference-2026-08-27/`

## Observed information architecture

- Approval Center is reached from enterprise management rather than the knowledge content navigation.
- Approval requests and approval rules are separate tabs.
- The rules page uses one primary create action, compact filters and a dense authority table.
- The request list filters by workspace, status, approval type, my-approval state and keyword.
- Request detail places approve/reject actions above a change-fact table.
- Approval process is a separate timeline tab containing actor, status and time evidence.
- Approval confirmation is concise; rejection requires a bounded non-empty comment.

## RAG4C translation

RAG4C will retain the restrained table-first layout while adding its governance evidence strip:

```text
┌ Approval authority / schema / execution adapter evidence ┐
├ Requests | Rules                                           ┤
├ workspace status action mine keyword filters              ┤
├ dense approval table / mobile priority cards              ┤
└ detail drawer: request facts | decision timeline           ┘
```

### Visual tokens

- primary blue: `#0052D9` / TDesign primary;
- canvas: `#F3F6F8`;
- primary ink: `#1D2129`;
- secondary text: `#4E5969`;
- approved: semantic green;
- rejected: semantic red;
- pending: blue dot/status tag;
- cancelled/expired: neutral slate.

Use 4–8 px radii, one-pixel borders, minimal shadow and compact row height. The page must not become a card dashboard.

## Signature element

The RAG4C-specific signature is an **approval authority strip** showing catalog revision, pending requests, decisions assigned to the current actor, active rules and `execution_adapter_not_connected` or connected consumer evidence.

## Responsive behavior

- desktop: dense table and right-side detail drawer;
- 375px: filter drawer plus priority cards;
- 280px: one action per row, full-width approve/reject controls in the detail drawer;
- rejection comment and confirmation actions must remain fully visible without horizontal scrolling.

## Boundary

The images are design calibration evidence only. RAG4C must not copy Tencent trademarks, proprietary assets or page markup.
