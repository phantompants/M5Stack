# Firmware maintenance

- For each changed firmware revision, increment `version::kNumber` in
  `shared/Version.h` before building or flashing, and update the current-version
  reference in `README.md`. This is the version displayed on FOSSIL RECORD.
  Rebuilds and upload retries of the same revision retain that number.
- The original Cardputer v1 profile excludes black-box backup and external
  temperature probes, including their pages. Keep vehicle temperatures read
  over OBD available.
