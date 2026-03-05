# Wayback Machine Downloader

Download an entire website from the Internet Archive Wayback Machine.

## Installation

You need Python >= 3.11 installed on your system.

### From PyPI

    pip install wayback-dl

### From source (global CLI)

To install from a local clone so `wayback-dl` is available system-wide:

    uv tool install /path/to/wayback-dl

Or with pip:

    pip install /path/to/wayback-dl

### For development

    uv venv && source .venv/bin/activate
    uv pip install -e ".[dev]"

## Basic Usage

Run wayback-dl with the base url of the website you want to retrieve as a parameter (e.g., http://example.com):

    wayback-dl http://example.com

## How it works

It will download the last version of every file present on Wayback Machine to `./websites/example.com/`. It will also re-create a directory structure and auto-create `index.html` pages to work seamlessly with Apache and Nginx. All files downloaded are the original ones and not Wayback Machine rewritten versions. This way, URLs and links structure are the same as before.

The tool does **not** parse HTML or crawl pages. Instead, it queries the [Wayback Machine CDX API](https://archive.org/developers/wayback-cdx-server.html) to get a complete index of all archived files (HTML, JS, CSS, images, fonts, etc.) under a domain.

## Advanced Usage

	Usage: wayback-dl [OPTIONS] [URL]

	Download an entire website from the Wayback Machine.

	Options:
	    -d, --directory PATH             Directory to save the downloaded files into
					     Default is ./websites/ plus the domain name
	    -A, --all-timestamps             Download all snapshots/timestamps for a given website
	    -f, --from DATE                  Only files on or after date (e.g., 2006-07-16 or 20060716231334)
	    -t, --to DATE                    Only files on or before date (e.g., 2010-09-16 or 20100916231334)
	    -e, --exact-url                  Download only the url provided and not the full site
	    -o, --only ONLY_FILTER           Restrict downloading to urls that match this filter
					     (use // notation for the filter to be treated as a regex)
	    -x, --exclude EXCLUDE_FILTER     Skip downloading of urls that match this filter
					     (use // notation for the filter to be treated as a regex)
	    -a, --all                        Expand downloading to error files (40x and 50x) and redirections (30x)
	    -c, --concurrency NUMBER         Number of concurrent downloads (default: 1)
	    -p, --max-pages NUMBER           Maximum snapshot pages to consider (default: 100)
	    -l, --list                       Only list file urls as JSON, don't download
	    -s, --session ID                 Resume a previous download session by ID
	    -S, --list-sessions              List all active/interrupted download sessions
	    -r, --redo                       Force re-download all files, ignoring previous progress
	    -v, --verbose                    Enable verbose/debug logging
	    -V, --version                    Show version and exit
	    -h, --help                       Show help and exit

## Specify directory to save files to

    -d, --directory PATH

Optional. By default, wayback-dl will download files to `./websites/` followed by the domain name of the website. You may want to save files in a specific directory using this option.

Example:

    wayback-dl http://example.com --directory downloaded-backup/

## All Timestamps

    -A, --all-timestamps

Optional. This option will download all timestamps/snapshots for a given website. It will use the timestamp of each snapshot as directory.

Example:

    wayback-dl http://example.com --all-timestamps

    Will download:
    	websites/example.com/20060715085250/index.html
    	websites/example.com/20051120005053/index.html
    	websites/example.com/20060111095815/img/logo.png
    	...

## From Date

    -f, --from DATE

Optional. Only download files archived on or after the specified date. Accepts ISO 8601 format (`2006-07-16`, `2006-07-16T23:13:34`) or raw Wayback Machine timestamps (`20060716231334`, `2006`, `200607`). Can be combined with `--to`.

Examples:

    wayback-dl http://example.com --from 2006-07-16
    wayback-dl http://example.com --from 2006-07-16T23:13:34
    wayback-dl http://example.com --from 20060716231334

## To Date

    -t, --to DATE

Optional. Only download files archived on or before the specified date. Same format as `--from`. Can be combined with `--from`.

Examples:

    wayback-dl http://example.com --to 2010-09-16
    wayback-dl http://example.com --from 2006-01-01 --to 2010-12-31

## Exact Url

	-e, --exact-url

Optional. If you want to retrieve only the file matching exactly the url provided, you can use this flag. It will avoid downloading anything else.

For example, if you only want to download only the html homepage file of example.com:

    wayback-dl http://example.com --exact-url

## Only URL Filter

     -o, --only ONLY_FILTER

Optional. You may want to retrieve files which are of a certain type (e.g., .pdf, .jpg, .wrd...) or are in a specific directory. To do so, you can supply the `--only` flag with a string or a regex (using the '/regex/' notation) to limit which files wayback-dl will download.

For example, if you only want to download files inside a specific `my_directory`:

    wayback-dl http://example.com --only my_directory

Or if you want to download every images without anything else:

    wayback-dl http://example.com --only "/\.(gif|jpg|jpeg)$/i"

## Exclude URL Filter

     -x, --exclude EXCLUDE_FILTER

Optional. You may want to retrieve files which aren't of a certain type (e.g., .pdf, .jpg, .wrd...) or aren't in a specific directory. To do so, you can supply the `--exclude` flag with a string or a regex (using the '/regex/' notation) to limit which files wayback-dl will download.

For example, if you want to avoid downloading files inside `my_directory`:

    wayback-dl http://example.com --exclude my_directory

Or if you want to download everything except images:

    wayback-dl http://example.com --exclude "/\.(gif|jpg|jpeg)$/i"

## Expand downloading to all file types

     -a, --all

Optional. By default, wayback-dl limits itself to files that responded with 200 OK code. If you also need errors files (40x and 50x codes) or redirections files (30x codes), you can use the `--all` or `-a` flag and wayback-dl will download them in addition of the 200 OK files. It will also keep empty files that are removed by default.

Example:

    wayback-dl http://example.com --all

## Only list files without downloading

     -l, --list

It will just display the files to be downloaded with their snapshot timestamps, urls, mimetypes, and sizes. The output format is JSON. It won't download anything. It's useful for debugging or to connect to another application.

Example:

    wayback-dl http://example.com --list

## Maximum number of snapshot pages to consider

    -p, --max-pages NUMBER

Optional. Specify the maximum number of snapshot pages to consider. Count an average of 150,000 snapshots per page. 100 is the default maximum number of snapshot pages and should be sufficient for most websites. Use a bigger number if you want to download a very large website.

Example:

    wayback-dl http://example.com --max-pages 300

## Download multiple files at a time

    -c, --concurrency NUMBER

Optional. Specify the number of multiple files you want to download at the same time. Allows one to speed up the download of a website significantly. Default is to download one file at a time. Uses async I/O for efficient concurrent downloads. The progress display shows all active downloads.

Example:

    wayback-dl http://example.com --concurrency 20

## Resume interrupted downloads

Downloads are automatically resumable. Each download creates a session that tracks per-file completion status. If interrupted (Ctrl+C or crash), the tool prints a resume command:

    Aborted. 45/168 files downloaded.

    To resume:
      wayback-dl -s 1709571234

To list all active/interrupted sessions:

    wayback-dl --list-sessions

Sessions are stored in `~/.wayback_dl/sessions/` and are automatically cleaned up after successful completion. Multiple downloads of different domains can run in parallel without conflicts.

## Force re-download

    -r, --redo

Force re-download all files, ignoring any previous session progress. Useful when you want a fresh copy.

    wayback-dl http://example.com --redo

## Verbose output

    -v, --verbose

Show detailed output including CDX API requests, response timing, per-file download status, and file type breakdown.

    wayback-dl http://example.com --verbose

## Using the Docker image

As an alternative installation way, build the Docker image:

    docker build -t wayback-dl .

Then, you should be able to use the Docker image to download websites. For example:

    docker run --rm -it -v $PWD/websites:/websites wayback-dl http://example.com

## Contributing

Contributions are welcome! Just submit a pull request via GitHub.

To run the tests:

    uv venv && source .venv/bin/activate
    uv pip install -e ".[dev]"
    pytest tests/ -v
