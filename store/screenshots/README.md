# Chrome Web Store listing assets

## `screenshots/`

Five 1280 × 800 PNGs, the size and count the Chrome Web Store asks for
(1–5 images, 1280 × 800 or 640 × 400).

| File | Left panel says | Side panel shows |
| --- | --- | --- |
| `01-check-a-posting.png` | Check a job offer before you trust it. | The empty state, and the box you paste into. |
| `02-paste-what-you-have.png` | Give it whatever you have. | A pasted posting, ready to investigate. |
| `03-searches-run-in-your-browser.png` | The searches run in your own browser. | A run in progress: `Reading pages · 7/12`. |
| `04-a-verdict-you-can-argue-with.png` | A score you can argue with. | An 87/100 HIGH RISK verdict and the signals behind it. |
| `05-shows-what-it-read.png` | It shows what it read — and what it could not. | Evidence coverage, and the sources that carried a signal. |

### These are placeholders — read before publishing

The **layout is the real product**: every frame embeds `extension/sidepanel.css`
and markup copied from `extension/sidepanel.html` and the `render*` functions in
`extension/sidepanel.js`, so the panel, its type and its colours are what a user
actually sees. Regenerate them whenever the side panel changes:

```bash
make extension-screenshots          # or: node extension/tools/make-screenshots.mjs
```

The **analysis content is not a recorded run**. No investigation was executed to
produce these images; the posting is the synthetic `SYN-001` sample from
`backend/tests/fixtures/seed_posts.json`, and the verdict, signals, coverage
numbers and evidence cards are illustrative sample data shaped like the real
response schema. Screens 04 and 05 are crops of the result view: a real panel is
scrolled, so the top of the input card is not in frame.

Replace 04 and 05 — and, if you like, 02 and 03 — with captures of a genuine
investigation before you submit, so the numbers and sources shown are ones the
product actually produced. 01 (the empty state) has no analysis in it and can
stay as it is.

## Still needed for the listing

- **Small promo tile** (440 × 280) and, if you want the listing featured, a
  **marquee** (1400 × 560). Neither is required to publish.
- A hosted privacy policy — `frontend/public/privacy.html` is committed and
  deploys to `/privacy`.
