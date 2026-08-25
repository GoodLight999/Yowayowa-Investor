# Yowayowa-Investor Design System

## Overview
Yowayowa-Investor is a personal investment-research workstation, not a marketing site and not a generic SaaS dashboard. The visual model is **analyst terminal × research notebook**: dense enough for serious financial work, calm enough for prolonged reading, and explicit about data provenance.

The existing identity is intentional and should be preserved: warm paper surfaces, a near-black navigation rail, square geometry, thin rules, restrained use of blue, and tabular/monospaced numerics. Redesigns must improve hierarchy and usability without replacing this identity with fashionable UI defaults.

## Sources of design discipline
Use these as review references, not as styles to copy mechanically:
- Impeccable: https://impeccable.style/ — especially its AI-slop and production-quality checks.
- Taste Skill v2 / gpt-tasteskill: https://www.tasteskill.dev/ — audit an existing product before redesigning it; infer interface decisions from product purpose and audience.
- Emil Kowalski Skills: https://github.com/emilkowalski/skills — motion and interaction details should be purposeful and economical.
- UI UX Pro Max: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill — use as a broad UX checklist, never as permission to add decorative UI or reduce first-screen information priority.

## Information hierarchy
1. **Primary task first.** On every screen, the most likely user task must be visually obvious within the first viewport.
2. **Data before decoration.** Financial values, labels, time basis, source, and next action outrank ornamental chrome.
3. **Progressive disclosure.** Advanced controls belong behind deliberate secondary affordances when they compete with the primary task.
4. **One job per region.** Search, natural-language operation, navigation, and AI assistance are distinct concepts. Do not present several equal-weight entry points for the same action.
5. **First screen earns its space.** Do not spend the initial viewport on duplicate branding, generic hero copy, tutorial cards, or decorative summaries.

## Colors
Canonical palette is sober and functional:
- paper: warm neutral background
- sheet: near-white content surface
- ink: near-black text and primary action
- rail: near-black navigation
- blue: single interaction/data accent
- green: positive financial state
- red: negative financial state

Rules:
- No purple/blue gradients.
- No neon glows.
- No glassmorphism.
- Do not use semantic red/green as decoration.
- A state must remain understandable without relying on color alone.

## Typography
- UI body text: **14px minimum** in the normal application shell.
- Inputs and primary interactive controls: **14px target**, never microcopy-sized.
- Navigation: **13px minimum**.
- Supporting copy: normally **11–13px**; 9–10px is reserved for terse machine metadata only.
- Financial numbers should use tabular numerals and may use the mono stack.
- Prose, labels, and explanations use the sans stack.
- Avoid all-caps body labels and excessive letter spacing; reserve uppercase for very short machine metadata.

## Layout
- Desktop: persistent left research rail is valid because this is a workstation.
- Mobile: primary navigation must not require horizontal scrolling. It must fit visibly as a compact grid/menu with touch-sized targets.
- Content width should favor readable analysis rather than maximizing filled screen area.
- Use spacing to group related information; do not put every concept in a bordered card.
- Tables remain compact, but row height and labels must remain readable.

## Shapes and surfaces
- Square or nearly-square geometry is part of the identity.
- Border radius is not a default decoration. Use zero or very small radii unless the platform control itself benefits from one.
- Shadows are reserved for overlays such as command palette/dialog/drawer; normal content should use rules, spacing, and surface contrast.
- Avoid card grids as navigation. Prefer lists, tabs, toolbars, inline actions, or task-specific regions.
- Avoid pills/chips except where the compact categorical shape carries real semantic meaning.

## Navigation
- Primary rail contains only the seven everyday destinations: Home, Discover, Markets, Macro, Portfolio, AI, Settings.
- Advanced/specialist destinations remain discoverable through the command palette or contextual links rather than becoming a tool dump.
- Active state must be unmistakable.
- Navigation labels must be human task names; provider names and implementation jargon do not belong in primary navigation.

## Forms and interaction
- Primary controls should be at least **36px** high on desktop and **40px** on touch-oriented narrow layouts.
- Touch targets should not be cramped together.
- Placeholder text is an example, not a replacement for a needed label.
- Loading, empty, error, and success states must say what happened and what the user can do next.
- Do not expose raw provider errors when a stable product-level explanation exists.
- Destructive or irreversible actions require clear distinction from ordinary actions.

## Motion
- Default answer is no animation.
- When motion improves spatial understanding, prefer transform/opacity and short ease-out transitions.
- Avoid bounce, elastic motion, entrance ease-in, continuous decorative motion, and layout-property animation.
- Respect `prefers-reduced-motion`.

## AI surfaces
AI is an assistant, not the product chrome.
- AI actions are secondary to source-backed data and deterministic tools.
- Do not scatter identical “Ask AI” buttons through every block unless the context materially changes the prompt.
- Separate sourced facts from AI inference visually and textually.
- AI configuration belongs in Settings; ordinary research screens should not expose provider plumbing.
- Do not use sparkles, magic-wand iconography, gradients, or “AI” decoration as visual hierarchy.

## Accessibility and responsive quality
- Keyboard focus must always be visible.
- No page-level horizontal overflow at supported viewport widths.
- Primary navigation must not horizontally scroll on mobile.
- Interactive text must remain legible under browser zoom and narrow widths.
- Hover-only information is prohibited for essential content.
- Respect reduced motion.

## Anti-slop preflight
Before accepting a UI change, reject it if any answer is “yes”:
- Did we add a card because we did not know how else to group content?
- Did we add a pill, gradient, glow, glass surface, oversized hero, or decorative icon without semantic necessity?
- Are there multiple equal-weight primary actions competing in the first viewport?
- Is important information pushed below generic introductory copy?
- Did typography fall below the minimum simply to make the layout fit?
- Does mobile navigation require sideways scrolling?
- Did a new animation merely make the interface feel “alive” rather than clarify an interaction?
- Did we expose implementation/provider terminology where a user task name would be clearer?
- Did we copy a design-skill recommendation without checking whether it fits an investment research workstation?

## Implementation ownership
`src/yowayowa/web/static/interface.css` is the canonical shell, typography, hierarchy, and responsive design layer. Existing component stylesheets retain page/component-specific geometry during migration. New global visual decisions belong in `interface.css`; do not create another global override stylesheet.
