# VERGE — Design System (v1)

Verge is a personal decision-support tool for Polymarket's hourly BTC Up/Down
market — a **bet filter with a memory**, not a prediction engine. The design
system serves two distinct surfaces:

1. **Storefront** — the brand and marketing layer. Warm, editorial, trustworthy.
   The first thing someone sees; the landing page, the share card, the Telegram
   message preview. Borrowed idiom: the warm-cream + serif-editorial language of
   Claude.com, adapted for a trading-tool context.

2. **Instrument** — the product itself. A dark, information-dense signal
   dashboard read in seconds under a ticking clock. Borrowed idiom: crypto
   trading terminals (Hyperliquid-style) for restraint and density.

The split is deliberate. The storefront earns trust through warmth and
typographic seriousness; the instrument earns trust through precision and
zero-decoration clarity. They meet in the middle via shared semantic meaning
(coral = action, amber = cost, green = up, red = down) and a consistent
typographic voice (display serif on brand, geometric sans + monospace on data),
but they are **not** the same surface — they have different palettes, different
typography stacks, and different rules. This document defines both, and the
rules for keeping them in sync.

---

## Surface 1 — Storefront (brand)

### Color

Warm, cream-tinted canvas. Coral is the only saturated accent, used sparingly
on individual CTAs and generously only on full-bleed callout cards. Dark navy
surfaces carry product mockups and code chrome, not body content.

| Token | Hex | Role |
|---|---|---|
| `--brand-canvas` | `#faf9f5` | Default page floor. Warm cream — deliberately not pure white. |
| `--brand-surface-soft` | `#f5f0e8` | Section dividers, soft band backgrounds. |
| `--brand-surface-card` | `#efe9de` | Feature cards, content cards. One step darker than canvas. |
| `--brand-surface-strong` | `#e8e0d2` | Emphasized section bands, selected category tabs. |
| `--brand-ink` | `#141413` | Headlines and primary text. Warm dark, slightly off-pure-black. |
| `--brand-body` | `#3d3d3a` | Default running text. |
| `--brand-body-strong` | `#252523` | Emphasized paragraphs, lead text. |
| `--brand-muted` | `#6c6a64` | Sub-headings, breadcrumbs, secondary text. |
| `--brand-muted-soft` | `#8e8b82` | Captions, fine print. |
| `--brand-coral` | `#cc785c` | Primary CTA accent. The brand voltage — warm, slightly muted, never cyan. |
| `--brand-coral-active` | `#a9583e` | Press / hover-darken variant. |
| `--brand-coral-disabled` | `#e6dfd8` | Desaturated cream-tinted disabled state. |
| `--brand-on-coral` | `#ffffff` | Text on coral buttons. |
| `--brand-on-dark` | `#faf9f5` | Cream-tinted white on dark surfaces (echoes canvas tone). |
| `--brand-on-dark-soft` | `#a09d96` | Secondary labels on dark surfaces. |
| `--brand-dark` | `#181715` | Code editor mockups, model showcase cards, footer. |
| `--brand-dark-elevated` | `#252320` | Elevated cards inside dark bands. |
| `--brand-dark-soft` | `#1f1e1b` | Slightly lighter dark for inner code blocks. |
| `--brand-hairline` | `#e6dfd8` | 1px border on cream surfaces. |
| `--brand-accent-teal` | `#5db8a6` | Secondary accents (status dots, "active connection"). Sparingly. |
| `--brand-accent-amber` | `#e8a55a` | Category badges, inline highlights. Small companion to coral. |
| `--brand-success` | `#5db872` | "Available" indicators, positive states. |
| `--brand-warning` | `#d4a017` | Warning callouts (rare). |
| `--brand-error` | `#c64545` | Validation errors. |

Rule: coral is **scarce** on individual elements — a button here, a badge there
— and **generous** only on full-bleed `{component.brand-callout-coral}` cards.
Don't paint accent moments coral elsewhere.

### Typography

Two families, three roles. The editorial split is non-negotiable: serif for
display, sans for body.

