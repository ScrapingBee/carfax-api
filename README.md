# Carfax API

<p align="center">
  <a href="https://www.scrapingbee.com/">
    <img src="https://github.com/user-attachments/assets/a3cae0ac-b1f7-4e6f-ac2b-c0f934220c65" alt="carfax-api" />
  </a>
</p>



[![checks](https://github.com/ScrapingBee/carfax-api/workflows/checks/badge.svg)](https://github.com/ScrapingBee/carfax-api/actions)
[![license](https://img.shields.io/github/license/ScrapingBee/carfax-api.svg)](LICENSE)

Most people who search for a Carfax API want one of two very different things: the vehicle history report for a VIN, or the used car listings and dealer reviews anyone can browse on carfax.com. This repo covers the second one and refuses the first. It is a [Carfax API](https://www.scrapingbee.com/scrapers/carfax-api/) client built on [ScrapingBee](https://www.scrapingbee.com/) for public, logged out carfax.com pages, tested on 2026-10-29. Read the decision table first; if your job is in the right hand column, the code below will not help you.

## Decide first: which Carfax data is in reach

| | Public carfax.com pages (this repo) | Vehicle History Report (out of scope) |
|---|---|---|
| What it is | Used car search results, vehicle listing pages, dealer profiles and reviews | The paid per VIN report Carfax sells to consumers and businesses |
| Who can see it | Any visitor, no account | A buyer of the report, or someone a dealer shares it with |
| Example URL | `/Used-Toyota-Camry_w630`, `/Reviews-<dealer>_<code>` | `/vehiclehistory/...` |
| History content | One line summaries printed on each listing card, such as "No Accident or Damage Reported" and "CARFAX 1-Owner" | Full ownership, title, odometer, accident and service records |
| Fetched by this code | Yes | Never. The listing JSON includes a report link (`icrUrl`); the parser drops it |
| ScrapingBee tier | premium proxy, US, no JavaScript: 10 credits a page | not attempted |

The summaries in the left column are part of the listing that the dealer advertises, and Carfax shows them to every visitor. The page footer says as much: "The CARFAX Vehicle History Report for the vehicle may contain more current information." If you need the report itself, buy it or use a licensed Carfax data product. Scraping it would mean getting past a paywall or reusing someone else's access, and this repo does neither.

## What the public pages expose, by page type

| Page | Data | Where it sits in the HTML | Per request |
|---|---|---|---|
| Search results, e.g. `/Used-Honda-CR-V_w301?zip=10001&radius=50` | 24 listings with price, mileage, deal badge, history summaries, dealer name, rating and review count | `window.__MOBX_STATE__` → `SearchRequestStore.results.listings` | 10 credits |
| Make page, e.g. `/Used-Toyota_m33` | every model URL with a local listing count | `SearchRequestStore.searchInfo.relatedLinks` | 10 credits |
| Vehicle page, `/vehicle/<VIN>` | one listing in full: 101 feature strings on the Ram 2500 we opened, seller comments, fees, six line history snapshot | `window.__MOBX_STATE__` → `SearchRequestStore.selectedVehicle` | 10 credits |
| Dealer reviews, `/Reviews-<name>_<code>` | rating split for sales and service, star percentages, the first 20 reviews | `__NEXT_DATA__` → Apollo `ROOT_QUERY` | 10 credits |

## Which ScrapingBee settings work

| Settings | Credits | What came back |
|---|---|---|
| `mode=auto`, `max_cost=25` | 1 | HTTP 301 to `https://www.carfax.eu/de`, the German site. Auto mode saw a 200 and stopped |
| `premium_proxy=true`, `country_code=us`, `render_js=false` | 10 | the US page with the full state object |
| same plus `render_js=true` and a click on "Go to page 2" | 25 | still page 1; the only search related XHR, a POST to `/api/ubs/search`, got 403 |
| direct call to the search backend `helix.carfax.com/search/v2/vehicles` | 0 | unbilled 500 both times: a DataDome 403 behind it on classic, an upstream 500 on premium |

The first row is the trap. Carfax redirects visitors outside the US, the redirect target answers 200, and Auto Mode counts any 200 as success. Check `spb-resolved-url` on every response; the client raises `NotCarfaxUS` when the host is not `www.carfax.com`. The blocked backend answered with a DataDome page:

```html
<p id="cmsg">Please enable JS and disable any ad blocker</p>
<script data-cfasync="false">var dd={'rt':'c', ... 'host':'geo.captcha-delivery.com', ...}</script>
```

## Carfax scraper code

### The listing state object

Search pages are server rendered. Near the end of the HTML:

```html
<script>window.__MOBX_STATE__= {"AppStore":{...},"SearchRequestStore":{...},"FilterStore":{...}}</script>
```

Inside `SearchRequestStore`, the pagination block and one listing, trimmed:

```json
"pagination": {"pageNum": 1, "totalPages": 29, "totalRecords": 678, "pageSize": 24, "displaying": "1 - 24"},
"results": {"listings": [{
  "vin": "3C63R5DL1SG522873", "year": 2025, "make": "Ram", "model": "2500", "trim": "Big Horn",
  "badge": "GREAT", "mileage": {"label": "22,726", "value": 22726},
  "listPrice": 52527, "currentPrice": 52527,
  "onePrice": {"label": "$57,110", "difference": "$4,583 below"},
  "pillars": {
    "accidentHistory": {"text": "No Accident or Damage Reported", ...},
    "ownerHistory": {"text": "CARFAX 1-Owner", ...},
    "vehicleUseHistory": {"text": "Personal Use", ...},
    "serviceHistory": {"text": "Service History", "number": 8, ...}},
  "dealer": {"label": "Geaux Chevrolet West", "phone": "(225) 425-3830",
             "dealerAverageRating": 4.8, "dealerReviewCount": 95,
             "url": "https://www.carfax.com/Reviews-Geaux-Chevrolet-West-Plaquemine-LA_VJRQHOMPSQ", ...},
  "icrUrl": "https://www.carfax.com/vehiclehistory/ccl/...", ...}]}
```

The same store also records which backend rendered the page. `searchRequest.apiUrl` points at `helix-web.internal.us-east-1.k8s.cxd.production.aws.carfax.io/search/v2/vehicles?make=Honda&model=CR-V&zip=10001&radius=50&vehicleCondition=USED...`, an internal host the browser never calls.

Reading the object takes `json.JSONDecoder().raw_decode`, which stops at the closing brace and ignores the script that follows:

```python
m = re.search(r"window\.__MOBX_STATE__\s*=\s*", html)
mobx, _ = json.JSONDecoder().raw_decode(html[m.end():])
listings = mobx["SearchRequestStore"]["results"]["listings"]
```

### Listings from the command line

```bash
export SCRAPINGBEE_API_KEY=...
python examples/carfax_client.py listings /Used-Toyota-Camry_w630 --zip 10001 --csv camry.csv --json camry.json
```

```
  zip ignored (got 'Nationwide'), asking again
  zip ignored (got 'Nationwide'), asking again
678 listings in New York, NY 10001 +50mi, page 1 holds 24, sort BEST, 30 credits
  2024 Toyota Camry LE           $ 23,182  32,500 mi  GOOD   No Accident or Damage Reported Empire Nissan of Hillside
  2025 Toyota Camry SE           $ 30,037  30,360 mi  GREAT  No Accident or Damage Reported Motion Kia
  2023 Toyota Camry LE           $ 22,170  27,756 mi  GREAT  No Accident or Damage Reported Kia City
  2020 Toyota Camry SE           $ 21,995  31,168 mi  GREAT  No Accident or Damage Reported Sunrise Toyota
```

That run shows the most annoying behaviour we found. Carfax sometimes ignores the `zip` parameter and answers "Nationwide" with a different result set (19,521 Camrys instead of 678). It happened on 4 of 13 zip requests during testing, with identical URLs. `search()` compares `searchArea.zip` with what you asked for and repeats the request up to two times, so a bad roll costs 10 more credits.

Across those 24 New York Camrys: 18 carried a GREAT badge and 6 GOOD; 18 said "No Accident or Damage Reported", 4 "Minor Damage", one "Accident/Minor Damage" and one "Accident Reported"; 21 were one owner cars.

### Finding model URLs

The number after the underscore is what Carfax routes on. `/Used-Toyota_m31` redirected to `/Used-Scion_m31`, and the zip was lost on the way. Pull the right paths from a make page instead of guessing:

```
$ python examples/carfax_client.py models /Used-Toyota_m33 --zip 60601
     120  Used Toyota 4Runner                      https://www.carfax.com/Used-Toyota-4Runner_w628
      32  Used Toyota Avalon                       https://www.carfax.com/Used-Toyota-Avalon_w629
     447  Used Toyota Camry                        https://www.carfax.com/Used-Toyota-Camry_w630
     372  Used Toyota Corolla                      https://www.carfax.com/Used-Toyota-Corolla_w631
```

### Only page 1 is reachable

Each search URL gives you its first 24 listings and no more. `?page=2` is ignored on the server side, clicking the page 2 button in a rendered session left the page on page 1, and calling the search backend from inside the page got the DataDome 403 shown above. `?sort=PRICE_ASC` was worse: Carfax dropped the zip and fell back to its default sort. The way to cover more cars is more URLs, not more pages: other zips, other models, trims and model years (the breadcrumb on a vehicle page links paths like `/Used-2025-Ram-2500_z40121` and `/Used-2025-Ram-2500-Big-Horn_x55142`), and the price bands from `/cars-for-sale` such as `/Used-Cars-Under-10000_f6`.

The pagination block is honest about this in its own way. For Toyota around New York it reported `totalRecords: 4752` and `totalPages: 50`, which at 24 a page would only be 1,200 cars.

## Carfax reviews API: dealer ratings and review text

Every listing card carries `dealerAverageRating` and `dealerReviewCount`. The dealer page adds the split between sales and service, the star distribution, and the review text. It is a Next.js page, so the data is in `__NEXT_DATA__` as Apollo cache keys:

```json
"shop({\"compCode\":\"JVQRHOMPSQ\"})": {
  "companyName": "Geaux Chevrolet West", "dealerBadge": "TOP_RATED_LIFETIME",
  "ratingsByType": {
    "all":   {"averageRating": 4.8, "numberOfReviews": 95, "ratingPercentages": [91, 5, 0, 1, 3]},
    "sales": {"averageRating": 4.8, "numberOfReviews": 5,  "ratingPercentages": [80, 20, 0, 0, 0]},
    "shop":  {"averageRating": 4.8, "numberOfReviews": 90, "ratingPercentages": [91, 5, 0, 1, 3]}}},
"reviews({\"compCode\":\"JVQRHOMPSQ\",\"reviewParameters\":{\"paginationParameters\":{\"page\":1,\"rows\":20},...}})": {
  "paginationDetails": {"page": 1, "row": 20, "totalRecords": 95, "totalPages": 5},
  "items": [{"date": "2026-10-22T16:08:46", "rating": 5, "type": "SALES_REVIEW",
             "username": "2023 GMC CANYON AT4 Shopper", "badge": "Verified Purchase", ...}]}
```

`ratingPercentages` runs from five stars down to one. A JSON-LD block on the same page repeats the headline numbers (`"@type": "AutomotiveBusiness"`, `"ratingValue": 4.8`, `"reviewCount": 95`), which is a handy cross check.

The page holds the first 20 reviews in Carfax's "best match" order. `?page=2` and `?reviewPage=2` both came back with the same 20. For a dealer with 95 reviews you get the full star distribution and a fifth of the text.

```
$ python examples/carfax_client.py dealer https://www.carfax.com/Reviews-Geaux-Chevrolet-West-Plaquemine-LA_VJRQHOMPSQ --csv reviews.csv
{
  "code": "JVQRHOMPSQ",
  "name": "Geaux Chevrolet West",
  ...
  "badge": "TOP_RATED_LIFETIME",
  "rating_all": 4.8,
  "reviews_all": 95,
  "rating_sales": 4.8,
  "reviews_sales": 5,
  "rating_service": 4.8,
  "reviews_service": 90,
  "stars_5_to_1_pct": "91/5/0/1/3",
  ...
}
  2026-05-02 4* SHOP_REVIEW  Service is quick & people are nice, but the tech who changes oil & rot
  2026-10-22 5* SALES_REVIEW We have felt with Brad Hanks for years. Glad to see he is back in the
20 reviews read, 10 credits
```

### A dealer scorecard for one search

[`examples/carfax_dealers.py`](examples/carfax_dealers.py) chains the two: it reads a search page, groups the cars by dealer, then opens the review pages of the busiest dealers.

```
$ python examples/carfax_dealers.py /Used-Honda-CR-V_w301 --zip 10001 --dealers 3
1704 listings in New York, NY 10001 +50mi; scoring dealers from the 24 on page 1
  Lia Honda Brewster NY          4 cars  median $25,656  rating 4.6 (857)  7% one or two stars
  Bay Ridge Honda                3 cars  median $29,470  rating 4.6 (882)  6% one or two stars
  North Shore Honda              2 cars  median $26,495  rating 4.8 (1121)  4% one or two stars
  Route 7 Nissan                 1 cars  median $29,965  rating 4.7 (32)  review page not opened
  Empire Mazda of Huntington     1 cars  median $9,675  rating 4.3 (343)  review page not opened
18 dealers, 60 reviews, 40 credits
```

### Node.js

[`examples/carfax_listings.js`](examples/carfax_listings.js) prints one JSON line per listing. It cuts the state object out by counting braces, because the script tag continues after it:

```
$ node examples/carfax_listings.js /Used-Tesla-Model-3_w9421 94103 > tesla.jsonl
10 credits, https://www.carfax.com/Used-Tesla-Model-3_w9421?zip=94103&radius=50
189 listings, area: San Francisco 94103
{"vin":"5YJ3E1EA0PF612071","title":"2023 Tesla Model 3 Standard Range","price":22084,"mileage":111997,"badge":"GREAT","accidents":"Accident/Minor Damage","owners":"CARFAX 1-Owner","service_records":3,"dealer":"One Toyota of Oakland","dealer_rating":4.4}
```

## Budget

| Job | Credits |
|---|---|
| 24 listings for one model and zip | 10, or 20 to 30 when the zip is ignored |
| Model list for a make | 10 |
| One dealer: rating split and 20 reviews | 10 |
| Scorecard: one search and three dealers | 40 |

New accounts get 1,000 free credits, so about 100 Carfax pages. Plans are on the [pricing page](https://www.scrapingbee.com/pricing/). Parameter reference: [HTML API documentation](https://www.scrapingbee.com/documentation/), [JavaScript scenarios](https://www.scrapingbee.com/documentation/js-scenario/), [data extraction](https://www.scrapingbee.com/documentation/data-extraction/).

## Scope and data handling

Logged out pages only. Saved cars, followed listings, price alerts and the garage need a Carfax account and are not touched; ScrapingBee's terms of service do not allow scraping under login credentials, and this repo does not try. Review authors appear as a vehicle ("2018 HONDA PILOT EXL Owner") or a first name and initial, which is still personal data in some jurisdictions, so store it only if you need it. Carfax's own terms of use apply to the site.

Related endpoints: [Copart API](https://www.scrapingbee.com/scrapers/copart-api/), [CarGurus API](https://www.scrapingbee.com/scrapers/cargurus-api/), [Cars.com API](https://www.scrapingbee.com/scrapers/cars.com-api/), [Autotrader API](https://www.scrapingbee.com/scrapers/autotrader-api/), [vehicle data API](https://www.scrapingbee.com/scrapers/vehicle-data-api/), [Google reviews API](https://www.scrapingbee.com/scrapers/google-reviews-results-api/).

## FAQ

**Can this pull a Carfax report by VIN?**
No. The report is a paid product and stays out of scope. For a VIN that is currently listed for sale you get the public listing at `/vehicle/<VIN>`, with the history snapshot the dealer shows: accident line, owner count, service record count, last reported odometer.

**Is there an official Carfax API for developers?**
Carfax sells data services to businesses under contract. This repo does not use them and makes no claims about them; ask Carfax if your use needs licensed history data.

**How do I scrape Carfax reviews for a dealer?**
Take the `dealer.url` from any listing, or the `/Reviews-..._<code>` URL from the dealer directory, and run `carfax_client.py dealer <url>`. You get averages for sales and service, the star percentages and the first 20 reviews.

**Why did my Carfax scraper return a German page?**
Your request left from outside the US. Carfax redirects those to carfax.eu. Use `premium_proxy=true` with `country_code=us` and check `spb-resolved-url`.

**How many listings can I collect per search?**
24 per URL. Spread the job across zips, models, years and price bands; each URL costs 10 credits.

## License

MIT. See [LICENSE](LICENSE).
