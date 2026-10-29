"""Dealer scorecard: who is selling a model near you, and what their reviews say.

    python carfax_dealers.py /Used-Honda-CR-V_w301 --zip 10001 --dealers 5 \
        --out-dealers dealers.csv --out-reviews reviews.csv

Step 1 reads the first results page for the model (10 credits, 24 cars).
Step 2 groups the cars by dealer and opens each dealer's public review page
(10 credits each, up to --dealers of them, busiest dealers first).
Step 3 writes one row per dealer and one row per review.

Every listing card already carries the dealer's average rating and review
count, so step 2 is only needed for the star split, the sales versus
service breakdown and the review text.

Needs carfax_client.py in the same folder and SCRAPINGBEE_API_KEY set.
"""

import argparse
import csv
from collections import defaultdict
from statistics import median

from carfax_client import CarfaxClient, NotCarfaxUS, write_csv


def main():
    ap = argparse.ArgumentParser(description="Carfax dealer scorecard for one search")
    ap.add_argument("path", help="search path, e.g. /Used-Honda-CR-V_w301")
    ap.add_argument("--zip", required=True)
    ap.add_argument("--radius", type=int, default=50)
    ap.add_argument("--dealers", type=int, default=5, help="dealer pages to open")
    ap.add_argument("--out-dealers", default="carfax_dealers.csv")
    ap.add_argument("--out-reviews", default="carfax_reviews.csv")
    a = ap.parse_args()

    client = CarfaxClient()
    meta, cars = client.search(a.path, a.zip, a.radius)
    print(f"{meta['total_records']} listings in {meta['area']}; "
          f"scoring dealers from the {len(cars)} on page 1")

    by_dealer = defaultdict(list)
    for car in cars:
        if car.dealer_reviews_url:
            by_dealer[car.dealer_reviews_url].append(car)

    ranked = sorted(by_dealer.items(), key=lambda kv: -len(kv[1]))
    rows, all_reviews = [], []
    for i, (url, dealer_cars) in enumerate(ranked):
        prices = [c.price for c in dealer_cars if c.price]
        row = {
            "dealer": dealer_cars[0].dealer_name,
            "city": dealer_cars[0].dealer_city,
            "state": dealer_cars[0].dealer_state,
            "cars_on_page": len(dealer_cars),
            "median_price": int(median(prices)) if prices else "",
            "great_deals": sum(c.deal_badge == "GREAT" for c in dealer_cars),
            "card_rating": dealer_cars[0].dealer_rating,
            "card_reviews": dealer_cars[0].dealer_reviews,
            "badge": "", "sales_rating": "", "service_rating": "",
            "stars_5_to_1_pct": "", "low_star_share_pct": "",
            "reviews_url": url,
        }
        if i < a.dealers:
            try:
                dealer, reviews = client.dealer(url)
            except NotCarfaxUS as exc:
                print(f"  skipped {row['dealer']}: {exc}")
            else:
                pct = [int(x) for x in dealer.stars_5_to_1_pct.split("/") if x]
                row.update(
                    badge=dealer.badge,
                    sales_rating=dealer.rating_sales,
                    service_rating=dealer.rating_service,
                    stars_5_to_1_pct=dealer.stars_5_to_1_pct,
                    # share of one and two star reviews across all reviews
                    low_star_share_pct=sum(pct[3:]) if len(pct) == 5 else "",
                )
                all_reviews += reviews
        rows.append(row)

    with open(a.out_dealers, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    write_csv(all_reviews, a.out_reviews)

    for r in rows[: max(a.dealers, 5)]:
        low = r["low_star_share_pct"]
        low = f"{low}% one or two stars" if low != "" else "review page not opened"
        print(f"  {r['dealer'][:30]:30} {r['cars_on_page']} cars  median ${r['median_price']:,}  "
              f"rating {r['card_rating']} ({r['card_reviews']})  {low}")
    print(f"{len(rows)} dealers, {len(all_reviews)} reviews, {client.credits} credits")
    print(f"wrote {a.out_dealers} and {a.out_reviews}")


if __name__ == "__main__":
    main()
