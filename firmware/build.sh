#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
idf_image='espressif/idf@sha256:dfa2d076c796769c07c155eba6c672b9f395aec943b2ba3701b73379b5f9e884'
# ESP-IDF 5.5.1. Build only: the container gets no USB device or privileged mode.
docker run --rm -v "$project_dir:/project" -w /project \
  -e IDF_TARGET=esp32s3 -e "BUILD_UID=$(id -u)" -e "BUILD_GID=$(id -g)" \
  -e "SHARK_IDF_IMAGE=$idf_image" "$idf_image" bash -c '
    trap '\''chown -R "$BUILD_UID:$BUILD_GID" build managed_components sdkconfig dependencies.lock 2>/dev/null || true'\'' EXIT
    set -e
    idf.py build
    python3 collect_licenses.py --idf-root "$IDF_PATH" --idf-image "$SHARK_IDF_IMAGE"
  '
mkdir -p "$project_dir/build/release/bootloader" "$project_dir/build/release/partition_table" \
  "$project_dir/build/release/docs" "$project_dir/build/release/firmware/main"
cp "$project_dir/build/bootloader/bootloader.bin" "$project_dir/build/release/bootloader/"
cp "$project_dir/build/partition_table/partition-table.bin" "$project_dir/build/release/partition_table/"
cp "$project_dir/build/shark_display.bin" "$project_dir/build/flash_args" \
  "$project_dir/build/flasher_args.json" "$project_dir/build/release/"
cp "$project_dir/../LICENSE" "$project_dir/../NOTICE" "$project_dir/build/release/"
cp "$project_dir/../docs/dependencies.md" "$project_dir/build/release/docs/"
cp "$project_dir/dependencies.lock" "$project_dir/build.sh" "$project_dir/build/release/firmware/"
cp "$project_dir/main/idf_component.yml" "$project_dir/build/release/firmware/main/"
cd "$project_dir/build/release"
find . -type f ! -path ./SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
