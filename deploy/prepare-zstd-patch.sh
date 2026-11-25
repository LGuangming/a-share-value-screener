#!/bin/sh
# Assemble a complete signed-repository Debian package in Distroless format.
set -eu
version='1.5.7+dfsg-1+deb13u1'
export DEBIAN_FRONTEND=noninteractive
apt-get -o APT::Update::Error-Mode=any update
mkdir -p /tmp/zstd-download /patch/var/lib/dpkg/status.d
cd /tmp/zstd-download
apt-get download "libzstd1:amd64=$version"
set -- ./*.deb
test "$#" = 1
deb="$1"
test "$(dpkg-deb -f "$deb" Package)" = libzstd1
test "$(dpkg-deb -f "$deb" Version)" = "$version"
test "$(dpkg-deb -f "$deb" Architecture)" = amd64
# APT validates repository signatures and the package hash. Preserve original
# control and md5sums as specified by Distroless PACKAGE_METADATA.md.
sha256sum "$deb"
dpkg-deb -x "$deb" /patch
dpkg-deb -e "$deb" /tmp/zstd-control
dpkg-deb -f "$deb" > /patch/var/lib/dpkg/status.d/libzstd1
cp /tmp/zstd-control/md5sums /patch/var/lib/dpkg/status.d/libzstd1.md5sums
(cd /patch && md5sum -c var/lib/dpkg/status.d/libzstd1.md5sums)
echo "PASS: signed repository libzstd1 $version package payload and metadata"