- **Display — Copernicus** (or Tiempos Headline as substitute; open-source
  fallback: Cormorant Garamond at weight 500 with -0.02em tracking, or EB
  Garamond). All h1–h3 and hero display text. Weight **400** — never bold.
  Negative letter-spacing (-0.3px to -1.5px depending on size) is essential;
  the serif character is the brand voice.
- **Body — StyreneB** (or Inter as substitute; both humanist sans designed for
  screen reading). Navigation, buttons, captions, labels, running text. Weight
  400 for paragraphs, 500 for labels and emphasized phrases. Never Helvetica or
  Arial — too neutral, breaks the warm feel.
- **Code — JetBrains Mono.** All code blocks, terminal text, inline technical
  references.

| Token | Family | Size | Weight | Line Height | Tracking | Use |
|---|---|---|---|---|---|---|
| `--brand-type-display-xl` | Copernicus | 64px | 400 | 1.05 | -1.5px | Homepage hero h1 |
| `--brand-type-display-lg` | Copernicus | 48px | 400 | 1.1 | -1px | Section heads |
| `--brand-type-display-md` | Copernicus | 36px | 400 | 1.15 | -0.5px | Sub-section heads |
| `--brand-type-display-sm` | Copernicus | 28px | 400 | 1.2 | -0.3px | Pricing tier names, callout headlines |
| `--brand-type-title-lg` | StyreneB | 22px | 500 | 1.3 | 0 | Plan size labels |
| `--brand-type-title-md` | StyreneB | 18px | 500 | 1.4 | 0 | Feature card titles |
| `--brand-type-title-sm` | StyreneB | 16px | 500 | 1.4 | 0 | Connector tile titles |
| `--brand-type-body-md` | StyreneB | 16px | 400 | 1.55 | 0 | Default running text |
| `--brand-type-body-sm` | StyreneB | 14px | 400 | 1.55 | 0 | Footer body, fine print |
| `--brand-type-caption` | StyreneB | 13px | 500 | 1.4 | 0 | Badge labels |
| `--brand-type-caption-upper` | StyreneB | 12px | 500 | 1.4 | 1.5px | Category tags, "NEW" badges |
| `--brand-type-code` | JetBrains Mono | 14px | 400 | 1.6 | 0 | Code blocks |
| `--brand-type-button` | StyreneB | 14px | 500 | 1.0 | 0 | Button labels |
| `--brand-type-nav-link` | StyreneB | 14px | 500 | 1.4 | 0 | Top-nav menu items |

### Layout

**Spacing.** Base unit: 4px.

| Token | Value | Use |
|---|---|---|
| `--brand-space-xxs` | 4px | Micro gaps |
| `--brand-space-xs` | 8px | Tight padding |
| `--brand-space-sm` | 12px | Compact padding |
| `--brand-space-md` | 16px | Standard internal padding |
| `--brand-space-lg` | 24px | Card padding (code windows, connector tiles) |
| `--brand-space-xl` | 32px | Feature card / pricing tier / model card padding |
| `--brand-space-xxl` | 48px | Coral callout card padding |
| `--brand-space-section` | 96px | Vertical rhythm between major bands |

**Grid.** Max content width: ~1200px centered. 12-column grid. Hero uses 6/6
split (h1 left, illustration right). Feature grids: 3-up desktop, 2-up tablet,
1-up mobile. Connector tiles: 4–6-up desktop, 2-up tablet, 1-up mobile.

**Pacing.** Alternating surface modes across consecutive bands: cream canvas →
cream card → dark mockup → cream → coral callout → dark footer. Never repeat
the same surface mode in two consecutive bands.

### Shapes

| Token | Value | Use |
|---|---|---|
| `--brand-radius-xs` | 4px | Badge accents, tiny dropdowns |
| `--brand-radius-sm` | 6px | Small inline buttons, dropdown items |
| `--brand-radius-md` | 8px | CTA buttons, inputs, category tabs |
| `--brand-radius-lg` | 12px | Content cards (feature, pricing, code-window, model comparison) |
| `--brand-radius-xl` | 16px | Hero illustration container |
| `--brand-radius-pill` | 9999px | Badge pills |
| `--brand-radius-full` | 50% | Avatar circles, icon buttons |

