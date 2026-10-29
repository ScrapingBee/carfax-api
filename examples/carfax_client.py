"""Public Carfax pages (used car listings, dealer reviews) through ScrapingBee.

Measured on 2026-10-29 against www.carfax.com, logged out:

* Search result pages (/Used-Toyota-Camry_w630) are server rendered. The
  full result set for the first page sits in window.__MOBX_STATE__, 24
  listings, no JavaScript needed.
* Dealer review pages (/Reviews-<name>_<code>) are Next.js. The dealer and
  the first 20 reviews sit in __NEXT_DATA__ as Apollo cache entries.
* Carfax redirects non US visitors to carfax.eu. Plain requests and
  mode=auto landed on the German carfax.eu homepage (and auto mode stopped
  there at 1 credit, because the redirect target answered 200). The US site
  needs premium_proxy + country_code=us: 10 credits a page, no JS.

Paid Vehicle History Reports are out of scope. This module never requests
/vehiclehistory/ URLs or anything behind a Carfax account.

Set SCRAPINGBEE_API_KEY in your environment before running.
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, fields
from urllib.parse import urlencode, urlparse

import requests

API = "https://app.scrapingbee.com/api/v1/"
CARFAX = "https://www.carfax.com"

MOBX = re.compile(r"window\.__MOBX_STATE__\s*=\s*")
NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
TAGS = re.compile(r"<[^>]+>")


class NotCarfaxUS(Exception):
    """The response was a redirect target or a bot check, not carfax.com."""


def _clean(text):
    return re.sub(r"\s+", " ", html.unescape(TAGS.sub(" ", text or ""))).strip()


@dataclass
class Listing:
    """One used car listing card. Keys from SearchRequestStore.results.listings."""

    listing_id: str
    vin: str
    title: str
    year: int
    make: str
    model: str
    trim: str
    price: int               # currentPrice
    list_price: int          # listPrice
    mileage: int             # mileage.value
    deal_badge: str          # badge: GREAT, GOOD, FAIR ...
    body_type: str
    drivetrain: str          # drivetype
    engine: str
    transmission: str
    fuel: str
    exterior_color: str
    interior_color: str
    accident_summary: str    # pillars.accidentHistory.text
    owner_summary: str       # pillars.ownerHistory.text
    use_summary: str         # pillars.vehicleUseHistory.text
    service_records: int     # pillars.serviceHistory.number
    first_seen: str
    dealer_name: str
    dealer_city: str
    dealer_state: str
    dealer_phone: str
    dealer_rating: float     # dealer.dealerAverageRating
    dealer_reviews: int      # dealer.dealerReviewCount
    dealer_reviews_url: str
    distance_miles: int
    url: str

    @classmethod
    def from_json(cls, d):
        p = d.get("pillars") or {}
        dealer = d.get("dealer") or {}
        loc = dealer.get("location") or {}
        return cls(
            listing_id=d.get("id", ""),
            vin=d.get("vin", ""),
            title=d.get("title", ""),
            year=d.get("year"),
            make=d.get("make", ""),
            model=d.get("model", ""),
            trim=d.get("trim", ""),
            price=d.get("currentPrice"),
            list_price=d.get("listPrice"),
            mileage=(d.get("mileage") or {}).get("value"),
            deal_badge=d.get("badge", ""),
            body_type=d.get("bodytype", ""),
            drivetrain=d.get("drivetype", ""),
            engine=d.get("engine", ""),
            transmission=d.get("transmission", ""),
            fuel=d.get("fuel", ""),
            exterior_color=d.get("exteriorColor", ""),
            interior_color=d.get("interiorColor", ""),
            accident_summary=(p.get("accidentHistory") or {}).get("text", ""),
            owner_summary=(p.get("ownerHistory") or {}).get("text", ""),
            use_summary=(p.get("vehicleUseHistory") or {}).get("text", ""),
            service_records=(p.get("serviceHistory") or {}).get("number") or 0,
            first_seen=d.get("firstSeen", ""),
            dealer_name=dealer.get("label", ""),
            dealer_city=loc.get("city", ""),
            dealer_state=loc.get("state", ""),
            dealer_phone=dealer.get("phone", ""),
            dealer_rating=dealer.get("dealerAverageRating"),
            dealer_reviews=dealer.get("dealerReviewCount"),
            dealer_reviews_url=dealer.get("url", ""),
            distance_miles=d.get("distanceToDealer"),
            url=f"{CARFAX}/vehicle/{d.get('vin', '')}",
        )


@dataclass
class Review:
    dealer_code: str
    date: str
    rating: int
    kind: str                # SALES_REVIEW or SHOP_REVIEW
    badge: str               # e.g. "Verified Purchase", "Verified Service"
    author: str              # Carfax shows the vehicle, not a name
    text: str
    dealer_reply: str


@dataclass
class Dealer:
    code: str
    name: str
    city: str
    state: str
    phone: str
    website: str
    badge: str               # dealerBadge, e.g. TOP_RATED_LIFETIME
    rating_all: float
    reviews_all: int
    rating_sales: float
    reviews_sales: int
    rating_service: float
    reviews_service: int
    stars_5_to_1_pct: str    # ratingPercentages, five star first
    makes: str
    reviews_on_page: int
    reviews_total_pages: int


class CarfaxClient:
    def __init__(self, api_key=None, retries=3, zip_retries=2, log=print):
        key = api_key or os.environ.get("SCRAPINGBEE_API_KEY")
        if not key:
            sys.exit("Set SCRAPINGBEE_API_KEY first.")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {key}"
        self.retries = retries
        self.zip_retries = zip_retries
        self.log = log
        self.credits = 0

    def page(self, url):
        """10 credits. Retries ScrapingBee 500s, which are not billed."""
        params = {
            "url": url,
            "premium_proxy": "true",
            "country_code": "us",
            "render_js": "false",
        }
        for attempt in range(1, self.retries + 1):
            r = self.session.get(API, params=params, timeout=120)
            self.credits += int(r.headers.get("spb-cost") or 0)
            if r.status_code == 500:
                self.log(f"  attempt {attempt}: 500 (not billed), retrying")
                time.sleep(3 * attempt)
                continue
            r.raise_for_status()
            final = r.headers.get("spb-resolved-url") or url
            if urlparse(final).netloc != "www.carfax.com":
                raise NotCarfaxUS(f"redirected to {final}")
            if "captcha-delivery.com" in r.text[:3000]:
                raise NotCarfaxUS("DataDome challenge page")
            return r.text, final
        raise NotCarfaxUS("gave up after retries")

    # -- search pages --------------------------------------------------
    @staticmethod
    def _mobx(text):
        m = MOBX.search(text)
        if not m:
            raise NotCarfaxUS("no __MOBX_STATE__ on page")
        state, _ = json.JSONDecoder().raw_decode(text[m.end():])
        return state

    def search(self, path, zip_code=None, radius=50):
        """First results page of a Carfax search URL path.

        path is what you see on carfax.com, e.g. /Used-Toyota-Camry_w630 or
        /Used-Cars-Under-10000_f6. The number after the underscore is what
        Carfax routes on: a wrong number with a right name 301s to another
        make. Only page 1 is server rendered; ?page=2 is ignored, and adding
        ?sort=PRICE_ASC made Carfax drop the zip and answer nationwide with
        its default sort, so this method sends zip and radius only.
        """
        query = {}
        if zip_code:
            query.update(zip=zip_code, radius=radius)
        url = CARFAX + "/" + path.lstrip("/") + ("?" + urlencode(query) if query else "")
        # Carfax sometimes ignores the zip and answers "Nationwide" (1 of 3
        # identical requests on 2026-10-29). Check and ask again; the extra
        # request is billed, so stop after zip_retries.
        for attempt in range(1 + (self.zip_retries if zip_code else 0)):
            text, final = self.page(url)
            store = self._mobx(text)["SearchRequestStore"]
            area = store.get("searchArea")
            if not zip_code or (isinstance(area, dict) and area.get("zip") == str(zip_code)):
                break
            self.log(f"  zip ignored (got {area!r}), asking again")
        meta = {
            "url": final,
            "total_records": store["pagination"]["totalRecords"],
            "total_pages_shown": store["pagination"]["totalPages"],
            "page_size": store["pagination"]["pageSize"],
            "area": area if isinstance(area, str) else
            f"{area.get('city')}, {area.get('state')} {area.get('zip')} +{area.get('radius')}mi",
            "sort": (store.get("params") or {}).get("sort"),
        }
        rows = [Listing.from_json(d) for d in store["results"]["listings"]]
        return meta, rows

    def models(self, make_path, zip_code=None):
        """Model URLs and local counts from a make page such as /Used-Toyota_m33."""
        query = f"?zip={zip_code}&radius=50" if zip_code else ""
        text, _ = self.page(CARFAX + "/" + make_path.lstrip("/") + query)
        links = self._mobx(text)["SearchRequestStore"]["searchInfo"]["relatedLinks"]
        out = []
        for group, items in links.items():
            if group.endswith("Models"):
                out += [(i["text"], i.get("count"), i["url"]) for i in items]
        return out

    # -- dealer review pages -------------------------------------------
    def dealer(self, reviews_url):
        """Dealer profile, rating split and the first 20 reviews (10 credits)."""
        text, _ = self.page(reviews_url)
        m = NEXT.search(text)
        if not m:
            raise NotCarfaxUS("no __NEXT_DATA__ on dealer page")
        props = json.loads(m.group(1))["props"]["pageProps"]
        root = props["initialApolloState"]["ROOT_QUERY"]
        shop = next(v for k, v in root.items() if k.startswith("shop("))
        rev = next((v for k, v in root.items() if k.startswith("reviews(")), {})
        rt = shop.get("ratingsByType") or {}
        contact = shop.get("salesContactInfo") or {}

        def r(kind):
            block = rt.get(kind) or {}
            return block.get("averageRating"), block.get("numberOfReviews")

        dealer = Dealer(
            code=shop.get("compCode", ""),
            name=shop.get("companyName", ""),
            city=contact.get("city", ""),
            state=contact.get("state", ""),
            phone=contact.get("phoneNumber", ""),
            website=contact.get("website", ""),
            badge=shop.get("dealerBadge", ""),
            rating_all=r("all")[0], reviews_all=r("all")[1],
            rating_sales=r("sales")[0], reviews_sales=r("sales")[1],
            rating_service=r("shop")[0], reviews_service=r("shop")[1],
            stars_5_to_1_pct="/".join(str(x) for x in (rt.get("all") or {}).get("ratingPercentages", [])),
            makes=", ".join(shop.get("makes") or []),
            reviews_on_page=len(rev.get("items") or []),
            reviews_total_pages=(rev.get("paginationDetails") or {}).get("totalPages", 0),
        )
        reviews = [
            Review(
                dealer_code=dealer.code,
                date=(i.get("date") or "")[:10],
                rating=i.get("rating"),
                kind=i.get("type", ""),
                badge=i.get("badge", ""),
                author=i.get("username", ""),
                text=_clean(i.get("message")),
                dealer_reply=_clean((i.get("response") or {}).get("message")),
            )
            for i in rev.get("items") or []
        ]
        return dealer, reviews


def write_csv(rows, path):
    if not rows:
        return
    names = [f.name for f in fields(rows[0])]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=names)
        w.writeheader()
        for row in rows:
            w.writerow(asdict(row))


def write_json(rows, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([asdict(r) for r in rows], fh, indent=2)


def main():
    ap = argparse.ArgumentParser(description="Carfax public pages via ScrapingBee")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("listings", help="first page of a search URL path")
    s.add_argument("path", help="e.g. /Used-Toyota-Camry_w630")
    s.add_argument("--zip")
    s.add_argument("--radius", type=int, default=50)
    s.add_argument("--csv")
    s.add_argument("--json")

    m = sub.add_parser("models", help="model URLs for a make page")
    m.add_argument("make_path", help="e.g. /Used-Toyota_m33")
    m.add_argument("--zip")

    d = sub.add_parser("dealer", help="dealer rating and first 20 reviews")
    d.add_argument("url", help="a carfax.com/Reviews-..._<code> URL")
    d.add_argument("--csv", help="write the reviews here")

    a = ap.parse_args()
    c = CarfaxClient()

    if a.cmd == "listings":
        meta, rows = c.search(a.path, a.zip, a.radius)
        print(f"{meta['total_records']} listings in {meta['area']}, "
              f"page 1 holds {len(rows)}, sort {meta['sort']}, {c.credits} credits")
        for r in rows[:8]:
            print(f"  {r.title[:30]:30} ${r.price:>7,} {r.mileage or 0:>7,} mi  "
                  f"{r.deal_badge:6} {r.accident_summary[:30]:30} {r.dealer_name[:26]}")
        if a.csv:
            write_csv(rows, a.csv)
        if a.json:
            write_json(rows, a.json)
    elif a.cmd == "models":
        for name, count, url in c.models(a.make_path, a.zip):
            print(f"  {count or '':>6}  {name:40} {url}")
        print(f"{c.credits} credits")
    else:
        dealer, reviews = c.dealer(a.url)
        print(json.dumps(asdict(dealer), indent=2))
        for rv in reviews[:5]:
            print(f"  {rv.date} {rv.rating}* {rv.kind:12} {rv.text[:70]}")
        print(f"{len(reviews)} reviews read, {c.credits} credits")
        if a.csv:
            write_csv(reviews, a.csv)


if __name__ == "__main__":
    main()
