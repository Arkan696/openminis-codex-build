# v0.2：独立 Apple 编译和 mock XCTest 验证入口

当前已完成的是**工程生成与验证工具**，并不是 Swift 编译成功。`VERIFICATION.json` 中 `swift_typechecked`、`xctest_executed`、`apple_validation_project_built`、`app_or_tipa_built` 仍为 false。

## 为什么增加这个入口

完整 OpenMinis 先要求构建 iSH、FFmpeg、LAME、rclone 等，不能用这些依赖缺失掩盖 Codex overlay 本身的编译问题。这里生成一个独立的 UIKit 空白测试宿主和 XCTest target，直接编译 overlay 中**未改写的真实生产文件**：三个 Swift、一个 ObjC、一个 C，以及原生 bridging header；测试 target 以 `@testable import CodexOverlayValidation` 导入生产 Swift 模块。

宿主启动不创建 `CodexSessionEngine`，不打开 Codex 页面，不连接核心。所有 XCTest 使用 `FixtureTransport`，没有 CodexRuntime、API Key、Keychain、auth.json、签名 entitlement 或用户会话。不能用这类 mock 测试证明真实 OAuth/TLS/管道/后台行为成功。

## 在当前手机上能验证的内容

- 生成的 OpenStep PBX 工程可被解析，所有对象引用、app/test target 依赖、模块导入、test-host 路径、五个真实源文件和单独测试宿主的编译项完整。
- 共享 scheme 把 XCTest target 加入测试。
- 复制的生产源文件与当前 overlay 的 SHA-256 一致，不使用“假 Foundation/Combine”或 Python 重写状态机来冒充 Swift 编译。
- 默认命令只输出计划，不创建输出、不调用 xcodebuild；Linux 上 `--execute` 明确拒绝。
- 构建失败保留独立日志和 false 成功标记；Xcode 返回0但缺少 xcresult，或0个/少于预期/失败测试，不记录 XCTest 成功。
- v0.2.1 中 Apple 工具测试为13项，与原有13项 overlay 工具测试合计26项。它们不运行Swift/UIKit；编译产物和完整结果分支使用临时的模拟标记数据，不代表实际Apple编译。源码中的16项Swift XCTest仍未执行。

## 在有完整 Xcode 的 Mac 上执行

下列命令不上传任何文件，不使用云服务，不需要签名/开发者账号；每次使用一个**尚不存在**的新输出目录，避免覆盖旧证据。生成工程的app身份是 `local.openminis.codex.validation-host`，不能当正式TrollStore应用安装。

先预览（任何操作系统可运行）：

```sh
python3 scripts/verify_apple.py --out /tmp/codex-build-check --mode build
```

仅生成可在Xcode中打开的工程（不调用编译器）：

```sh
python3 scripts/apple_validation_project.py --out /tmp/codex-generated-project
```

真实编译全部生产源和测试源，iOS Simulator目标、不启动模拟器：

```sh
python3 scripts/verify_apple.py --out /tmp/codex-build-check --mode build --execute
```

这里调用 `xcodebuild build-for-testing`，Swift语言模式5、deployment16.5。它使用机器上安装的Apple SDK，不保证安装在iOS16.5设备上可运行。若编译错误，`xcode-1.log` 保留真实诊断；脚本不能修复或忽略错误。

执行16项mock XCTest需要你事先在Xcode打开一个可用的iOS Simulator。脚本只读列出booted设备验证指定UDID，不创建、启动、关闭模拟器，也不选真实iPhone。测试会把**无runtime的验证宿主**运行在该模拟器里：

```sh
xcrun simctl list devices booted
python3 scripts/verify_apple.py --out /tmp/codex-xctest-check \
  --mode test --simulator-id <已启动的iOS模拟器UDID> --execute
```

要求Xcode支持 `xcresulttool get test-results summary --path` 的JSON汇总（Xcode16+）。结果为 Passed 且 total/passed与当前 fixture test方法数一致、failed=0，才标记成功。预期方法数从源文件计算，不能把未运行的测试算作通过。

## 结果文件

- `VALIDATION-PROJECT.json`：生成时的生产源码/fixture摘要；单独生成工程不代表已执行。
- `xcode-1.log`：xcodebuild实际输出。
- `APPLE-VERIFICATION.json`：执行状态、工具版本、源码摘要、命令和分项成功标记；仅由真正的Mac执行路径生成，不改写本仓库 `VERIFICATION.json`。
- `MockTests.xcresult`：实际XCTest结果（只在test模式请求）。

`swift_sources_compiled=true`只说明生成工程中的源已通过Apple模拟器编译。`xctest_passed=true`只说明这些mock断言通过。`full_openminis_built`、`real_oauth_tested`、`physical_device_tested`、`codex_executed`、`tipa_built`始终单独保留false。这不是完整OpenMinis、iOS16.5真机或Codex核心验收。

## 当前Linux工具链核查

2026-11-16按官方Swift安装资料核查：未找到并验证可直接在当前Alpine3.21/musl/aarch64模拟内核运行的官方宿主Swift编译器。官方Ubuntu ARM64工具链面向glibc；静态Linux musl SDK是交叉目标SDK，仍需要可运行的匹配Swift宿主工具链。Combine也不能因Foundation支持Linux而假定存在。因此本次没有下载GB级工具链、安装glibc环境或把SDK当编译器，不作“本机Swift类型检查通过”的结论。

参考：[官方Linux安装](https://www.swift.org/install/linux/)、[静态Linux SDK文档](https://www.swift.org/documentation/articles/static-linux-getting-started.html)。页面/包未来可能变化，本次未验证内核系统调用兼容性。
