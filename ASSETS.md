# Assets

Manifest of every design asset used on the site. An asset is only approved once it is recorded here. To change an asset, change its entry here first, then the code.

## Typeface: display and reading
- Asset: Schibsted Grotesk, weights 400, 500, 600, 700, 800 (normal), Latin subset, WOFF2
- Source: Fontsource — `@fontsource/schibsted-grotesk` v5.3.0 (upstream: https://github.com/schibsted/schibsted-grotesk, also on Google Fonts)
- Licence: SIL Open Font License 1.1; attribution: none required on the page; licence text shipped alongside the files (`assets/fonts/OFL.txt`)
- File: `assets/fonts/schibsted-grotesk-latin-{400,500,600,700,800}-normal.woff2`
- Used in: `art-direction.css` (`@font-face` and the `--serif` / `--sans` font variables), so every heading, number and reading text on the page
- Replaces: Iowan Old Style (Apple system serif) for display and reading; Inter / Avenir Next for interface text. Mono labels unchanged (system monospace).
- Why: the bookish serif on cream read as AI-generated. Schibsted Grotesk was commissioned by a Nordic news-media group for its publications, so it keeps the editorial feel without the serif. Chosen from an eight-font shortlist; Inter, Space Grotesk, Geist, Manrope, DM Sans, Instrument Sans/Serif, Fraunces, Playfair Display and Bricolage Grotesque were excluded as common defaults on AI-built sites.
- Self-hosted: no request to Google or any third party when a visitor loads the page.
- Chosen: 29 September 2026

## Illustration: hero
- Asset: bank building with a coral brain beneath its lifted roof
- Source: AI-generated; credited on the site as "Illustrations generated with AI"
- File: `assets/bbb-hero.png` (1942 × 809, cream background baked in, no transparency)
- Used in: `index.html` arrival section (on a cream plate), Open Graph and Twitter card image

## Illustration: footer street
- Asset: street of shops centred on the bank with the coral brain
- Source: AI-generated; credited on the site as "Illustrations generated with AI"
- File: `assets/bbb-street.jpg` (1800 × 600)
- Used in: `index.html` footer
