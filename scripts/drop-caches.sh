#!/bin/sh
# Drop page/dentries/inodes cache. Must be run as root.
sync
echo 3 > /proc/sys/vm/drop_caches
