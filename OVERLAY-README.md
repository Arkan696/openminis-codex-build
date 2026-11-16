# OpenMinis Codex 源码补丁 v0.2.2 · 独立 Apple 验证工程

v0.2.2 在 v0.2.1 基础上重新生成验证摘要并修正授权页面竞态：关闭回调携带页面 UUID，旧 Safari 页面迟到关闭不会取消新登录；generic sheet dismiss 没有页面身份，不再触发取消。新增的 Swift 回归用例和26项本地工具测试均已记录，但**没有运行 Xcode 或 Swift 编译器**；入口和结果解释见 [Apple 验证说明](APPLE-VALIDATION.md)。旧源码包保留，不被覆盖。

目标：iOS16.5 / TrollStore；固定 OpenMinis commit `1a71df0f381cefde80bf912b723de6f9831bc177`。用户已批准独立 CodexSessionEngine 架构。该版本有实际源代码、工程补丁和测试，但**不是 IPA/TIPA，也没有完成 Swift/Xcode 编译或真机验证**。

第一切片在 OpenMinis 侧栏工具菜单增加“Codex 独立会话”，使用独立 SwiftUI 页面和 app-server，不进入 OpenMinis 的 `runAgentLoop`，不迁移其消息数据库/凭据。仅手动连接和发送；关闭页面、进入后台、超时会停止当前核心。OAuth 在系统 Safari 页面完成，账户以核心响应确认。

## 本轮完成

- `patches/openminis-codex-integration.patch`：五个编译源（3 Swift、1 ObjC、1 C）、两个头文件引用、CodexRuntime 文件夹资源、bridge import、统一 ToolSheet 菜单入口。头文件不进入 Sources 编译列表。新增 PBX ID 是确定性24位十六进制，解析后确认仅修改主目标相关的三个对象；原有签名、Bundle ID、deployment target、provider配置和扩展配置保持不变。
- `scripts/apply_overlay.py`：统一应用入口。默认 dry-run，拒绝错误提交、脏工作区、已有引擎目录、上游关键文件哈希不符；先准备独立目录，再应用同一份工程补丁，失败回滚本工具写入的内容，不覆盖并发用户修改。`apply_ui_overlay.py` 仅为同一入口的兼容别名。不要先应用旧的 `openminis-codex-ui.patch` 再应用完整补丁，完整补丁已经包含UI修改。
- `scripts/prepare_codex_runtime.py`：本地流式读取用户授权 deb，只提取两个指定的普通 Mach-O 文件，拒绝链接/重复/超大/路径穿越/错误设备平台；校验固定公开 Mozilla CA、使用审阅过的 ephemeral 配置；成功后原子发布输出目录，失败不留下看似完整的 runtime。不下载、不上传、不运行核心，不提取 deb 中的配置或 auth.json。
- `OMCodexProcessHost`：单owner进程、固定RPC参数allowlist、独立HOME/CODEX_HOME、NDJSON三管道、未实现请求断开、停止清理；配置和CA摘要检查，尾部排空有时限，父端管道标志在spawn前完成检查。底层签名是否有效仍由单独验收判断。
- `tests/test_overlay_tools.py`：13项 Python/Git/PBX/合成deb 实际测试；`tests/test_apple_validation_tools.py`：13项生成器/失败报告及源码守卫测试，合计26项通过。`overlay/tests/test_framer.c`通过本机shared-ELF/ctypes运行；16个Swift XCTest用例已写，**未执行**。新增的授权竞态Swift用例不能用Python源码守卫代替。
- `scripts/apple_validation_project.py` / `verify_apple.py`：在独立目录生成无runtime的UIKit测试宿主，真实生产源码原样编译，XCTest通过模块导入验证。默认只预览；实际执行要求macOS/Xcode。`test`只接收用户指定的已启动iOS模拟器，核对xcresult测试数，零测试/缺结果不算成功。不触碰真机或正式App。
- 子进程新增`POSIX_SPAWN_CLOEXEC_DEFAULT`，避免继承宿主无关的打开文件；仍不把工作目录配置当作内核安全沙箱。

## 验证事实

| 项目 | 结果 |
|---|---|
| ObjC iOS16.5 `-Werror -fsyntax-only` | 通过 |
| C iOS16.5 ARM64 object编译、本机分帧执行 | 通过 |
| ephemeral profile TOML和摘要 | 通过 |
| PBX解析、只增加正确主target编译/资源项 | 通过 |
| Git应用、dry-run不写入、失败回滚、重复应用拒绝 | 通过 |
| 合成deb提取/错误/链接/平台/配置拒绝 | 通过 |
| Swift typecheck / XCTest | 未进行 |
| Xcode打开、OpenMinis依赖/全App构建 | 未进行 |
| Runtime签名认证、Codex核心执行、真机测试 | 未进行 |
| IPA/TIPA生成 | 未进行 |

详情见 `VERIFICATION.json`。PBX解析不等于Xcode构建，合成56字节Mach-O测试文件不包含可运行核心。现有 OpenMinis 研究树与 Codex Pocket 均未改动。

## 本地验证复现

Python需要3.11+；测试额外依赖纯Python解析器：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python scripts/verify_local.py \
  --baseline /path/to/audited/OpenMinis \
  --ca-bundle /path/to/public/mozilla-ca.pem \
  --sdk /path/to/iPhoneOS.sdk --cc clang
```

这里需要能交叉编译iOS的Clang和SDK；手机本次使用clang19/Theos iPhoneOS16.5目录（元数据16.4）。不会生成可执行iOS App。测试日志写入build/，不得从日志推断真实账户或端点已通过。

## 应用与Mac验收

先在完整、干净的固定提交checkout上预览，源码应用不需要runtime：

```sh
python3 scripts/apply_overlay.py --repo /path/to/OpenMinis
python3 scripts/apply_overlay.py --repo /path/to/OpenMinis --apply
```

源码模式会放置profile但不包含Codex，连接会明确拒绝。若有已授权本地runtime，可在应用时添加 `--runtime /path/to/CodexRuntime`。必须在应用前准备runtime，不能重复覆盖已应用的目录。

```sh
python3 scripts/prepare_codex_runtime.py \
  --deb /path/to/user-supplied.deb \
  --ca-bundle /path/to/public/mozilla-ca.pem \
  --out /path/to/local/CodexRuntime --authorized
```

`--authorized`只确认本地使用，不证明再分发许可。Mozilla CA固定摘要 `427c064216761a1e72a8b30f0d954d08cb6e4435a82091fb865afd20c8497285`；profile摘要由宿主固定，修改profile后必须重新审阅和同步。

**安装前尚需**：先构建原OpenMinis和原生依赖，设置独立测试App/扩展身份与app groups，避免覆盖现有Minis；审阅TrollStore签名/嵌套Mach-O权限与框架链接；运行Swift类型检查及XCTest，再测版本/握手/OAuth/文本回合/取消/后台/断网。工具MCP、Shell和文件修改不在本切片范围。不能凭Codex配置保证整个高权限进程的内核级隔离。

## 许可与边界

本补丁原创代码按文件SPDX声明使用 GPL-3.0-or-later，随包保留GPLv3文本。OpenMinis及各依赖的原有版权与许可义务继续适用。源码包不含完整OpenMinis仓库、Codex二进制、CA文件、用户令牌/密钥或SDK。真实用户包有高权限签名声明；Mach-O平台校验不等于签名认证或行为可信，不作重新分发许可判断。

没有Mac/Xcode时当前能交付的是这份可复验源码补丁包，不能把它称为完成移植或可安装版本。