### Elevation & Depth

| Level | Treatment | Use |
|---|---|---|
| Flat | No shadow, no border | Body sections, top nav, hero bands |
| Hairline | 1px `--brand-hairline` | Inputs, sub-nav, occasionally cards |
| Cream card | `--brand-surface-card` bg, no shadow | Feature cards |
| Dark card | `--brand-dark` bg, no shadow | Code editor mockups, product chrome |
| Hover | Rare, `0 1px 3px rgba(20,20,19,0.08)` | Elevated hover states only |

### Components

**Top nav** — Cream bar, 64px tall, `--brand-canvas` bg. Spike-mark + wordmark left, horizontal menu center-left, "Sign in" text-link + "Try Verge" primary button right.

**Buttons**

- `button-primary` — `--brand-coral` bg, `--brand-on-coral` text, 12px × 20px padding, 40px height, `--brand-radius-md`. Active darkens to `--brand-coral-active`.
- `button-secondary` — `--brand-canvas` bg, `--brand-ink` text, 1px hairline border. Same dimensions as primary.
- `button-secondary-on-dark` — `--brand-dark-elevated` bg, `--brand-on-dark` text. Never inverts to light-on-dark.
- `button-text-link` — No background, inline. Used for "Sign in" and inline CTA links.
- `button-icon-circular` — 36px circle, `--brand-canvas` bg, hairline border.

**Cards**

- `brand-hero-band` — Cream hero, 6/6 grid. Left: h1 + sub-headline + buttons. Right: hero illustration or dark product mockup. `--brand-space-section` vertical padding.
- `brand-feature-card` — `--brand-surface-card` bg, `--brand-radius-lg`, 32px padding. Icon + title-md + body-md.
- `brand-mockup-card-dark` — `--brand-dark` bg, 32px padding. Carries actual product chrome (code, terminal, agent controls).
- `brand-code-window-card` — Dark card, code with line numbers and syntax highlighting. Inner code block in `--brand-dark-soft`. JetBrains Mono.
- `brand-model-comparison-card` — Cream bg, hairline border, 32px padding. Model name + blurb + text-link.
- `brand-pricing-card` — Cream bg, hairline border, 32px padding. Plan name (title-lg StyreneB), price (display-sm Copernicus), feature checklist, primary button.
- `brand-pricing-card-featured` — Flips to `--brand-dark` bg, `--brand-on-dark` text. Dark surface IS the featured signal.
- `brand-callout-coral` — Full-bleed coral, `--brand-radius-lg`, 48px padding. White text. The coral surface IS the voltage.
- `brand-connector-tile` — Cream bg, hairline border, 20px padding. Logo + title-sm + description.
- `brand-cta-band-coral` — Pre-footer, full-width coral, display-sm headline, cream button.
- `brand-cta-band-dark` — Pre-footer on dev pages, dark bg, on-dark text, often pairs with code-window card.
- `brand-footer` — Dark navy. 4-column link list. 64px vertical padding. Never inverts.

**Badges**

- `brand-badge-pill` — `--brand-surface-card` bg, 13px/500 caption, `--brand-radius-pill`.
- `brand-badge-coral` — `--brand-coral` bg, white text, caption-uppercase, `--brand-radius-pill`.

**Tabs**

- `brand-category-tab` — Transparent bg, muted text. Padding 8px × 14px.
- `brand-category-tab-active` — `--brand-surface-card` bg, ink text.

**Forms**

- `brand-text-input` — Cream bg, ink text, body-md type, `--brand-radius-md`, 10px × 14px padding, 40px height.
- `brand-text-input-focused` — Coral border, 3px coral-at-15% outer ring.

---

## Surface 2 — Instrument (product)

### Brief, restated

One person, one screen, one recurring decision: does this hour's BTC market have
an edge worth a limit order, after fees — or do you skip it. The page is read in
seconds, under a ticking clock, and consulted dozens of times a day. Its job is
not to look like a dashboard — it's to make the decision itself impossible to
misread, and to make **SKIP** feel like a real, disciplined outcome, not an
empty state where nothing happened.

### Color

