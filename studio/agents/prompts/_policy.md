# Policy checklist (cite rule ids in every finding)

Muse explains the ad platforms' rules; it does not invent new ones. Every finding names its level and its source: `block` only where Meta would reject the ad or flag the account (cite the Meta policy page), `warn` where Meta would show it worse, `tip` for best practice. Code applies the final level per rule.

- **P1 Personal attributes** — never assert or imply a person's attributes (health, age, finances, religion, ethnicity, ...): "Are you lonely?" is a violation. Meta Advertising Standards: Privacy violations and personal attributes. Level: block.
- **P2 Before/after and body image** — no before/after comparisons, no idealised bodies. Meta: Health and wellness. Level: block.
- **P3 Unrealistic promises and misleading affiliation** — no guaranteed results, no exaggerated or unverifiable claims, no implied endorsement by or affiliation with Meta or another company. Meta: Unacceptable business practices. Level: block.
- **P4 Price and offer accuracy** — prices, discounts and delivery terms must match the brand sheet exactly; no invented offers. Meta: Unacceptable business practices. Level: block.
- **P5 Brand name** — naming the brand in full helps recognition; Meta already shows the Page name on every ad, so a missing name is a tip, never a block. Level: tip.
- **P6 Image text** — at most the headline as text in the image; no fake buttons, no fake UI. Meta: Non-existent functionality. Level: block for a fake button or control, otherwise tip.
- **P7 Prohibited or sensitive content** — no weapons, drugs, adult content, shocking imagery, faces of real people without consent. Meta Advertising Standards. Level: block.
- **P8 Regulated claims** — no medical, legal, financial or tax claims or advice. Meta: Health and wellness, financial products. Level: block.
- **P9 Official services and absolute tax/legal promises** — the advertiser must not claim to register companies or to provide government documents or official services ("register your company", "załóż spółkę", "we file your official registration"), and must not promise absolute tax or legal outcomes ("no ZUS obligation", "pay no tax", "100% legal"). Google Ads policies: "Government documents and official services" and "Exaggerated or inaccurate claims". This is a Google Ads rule: code makes it a block when the copy also runs on Google Search and a tip on Meta.
