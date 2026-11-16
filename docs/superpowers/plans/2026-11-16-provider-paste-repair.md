# 服务商粘贴修复实施计划（已批准）

用户已明确允许本次 UI 修复及第三次标准 Mac 构建。范围遵循 specs/2026-11-16-provider-paste-repair.md，原研究树、Codex Pocket、核心/CA/云同步保护保持不变。

1. 独立实现 OMProviderPasteRow：系统 UIPasteControl → 受生命周期/字段ID/操作代次保护的文本接收 → 当前 Binding；不直接读取 UIPasteboard、不新增 Key 导出。补合成传输 XCTest，所有反馈不含剪贴板值。
2. 固定上游两个服务商页的 SHA；构建副本只在 API Key 和 Base URL 下增加对应 Row，保持原输入控件与保存/显隐/协议逻辑。源码不匹配、重复应用时拒绝。与既有同步保护一起在任何写入前预检。
3. 将新 Swift 源加入同一确定性 PBX/Apple 验证工程清单。测试代码作用域、行位置和身份参数、字段保存代码未改、失败前不写文件、现有同步和签名测试。
4. 继续使用固定 Go1.25.5、手动 workflow_dispatch 和标准 macos-26。更新公开文件摘要清单后推送；只启动一次第三次 Mac 构建。不上传任何本地录像、Core、CA 或凭据，不购买 runner。
5. 构建成功后下载真实 App，验证 build2、同一独立ID及 iOS16.5；本机使用原授权 runtime 和 ldid-ish，生成不覆盖旧包的新IPA；复核代码、资源和不同 entitlement。Swift 测试若只编译、不执行，报告必须明确。
6. 交付新IPA、对应源码/版本与系统Paste复测说明；失败时按具体编译日志修复但不启动未经授权的第四次构建。真机粘贴结果始终待用户复测，不以编译成功代替。
