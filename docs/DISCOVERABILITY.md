# Search and agent discoverability

Chopify's public documentation describes the actual local editing workflow.
Content targets recording-to-shorts, local video clipping, caption correction,
SRT export, podcast reframing and resumable batch work. Claims are backed by
source code, examples and tests; no fake ratings or invented performance figures.

## What is included

- A static HTML landing page: meaningful title/description, canonical URL,
  visible content without JavaScript, accessible headings, feature explanations,
  installation steps, FAQs, and links to the actual guides.
- Open Graph/Twitter preview metadata and a captioned clip demo.
- JSON-LD describing the software, source repository, license, free offer,
  requirements, author and documentation page. It contains no fabricated reviews.
- A sitemap listing the canonical HTML page; a permissive robots.txt template.
- `llms.txt` as a factual documentation index, generated `llms-full.txt` with
  complete guides, and `product.json` with capabilities and explicit limitations.
- A Pages build that copies only an explicit allowlist of public assets/docs.
  It never publishes local projects, transcripts, recordings, logs or presets.

The canonical URL is `https://mehbul.github.io/chopify/`. The local Studio editor
is marked `noindex` and is not part of the public website.

## Build and publish

```powershell
python tools/build_site.py --out _site
python -m http.server 8080 --directory _site
```

The GitHub Actions **Documentation site** workflow builds and deploys `_site` to
GitHub Pages on pushes to main, or manually. The repository's Pages setting must
use **GitHub Actions** as its source. The workflow uses standard Pages artifact
and deployment actions. The site URL must match metadata if moved to a custom
domain; update index.html, product.json, sitemap.xml, robots.txt and llms.txt.

GitHub project sites live under `/chopify/`. A robots.txt at
`/chopify/robots.txt` is not authoritative: crawlers read only
`https://mehbul.github.io/robots.txt`. Manage that origin-root file in the user
site repository if needed. The project robots file is a portable template, not a
claim that project-level directives control the whole origin.

## What remains external to the repository

After the public URL works, its owner can verify a URL-prefix property in Google
Search Console and submit `https://mehbul.github.io/chopify/sitemap.xml`. Domain
ownership/Google-account verification is not automated by Chopify. Indexing,
ranking, AI citations and rich results are determined by search providers.

Google says its normal SEO best practices apply to AI features; no special AI
file or markup is required. `llms.txt` is a convenience for tools that read it,
not a guaranteed indexing or ranking mechanism. Software structured data does
not guarantee a rich result; this page deliberately does not invent ratings to
meet review-related eligibility criteria.

## Maintenance checks

1. Keep commands/features in README, guides, llms.txt and product.json aligned.
2. Update dates when substantive documentation changes, not on every build.
3. Check canonical URLs and relative public assets after changing hosting paths.
4. Use the Rich Results Test/Schema.org validator and Search Console on the live
   page. Review coverage/indexing before making visibility claims.
5. Keep the build's allowlist restricted to public content.

References: [Google AI search guidance](https://developers.google.com/search/docs/appearance/ai-features),
[software structured data](https://developers.google.com/search/docs/appearance/structured-data/software-app),
[robots.txt scope](https://developers.google.com/search/docs/crawling-indexing/robots/intro).
