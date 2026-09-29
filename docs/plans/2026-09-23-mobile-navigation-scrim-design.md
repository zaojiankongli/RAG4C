# Mobile navigation focus layer (2026-09-23)

## Brief and evidence

RAG4C is an enterprise RAG knowledge-operations console. On narrow screens the persistent desktop rail becomes an overlay (`.app-sider.is-mobile` is absolutely positioned at z-index 30), while the workspace remains visible and the menu currently has no outside-click dismissal or Escape path. The existing contract is a 232px navigation rail, a persistent workspace scope header, and route-owned page state; those are not changing here.

## Design pass

Use the product's existing tokens rather than introducing another visual language:

| Role | Existing value |
|---|---|
| Ink rail / scrim base | `#0f1523` |
| Workspace canvas | `#f4f6fa` |
| Elevated surface | `#ffffff` |
| Primary action | `#3164f4` |
| Primary hover | `#2755d8` |
| Primary text | `#101828` |

Typography remains the existing system UI stack (`-apple-system`, Segoe UI, PingFang SC, Microsoft YaHei); IDs/data continue using the existing monospace token. No font, brand, radius, or animation changes are proposed.

**Layout concept:** On desktop, keep the persistent rail and full work surface. On mobile, opening navigation raises the same ink rail above one restrained ink scrim; the scrim is an outside-click dismissal target, while Escape and the existing close control remain keyboard/touch alternatives.

```text
Desktop:  [ persistent rail 232/72 ][ workspace scope + page content             ]
Mobile:   [ rail 232px ] [ dimmed workspace: scope + active page                ]
Closed:   [ 44px menu control ][ undimmed workspace                              ]
```

**Signature:** a clear, quiet “navigation is temporarily in front of your workspace” layer—no new modal card, gradient, or decorative transition. The state change is represented by one translucent veil, not by recoloring every surface.

## Critique before implementation

A generic mobile redesign could replace the enterprise rail with a bottom tab bar or a full-screen menu, but either would invent a second navigation model and weaken the existing route hierarchy. This plan keeps RAG4C's established ink rail/canvas composition and uses the scrim only to separate navigation from content. The only new interaction is dismissal; existing auth, route, dirty-workspace confirmation, and keep-alive state remain in `App`.

## Implementation and verification contract

- Add a mobile-only dismissible backdrop below the rail and above page content.
- Escape closes the menu and restores focus to the menu toggle; tapping the backdrop closes it and also restores focus.
- Keep route selection and existing “收起侧栏” / menu toggle behaviors intact. Keep keyboard focus inside the navigation while it is open; if the onboarding modal takes focus, suspend the nav trap and return focus to the right context when it closes.
- Add behavior tests first; then run App mobile/a11y tests, lint, typecheck/build, and a narrow screenshot check at a mobile viewport with the rail open. Also keep focus inside the open rail with Tab/Shift+Tab and isolate the page with `inert`.
- No database/backend/OpenAPI contract changes.
