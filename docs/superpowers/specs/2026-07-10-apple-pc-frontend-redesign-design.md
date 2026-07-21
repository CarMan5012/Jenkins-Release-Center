# Apple-style PC frontend redesign

## Goal

Redesign the existing Jenkins Release Center frontend as a restrained Apple-style desktop web application. Preserve all business behavior, API contracts, routes, and release operations while improving visual hierarchy, consistency, and usability.

## Scope

- Target desktop web only, with a practical minimum viewport width of 1024px.
- Keep Vue 3, Vite, TypeScript, Vue Router, Pinia, Naive UI, Axios, ECharts, and the existing icon package.
- Redesign the application shell and every existing page: login, dashboard, Jenkins, release plans, release detail, history, and configuration.
- Remove only files, styles, imports, and assets proven unused by repository-wide reference checks.
- Do not change backend endpoints, request payloads, state transitions, permissions, or route URLs.

## Visual direction

- Use an Apple desktop-app vocabulary rather than a marketing-site aesthetic.
- Use a soft `#f5f5f7` canvas, translucent navigation surfaces, white content layers, fine neutral borders, and restrained tinted shadows.
- Use the native Apple-compatible system font stack; do not download or add a font dependency.
- Use Apple blue as the single interaction accent. Keep success, warning, running, disabled, and failure colors only for operational meaning.
- Use 12–16px outer radii and smaller inner radii so nested controls retain hierarchy.
- Avoid decorative gradients, heavy glassmorphism, oversized marketing typography, and animation that distracts from release operations.

## Application shell

- Keep a persistent desktop sidebar for the five primary destinations.
- Restyle the sidebar as a light, subtly translucent macOS-style source list with clear selected, hover, and focus states.
- Keep a compact top bar for page context and the signed-in account action.
- Give the content area a readable maximum width while allowing data tables and logs to use the available desktop width.
- Remove mobile navigation and mobile-only responsive transformations.

## Components and states

- Centralize color, spacing, radius, shadow, and typography tokens in the existing global stylesheet and Naive UI theme overrides.
- Replace generic bordered cards with quiet grouped surfaces; use elevation only where hierarchy requires it.
- Keep operational tables dense and scannable, using subtle row separators, sticky or distinct headers where useful, tabular numerals, and clear row actions.
- Preserve the dark log console and improve its toolbar, contrast, wrapping, and state presentation.
- Retain visible hover, pressed, keyboard-focus, loading, empty, disabled, and error states.
- Keep destructive actions visually distinct without making every action equally prominent.

## Page behavior

- Existing stores, router guards, API utilities, filtering, sorting, dialogs, forms, release controls, and log behavior remain unchanged unless a presentation-only adjustment is required.
- Dashboard metrics become a compact overview strip rather than generic equal cards.
- Forms use grouped sections, aligned labels, consistent control widths, and clear validation feedback.
- Detail pages prioritize status, primary action, task progress, and logs in that order.
- Login becomes a focused desktop sign-in surface without decorative or unused demo content.

## Cleanup

- Search every candidate file and exported symbol before deletion.
- Remove the default Vue demo component and default Vue/Vite assets when no references remain.
- Remove unused CSS rules, unused imports, commented-out code, and PC-irrelevant media queries.
- Keep existing design documents and verification artifacts unless they are confirmed generated or obsolete project assets rather than source inputs.

## Verification

- Run the existing UI test script.
- Run TypeScript checking and the production Vite build through the existing `npm run build` command.
- Review every route at a desktop viewport and confirm navigation, forms, tables, dialogs, release actions, loading states, empty states, errors, and logs remain usable.
- Treat pre-existing failures separately from regressions introduced by the redesign.

## Non-goals

- No backend changes.
- No framework, component-library, routing, state-management, or chart-library migration.
- No new dependencies.
- No mobile or tablet layout work.
- No speculative component framework or abstraction layer.
