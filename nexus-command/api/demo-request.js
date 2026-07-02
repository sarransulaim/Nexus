// Vercel serverless function — POST /api/demo-request
// Emails the founder the demo request + preferred slot so they can send a meeting link.
// Fully self-contained: only dependency is `resend`. Degrades gracefully if unconfigured
// (still returns ok so the page never breaks; logs the lead to the function logs).
import { Resend } from 'resend';

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const esc = (s = '') =>
  String(s).replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]));

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }
  try {
    const b = req.body || {};
    const name = (b.name || '').toString().trim().slice(0, 200);
    const email = (b.email || '').toString().trim().slice(0, 200);
    const company = (b.company || '').toString().trim().slice(0, 200);
    const role = (b.role || '').toString().trim().slice(0, 200);
    const teamSize = (b.teamSize || '').toString().trim().slice(0, 60);
    const slot = (b.slot || '').toString().trim().slice(0, 200);
    const message = (b.message || '').toString().trim().slice(0, 2000);

    if (!name || !EMAIL_RE.test(email)) {
      return res.status(400).json({ ok: false, error: 'Please enter your name and a valid email.' });
    }

    const apiKey = process.env.RESEND_API_KEY;
    const notify = process.env.NOTIFY_EMAIL;
    const from = process.env.MAIL_FROM || 'Nexus <onboarding@resend.dev>';

    if (apiKey && notify) {
      const resend = new Resend(apiKey);
      const row = (k, v) =>
        v ? `<tr><td style="padding:6px 14px 6px 0;color:#94a3b8;white-space:nowrap;vertical-align:top">${k}</td><td style="padding:6px 0;color:#0f172a"><strong>${esc(v)}</strong></td></tr>` : '';
      const html = `
        <div style="font-family:Inter,Arial,sans-serif;max-width:560px">
          <h2 style="margin:0 0 4px">New demo request</h2>
          <p style="margin:0 0 18px;color:#64748b">Someone wants a Nexus demo. Reply to this email to reach them, then send a meeting link.</p>
          <table style="border-collapse:collapse;font-size:14px">
            ${row('Name', name)}
            ${row('Email', email)}
            ${row('Company', company)}
            ${row('Role', role)}
            ${row('Team size', teamSize)}
            ${row('Preferred slot', slot)}
            ${row('Message', message)}
          </table>
        </div>`;
      await resend.emails.send({
        from,
        to: notify,
        replyTo: email,
        subject: `New demo request — ${name}${company ? ` (${company})` : ''}`,
        html,
      });
    } else {
      console.log('[demo-request] email not configured; lead =', {
        name, email, company, role, teamSize, slot, message,
      });
    }

    return res.status(200).json({ ok: true });
  } catch (err) {
    console.error('[demo-request] error', err);
    return res.status(500).json({ ok: false, error: 'Something went wrong. Please email us directly.' });
  }
}
