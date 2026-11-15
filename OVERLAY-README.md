# OpenMinis Codex overlay（源码草案）

针对 OpenMinis 固定 commit `1a71df0f381cefde80bf912b723de6f9831bc177`。目标 iOS16.5/TrollStore。此 overlay 未修改 `/var/minis/workspace/openminis-research/src/ios`，未安装或启动 Codex，不含用户二进制、密钥、auth.json 或会话数据。

## overlay 内容

- `OMCodexFramer.h/.c`：有限长度/总量、CRLF、跨 chunk 和逐字节 UTF-8 NDJSON 分帧。
- `OMCodexProcessHost.h/.m`：bundle 内 ARM64 iOS Mach-O 校验、固定 CA 摘要、独立临时 workspace、三管道、白名单环境、出站 RPC allowlist、意外请求 fail-closed、进程组终止/单 owner waitpid。
- `CodexProtocol.swift`：Codex 事件/状态、官方 OAuth URL 校验、错误脱敏。
- `CodexSessionEngine.swift`：独立 engine 状态机和固定 app-server RPC 映射。
- `CodexSessionView.swift`：iOS16 SwiftUI standalone 页面和 App 内系统 Safari OAuth。
- `CodexEngineResources/text-only.toml`：ephemeral认证、MCP/shell/图像/目录发现等功能禁用。用户授权的 codex Mach-O 和 CA 不随 overlay 公开。
- `tests/test_framer.c` 和 `tests/swift-fixture/CodexSessionEngineTests.swift`：C 可本机运行；Swift XCTest 仅供 Mac/Xcode 加入 XCTest target。

## 本机验证结果

```text
OMCodexProcessHost.m: clang -target arm64-apple-ios16.5 -fsyntax-only PASSED
OMCodexFramer.c: iOS16.5 ARM64 object compile PASSED
OMCodexFramer C runtime unit test: PASSED (executed as shared ELF via Python ctypes)
text-only.toml: Python tomllib parser PASSED
Swift: NOT TYPECHECKED (本机无 swift/swiftc/Xcode)
OpenMinis PBX/Xcode build: NOT PERFORMED
TrollStore/iOS device: NOT INSTALLED OR TESTED
```

Swift test fixture记录了 initialize、OAuth URL/loginId、thread/turn隔离参数、delta/final、interrupt、迟到回调、超时与后台停止；没有 XCTest 跑测结果。

## Xcode 应用步骤

```sh
python3 scripts/apply_overlay.py --repo /path/to/OpenMinis --runtime /path/to/authorized/CodexRuntime
```

仅dry-run。生产应用脚本会校验commit `1a71df0f381cefde80bf912b723de6f9831bc177` 和 iOS16主target；默认不执行修改。Mac/Xcode 人工审阅确认后：

1. 将 `overlay/src/ios/CodexEngine` 代码文件加入 **Minis主App target**，并仅添加主 App 需要的系统 framework。
2. 把 bridging header `#import "CodexEngine/OMCodexProcessHost.h"` 加到主target现有 `MinisApp-Bridging-Header.h`。
3. 将经用户授权的 `CodexRuntime/codex`、`ca-certificates.crt`、`text-only.toml` 放到独立 bundle Resources subgroup，不放进 iSH rootfs。
4. 在 `ContentView` 工具菜单增加“Codex 独立会话”，sheet 显示 `CodexSessionView()`；不能调用 `AIChatViewModel.runAgentLoop`。打开前必须由 UI 显式用户操作。
5. 添加 `CodexSessionEngineTests.swift` 到独立 XCTest target，先纯 mock 验证 Swift 协议；接着完整依赖 build、App install、TrollStore 真机测试。
6. LICENSE/第三方 notices 与修改源码/分发义务单独核对。

不要因Theos SDK名为iPhoneOS16.5就认为其等同完整Xcode SDK；它元数据为16.4。本机没有macOS、Swift、Xcode和OpenMinis native deps，因此无法生成可信OpenMinis TIPA。现有 Codex Pocket 保持不动。
