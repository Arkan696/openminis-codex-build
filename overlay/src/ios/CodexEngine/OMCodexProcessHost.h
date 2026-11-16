// SPDX-License-Identifier: GPL-3.0-or-later
#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
// One-shot native iOS process owner. Call public methods and install callbacks on main.
// Callbacks always arrive on main. Create a NEW host for each connection generation.
@interface OMCodexProcessHost : NSObject
@property(nonatomic,copy,nullable) void (^messageHandler)(NSDictionary<NSString *,id> *message);
@property(nonatomic,copy,nullable) void (^diagnosticHandler)(NSString *safeSummary);
@property(nonatomic,copy,nullable) void (^terminationHandler)(int exitCode,int signalNumber);
@property(atomic,readonly) BOOL running;
@property(atomic,readonly) int processIdentifier;
@property(atomic,copy,readonly,nullable) NSString *workingDirectory;
// Research/TrollStore-only for the currently provided core: its Mach-O declares
// platform-application + no-sandbox private entitlements. This is NOT App Store
// safe, and the dedicated workspace is NOT a kernel security boundary. Bundle
// resources must be CodexRuntime/codex, CodexRuntime/ca-certificates.crt and
// CodexRuntime/text-only.toml. Never launches automatically or via iSH.
- (void)startInBundle:(NSBundle *)bundle completion:(void (^)(NSError * _Nullable error))completion NS_SWIFT_NAME(start(in:completion:));
// Bare NDJSON envelope, no jsonrpc field. Only the milestone's fixed RPC allowlist.
- (void)sendMessage:(NSDictionary<NSString *,id> *)message completion:(void (^)(NSError * _Nullable error))completion NS_SWIFT_NAME(send(_:completion:));
// Stops the process group; completes after waitpid and workspace cleanup. Idempotent.
- (void)stopWithCompletion:(void (^)(void))completion NS_SWIFT_NAME(stop(completion:));
@end
NS_ASSUME_NONNULL_END
