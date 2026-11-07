"""Check SDR10 policy, the pinned SDP serializer, and coded-depth diagnostics."""
from pathlib import Path
import argparse
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--native-probe", action="store_true", help="also encode/decode real local HEVC frames using VideoToolbox")
args = parser.parse_args()
connection = (root / "Limelight/Stream/Connection.m").read_text()
renderer = (root / "Limelight/Stream/VideoDecoderRenderer.m").read_text()
sdp = (root / "moonlight-common/moonlight-common-c/src/SdpGenerator.c").read_text()


def function(source, signature):
    start = source.index(signature)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        end += 1
    return source[start:end]


policy = function(connection, "static int MLResolvedDynamicRangeModeForPreference(")
coded_depth = function(renderer, "static int MLCodedVideoBitDepth(")
start = sdp.index("// Advertise the selected dynamic range mode")
wire = sdp[start:sdp.index("// If the decoder supports reference", start)]

fixture = r'''
#import <Foundation/Foundation.h>
#import <CoreMedia/CoreMedia.h>
#include <assert.h>
#include "Limelight.h"
static NSString *attribute;
static int addAttributeString(void **head, const char *name, const char *value) {
    assert(strcmp(name, "x-nv-video[0].dynamicRangeMode") == 0);
    attribute = @(value);
    return 0;
}
'''
serializer = r'''
static NSString *serialize(int NegotiatedVideoFormat, BOOL hdr, BOOL sdr10, int transfer) {
    STREAM_CONFIGURATION StreamConfig = {0};
    StreamConfig.dynamicRangeMode = MLResolvedDynamicRangeModeForPreference(hdr, sdr10, transfer);
    char payloadStr[32];
    void *optionHead = NULL;
    int err = 0;
    attribute = nil;
''' + wire + r'''
    assert(err == 0 && attribute != nil);
    return attribute;
}
static CMVideoFormatDescriptionRef description(OSType codec, NSDictionary *extensions) {
    CMVideoFormatDescriptionRef result = NULL;
    OSStatus status = CMVideoFormatDescriptionCreate(NULL, codec, 128, 128,
        (__bridge CFDictionaryRef)extensions, &result);
    assert(status == noErr && result != NULL);
    return result;
}
static void checkDepth(NSDictionary *extensions, OSType codec, int expected) {
    CMVideoFormatDescriptionRef format = description(codec, extensions);
    assert(MLCodedVideoBitDepth(format) == expected);
    CFRelease(format);
}
static NSData *record(int luma, int chroma) {
    UInt8 bytes[23] = {1};
    bytes[17] = 0xf8 | (luma - 8);
    bytes[18] = 0xf8 | (chroma - 8);
    return [NSData dataWithBytes:bytes length:sizeof(bytes)];
}
static NSDictionary *withRecord(id value) {
    return @{(__bridge NSString *)kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms:
        @{ @"hvcC": value }};
}
int main(void) { @autoreleasepool {
    const int transfers[] = {-1, 0, 1, 2, 99};
    const int formats[] = {
        VIDEO_FORMAT_H264, VIDEO_FORMAT_H264_HIGH8_444,
        VIDEO_FORMAT_H265, VIDEO_FORMAT_H265_MAIN10,
        VIDEO_FORMAT_H265_REXT8_444, VIDEO_FORMAT_H265_REXT10_444,
        VIDEO_FORMAT_AV1_MAIN8, VIDEO_FORMAT_AV1_MAIN10,
        VIDEO_FORMAT_AV1_HIGH8_444, VIDEO_FORMAT_AV1_HIGH10_444,
    };
    for (int hdr = 0; hdr <= 1; hdr++) for (int sdr10 = 0; sdr10 <= 1; sdr10++) {
        for (unsigned t = 0; t < sizeof(transfers)/sizeof(*transfers); t++) {
            int transfer = transfers[t];
            int wanted = hdr ? (transfer == 2 ? 2 : 1) : (sdr10 ? 1 : 0);
            assert(MLResolvedDynamicRangeModeForPreference(hdr, sdr10, transfer) == wanted);
            for (unsigned f = 0; f < sizeof(formats)/sizeof(*formats); f++) {
                int expected = (formats[f] & VIDEO_FORMAT_MASK_10BIT) ? wanted : 0;
                assert(([serialize(formats[f], hdr, sdr10, transfer)
                    isEqualToString:[NSString stringWithFormat:@"%d", expected]]));
            }
        }
    }
    assert(MLCodedVideoBitDepth(NULL) == 0);
    checkDepth(withRecord(record(8, 8)), kCMVideoCodecType_HEVC, 8);
    checkDepth(withRecord(record(10, 10)), kCMVideoCodecType_HEVC, 10);
    NSMutableDictionary *conflict = [withRecord(record(8, 8)) mutableCopy];
    conflict[@"BitsPerComponent"] = @10;
    checkDepth(conflict, kCMVideoCodecType_HEVC, 8);
    checkDepth(withRecord([NSMutableData dataWithLength:18]), kCMVideoCodecType_HEVC, 0);
    checkDepth(withRecord(@"invalid"), kCMVideoCodecType_HEVC, 0);
    checkDepth(@{}, kCMVideoCodecType_HEVC, 0);
    checkDepth(@{@"BitsPerComponent": @8}, kCMVideoCodecType_AV1, 8);
    checkDepth(@{@"BitsPerComponent": @10}, kCMVideoCodecType_AV1, 10);
    checkDepth(@{@"BitsPerComponent": @"10"}, kCMVideoCodecType_AV1, 0);
    checkDepth(@{@"BitsPerComponent": @32}, kCMVideoCodecType_AV1, 0);
    puts("PASS: SDR10 emits mode 1; SDR8, HDR PQ/HLG, and 8-bit fallback remain correct (200 SDP cases)");
    puts("PASS: coded-depth diagnostics distinguish 8/10-bit source records from output buffer precision");
} return 0; }
'''

env = os.environ.copy()
default_xcode = Path("/Applications/Xcode.app/Contents/Developer")
if default_xcode.is_dir():
    env.setdefault("DEVELOPER_DIR", str(default_xcode))
with tempfile.TemporaryDirectory(prefix="moonlight-sdr10-") as directory:
    path = Path(directory)
    source = path / "test.m"
    source.write_text(fixture + policy + coded_depth + serializer)
    subprocess.run([
        "xcrun", "clang", "-fobjc-arc", "-fblocks", "-framework", "Foundation",
        "-framework", "CoreMedia", "-I", str(root / "moonlight-common/moonlight-common-c/src"),
        str(source), "-o", str(path / "test"),
    ], check=True, env=env)
    subprocess.run([str(path / "test")], check=True, timeout=10)
    if args.native_probe:
        source.write_text(fixture + coded_depth + (root / "tests/sdr10/native-probe.m").read_text())
        subprocess.run([
            "xcrun", "clang", "-fobjc-arc", "-fblocks", "-framework", "Foundation",
            "-framework", "CoreMedia", "-framework", "CoreVideo", "-framework", "VideoToolbox",
            "-I", str(root / "moonlight-common/moonlight-common-c/src"),
            str(source), "-o", str(path / "native-probe"),
        ], check=True, env=env)
        subprocess.run([str(path / "native-probe")], check=True, timeout=30)