Dark-only. Eight tokens, each with exactly one job — same discipline as
Ciphra's system (blue=trust, amber=value-movement, purple=locked). Here the
jobs map directly onto the three possible decisions plus cost and structure.

| Token | Hex | Job |
|---|---|---|
| `--void` | `#0B0D10` | Canvas. Never used for panels, only the base. |
| `--deck` | `#15181D` | Panel/card surface, one step up from void. |
| `--chalk` | `#E7E9EC` | Primary text, primary numerals. |
| `--static` | `#6B7280` | Secondary text, labels, timestamps. Never used for numbers that matter. |
| `--signal-up` | `#33C17E` | BET HIGHER only. Also the "win" mark in history strip. |
| `--signal-down` | `#E24C4C` | BET LOWER only. Also the "loss" mark in history strip. |
| `--idle-slate` | `#7C8695` | SKIP only. Deliberately *not* gray-out/disabled styling — full opacity, same weight as up/down, just a third hue instead of an absence of one. |
| `--cost-amber` | `#D6A544` | Reserved exclusively for fee/edge-after-cost numbers. Never used for anything else, so the moment amber appears, the eye knows "this is what it costs you." |

**Semantic-drift rule** (ported from Ciphra): if you're about to reuse
`--signal-up` or `--signal-down` for something that isn't the actual bet
direction (e.g. a generic "success" toast), stop — add a new token instead.
Semantic drift is what makes trading UIs unreadable under pressure.

### Typography

Two families, three roles. This is a numbers-first page — the type system's
job is to make every number instantly comparable to every other number, which
means one face handles *all* numerals, full stop.

- **Display / labels — Geist Sans.** Headers, state labels ("BET HIGHER",
  "SKIP"), button text. Geometric, modern, slightly technical without being
  cold.
- **Data — Geist Mono.** Every single number on the page: prices, RSI, MA
  values, odds percentages, countdown, position size, fee, ROI. Tabular
  figures throughout so columns of numbers actually align. No exceptions —
  the instant something is a number, it's in Geist Mono, so the eye learns
  that rule within the first five seconds of using the tool.
- **Weight, not a third face, for hierarchy.** The decision label
  (BET HIGHER / BET LOWER / SKIP) is Geist Sans at 700, ~48–64px. Everything
  else stays 400–500. One heavy weight, spent once, on the thing that matters.

### Layout

Single screen, no scroll at the primary breakpoint. Four horizontal bands,
top to bottom in order of "how fast do you need this."

```
┌───────────────────────────────────────────────────────┐
│ BTC · 1H          ⏱ 42:17 remaining          ● live    │  <- status band
├───────────────────────────────────────────────────────┤
│                                                         │
│              ▲  BET HIGHER                             │  <- decision band
│              Confidence: High · Score +0.62             │     (the hero)
│                                                         │
├──────────────┬──────────────┬──────────────────────────┤
│ RSI 78  -0.40│ VOL 4.1x +0.30│ MA  ▲trend  +0.25        │  <- evidence band
├──────────────┴──────────────┴──────────────────────────┤
│ Odds 65% ──────●────────────────  Indicator-implied 55% │  <- probability
│ Edge after fee: +8.2%   Suggested: limit YES @ 0.63     │     bar (Polymarket
│                                                          │     pattern, adapted)
├───────────────────────────────────────────────────────┤
│ Last 10 →  ▲ ▲ ▼ skip ▲ skip ▲ ▼ ▲ skip     Win 62%     │  <- history band
└───────────────────────────────────────────────────────┘
```

- **Status band:** asset, market duration, live countdown to the hour's close
  (a monospace ticking clock, not a progress bar, since seconds matter more
  than proportion here), and a small heartbeat dot (pulses on each successful
  `/api/heartbeat` tick — doubles as system-health indicator).
- **Decision band:** the hero. One of three states, full-width, full color
  commitment — see Signature Element below.
- **Evidence band:** the three indicators as compact stat blocks, each
  showing its raw value, its score contribution, and a one-line reason —
  this is "why," and it's the difference between trusting the tool and just
  obeying it.
