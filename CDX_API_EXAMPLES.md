# CDX API Examples

Working examples using `www.touchcommerce.com` and the Wayback Machine CDX API.

## List all archived URLs for a site (2017)

```bash
curl "https://web.archive.org/cdx/search/cdx?url=www.touchcommerce.com/*&output=json&fl=timestamp,original&from=20170101&to=20171231&collapse=digest&filter=statuscode:200"
```

Response (JSON array of `[timestamp, original_url]` pairs):

```json
[
  ["timestamp", "original"],
  ["20170105063926", "http://www.touchcommerce.com/"],
  ["20170112063917", "http://www.touchcommerce.com/"],
  ["20170127085539", "http://www.touchcommerce.com:80/bundles/common?v=spkBxJOHhBb..."],
  ["20170603011900", "http://www.touchcommerce.com/Content/css/modules/flexslider.css"],
  ...
]
```

The first row is always the header — the tool strips it after parsing (see `cdx_api.py`).

## Include MIME type to see file types

```bash
curl "https://web.archive.org/cdx/search/cdx?url=www.touchcommerce.com/*&output=text&fl=timestamp,original,mimetype&from=20170101&to=20171231&collapse=digest&filter=statuscode:200"
```

Response (space-separated text):

```
20170105063926 http://www.touchcommerce.com/ text/html
20170127085539 http://www.touchcommerce.com:80/bundles/common?v=... text/javascript
20170127085545 http://www.touchcommerce.com:80/bundles/jquery?v=... text/javascript
20170603011900 http://www.touchcommerce.com/Content/css/modules/flexslider.css text/css
20170603012144 http://www.touchcommerce.com/Content/fonts/gothambold.eot application/vnd.ms-fontobject
20170603012124 http://www.touchcommerce.com/Content/fonts/gothambold.svg image/svg+xml
20170603012134 http://www.touchcommerce.com/Content/fonts/gothambold.woff application/x-font-woff
```

Notice the API returns **all asset types** — HTML, JS, CSS, fonts, images — without any HTML parsing.

## Count total snapshots

```bash
curl -s "https://web.archive.org/cdx/search/cdx?url=www.touchcommerce.com/*&output=text&from=20170101&to=20171231&collapse=digest&filter=statuscode:200" | wc -l
```

Result: **720 unique files** for touchcommerce.com in 2017.

## Get only non-HTML assets (JS, CSS, fonts, images)

Use text output and filter with grep:

```bash
curl -s "https://web.archive.org/cdx/search/cdx?url=www.touchcommerce.com/*&output=text&fl=timestamp,original,mimetype&from=20170101&to=20171231&collapse=digest&filter=statuscode:200" | grep -v "text/html"
```

## Limit results

```bash
curl "https://web.archive.org/cdx/search/cdx?url=www.touchcommerce.com/*&output=json&fl=timestamp,original&from=20170101&to=20171231&collapse=digest&filter=statuscode:200&limit=10"
```

## Get exact URL only (no wildcard)

```bash
curl "https://web.archive.org/cdx/search/cdx?url=www.touchcommerce.com&output=json&fl=timestamp,original&from=20170101&to=20171231"
```

This returns only snapshots of the root page, not subpages or assets.

## Download an original file

Once you have a timestamp and URL from the CDX API, download the original file:

```bash
# Download the homepage as archived on 2017-01-05
curl -o index.html "https://web.archive.org/web/20170105063926id_/http://www.touchcommerce.com/"

# Download a CSS file as archived on 2017-06-03
curl -o flexslider.css "https://web.archive.org/web/20170603011900id_/http://www.touchcommerce.com/Content/css/modules/flexslider.css"
```

The `id_` between the timestamp and the URL is what makes the Wayback Machine return the **original unmodified file** (no toolbar injection, no URL rewriting).

Without `id_`:
```
https://web.archive.org/web/20170105063926/http://www.touchcommerce.com/
  → Returns modified HTML with Wayback Machine toolbar and rewritten URLs
```

With `id_`:
```
https://web.archive.org/web/20170105063926id_/http://www.touchcommerce.com/
  → Returns the original file exactly as it was archived
```

## Include error pages and redirects

By default, `filter=statuscode:200` limits results to successful responses. Remove that filter to include 30x redirects and 40x/50x errors:

```bash
curl "https://web.archive.org/cdx/search/cdx?url=www.touchcommerce.com/*&output=json&fl=timestamp,original,statuscode&from=20170101&to=20171231&collapse=digest"
```
