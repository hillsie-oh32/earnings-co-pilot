// Cloudflare Worker for earnings-copilot.co.uk
//
// Serves one page that shows the Streamlit app full-screen, so the address
// bar keeps showing earnings-copilot.co.uk (a redirect would change it to
// earnings-co-pilot.streamlit.app). It also gives the site a tab title, an
// icon and link-preview tags for LinkedIn, WhatsApp, email etc.
//
// To update: Cloudflare dashboard -> Workers & Pages -> earnings-copilot ->
// Edit code -> paste this file -> Deploy.

const APP = "https://earnings-co-pilot.streamlit.app/?embed=true";

const TITLE = "Earnings Copilot";
const DESCRIPTION =
  "Equity research from the source filings: a company's numbers, accounting red flags, " +
  "valuation, peers, a DCF and insider dealing, built from official SEC and UK/EU filings, " +
  "plus an AI-written analyst briefing pack.";

// The site's logo (three rising bars) as a small inline icon.
const ICON =
  "data:image/svg+xml," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
      '<rect width="32" height="32" rx="6" fill="#1d3d6e"/>' +
      '<rect x="7" y="18" width="4.5" height="7" rx="1" fill="#fff"/>' +
      '<rect x="13.75" y="13" width="4.5" height="12" rx="1" fill="#fff"/>' +
      '<rect x="20.5" y="7" width="4.5" height="18" rx="1" fill="#fff"/>' +
      "</svg>"
  );

const HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${TITLE}</title>
  <meta name="description" content="${DESCRIPTION}">
  <link rel="icon" href="${ICON}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://earnings-copilot.co.uk/">
  <meta property="og:title" content="${TITLE}: read a company's results like an analyst">
  <meta property="og:description" content="${DESCRIPTION}">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="${TITLE}">
  <meta name="twitter:description" content="${DESCRIPTION}">
  <meta name="theme-color" content="#1d3d6e">
  <style>
    html, body { margin: 0; height: 100%; background: #ffffff; }
    @media (prefers-color-scheme: dark) { html, body { background: #0e1522; } }
    iframe { display: block; border: 0; width: 100%; height: 100vh; height: 100dvh; }
    .loading { position: fixed; inset: 0; display: flex; align-items: center;
      justify-content: center; font: 500 15px system-ui, sans-serif; color: #5b6779;
      z-index: -1; }
  </style>
</head>
<body>
  <div class="loading">Loading Earnings Copilot&hellip;</div>
  <iframe src="${APP}" title="${TITLE}" allow="clipboard-write; fullscreen"></iframe>
  <noscript><p><a href="${APP}">Open Earnings Copilot</a></p></noscript>
</body>
</html>`;

export default {
  async fetch(request) {
    const url = new URL(request.url);
    // One secure address for everyone: http:// and www. both go to
    // https://earnings-copilot.co.uk, in a single step.
    if (url.protocol === "http:" || url.hostname.startsWith("www.")) {
      url.protocol = "https:";
      url.hostname = url.hostname.replace(/^www\./, "");
      return Response.redirect(url.toString(), 301);
    }
    return new Response(HTML, {
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "public, max-age=300",
      },
    });
  },
};
