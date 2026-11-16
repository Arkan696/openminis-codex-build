# 合并 OpenMinis + Codex 的 IPA 构建计划

目标仍是完整 OpenMinis（独立 CodexSessionEngine），iOS16.5/TrollStore、与现有App并存。用户已要求继续产出IPA，没有Mac或CI。执行目标没有改变；当前只准备可审阅的构建链，不把生成脚本当作已生成IPA。

## 可落实路线
1. macOS/Xcode机器从固定官方commit获取完整OpenMinis及原生子模块；应用已批准的Codex overlay。先编译隔离引擎工程，再构建iSH、LAME、FFmpeg、Alpine rootfs及rclone，最后构建arm64 iPhoneOS完整Minis目标。
2. 使用独立测试Bundle ID `local.openminis.codex`、app group `group.local.openminis.codex`、测试深链接 `miniscodex`。修改仅作用于新的构建副本，不覆盖现有Minis，不导入现有应用数据。iCloud等需要独立有效签名的服务不保证可用。
3. 云端只包含公开OpenMinis源码和本次原创overlay源码，不包含用户Codex、CA、API Key、OAuth凭据、SDK、手机文件或会话。云端产物为真实编译的无核心 `.app`、编译日志、公开源和entitlement清单；没有核心时Connect必须拒绝启动。
4. 下载真实 `.app` 后，本机用已存在工具注入用户授权iOS Codex运行时和固定CA，保留严格TLS，逐层ad-hoc签名，打包Payload/App.app为IPA；模式仍只支持TrollStore。IPA存在仅证明打包完成，安装和真机行为另测。

## 边界与授权
- 目前手机Alpine没有Swift/Xcode，也没有官方可用iOS预编译发布。因此不能在当前设备直接编译完整App，不能把Codex Pocket改名当成结果。
- 代码/工作流的本地准备在已请求IPA范围内；向GitHub发布新仓库、上传这些源码和运行Actions必须明确授权后再做。
- 只使用GitHub标准macOS runner；优先公开GPL源码仓库的免费Actions规则。若账号显示需付费或额度不足，停止，不购买或自动升级。
- 不上传用户二进制。官方依赖下载只发生在已授权的构建环境。失败时保留真实编译日志，不生成伪IPA。
- 不关闭证书验证，不绕过App Store加密；目前无合格预编译IPA，不走二进制注入路线。

## 验证
本地只检查构建脚本/工作流/命名空间变换/打包校验测试；必须保留`xcode_built=false`和`ipa_built=false`直到各步骤真实执行。不能把模拟Mach-O文件或mock subprocess作为App编译/签名成功证据。