- **Probability band:** a horizontal bar, market odds on one end, your
  indicator-implied probability marked as a separate tick — the *gap*
  between the two is rendered as visible space, because that gap is
  literally your thesis. Fee-adjusted edge sits directly below in
  `--cost-amber`, and the suggested limit price/size sits beside it — this
  is the one line that answers "what do I actually do."
- **History band:** thin, low-visual-weight, always visible. Up/down/skip
  marks plus a running win rate — this is what turns "vibes" into "200
  paper trades logged," visible on every single load instead of buried in a
  spreadsheet.

### Signature element

**SKIP rendered with full conviction, not as an absence.**

Every trading interface in existence renders "no trade" as a grayed-out,
disabled, low-opacity nothing — because visually, nothing happened. That's
backwards for this tool: skipping 80% of markets *is the strategy*, so a
skip needs to read as a decision made with exactly as much confidence as a
bet fired. `--idle-slate` gets the same saturation, same type weight, same
full-width decision-band treatment as `--signal-up` and `--signal-down` — the
only thing that changes is the hue and a small shield glyph instead of an
arrow. The first time you see three skips in a row rendered as boldly as
three bets, the tool has done its job: it's telling you discipline is
working, not that nothing is happening.

### States & motion

- **Decision transition:** when the score crosses a threshold and the
  decision band changes, cross-fade the color + label over ~200ms — no
  bounce, no confetti. This is a serious instrument, not a game.
- **Heartbeat pulse:** the status dot pulses once per successful backend
  tick (every 5 min). A missed pulse for >10 min turns it `--signal-down`
  red — your one system-health signal, at a glance.
- **Reduced motion:** all transitions collapse to instant swaps if
  `prefers-reduced-motion` is set. Nothing here is decorative, so nothing is
  lost by turning it off.

### What NOT to borrow from Hyperliquid-style terminals

Full order books, multi-pane chart layouts, and leverage sliders are core to
perp terminals but have no place here — this tool makes one binary decision
per market window, not continuous position management. Resist the pull to
add a candlestick chart just because "trading terminal" implies one; a
sparkline inside the MA stat block is enough context, and a full chart would
compete with the decision band for attention it hasn't earned.

---

## Cross-layer mapping

The storefront (cream, coral, serif) and the instrument (void, chalk, Geist)
are separate surfaces with a shared semantic core. The mapping rules below
keep them from drifting apart while preventing token misuse.

### Shared semantic meaning

