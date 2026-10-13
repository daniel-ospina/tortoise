# Writing a blog post body

The blog renderer accepts a **fixed markdown subset** — it escapes raw HTML by design, so an
unsupported construct publishes as literal text rather than being silently interpreted. This page is
the contract: what the body may contain, and what each construct renders as.

> Verified against `website/functions/blog/_lib.ts` at `9b9c2c7be`. If a construct is missing from this
> page, assume it is **not** supported rather than assuming it works.

---

## Basics

| You write | You get |
|---|---|
| `#`–`####` headings | `<h1>`–`<h4>` — **`##` and `###` are the ones to use** in a post body |
| `**bold**`, `*italic*`, `` `code` `` | the obvious tags |
| `[text](https://example.com)` | an external link, `target="_blank"` + `rel="noopener"` |
| `[text](/blog/other-post)` or `[text](#a-heading)` | an **internal** link — same tab, no `target` |
| `![alt](https://…/x.png)` | an image |
| pipe tables (`\| a \| b \|`) | a real `<table>`, wrapped so it scrolls on mobile |
| `> quote` | a blockquote |

**Headings get anchors automatically.** Every `##`/`###`/`####` heading is given an `id` derived from
its text, so a deep link like `/blog/a-post#why-it-matters` works without you doing anything.

⚠️ **Raw HTML does not render.** `<table>`, `<div>`, `<iframe>` etc. publish as visible text. Use the
markdown construct instead.

---

## Table of contents

Write the marker on its own line:

```text
[[toc]]
```text
It is replaced with a linked table of contents built from the article's `##` headings **after**
parsing — so the links can never drift from the headings they name.

**If your body already has a hand-written TOC** in this shape, it is recognised and replaced too —
no need to convert it:

```text
**Table of contents**

1. First section
2. Second section
```text
The generated version replaces the paragraph *and* the list. If there is no `##` heading to link to,
your hand-written list is left alone (content is never deleted to make room for an empty replacement).

---

## Callouts (including the answer box)

GitHub-style alerts, as a blockquote whose first line is the marker:

```text
> [!ANSWER] Agent memory is any system that lets an agent retain and use information
> beyond what is immediately available in its current context.

> [!WARNING] Self-hosting requires a FalkorDB sidecar.
```text
Supported: `[!ANSWER]`, `[!KEY]` / `[!TAKEAWAY]` / `[!TAKEAWAYS]`, `[!NOTE]`, `[!TIP]`,
`[!IMPORTANT]`, `[!WARNING]`.

An unrecognised marker degrades to an ordinary blockquote — it never eats your text.

### The answer box, house style

A blockquote whose first line is `**Quick answer:** …` renders as the answer box directly:

```text
> **Quick answer:** Agent memory is any system that lets an agent retain and use
> information beyond what is immediately available in its current context.
```text
This is what the article templates already produce. **The quick answer belongs in the first ~150
words**, before the first `##`.

The rule, so you can predict it: the marker must **start** the quote, and it must be followed by a
colon. `> Quick answers are short.` is an ordinary blockquote — only `Quick answer:` is the marker.

---

## Key takeaways

```text
> [!KEY]
> - One per line.
> - Five to seven of them.
```text
---

## FAQ section

Give the section an `##` heading containing **"FAQ"** or **"Frequently asked"**, then make **each
question an `###` heading** and the answer the prose beneath it:

```text
## FAQ

### What is agent memory?

Any system that lets an agent retain and use information beyond its current context.

### Is Tortoise open source?

Tortoise is BSL 1.1 — free below $5M revenue.
```text
The page then emits `FAQPage` structured data built from exactly those pairs, so the questions are
worth writing as real search queries.

⚠️ **Google will not show an FAQ rich result for this site.** Since 2023-08-08 Google restricts FAQ
rich results to well-known government and health sites. The schema is still worth emitting — it is
read by Bing and by AI answer engines — but do not expect a Google SERP feature from it.

---

## Video

Write the marker on its own line, with the video's URL:

```text
[[video: https://www.youtube.com/watch?v=VIDEOID]]
```text
That renders the embed **and** emits `VideoObject` structured data. YouTube and Vimeo are supported
(`youtu.be/ID`, `/watch?v=ID`, `/embed/ID`, `/shorts/ID`, `vimeo.com/ID`).

Optional fields, after `|` — a title, an ISO-8601 duration, and a `YYYY-MM-DD` date. The two typed
fields are recognised **by shape**, so they may appear in any order:

```text
[[video: https://youtu.be/VIDEOID | The 2026 interview | PT42M10S | 2026-10-14]]
```text
Anything the marker does not supply falls back to the post's own metadata — its title, its
`meta_description`, and its publish date. For a companion interview published with its article those
fallbacks are correct, which is why the bare form is the normal one.

Two notes on the structured data:

- `thumbnailUrl` is **derived automatically for YouTube** (and so is the privacy-preserving
  `youtube-nocookie.com` embed). For Vimeo it is not derivable without an API call, so no thumbnail
  is emitted rather than a URL that would 404.
- A marker the renderer does not recognise is left as **ordinary text** — it never becomes a broken
  iframe.

### Transcripts

**A transcript is a normal post.** There is deliberately no separate route or content type: write the
transcript as the post body and include the `[[video: …]]` marker, and the page renders with the video
embedded and `VideoObject` emitted, exactly like any other article.

The convention:

1. **The transcript is its own post** — a distinct slug, e.g.
   `state-of-agentic-memory-2026-transcript`, titled `… — transcript`.
2. **Its body carries the same `[[video: …]]` marker**, so the video is embedded at the top and the
   `VideoObject` is emitted.
3. **The article links to it, and it links back**, with an internal link:
   `[Read the full transcript](/blog/state-of-agentic-memory-2026-transcript)`.

Point 3 is not cosmetic — reciprocal internal links are most of the SEO value of publishing a
transcript separately rather than pasting it into the article.

---

## What is *not* supported

- Raw HTML — escaped, publishes as text.
- Footers/footnote syntax, definition lists, `<details>`.
- Autolinked bare URLs — link them explicitly.
- Anything requiring a custom `id`: heading anchors are generated from heading text.
