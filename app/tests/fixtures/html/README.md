# Optional HTML snapshots

The historical parser tests use ten pages listed in `urls.json`. Those saved
pages are not distributed here. They contain third-party material and personal
information that this project's MIT license does not cover.

The synthetic fixture, network and search tests run without these files.
`test_html_fixtures.py` explicitly skips its 11 cases unless all ten snapshots
are present. To compare with your own locally obtained copies, save gzip HTML
as `<name>.html.gz` beside this file. The filenames come from `urls.json`.
Current pages may differ from the historical assertions. Do not commit the
snapshots or change assertions just to accept a changed website.
