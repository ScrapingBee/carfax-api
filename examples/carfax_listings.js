// First results page of a Carfax search, as JSON lines. Node 18+.
//
//   node carfax_listings.js /Used-Honda-CR-V_w301 10001 > crv.jsonl
//
// 10 credits a page: premium proxy + country_code=us, no JavaScript.
// Carfax sends non US traffic to carfax.eu, so the resolved URL is checked.
// Set SCRAPINGBEE_API_KEY first.

const API = "https://app.scrapingbee.com/api/v1/";
const KEY = process.env.SCRAPINGBEE_API_KEY;
if (!KEY) {
  console.error("Set SCRAPINGBEE_API_KEY first.");
  process.exit(1);
}

// window.__MOBX_STATE__ = {...};  is followed by more script, so cut the
// object out by counting braces outside of strings instead of a regex.
function extractObject(text, marker) {
  const start = text.indexOf("{", text.indexOf(marker));
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === "\\") esc = true;
      else if (ch === '"') inStr = false;
    } else if (ch === '"') inStr = true;
    else if (ch === "{") depth++;
    else if (ch === "}" && --depth === 0) return JSON.parse(text.slice(start, i + 1));
  }
  throw new Error("state object not closed");
}

async function page(url, tries = 3) {
  const params = new URLSearchParams({
    url, premium_proxy: "true", country_code: "us", render_js: "false",
  });
  for (let i = 1; i <= tries; i++) {
    const res = await fetch(`${API}?${params}`, { headers: { Authorization: `Bearer ${KEY}` } });
    const cost = res.headers.get("spb-cost");
    const resolved = res.headers.get("spb-resolved-url") || url;
    if (res.status === 500) {               // not billed, try again
      console.error(`attempt ${i}: 500`);
      continue;
    }
    if (!resolved.startsWith("https://www.carfax.com/")) {
      throw new Error(`redirected to ${resolved}`);
    }
    console.error(`${cost} credits, ${resolved}`);
    return res.text();
  }
  throw new Error("no page after retries");
}

async function main() {
  const [path, zip] = process.argv.slice(2);
  if (!path) {
    console.error("usage: node carfax_listings.js </Used-Make-Model_wNNN> [zip]");
    process.exit(2);
  }
  const qs = zip ? `?zip=${encodeURIComponent(zip)}&radius=50` : "";
  const html = await page(`https://www.carfax.com/${path.replace(/^\//, "")}${qs}`);
  const store = extractObject(html, "window.__MOBX_STATE__").SearchRequestStore;

  const area = store.searchArea;
  console.error(`${store.pagination.totalRecords} listings, area: ` +
    (typeof area === "string" ? area : `${area.city} ${area.zip}`));

  for (const d of store.results.listings) {
    const p = d.pillars || {};
    console.log(JSON.stringify({
      vin: d.vin,
      title: d.title,
      price: d.currentPrice,
      mileage: d.mileage && d.mileage.value,
      badge: d.badge,
      accidents: p.accidentHistory && p.accidentHistory.text,
      owners: p.ownerHistory && p.ownerHistory.text,
      service_records: p.serviceHistory ? p.serviceHistory.number : 0,
      dealer: d.dealer && d.dealer.label,
      dealer_rating: d.dealer && d.dealer.dealerAverageRating,
    }));
  }
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
