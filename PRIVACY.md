# Privacy Policy — CLIP.PULL

_Last updated: 2026-07-15_

CLIP.PULL ("the app," "the software") is a desktop application, developed and
operated by **Ramuel De Luna** ("the Developer," "we," "us"), that helps you
download and organize videos from lessons and courses you have legitimate
access to. This policy explains, plainly and specifically, what data the app
touches, where it goes, and what stays on your device.

There is no CLIP.PULL account, no sign-up, and no cloud service behind this
app. Read on for exactly what that means in practice.

## The short version

CLIP.PULL runs entirely on your own computer. Its "backend" is a small local
server that only ever talks to `127.0.0.1` (your own machine) — it is not a
remote server, and the Developer does not operate any server that the app
talks to. There is exactly **one** case where CLIP.PULL sends data off your
device on its own: verifying a CLIP.PULL Pro license key with Gumroad. Every
other outbound connection — downloading a video, or using an optional AI
feature — is a connection *you* configured, going directly from your device to
a service *you* chose, never through us.

## What the app stores locally

Everything below is written to a single local database file on your own
computer (in your Windows/macOS user profile's application-data folder) and
is never transmitted anywhere unless stated otherwise:

- **Download queue and history** — the URLs, titles, output folder paths, and
  status of videos you've queued or downloaded.
- **App settings** — your default download folder, theme, and similar
  preferences.
- **API keys**, if you choose to enter any (see "AI features" below) —
  stored locally only, never sent to the Developer.
- **AI usage statistics** — token counts and audio-duration figures for any
  AI calls you make, used solely to power the in-app Insights view (cost/time
  estimates). This is aggregated locally and is never transmitted anywhere,
  including to the Developer.
- **License status**, if you activate CLIP.PULL Pro — your license key
  (only its last 4 characters are ever displayed), the purchase email
  Gumroad returns, and activation timestamps.

Uninstalling the app removes this database along with everything else the
installer placed on your machine.

## Where your data actually goes

### Downloading videos

When you download a video, CLIP.PULL connects **directly from your device**
to the platform hosting it (e.g., Vimeo, Loom, YouTube, Wistia, Bunny
Stream, or the CDN behind a Skool/Teachable/Kajabi/Thinkific/Circle/
Systeme.io/ClickFunnels/GoHighLevel lesson) to fetch the video stream. This
traffic never passes through, or is visible to, the Developer — there is no
Developer-operated server in this path.

### CLIP.PULL Pro license verification (Gumroad)

If you enter a license key to activate CLIP.PULL Pro, that key is sent to
**Gumroad**, our third-party payment and licensing processor, to confirm it
corresponds to a valid, non-refunded purchase. Gumroad's response (purchase
status and the email associated with the purchase) is stored locally as
described above. CLIP.PULL periodically repeats this same check in the
background to confirm your license is still valid — for example, if a
purchase is later refunded or disputed, the next check will deactivate Pro
features automatically. Gumroad's own privacy policy governs how it handles
your license/purchase data: https://gumroad.com/privacy.

This is the only data the Developer's own commercial relationship (via
Gumroad) ever provides visibility into — and it is limited to license
validity and a purchase email, never your download activity, queue, history,
or any file on your device.

### AI features (transcription and summarization) — optional, off by default

CLIP.PULL can optionally transcribe and summarize video content using an AI
provider of your choice: Google Gemini, Anthropic, OpenAI, Groq, or
OpenRouter. These features only run if you supply your own API key for that
provider in Settings. When you use them:

- The relevant audio or transcript text is sent **directly from your device**
  to the provider you selected, using your own API key and your own account
  with that provider — never through a CLIP.PULL server, because none exists.
- Each provider's own privacy policy and terms govern how they handle that
  data. The Developer has no visibility into, and no access to, what you send
  them or what they return.
- Your API key is stored locally only and is never transmitted to the
  Developer.

If you never enter an API key or use these features, no data of this kind is
ever sent anywhere.

### The browser extension

The companion browser extension has its own privacy policy, since it runs in
a different context (your browser, not the desktop app):
[`extension/PRIVACY.md`](extension/PRIVACY.md). In short: it only reads a
page when you click its icon (or automatically on skool.com/loom.com), and it
only ever sends the detected video's URL/title to the CLIP.PULL app on your
own machine (`127.0.0.1`) — never to any remote server.

## What we do NOT do

- We do not operate a server that receives your download history, queue,
  settings, files, or API keys. No such server exists.
- We do not use analytics, telemetry-to-us, crash reporting, or advertising
  SDKs of any kind. Nothing in the app phones home usage data.
- We do not sell, rent, or share any data with third parties for marketing or
  advertising.
- We do not have the technical ability to see what videos you've downloaded,
  what you've transcribed, or what's in your queue — because none of it is
  ever sent to us.

## Data retention and control

Because everything is stored locally, you are always in full control of it:

- Clear your download history or queue from within the app at any time.
- Remove a stored API key by deleting it from Settings.
- Deactivate your CLIP.PULL Pro license from within the app (this also
  clears the locally stored license key and purchase email).
- Uninstall the app to remove the entire local database.

Since we never receive this data, there is nothing for us to delete on our
end beyond what Gumroad independently retains for the purchase itself (see
Gumroad's own privacy policy for that).

## Children's privacy

CLIP.PULL is not directed at children and is not knowingly used to collect
data from children. Since the app does not collect personal data on our
servers in the first place, this is largely academic — but if you believe a
child has provided us information in a support request or similar, contact
us and we will delete it.

## Changes to this policy

If how CLIP.PULL handles data changes, this document will be updated and the
"Last updated" date at the top will change accordingly. Material changes will
be called out in the app's release notes.

## Contact

Questions about this policy or how CLIP.PULL handles data:
**rtdeluna.dev@gmail.com**

---

_This policy describes CLIP.PULL's actual technical behavior as of the date
above, verified against its source code. It is provided by the Developer and
has not been reviewed by an attorney; it is not a substitute for independent
legal advice._
