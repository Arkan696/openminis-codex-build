# OpenMinis Codex · 服务商页粘贴故障修复规格

## 已有证据
用户已收到真实 OpenMinis＋Codex IPA（191741260 bytes，bundle local.openminis.codex）。用户本地录屏显示原生「配置OpenAI」页面可打开，API Key / Custom API Base 的粘贴操作没有可见成功结果。不是 Codex Pocket、不是独立 Codex 会话页。未读取设备剪贴板，未见实际 Key 内容，不能将故障根因确定为第三方键盘或粘贴权限。

## 推荐方案与范围
只依赖现有长按菜单无法给出错误原因；推荐为添加与编辑服务商页的 API Key 和 Custom API Base 加入 iOS16 原生 UIPasteControl（系统 Paste），以 NSItemProvider 接收文本并显式写入当前 SwiftUI Binding。保留原 TextField / SecureField、oneTimeCode、显隐、自动 /v1、保存和协议选择逻辑，不修改 Codex host、认证、CA、App Group 或云同步保护。

每个字段提供明确接收状态/失败提示，提示不包含 Key、URL全文或剪贴板内容。只在明确用户粘贴操作后处理文本；不在 onAppear/轮询时读取 UIPasteboard，不从 Keychain 读取值来测试。秘钥粘贴不增加自动公开/复制动作。异步接收必须绑定目标字段身份和操作代次；页面关闭、切换服务商、更新输入后旧回调不得覆盖新值。不会改动聊天输入或全 App 的 UIResponder 方法。

## 构建与交付
源代码补丁只应用到新的固定上游构建副本，原研究树和 Codex Pocket 保持不变。用户已明确批准一次标准 GitHub macos-26 构建（第三次任务）；仍只上传公开源码与本次原创UI修复，不上传录像、帧图、手机数据、Codex核心、CA、Key或OAuth信息。不购买 runner/额度，遇计费要求停止。完成后下载真实 App，本机加入同一授权核心并重新签名打包。新IPA独立文件，不覆盖旧文件；安装时保留 local.openminis.codex / group.local.openminis.codex 以供覆盖更新。

## 验证及验收
验证补丁可应用、目标字段正确绑定、敏感输入不回显、过期回调拒绝、无自动剪贴板读取；真实Apple编译与打包后核对代码/资源hash和权限。脚本守卫和模拟NSItemProvider不等同真机剪贴板通过。用户需复测系统 Paste 与普通文本/Key输入，失败时仅提供状态文字或脱敏日志。不宣称已解决或键盘兼容，直至新的真机结果。
