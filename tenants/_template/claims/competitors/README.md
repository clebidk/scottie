# claims/competitors/ — sourced comparison subjects

One JSON file per comparison subject the comparison cartridge may put in its
spec table. A file loads only once `"approved_by"` is set — pending files are
skipped, so no run can name a competitor before the operator approves the
claims. See the comparison cartridge's cartridge.md for the full rules, and
keep every row sourced (each row carries its own public `source` URL).
