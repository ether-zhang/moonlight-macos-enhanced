// Optional local probe: encode real 8/10-bit HEVC, then use native VT output.
// MLCodedVideoBitDepth is extracted from production by run.py.
#import <VideoToolbox/VideoToolbox.h>
#import <CoreVideo/CoreVideo.h>

typedef struct {
    CMSampleBufferRef encoded;
    CVPixelBufferRef decoded;
} ProbeFrames;

static void encoded(void *context, void *frame, OSStatus status,
                    VTEncodeInfoFlags flags, CMSampleBufferRef sample) {
    assert(status == noErr && sample != NULL);
    ((ProbeFrames *)context)->encoded = (CMSampleBufferRef)CFRetain(sample);
}

static void decoded(void *context, void *frame, OSStatus status,
                    VTDecodeInfoFlags flags, CVImageBufferRef image,
                    CMTime timestamp, CMTime duration) {
    assert(status == noErr && image != NULL);
    ((ProbeFrames *)context)->decoded = CVPixelBufferRetain(image);
}

static void probe(int depth) {
    ProbeFrames frames = {0};
    VTCompressionSessionRef encoder = NULL;
    NSDictionary *spec = @{(__bridge NSString *)kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder: @YES};
    OSStatus status = VTCompressionSessionCreate(NULL, 128, 128, kCMVideoCodecType_HEVC,
        (__bridge CFDictionaryRef)spec, NULL, NULL, encoded, &frames, &encoder);
    assert(status == noErr && encoder != NULL);
    assert(VTSessionSetProperty(encoder, kVTCompressionPropertyKey_ProfileLevel,
        depth == 10 ? kVTProfileLevel_HEVC_Main10_AutoLevel : kVTProfileLevel_HEVC_Main_AutoLevel) == noErr);
    assert(VTSessionSetProperty(encoder, kVTCompressionPropertyKey_AllowFrameReordering, kCFBooleanFalse) == noErr);
    assert(VTSessionSetProperty(encoder, kVTCompressionPropertyKey_RealTime, kCFBooleanTrue) == noErr);

    CVPixelBufferRef input = NULL;
    NSDictionary *attributes = @{(__bridge NSString *)kCVPixelBufferIOSurfacePropertiesKey: @{}};
    OSType inputFormat = depth == 10 ? kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange
                                   : kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange;
    assert(CVPixelBufferCreate(NULL, 128, 128, inputFormat,
        (__bridge CFDictionaryRef)attributes, &input) == kCVReturnSuccess);
    assert(CVPixelBufferLockBaseAddress(input, 0) == kCVReturnSuccess);
    for (size_t plane = 0; plane < 2; plane++) {
        unsigned char *base = CVPixelBufferGetBaseAddressOfPlane(input, plane);
        size_t stride = CVPixelBufferGetBytesPerRowOfPlane(input, plane);
        size_t height = CVPixelBufferGetHeightOfPlane(input, plane);
        memset(base, 0, stride * height);
        for (size_t y = 0; y < height; y++) for (size_t x = 0; x < 128; x++) {
            if (depth == 10) {
                ((uint16_t *)(base + y * stride))[x] = (plane ? 512 : 64 + x * 876 / 127) << 6;
            } else {
                (base + y * stride)[x] = plane ? 128 : 16 + x * 219 / 127;
            }
        }
    }
    CVPixelBufferUnlockBaseAddress(input, 0);
    assert(VTCompressionSessionEncodeFrame(encoder, input, CMTimeMake(0, 60),
        CMTimeMake(1, 60), NULL, NULL, NULL) == noErr);
    assert(VTCompressionSessionCompleteFrames(encoder, kCMTimeInvalid) == noErr);
    assert(frames.encoded != NULL);
    CMVideoFormatDescriptionRef format = CMSampleBufferGetFormatDescription(frames.encoded);
    int codedDepth = MLCodedVideoBitDepth(format);
    assert(codedDepth == depth);

    // Match the production native path: no forced output pixel format.
    NSDictionary *outputAttributes = @{
        (__bridge NSString *)kCVPixelBufferMetalCompatibilityKey: @YES,
        (__bridge NSString *)kCVPixelBufferIOSurfacePropertiesKey: @{},
    };
    VTDecompressionSessionRef decoder = NULL;
    VTDecompressionOutputCallbackRecord callback = {decoded, &frames};
    assert(VTDecompressionSessionCreate(NULL, format, NULL,
        (__bridge CFDictionaryRef)outputAttributes, &callback, &decoder) == noErr);
    assert(VTDecompressionSessionDecodeFrame(decoder, frames.encoded,
        kVTDecodeFrame_EnableAsynchronousDecompression, NULL, NULL) == noErr);
    assert(VTDecompressionSessionWaitForAsynchronousFrames(decoder) == noErr);
    assert(frames.decoded != NULL);
    OSType output = CVPixelBufferGetPixelFormatType(frames.decoded);
    NSDictionary *pixelDescription = CFBridgingRelease(CVPixelFormatDescriptionCreateWithPixelFormatType(NULL, output));
    int outputDepth = 0;
    if (@available(macOS 15.0, *)) {
        outputDepth = [pixelDescription[(__bridge NSString *)kCVPixelFormatBitsPerComponent] intValue];
    }
    printf("Native VT probe: input=%d-bit, actual coded=%d-bit, decoded=%c%c%c%c (0x%X), outputDepth=%d\n",
        depth, codedDepth, (char)(output >> 24), (char)(output >> 16),
        (char)(output >> 8), (char)output, (unsigned)output, outputDepth);
    if (outputDepth != 0) {
        assert(outputDepth == depth);
    }
    VTDecompressionSessionInvalidate(decoder);
    CFRelease(decoder);
    VTCompressionSessionInvalidate(encoder);
    CFRelease(encoder);
    CFRelease(frames.encoded);
    CVPixelBufferRelease(frames.decoded);
    CVPixelBufferRelease(input);
}

int main(void) { @autoreleasepool {
    probe(8);
    probe(10);
    puts("PASS: coded-depth reader validated against actual local HEVC encoder output");
} return 0; }
