# Aanya Hero Headline Design

## Goal

Refine only the landing-page hero so Aanya is the clear visual focus, while preserving the existing voice-agent experience and the rest of the page.

## Approved Content and Layout

The headline reads:

> Meet Aanya.  
> Here to listen, guide, and support.

`Meet Aanya.` stays on one line at every supported viewport. `Meet` remains near-black, while `Aanya.` is larger and uses a pink-to-purple text gradient. The supporting sentence sits below in near-black and may wrap naturally on smaller screens.

## Typography

Import Titillium Web from the supplied Google Fonts stylesheet. Scope the font to the hero headline only; navigation, body copy, cards, footer, and the connected voice-session interface keep their existing typography.

Use responsive `clamp()` sizing so `Aanya.` is visibly larger without forcing the first line outside the viewport. Preserve strong contrast for all non-gradient words.

## Motion and Accessibility

Replace the single visual `BlurText` sentence with a coordinated segmented heading. Keep the existing soft blur-and-rise reveal, maintain a semantic `<h1>`, and respect `prefers-reduced-motion` through the component's existing reduced-motion flag.

## Hero Spacing

Top-align the landing-page hero content with responsive top padding instead of vertically centering it in the viewport. Target roughly 32–40px between the sticky navigation bar and the first hero element on desktop, with compact responsive spacing on mobile. Do not alter spacing in later sections or in the connected voice-session view.

## Verification

- Run the frontend lint and formatting checks.
- Confirm the first headline line remains unbroken on mobile and desktop.
- Confirm only `Aanya.` receives the gradient and enlarged treatment.
- Confirm Titillium Web is limited to the hero headline.
- Confirm reduced-motion behavior and accessible heading text remain intact.
