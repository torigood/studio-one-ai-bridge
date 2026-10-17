#!/bin/sh
# Build vst3dump. Needs the MIT-licensed VST3 interface headers:
#   git clone --depth 1 https://github.com/steinbergmedia/vst3_pluginterfaces.git third_party/pluginterfaces
set -e
cd "$(dirname "$0")"
P=../../third_party/pluginterfaces
mkdir -p build
clang++ -std=c++17 -O1 -I../../third_party -x objective-c++ vst3dump.mm -x c++ "$P/base/funknown.cpp" \
	-framework CoreFoundation -o build/vst3dump
echo "built $(pwd)/build/vst3dump"
