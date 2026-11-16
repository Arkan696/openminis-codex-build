// SPDX-License-Identifier: GPL-3.0-or-later
#import "OMCodexProcessHost.h"
#import "OMCodexFramer.h"
#import <CommonCrypto/CommonDigest.h>
#include <spawn.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <unistd.h>
#include <mach-o/loader.h>
#include <string.h>
#include <errno.h>
#include <time.h>

static double OMNow(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static NSError *OMError(NSInteger code,NSString *description){return [NSError errorWithDomain:@"OpenMinis.CodexHost" code:code userInfo:@{NSLocalizedDescriptionKey:description}];}
static NSError *OMPosix(int e,NSString *operation){return OMError(e,[NSString stringWithFormat:@"%@ failed (errno=%d)",operation,e]);}
static BOOL OMSafeEnvelope(NSDictionary *m){
 if(![m isKindOfClass:NSDictionary.class] || m[@"jsonrpc"] || m.count>3)return NO;
 NSString *method=m[@"method"];if(![method isKindOfClass:NSString.class])return NO;
 NSSet *allowed=[NSSet setWithArray:@[@"initialize",@"initialized",@"account/read",@"account/login/start",@"account/login/cancel",@"account/logout",@"thread/start",@"turn/start",@"turn/interrupt"]];
 if(![allowed containsObject:method])return NO;
 for(id key in m)if(![@[@"id",@"method",@"params"] containsObject:key])return NO;
 if([method isEqual:@"initialized"])return m.count==1;
 id requestID=m[@"id"];
 if(![requestID isKindOfClass:NSNumber.class] && !([requestID isKindOfClass:NSString.class] && [requestID length]>0 && [requestID length]<=64))return NO;
 NSDictionary *p=m[@"params"];
 if(![p isKindOfClass:NSDictionary.class])return NO;
 if([method isEqual:@"initialize"]){
  NSDictionary *expected=@{@"clientInfo":@{@"name":@"openminis_codex",@"title":@"OpenMinis Codex",@"version":@"0.1"},@"capabilities":@{@"experimentalApi":@YES,@"explicitGatewayOauth":@YES}};
  return m.count==3 && [p isEqual:expected];
 }
 if([method isEqual:@"account/login/start"])return m.count==3 && [p isEqual:@{@"type":@"chatgpt"}];
 if([method isEqual:@"account/read"])return m.count==3 && [p isEqual:@{@"refreshToken":@NO}];
 if([method isEqual:@"thread/start"]){
  NSSet *keys=[NSSet setWithArray:@[@"cwd",@"sandbox",@"approvalPolicy",@"ephemeral",@"environments"]];
  for(id key in p)if(![keys containsObject:key])return NO;
  if(p.count!=5)return NO;
  return [p[@"cwd"] isKindOfClass:NSString.class] && [p[@"cwd"] hasPrefix:@"/"] && [p[@"sandbox"] isEqual:@"read-only"] && [p[@"approvalPolicy"] isEqual:@"untrusted"] && [p[@"ephemeral"] isEqual:@YES] && [p[@"environments"] isEqual:@[]];
 }
 if([method isEqual:@"turn/start"]){
  NSSet *keys=[NSSet setWithArray:@[@"threadId",@"input",@"environments",@"approvalPolicy",@"sandboxPolicy"]];
  for(id key in p)if(![keys containsObject:key])return NO;
  id input=p[@"input"];
  if(p.count!=5 || ![p[@"threadId"] isKindOfClass:NSString.class] || ![p[@"approvalPolicy"] isEqual:@"untrusted"] || ![p[@"environments"] isEqual:@[]])return NO;
  if(![p[@"sandboxPolicy"] isEqual:@{@"type":@"readOnly",@"networkAccess":@NO}])return NO;
  if(![input isKindOfClass:NSArray.class] || [input count]!=1)return NO;
  NSDictionary *item=[input firstObject];
  return [item isKindOfClass:NSDictionary.class] && [item[@"type"] isEqual:@"text"] && [item[@"text"] isKindOfClass:NSString.class] && [item[@"text"] length]<=32768 && [item[@"text_elements"] isEqual:@[]];
 }
 if([method isEqual:@"turn/interrupt"])return p.count==2 && [p[@"threadId"] isKindOfClass:NSString.class] && [p[@"turnId"] isKindOfClass:NSString.class];
 if([method isEqual:@"account/logout"])return p.count==0;
 if([method isEqual:@"account/login/cancel"])return p.count==1 && [p[@"loginId"] isKindOfClass:NSString.class];
 return NO;
}
@interface OMCodexProcessHost () {
 NSCondition *_lock;
 NSMutableArray<NSDictionary *> *_writes;
 NSMutableArray<void (^)(void)> *_stoppers;
 BOOL _used,_stopRequested,_workerActive;
 int _wakeRead,_wakeWrite;
 NSString *_workspace;
}
@property(atomic,readwrite) BOOL running;
@property(atomic,readwrite) int processIdentifier;
@property(atomic,copy,readwrite) NSString *workingDirectory;
@end
@implementation OMCodexProcessHost
- (instancetype)init{if((self=[super init])){_lock=[NSCondition new];_writes=[NSMutableArray new];_stoppers=[NSMutableArray new];_wakeRead=-1;_wakeWrite=-1;}return self;}
- (void)diagnostic:(NSString *)summary {dispatch_async(dispatch_get_main_queue(),^{if(self.diagnosticHandler)self.diagnosticHandler(summary);});}
- (void)deliver:(NSDictionary *)message {dispatch_async(dispatch_get_main_queue(),^{if(self.messageHandler)self.messageHandler(message);});}
- (BOOL)shouldStop {[_lock lock];BOOL stopped=_stopRequested;[_lock unlock];return stopped;}
- (void)wakeLocked {if(_wakeWrite>=0){char byte='x';ssize_t ignored=write(_wakeWrite,&byte,1);(void)ignored;}}
- (void)sendMessage:(NSDictionary *)message completion:(void (^)(NSError *))completion {
 NSAssert(NSThread.isMainThread,@"Host API must be on main");
 NSError *error=nil;NSData *json=nil;
 if(!OMSafeEnvelope(message))error=OMError(2,@"RPC disabled or invalid for this integration slice");
 if(!error)json=[NSJSONSerialization dataWithJSONObject:message options:0 error:&error];
 if(!error && (!json.length || json.length>128*1024))error=OMError(3,@"Outgoing RPC exceeds 128 KiB");
 if(error){completion(error);return;}
 NSMutableData *line=[json mutableCopy];[line appendBytes:"\n" length:1];
 [_lock lock];
 if(!self.running || _stopRequested || _writes.count>=32){[_lock unlock];completion(OMError(4,@"Core stopped or request queue full"));return;}
 // NSData copy means callers cannot mutate a queued dictionary after validation.
 [_writes addObject:@{@"bytes":line,@"completion":[completion copy]}];[self wakeLocked];[_lock unlock];
}
- (void)stopWithCompletion:(void (^)(void))completion {
 NSAssert(NSThread.isMainThread,@"Host API must be on main");
 [_lock lock];
 if(!_workerActive){[_lock unlock];completion();return;}
 [_stoppers addObject:[completion copy]];_stopRequested=YES;[self wakeLocked];[_lock unlock];
}
- (BOOL)validateRuntime:(NSBundle *)bundle runtime:(NSString **)runtime error:(NSError **)error {
 NSString *dir=[bundle.bundlePath stringByAppendingPathComponent:@"CodexRuntime"];
 NSString *exe=[dir stringByAppendingPathComponent:@"codex"];
 int fd=open(exe.fileSystemRepresentation,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
 struct mach_header_64 h={0};struct stat st;
 BOOL valid=fd>=0 && fstat(fd,&st)==0 && S_ISREG(st.st_mode) && read(fd,&h,sizeof(h))==sizeof(h) && h.magic==MH_MAGIC_64 && h.cputype==CPU_TYPE_ARM64 && h.filetype==MH_EXECUTE;
 if(fd>=0)close(fd);
 NSString *canonical=[exe stringByResolvingSymlinksInPath];
 if(!valid || ![canonical hasPrefix:[bundle.bundlePath.stringByResolvingSymlinksInPath stringByAppendingString:@"/"]] || access(exe.fileSystemRepresentation,X_OK)!=0){*error=OMError(5,@"CodexRuntime/codex must be an executable ARM64 Mach-O inside this bundle; signature and device compatibility require separate validation");return NO;}
 NSString *ca=[dir stringByAppendingPathComponent:@"ca-certificates.crt"];
 NSData *pem=[NSData dataWithContentsOfFile:ca];unsigned char digest[CC_SHA256_DIGEST_LENGTH];
 if(!pem.length || pem.length>1024*1024){*error=OMError(6,@"Bundled CA missing or oversized; verification will not be disabled");return NO;}
 CC_SHA256(pem.bytes,(CC_LONG)pem.length,digest);NSMutableString *hex=[NSMutableString new];for(int i=0;i<CC_SHA256_DIGEST_LENGTH;i++)[hex appendFormat:@"%02x",digest[i]];
 if(![hex isEqual:@"427c064216761a1e72a8b30f0d954d08cb6e4435a82091fb865afd20c8497285"]){*error=OMError(7,@"Bundled Mozilla CA integrity mismatch");return NO;}
 NSData *profile=[NSData dataWithContentsOfFile:[dir stringByAppendingPathComponent:@"text-only.toml"]];
 if(!profile.length || profile.length>16384){*error=OMError(8,@"Bundled text-only profile unavailable");return NO;}
 CC_SHA256(profile.bytes,(CC_LONG)profile.length,digest);[hex setString:@""];
 for(int i=0;i<CC_SHA256_DIGEST_LENGTH;i++)[hex appendFormat:@"%02x",digest[i]];
 if(![hex isEqual:@"81d1f268d529874409d315b95532d03ae092c488fdff8c7594609b1e3752afdd"]){*error=OMError(8,@"Text-only ephemeral profile integrity mismatch");return NO;}
 *runtime=dir;return YES;
}
- (NSString *)makeWorkspace:(NSString *)runtime error:(NSError **)error {
 NSFileManager *fm=NSFileManager.defaultManager;NSString *root=nil;
 for(NSString *base in @[NSTemporaryDirectory()?:@"",@"/private/var/tmp",@"/tmp"]){
  if(!base.length)continue;NSString *tpl=[base stringByAppendingPathComponent:@"local.openminis.codex-XXXXXX"];char *buf=strdup(tpl.fileSystemRepresentation);
  if(!buf){*error=OMPosix(ENOMEM,@"workspace allocation");return nil;}
  char *made=mkdtemp(buf);if(made)root=[fm stringWithFileSystemRepresentation:made length:strlen(made)];free(buf);if(root)break;
 }
 if(!root){*error=OMPosix(errno,@"private workspace");return nil;}
 (void)chmod(root.fileSystemRepresentation,0700);
 for(NSString *part in @[@"home/.codex",@"tmp",@"empty"]){
  if(![fm createDirectoryAtPath:[root stringByAppendingPathComponent:part] withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:error]){[fm removeItemAtPath:root error:NULL];return nil;}
  NSString *dir=[root stringByAppendingPathComponent:part];(void)chmod(dir.fileSystemRepresentation,0700);
 }
 NSData *profile=[NSData dataWithContentsOfFile:[runtime stringByAppendingPathComponent:@"text-only.toml"]];
 NSString *cfg=[root stringByAppendingPathComponent:@"home/.codex/config.toml"];
 if(![profile writeToFile:cfg options:NSDataWritingAtomic error:error]){[fm removeItemAtPath:root error:NULL];return nil;}
 chmod(cfg.fileSystemRepresentation,0600);return root;
}
- (void)startInBundle:(NSBundle *)bundle completion:(void (^)(NSError *))completion {
 NSAssert(NSThread.isMainThread,@"Host API must be on main");
 [_lock lock];
 if(_used){[_lock unlock];completion(OMError(9,@"Create a new host per connection"));return;}
 _used=YES;_workerActive=YES;
 int wake[2]={-1,-1};
 if(pipe(wake)!=0){_workerActive=NO;[_lock unlock];completion(OMPosix(errno,@"wake pipe"));return;}
 int setupError=0;
 for(int i=0;i<2;i++){
  int flags=fcntl(wake[i],F_GETFL,0);
  if(flags<0 || fcntl(wake[i],F_SETFD,FD_CLOEXEC)<0 || fcntl(wake[i],F_SETFL,flags|O_NONBLOCK)<0){setupError=errno;break;}
 }
 if(setupError){close(wake[0]);close(wake[1]);_workerActive=NO;[_lock unlock];completion(OMPosix(setupError,@"wake pipe flags"));return;}
 _wakeRead=wake[0];_wakeWrite=wake[1];
 [_lock unlock];
 dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{@autoreleasepool{[self runWithBundle:bundle completion:completion];}});
}
static BOOL OMPipe(int fds[2],NSError **error){
 fds[0]=-1;fds[1]=-1;
 if(pipe(fds)!=0){if(error)*error=OMPosix(errno,@"stdio pipe");return NO;}

 for(int i=0;i<2;i++){
  if(fds[i]<3){int next=fcntl(fds[i],F_DUPFD_CLOEXEC,3);close(fds[i]);fds[i]=next;}
  if(fds[i]<0 || fcntl(fds[i],F_SETFD,FD_CLOEXEC)<0){*error=OMPosix(errno,@"pipe flags");return NO;}
 }return YES;
}
- (pid_t)spawn:(NSString *)runtime workspace:(NSString *)root input:(int *)inFD output:(int *)outFD diagnostic:(int *)errFD error:(NSError **)error {
 int p[3][2]={{-1,-1},{-1,-1},{-1,-1}};pid_t pid=-1;
 posix_spawn_file_actions_t actions;posix_spawnattr_t attr;BOOL aOK=NO,tOK=NO;
 char **envp=NULL;
 do {
  if(!OMPipe(p[0],error)||!OMPipe(p[1],error)||!OMPipe(p[2],error))break;
  BOOL pipeFlagsOK=fcntl(p[0][1],F_SETNOSIGPIPE,1)==0;
  int parentFDs[3]={p[0][1],p[1][0],p[2][0]};
  for(int i=0;i<3 && pipeFlagsOK;i++){
   int flags=fcntl(parentFDs[i],F_GETFL,0);
   if(flags<0 || fcntl(parentFDs[i],F_SETFL,flags|O_NONBLOCK)<0)pipeFlagsOK=NO;
  }
  if(!pipeFlagsOK){*error=OMPosix(errno,@"parent pipe flags");break;}
  int e=posix_spawn_file_actions_init(&actions);if(e){*error=OMPosix(e,@"spawn actions");break;}aOK=YES;
  e=posix_spawnattr_init(&attr);if(e){*error=OMPosix(e,@"spawn attributes");break;}tOK=YES;
  e=posix_spawn_file_actions_adddup2(&actions,p[0][0],STDIN_FILENO);
  if(!e)e=posix_spawn_file_actions_adddup2(&actions,p[1][1],STDOUT_FILENO);
  if(!e)e=posix_spawn_file_actions_adddup2(&actions,p[2][1],STDERR_FILENO);
  for(int i=0;i<3 && !e;i++)for(int j=0;j<2 && !e;j++)e=posix_spawn_file_actions_addclose(&actions,p[i][j]);
  // iOS does not expose addchdir_np; thread/start receives this host's empty directory.
  if(!e)e=posix_spawnattr_setpgroup(&attr,0);
  if(!e)e=posix_spawnattr_setflags(&attr,POSIX_SPAWN_SETPGROUP);
  if(e){*error=OMPosix(e,@"spawn setup");break;}
  NSArray *env=@[[NSString stringWithFormat:@"HOME=%@/home",root],[NSString stringWithFormat:@"CODEX_HOME=%@/home/.codex",root],[NSString stringWithFormat:@"TMPDIR=%@/tmp",root],[NSString stringWithFormat:@"CODEX_CA_CERTIFICATE=%@/ca-certificates.crt",runtime],[NSString stringWithFormat:@"SSL_CERT_FILE=%@/ca-certificates.crt",runtime],@"PATH=/usr/bin:/bin",@"LANG=en_US.UTF-8",@"TERM=dumb",@"NO_COLOR=1"];
  envp=calloc(env.count+1,sizeof(char *));if(!envp){*error=OMPosix(ENOMEM,@"environment");break;}
  BOOL allocated=YES;for(NSUInteger i=0;i<env.count;i++){envp[i]=strdup([env[i] UTF8String]);if(!envp[i])allocated=NO;}
  if(!allocated){*error=OMPosix(ENOMEM,@"environment");break;}
  NSString *exe=[runtime stringByAppendingPathComponent:@"codex"];
  char *argv[]={(char *)exe.fileSystemRepresentation,"app-server","--listen","stdio://",NULL};
  e=posix_spawn(&pid,exe.fileSystemRepresentation,&actions,&attr,argv,envp);
  if(e){pid=-1;*error=OMPosix(e,@"posix_spawn");break;}
  *inFD=p[0][1];p[0][1]=-1;*outFD=p[1][0];p[1][0]=-1;*errFD=p[2][0];p[2][0]=-1;
 }while(NO);
 if(envp){for(NSUInteger i=0;envp[i];i++){memset(envp[i],0,strlen(envp[i]));free(envp[i]);}free(envp);}
 if(aOK)posix_spawn_file_actions_destroy(&actions);if(tOK)posix_spawnattr_destroy(&attr);
 for(int i=0;i<3;i++)for(int j=0;j<2;j++)if(p[i][j]>=0)close(p[i][j]);
 return pid;
}
- (BOOL)writeLine:(NSData *)line fd:(int)fd {
 const uint8_t *p=line.bytes;NSUInteger pos=0;double until=OMNow()+3;
 while(pos<line.length && ![self shouldStop] && OMNow()<until){
  ssize_t n=write(fd,p+pos,line.length-pos);
  if(n>0){pos+=(NSUInteger)n;continue;}
  if(n<0 && errno==EINTR)continue;
  if(n<0 && (errno==EAGAIN || errno==EWOULDBLOCK)){struct pollfd poller={fd,POLLOUT,0};poll(&poller,1,50);continue;}
  return NO;
 }return pos==line.length;
}
- (BOOL)processLine:(const unsigned char *)bytes length:(size_t)length {
 NSData *data=[NSData dataWithBytes:bytes length:length];
 if(![[NSString alloc]initWithData:data encoding:NSUTF8StringEncoding]){[self diagnostic:@"Invalid UTF-8 from core; disconnected."];return NO;}
 id obj=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
 if(![obj isKindOfClass:NSDictionary.class]){[self diagnostic:@"Invalid NDJSON object; disconnected."];return NO;}
 NSDictionary *message=obj;
 if(message[@"method"] && message[@"id"]){
  // Never forward params/command text to the UI. No request is approved.
  [self diagnostic:@"Unexpected core request was refused; the core will stop."];return NO;
 }
 if(message[@"method"]){
  NSSet *events=[NSSet setWithArray:@[@"account/login/completed",@"account/updated",@"turn/started",@"turn/completed",@"item/started",@"item/completed",@"item/agentMessage/delta",@"error"]];
  if(![events containsObject:message[@"method"]])return YES;
 }else if((![message[@"id"] isKindOfClass:NSNumber.class] && ![message[@"id"] isKindOfClass:NSString.class]) || !(message[@"result"] || message[@"error"]))return NO;
 [self deliver:message];return YES;
}
static bool OMFramedLine(const unsigned char *p,size_t n,void *owner){return [(__bridge OMCodexProcessHost *)owner processLine:p length:n];}
- (void)runWithBundle:(NSBundle *)bundle completion:(void (^)(NSError *))completion {
 NSError *error=nil;NSString *runtime=nil;NSString *root=nil;
 int input=-1,output=-1,err=-1,status=0;pid_t pid=-1;BOOL reaped=NO,startReported=NO;
 OMCodexFramer framer={0};
 if([self validateRuntime:bundle runtime:&runtime error:&error])root=[self makeWorkspace:runtime error:&error];
 if(root && ![self shouldStop])pid=[self spawn:runtime workspace:root input:&input output:&output diagnostic:&err error:&error];
 if(pid>0){
  _workspace=root;self.workingDirectory=root;self.processIdentifier=pid;self.running=YES;
  startReported=YES;dispatch_async(dispatch_get_main_queue(),^{completion(nil);});
  [self diagnostic:@"Native core started. No credentials/configuration were imported."];
  if(!OMCodexFramerInit(&framer,2*1024*1024,64*1024*1024))error=OMPosix(ENOMEM,@"line buffer");
  while(!error && ![self shouldStop]){@autoreleasepool{
   // Only this worker reaps or signals. No delayed kill can race PID reuse.
   pid_t w=waitpid(pid,&status,WNOHANG);
   if(w==pid){reaped=YES;}
   else if(w<0 && errno!=EINTR){error=OMPosix(errno,@"waitpid");break;}
   [_lock lock];NSArray *queue=[_writes copy];[_writes removeAllObjects];[_lock unlock];
   for(NSDictionary *entry in queue){
    void (^cb)(NSError *)=entry[@"completion"];
    BOOL sent=!reaped && [self writeLine:entry[@"bytes"] fd:input];
    NSError *failure=sent?nil:OMError(10,@"Core pipe closed or write deadline exceeded");
    dispatch_async(dispatch_get_main_queue(),^{cb(failure);});
    if(!sent)error=failure;
   }
   struct pollfd fds[3]={{output,POLLIN,0},{err,POLLIN,0},{_wakeRead,POLLIN,0}};
   int ready=poll(fds,3,reaped?0:80);
   if(ready<0 && errno!=EINTR){error=OMPosix(errno,@"pipe poll");break;}
   if(fds[2].revents&POLLIN){char b[64];while(read(_wakeRead,b,sizeof(b))>0){}}
   if(output>=0 && ((fds[0].revents&(POLLIN|POLLHUP)) || reaped)){
    size_t drained=0;char bytes[8192];ssize_t n;
    while(drained<65536 && (n=read(output,bytes,sizeof(bytes)))>0){
     drained+=(size_t)n;
     if(!OMCodexFramerFeed(&framer,bytes,(size_t)n,OMFramedLine,(__bridge void *)self)){error=OMError(11,@"Invalid or oversized protocol output");break;}
    }
    if(drained==0 && (fds[0].revents&POLLHUP) && !reaped){error=OMError(12,@"Protocol pipe reached EOF");}
   }
   if(err>=0 && ((fds[1].revents&(POLLIN|POLLHUP)) || reaped)){
    // Count only: stderr may contain auth URLs or tokens split across chunks.
    size_t consumed=0;char discarded[4096];ssize_t n;
    while(consumed<65536 && (n=read(err,discarded,sizeof(discarded)))>0)consumed+=(size_t)n;
    if(consumed)[self diagnostic:@"Core emitted stderr; raw content omitted from export."];
   }
   if(reaped){
    // waitpid says the child is gone, but both pipes may still contain buffered
    // NDJSON. Drain to EOF/EAGAIN before leaving this worker; otherwise a final
    // item/completed or turn/completed can be lost.
    BOOL outDone=NO,errDone=NO;
    double drainDeadline=OMNow()+1.0;
    while((!outDone || !errDone) && OMNow()<drainDeadline && ![self shouldStop]){
     if(!outDone){
      char tail[8192];ssize_t n=-1;size_t budget=65536;
      while(budget && (n=read(output,tail,MIN(sizeof(tail),budget)))>0){
       budget-=(size_t)n;
       if(!OMCodexFramerFeed(&framer,tail,(size_t)n,OMFramedLine,(__bridge void *)self)){error=OMError(11,@"Invalid or oversized protocol output");outDone=YES;break;}
      }
      if(n==0 || (n<0 && errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR))outDone=YES;
     }
     if(!errDone){
      char tail[4096];ssize_t n=-1;size_t budget=65536;
      while(budget && (n=read(err,tail,MIN(sizeof(tail),budget)))>0)budget-=(size_t)n;
      if(n==0 || (n<0 && errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR))errDone=YES;
     }
     if(!outDone || !errDone){struct pollfd tailPoll[2]={{outDone?-1:output,POLLIN,0},{errDone?-1:err,POLLIN,0}};poll(tailPoll,2,50);}
    }
    if(!outDone || !errDone)[self diagnostic:@"Tail-drain deadline reached; residual pipe data was not treated as a successful protocol completion."];
    break;
   }
  }}
 }
 self.running=NO;
 if(input>=0){close(input);input=-1;}
 if(pid>0 && !reaped){
  // The child remains ours until reaped; there is no unrelated PID reuse window.
  kill(-pid,SIGTERM);double deadline=OMNow()+1.5;
  while(OMNow()<deadline){
   pid_t w=waitpid(pid,&status,WNOHANG);if(w==pid){reaped=YES;break;}
   if(w<0 && errno!=EINTR){pid=-1;break;}
   struct pollfd p={-1,0,0};poll(&p,0,40);
  }
  if(pid>0 && !reaped){
   kill(-pid,SIGKILL);pid_t w;do{w=waitpid(pid,&status,0);}while(w<0 && errno==EINTR);reaped=w==pid;
  }
 }
 if(output>=0)close(output);if(err>=0)close(err);
 if(framer.bytes && !OMCodexFramerFinish(&framer) && ![self shouldStop])[self diagnostic:@"Protocol stream ended without a complete final line."];
 OMCodexFramerDestroy(&framer);
 self.processIdentifier=0;self.workingDirectory=nil;
 if(root){NSError *cleanup=nil;if(![NSFileManager.defaultManager removeItemAtPath:root error:&cleanup])[self diagnostic:@"Workspace cleanup failed; ephemeral authentication was configured, but storage cleanup is unconfirmed."];}
 _workspace=nil;
 [_lock lock];
 NSArray *left=[_writes copy];[_writes removeAllObjects];
 if(_wakeRead>=0)close(_wakeRead);if(_wakeWrite>=0)close(_wakeWrite);_wakeRead=_wakeWrite=-1;
 _workerActive=NO;NSArray *stoppers=[_stoppers copy];[_stoppers removeAllObjects];[_lock unlock];
 NSError *failure=error?:OMError(13,@"Core stopped");
 dispatch_async(dispatch_get_main_queue(),^{
  if(!startReported)completion(failure);
  for(NSDictionary *entry in left){void (^cb)(NSError *)=entry[@"completion"];cb(failure);}
  if(startReported && self.terminationHandler)self.terminationHandler(reaped&&WIFEXITED(status)?WEXITSTATUS(status):-1,reaped&&WIFSIGNALED(status)?WTERMSIG(status):0);
  for(void (^stopper)(void) in stoppers)stopper();
 });
}
@end
