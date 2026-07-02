// Vercel serverless function — POST /api/waitlist
// Saves the lead to a Resend Audience (NO email is sent). Degrades gracefully if
// unconfigured (returns ok and logs). Duplicate signups are treated as success.
import { Resend } from 'resend';

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }
  try {
    const b = req.body || {};
    const name = (b.name || '').toString().trim().slice(0, 200);
    const email = (b.email || '').toString().trim().slice(0, 200);

    if (!EMAIL_RE.test(email)) {
      return res.status(400).json({ ok: false, error: 'Please enter a valid email.' });
    }

    const apiKey = process.env.RESEND_API_KEY;
    const audienceId = process.env.RESEND_AUDIENCE_ID;

    if (apiKey && audienceId) {
      const resend = new Resend(apiKey);
      const parts = name.split(/\s+/).filter(Boolean);
      try {
        await resend.contacts.create({
          audienceId,
          email,
          firstName: parts[0] || undefined,
          lastName: parts.slice(1).join(' ') || undefined,
          unsubscribed: false,
        });
      } catch (dupErr) {
        // already on the list — that's fine, don't surface an error to the visitor
        console.log('[waitlist] contact create note:', dupErr?.message || dupErr);
      }
    } else {
      console.log('[waitlist] audience not configured; lead =', { name, email });
    }

    return res.status(200).json({ ok: true });
  } catch (err) {
    console.error('[waitlist] error', err);
    return res.status(500).json({ ok: false, error: 'Something went wrong. Please try again.' });
  }
}
