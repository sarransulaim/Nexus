# Nexus — Landing Page

A **completely standalone** marketing site for Nexus. No dependency on the main app — it's a static `index.html` plus two tiny [Vercel serverless functions](https://vercel.com/docs/functions) that capture leads. Drop it in its own repo and deploy to Vercel.

```
nexus-landing/
├── index.html            # the whole landing page (self-contained: HTML + CSS + JS)
├── api/
│   ├── demo-request.js    # POST → emails you the demo request + preferred slot
│   └── waitlist.js        # POST → adds the email to your Resend audience (no email)
├── package.json           # one dependency: resend
├── .env.example           # the env vars to set in Vercel
└── .gitignore
```

## How leads work

| Action | What happens |
|---|---|
| **Request a demo** | Saves the details and **emails you** (name, email, company, role, team size, **preferred slot**, message) so you can reply with a meeting link. |
| **Join the waitlist** | Adds the person to your **Resend audience** — **no email sent**. The thank-you screen then invites them to request a demo too. |

Both use one free service: **[Resend](https://resend.com)**. No database to run.

## Deploy (5 minutes)

1. **Put this folder in its own Git repo** (e.g. `nexus-landing`) and push it to GitHub.
2. **Resend setup** (free): create an account → **API Keys** → create one (`re_…`). Optional but recommended: **Audiences → +** to create a waitlist audience and copy its ID.
3. **Import to Vercel**: [vercel.com/new](https://vercel.com/new) → pick the repo → **Deploy**. Vercel auto-detects the static site and the `api/` functions — no config needed.
4. **Add environment variables** in Vercel → *Project → Settings → Environment Variables* (see `.env.example`):
   - `RESEND_API_KEY` — your Resend key
   - `NOTIFY_EMAIL` — your inbox (where demo requests land)
   - `RESEND_AUDIENCE_ID` — *(optional)* your waitlist audience
   - `MAIL_FROM` — *(optional)* a verified sender; the default only delivers to your own email until you verify a domain
5. **Redeploy** (Vercel → Deployments → ⋯ → Redeploy) so the new env vars take effect.

> The page works the instant it deploys. Until you add the env vars, form submissions succeed for the visitor and are written to the **Vercel function logs** (Project → Logs) — so you never lose a lead, you just won't get the email/audience entry until configured.

### Email deliverability note
`MAIL_FROM` defaults to Resend's test sender (`onboarding@resend.dev`), which **only delivers to your own Resend account email** — fine for receiving your *own* demo notifications. To send from your domain (and email anyone), verify a domain in Resend and set `MAIL_FROM` to e.g. `Nexus <hello@yourdomain.com>`.

## Local preview

```bash
# static preview (forms will 404 the /api routes — that's expected locally):
npx serve .

# full preview WITH the serverless functions:
npm i -g vercel
vercel dev        # then open the printed localhost URL
```

## Customize

- All copy, colors, and sections live in `index.html` (one file).
- Form field contract — keep these keys if you edit the forms:
  - `POST /api/demo-request` → `{ name, email, company, role, teamSize, slot, message }`
  - `POST /api/waitlist` → `{ name, email }`
