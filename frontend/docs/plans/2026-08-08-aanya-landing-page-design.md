# Aanya Landing Page Design

## Goal

Replace the starter welcome screen with a production-quality, light-theme landing page for **Aanya**, a **Health Advisory Voice Agent** powered by **Murf Falcon + LiveKit**. Every “Get started” action must launch the existing LiveKit voice session. Backend behavior, token handling, credentials, and agent logic are outside this change.

## Direction

Use a clear editorial voice-product layout rather than an immersive 3D scene or dashboard-style demo. This keeps the page credible for a health-advisory product, gives the requested navigation and content room to breathe, and still provides a distinctive interactive focal point.

The visual language is informed by the supplied reference site:

- cool off-white page background;
- deep navy typography and primary controls;
- blue-to-violet-to-magenta accents;
- pale lavender surfaces and soft neutral borders;
- a faint dotted grid and restrained shadows;
- rounded pill actions with polished micro-interactions.

The result must remain an original Aanya composition and must not copy the reference layout or content.

## Information Architecture

The disconnected state is a single scrolling page with these sections:

1. A sticky navigation bar with the Aanya wordmark, Home, About, FAQ, and Get started.
2. A hero containing the Aanya name, “Health Advisory Voice Agent” descriptor, concise supporting copy, technology attribution, and primary Get started action.
3. An interactive voice-orb presentation that communicates listening and conversation without pretending that a session is already connected.
4. An About section with three concise capability cards focused on natural conversation, always-available guidance, and voice-first accessibility.
5. A short “How it works” strip that explains starting, speaking, and receiving a response.
6. An accessible FAQ accordion.
7. A final call-to-action and compact footer.

## Components and Motion

Use official ReactBits components installed from the `@react-bits` shadcn registry. The initial selection is:

- `DotGrid` for the low-contrast hero atmosphere;
- `BlurText` for the hero headline reveal;
- `FadeContent` for section entrances;
- `SpotlightCard` for capability cards;
- `Magnet` for restrained desktop CTA movement.

If a registry component is incompatible with React 19 or Tailwind 4, use another official ReactBits component from the same category rather than introducing a custom effects framework. Motion must be purposeful, respect `prefers-reduced-motion`, avoid blocking pointer input, and simplify on small screens.

## Session Flow

The existing `ViewController` remains the authority for disconnected versus connected UI. It passes the existing `useSessionContext().start` callback into the landing page. The navigation CTA, hero CTA, and final CTA all call that same callback. Once LiveKit reports a connected session, the current `AgentSessionView_01` replaces the landing page through the existing `AnimatePresence` transition.

No new LiveKit API calls, agent prompts, token routes, or backend changes are permitted.

## Theme and Responsiveness

The public page is light-only. Remove the public theme toggle and system-theme switching so the reference palette is stable. The voice-session view inherits the same Aanya tokens.

Desktop uses a split hero and full navigation. Mobile uses a compact brand row with a menu sheet, stacked hero content, reduced background animation, and touch-friendly controls. Anchor targets include sticky-header offsets.

## Accessibility and Failure Handling

- Use semantic landmarks and heading order.
- Keep all interactive elements keyboard accessible with visible focus states.
- Ensure adequate color contrast and do not convey session state through color alone.
- Provide descriptive labels for decorative and interactive voice visuals.
- Respect reduced-motion preferences.
- Preserve the existing agent-error toast and disconnection behavior.
- Disable duplicate start actions while a connection attempt is in progress if the existing session state exposes that signal without adding new LiveKit behavior.

## Performance

- Prefer CSS and the existing `motion` dependency over adding 3D libraries.
- Dynamically load a heavy canvas-based background if the selected ReactBits implementation materially affects the initial bundle.
- Import icons and components from direct paths where supported.
- Keep static content outside frequently updating voice-session subtrees.
- Avoid animation loops when reduced motion is requested or the visual is off-screen.

## Verification

- Run Prettier and TypeScript checks for the frontend.
- Run the production build without changing backend configuration.
- Verify Home, About, FAQ, and Get started navigation.
- Verify all Get started actions call the existing session start flow.
- Inspect the disconnected landing page and connected-session transition at desktop and mobile viewports.
- Confirm light-theme palette, responsive navigation, reduced-motion behavior, focus states, and no horizontal overflow.
- Confirm `git diff` contains no backend changes.
