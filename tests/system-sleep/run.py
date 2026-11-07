"""Exercise the production sleep/reconnect policy without sleeping the Mac or contacting a host."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "Limelight/macOS/ViewControllers/StreamViewController.m").read_text()
diagnostics = (root / "Limelight/macOS/ViewControllers/StreamViewController+Diagnostics.m").read_text()


def method(text, signature):
    start = text.index(signature)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
        end += 1
    return text[start:end]


production = "\n".join([
    method(source, "- (void)installSystemSleepObserver"),
    method(source, "- (void)tearDownSystemSleepObserver"),
    method(source, "- (void)handleSystemWillSleep:"),
    method(source, "- (BOOL)shouldQuitHostAppAfterStream"),
    method(diagnostics, "- (void)cancelPendingReconnectForUserExitWithReason:"),
    method(diagnostics, "- (void)attemptReconnectWithReason:"),
])

fixture = r'''
#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>
#include <assert.h>
#define Log(...) ((void)0)
static CFTimeInterval CACurrentMediaTime(void) { return CFAbsoluteTimeGetCurrent(); }
static NSString * const NSWorkspaceWillSleepNotification = @"fixture-will-sleep";
static NSString * const NSWorkspaceDidWakeNotification = @"fixture-did-wake";
static BOOL quitPreference;
@interface NSWorkspace : NSObject
@property NSNotificationCenter *notificationCenter;
+ (instancetype)sharedWorkspace;
@end
@implementation NSWorkspace
+ (instancetype)sharedWorkspace {
    static NSWorkspace *workspace; static dispatch_once_t once;
    dispatch_once(&once, ^{ workspace=[NSWorkspace new]; workspace.notificationCenter=[NSNotificationCenter new]; });
    return workspace;
}
@end
@interface FakeHost : NSObject
@property NSString *uuid;
@end
@implementation FakeHost
@end
@interface FakeApp : NSObject
@property FakeHost *host;
@end
@implementation FakeApp
@end
@interface SettingsClass : NSObject
+ (BOOL)quitAppAfterStreamFor:(NSString *)host;
@end
@implementation SettingsClass
+ (BOOL)quitAppAfterStreamFor:(NSString *)host { return quitPreference; }
@end
@interface FakeHID : NSObject
@property int releases, teardowns;
- (void)releaseAllModifierKeys;
- (void)tearDownHidManager;
@end
@implementation FakeHID
- (void)releaseAllModifierKeys { self.releases++; }
- (void)tearDownHidManager { self.teardowns++; }
@end
@interface FakeManager : NSObject
@property BOOL blockStop;
@property int stops;
@property dispatch_semaphore_t entered, releaseStop;
- (void)stopStream;
@end
@implementation FakeManager
- (instancetype)init { if ((self=[super init])) { _entered=dispatch_semaphore_create(0); _releaseStop=dispatch_semaphore_create(0); } return self; }
- (void)stopStream {
    self.stops++; dispatch_semaphore_signal(self.entered);
    if (self.blockStop) dispatch_semaphore_wait(self.releaseStop, DISPATCH_TIME_FOREVER);
}
@end
@interface StreamViewController : NSObject
@property id systemWillSleepObserver;
@property BOOL systemSleepDisconnectRequested, shouldAttemptReconnect, disconnectWasUserInitiated;
@property BOOL reconnectInProgress, stopStreamInProgress, reconnectPreserveFullscreenStateValid, useSystemControllerDriver;
@property NSUInteger activeStreamGeneration, connectWatchdogToken;
@property NSInteger reconnectAttemptCount, reconnectPreservedWindowMode;
@property NSOperationQueue *streamOpQueue;
@property FakeHID *hidSupport;
@property FakeManager *streamMan;
@property FakeApp *app;
@property int closeRequests, uncaptures, powerReleases, prepares;
@property NSString *lastCloseSource;
- (void)installSystemSleepObserver;
- (void)tearDownSystemSleepObserver;
- (void)handleSystemWillSleep:(NSNotification *)notification;
- (BOOL)shouldQuitHostAppAfterStream;
- (void)cancelPendingReconnectForUserExitWithReason:(NSString *)reason;
- (void)attemptReconnectWithReason:(NSString *)reason;
- (void)markUserInitiatedDisconnectAndSuppressWarningsForSeconds:(double)seconds reason:(NSString *)reason;
- (void)uncaptureMouseWithCode:(NSString *)code reason:(NSString *)reason;
- (void)allowDisplaySleep;
- (void)requestStreamCloseWithSource:(NSString *)source;
- (void)hideReconnectOverlay;
- (void)showReconnectOverlayWithMessage:(NSString *)message;
- (void)suppressConnectionWarningsForSeconds:(double)seconds reason:(NSString *)reason;
- (BOOL)isWindowFullscreen;
- (BOOL)isWindowBorderlessMode;
- (BOOL)isActiveStreamGeneration:(NSUInteger)generation;
- (void)tearDownControllerSupportOnMainThreadIfNeeded;
- (void)prepareForStreaming;
@end
#define MLString(value, comment) (value)
@implementation StreamViewController
- (instancetype)init {
    if ((self=[super init])) {
        _shouldAttemptReconnect=YES; _activeStreamGeneration=1;
        _streamOpQueue=[NSOperationQueue new]; _hidSupport=[FakeHID new]; _streamMan=[FakeManager new];
        _app=[FakeApp new]; _app.host=[FakeHost new]; _app.host.uuid=@"fixture-host";
    }
    return self;
}
- (void)dealloc { [self tearDownSystemSleepObserver]; }
- (void)markUserInitiatedDisconnectAndSuppressWarningsForSeconds:(double)s reason:(NSString *)r { self.disconnectWasUserInitiated=YES; }
- (void)uncaptureMouseWithCode:(NSString *)c reason:(NSString *)r { self.uncaptures++; }
- (void)allowDisplaySleep { self.powerReleases++; }
- (void)requestStreamCloseWithSource:(NSString *)source { self.closeRequests++; self.lastCloseSource=source; }
- (void)hideReconnectOverlay {}
- (void)showReconnectOverlayWithMessage:(NSString *)message {}
- (void)suppressConnectionWarningsForSeconds:(double)s reason:(NSString *)r {}
- (BOOL)isWindowFullscreen { return NO; }
- (BOOL)isWindowBorderlessMode { return NO; }
- (BOOL)isActiveStreamGeneration:(NSUInteger)generation { return self.activeStreamGeneration==generation; }
- (void)tearDownControllerSupportOnMainThreadIfNeeded {}
- (void)prepareForStreaming { self.prepares++; }
'''

checks = r'''
@end
static void sleepNotice(void) {
    [NSWorkspace.sharedWorkspace.notificationCenter postNotificationName:NSWorkspaceWillSleepNotification object:nil];
}
static void pumpUntil(BOOL (^condition)(void)) {
    NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:2];
    while (!condition() && deadline.timeIntervalSinceNow>0)
        [[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.005]];
    assert(condition());
}
int main(void) { @autoreleasepool {
    StreamViewController *c=[StreamViewController new];
    [c installSystemSleepObserver]; id token=c.systemWillSleepObserver;
    [c installSystemSleepObserver]; assert(c.systemWillSleepObserver==token);
    [[NSNotificationCenter defaultCenter] postNotificationName:NSWorkspaceWillSleepNotification object:nil];
    assert(!c.systemSleepDisconnectRequested);
    c.streamOpQueue.suspended=YES;
    __block BOOL operationRan=NO;
    NSBlockOperation *operation=[NSBlockOperation blockOperationWithBlock:^{ operationRan=YES; }];
    [c.streamOpQueue addOperation:operation];
    quitPreference=YES; assert([c shouldQuitHostAppAfterStream]);
    NSUInteger before=c.activeStreamGeneration;
    sleepNotice();
    assert(c.systemSleepDisconnectRequested && c.disconnectWasUserInitiated && !c.shouldAttemptReconnect);
    assert(c.activeStreamGeneration>before && c.closeRequests==1 && [c.lastCloseSource isEqual:@"system-sleep"]);
    assert(c.hidSupport.releases==1 && c.uncaptures==1 && c.powerReleases==1);
    assert(operation.cancelled && ![c shouldQuitHostAppAfterStream]);
    c.streamOpQueue.suspended=NO; [c.streamOpQueue waitUntilAllOperationsAreFinished]; assert(!operationRan);
    sleepNotice(); assert(c.closeRequests==1);
    [NSWorkspace.sharedWorkspace.notificationCenter postNotificationName:NSWorkspaceDidWakeNotification object:nil];
    [c attemptReconnectWithReason:@"after-wake"]; assert(c.streamMan.stops==0 && c.prepares==0);
    [c tearDownSystemSleepObserver]; assert(c.systemWillSleepObserver==nil);
    c=nil;

    StreamViewController *removed=[StreamViewController new];
    [removed installSystemSleepObserver]; [removed tearDownSystemSleepObserver];
    sleepNotice(); assert(!removed.systemSleepDisconnectRequested && removed.closeRequests==0);
    __weak StreamViewController *weakOwner;
    @autoreleasepool { StreamViewController *owner=[StreamViewController new]; weakOwner=owner; [owner installSystemSleepObserver]; }
    assert(weakOwner==nil); sleepNotice();

    StreamViewController *normal=[StreamViewController new];
    [normal attemptReconnectWithReason:@"normal-network-recovery"];
    pumpUntil(^BOOL { return normal.prepares==1; });
    assert(normal.streamMan.stops==1 && normal.closeRequests==0);

    StreamViewController *racing=[StreamViewController new];
    racing.streamMan.blockStop=YES; FakeManager *manager=racing.streamMan;
    [racing installSystemSleepObserver];
    [racing attemptReconnectWithReason:@"recovery-before-sleep"];
    assert(dispatch_semaphore_wait(manager.entered, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC))==0);
    assert(racing.reconnectInProgress && racing.stopStreamInProgress);
    sleepNotice();
    assert(!racing.reconnectInProgress && !racing.shouldAttemptReconnect && racing.closeRequests==1);
    dispatch_semaphore_signal(manager.releaseStop);
    pumpUntil(^BOOL { return !racing.stopStreamInProgress; });
    assert(racing.prepares==0 && ![racing shouldQuitHostAppAfterStream]);
    // Even a stale callback restoring the generic retry flag cannot override the sleep policy.
    racing.shouldAttemptReconnect=YES;
    [racing attemptReconnectWithReason:@"stale-after-wake"];
    assert(manager.stops==1 && racing.prepares==0);
    [racing tearDownSystemSleepObserver];
    puts("System sleep: notification ownership, cancellation, reconnect race, host-app preservation, and wake policy passed");
} return 0; }
'''

with tempfile.TemporaryDirectory(prefix="moonlight-system-sleep-") as directory:
    path = Path(directory)
    (path / "test.m").write_text(fixture + production + checks)
    env = os.environ.copy()
    default_xcode = Path("/Applications/Xcode.app/Contents/Developer")
    if default_xcode.is_dir():
        env.setdefault("DEVELOPER_DIR", str(default_xcode))
    subprocess.run(["xcrun", "clang", "-fobjc-arc", "-fblocks", "-framework", "Foundation", str(path / "test.m"), "-o", str(path / "test")], check=True, env=env)
    subprocess.run([str(path / "test")], check=True, timeout=10)
