# Premise Labs — Landing Page

Single-scroll landing page for **Premise Labs**, the AI lab behind [Tortoise](https://github.com/daniel-ospina/tortoise).

**Live:** [premiselabs.co](https://premiselabs.co)

## Deploy

The page is a single static `index.html`. Deploys to Cloudflare Pages via Direct Upload:

```bash
npx wrangler pages deploy . --project-name=premise-labs --branch=main
```

## Configuration

Two placeholders to replace in `index.html`:
- **Formspree ID:** `REPLACE_ME` in `<form action="https://formspree.io/f/REPLACE_ME">`
- **Twitter link:** Already set to `@premiselabs`

## Tech

- GSAP + ScrollTrigger for canvas graph animation
- Dark slate/cyan palette with green/gold accents
- No framework, no build step — single HTML file