| Concept | Storefront token | Instrument token | Crossing rule |
|---|---|---|---|
| Positive / upward | `--brand-success` (#5db872) | `--signal-up` (#33C17E) | Same family (green), never swapped. |
| Negative / downward | `--brand-error` (#c64545) | `--signal-down` (#E24C4C) | Same family (red), never swapped. |
| Neutral / idle | — | `--idle-slate` (#7C8695) | Instrument-only. No storefront equivalent needed. |
| Cost / fee | `--brand-accent-amber` (#e8a55a) | `--cost-amber` (#D6A544) | Same hue family. Storefront uses it for badges; instrument uses it for edge numbers. Never cross-use across jobs. |
| Action / CTA | `--brand-coral` (#cc785c) | — | Storefront-only. Instrument has no "CTA" — it has decisions. |

### Typography handoff

- **Storefront:** Copernicus serif (display) + StyreneB sans (body) + JetBrains Mono (code). Display serif is non-negotiable — it IS the brand voice.
- **Instrument:** Geist Sans (labels/display) + Geist Mono (data). One face handles all numerals, no exceptions.
- **Both surfaces share:** tabular-nums for any number that might appear in a column or comparison. Weight-400 display serif (storefront) and weight-700 decision label (instrument) serve equivalent "hero" roles in their respective layers.

### Color crossing rules

1. Never use a storefront accent token (`--brand-coral`, `--brand-accent-teal`,
   `--brand-accent-amber`) inside the instrument dashboard.
2. Never use an instrument token (`--signal-up`, `--signal-down`, `--idle-slate`,
   `--cost-amber`) on a storefront surface.
3. The semantic greens and reds are *related but not identical* hex values —
   this is intentional. Each surface has its own calibrated palette. Never
   cherry-pick a hex from one layer into the other.
4. `--brand-dark` surfaces (#181715) appear on storefront as mockup containers.
   The instrument's `--void` (#0B0D10) is deliberately darker — the dashboard
   is a deeper, more focused space. Don't round one to the other.

### Journey: storefront → instrument

The user's path: cream landing page (trust, warmth, "what is this") → click /
sign in → dark dashboard (precision, zero-decoration, "what do I do next").
The transition is a deliberate surface-mode shift: warm cream flips to deep
void. No blended intermediate state — the contrast IS the signal that you've
crossed from brand to instrument.

---

## Responsive behavior

### Storefront breakpoints

| Name | Width | Changes |
|---|---|---|
| Mobile | < 768px | Hamburger nav (full-screen cream sheet). Hero h1 64→32px. Feature grids 1-up. Connector tiles 2-up. Pricing 1-up. Footer 4→1 columns. |
| Tablet | 768–1024px | Horizontal nav tightens. Feature cards 2-up. Connector tiles 3-up. Pricing 2-up. |
| Desktop | 1024–1440px | Full nav. 3-up features. 4–6-up connectors. 3-up pricing. |
| Wide | > 1440px | Same as desktop with more outer breathing room. Max content width caps at 1200px. |

### Instrument mobile

The instrument is designed for ~375px (phone check per the original
requirement). Bands stack vertically. No horizontal scroll required — stat
blocks reflow to 2-up or single-column at narrow widths. Countdown remains
legible. Decision label stays full-width and full-weight at every viewport.

### Touch targets

- Storefront buttons: minimum 40 × 40px.
- Instrument decision band: tappable area covers full width × minimum 60px
  height (large enough for a thumb on a phone, given the "check from
  anywhere" use case).

---

## Do's and Don'ts

### Storefront

**Do**
- Anchor on the cream canvas. Pure white reads as "any other SaaS"; the warm tint IS Verge.
- Use Copernicus serif for every display headline. Negative tracking is non-negotiable.
- Use coral sparingly on individual elements, generously on full-bleed callout cards.
- Show actual product chrome (dark mockup cards) instead of marketing illustrations.
- Alternate surface pacing: cream → card → dark → cream → coral → dark.

**Don't**
- Don't use cool grays or pure white for canvas.
- Don't bold the serif display (Copernicus at 700 reads as bombastic; stay at 400).
- Don't use cool blue or saturated cyan as an accent. Coral is the brand voltage.
- Don't paint coral everywhere — it's scarce on individual elements.
- Don't use Inter for display headlines. The serif character IS the voice.
- Don't repeat the same surface mode in two consecutive bands.

### Instrument

**Do**
- Give every token exactly one job. If you're about to reuse `--signal-up` for a
  toast notification, add a new token instead.
- Render SKIP with full visual conviction — same saturation, same weight, same
  width as BET HIGHER/LOWER.
- Keep every number in Geist Mono. Tabular figures, no exceptions.
- Make the probability band's gap visible — that gap IS the thesis.

**Don't**
- Don't gray out or reduce opacity on the SKIP state.
- Don't add a full candlestick chart. A sparkline in the MA stat block is enough.
- Don't add bounce, confetti, or decorative motion. This is a serious instrument.
- Don't reuse instrument tokens on storefront surfaces, or vice versa.

---

## Known gaps

- **Licensed typefaces.** Copernicus, StyreneB, and Geist Sans/Mono are not
  all freely available as web fonts. Substitutes documented in each typography
  section. Verify availability at build time.
- **Verge mark.** The brand wordmark/ligature is TBD. The Anthropic spike-mark
  references from the source Claude system have been stripped; the storefront
  layer needs its own mark before any landing page ships.
- **Animation timings.** Chat message reveal, code block typewriter effect,
  and agentic-flow animations from the source Claude system are out of scope.
- **Form validation states.** Beyond the focused input state, error / success
  states need a real flow to confirm.
- **Instrument legend.** The decision-band's shield glyph for SKIP and the
  heartbeat-dot sizing are not formalized as tokens yet — treat as prose
  guidance until componentized.
