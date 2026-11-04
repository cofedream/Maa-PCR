# 登录令牌复用

## 使用方式

B 服首次登录成功并核对玩家 ID 后，程序自动保存加密的 `uid + access_key`。下次运行黎明界或公会战账号资料同步时优先复用，重开程序也有效。游戏运行在模拟器内，登录程序和加密存储运行在 Windows 上。

缓存位于安装目录的 `data/auth`，用当前 Windows 用户的 DPAPI 加密；不会保存密码、游戏 SID、请求流水或验证码证明。缓存绑定服务器、玩家 ID 和登录凭据。修改凭据、切换服务器或删除账号时清理旧缓存，凭据不匹配时不复用。缓存不进入诊断导出和发行包，更新时保留用户数据。

## 失效与验证

只有游戏 SDK 登录接口明确拒绝令牌，程序才清掉缓存并重新密码登录一次。网络故障、维护和玩家 ID 不符不会触发这次回退。缓存无法解密时按首次登录处理；加密不可用时仍可登录，但不保存明文。

服务器可随时撤销令牌或要求额外风控验证，届时仍需人工完成验证码。程序不后台保活、不定时登录，也不重发战斗、领取或消费请求。渠道服继续使用原 UID/access_key 模式。

## 实现参考

复用流程参考上游固定提交 `b809b196988b019571caf5679ccf8d7ebb7f072d` 的 [BilibiliNativeLoginCoordinator](https://github.com/wbero/limingjie-assistant/blob/b809b196988b019571caf5679ccf8d7ebb7f072d/android/app/src/main/java/com/landosol/toolbox/protocol/bilibili/BilibiliNativeLoginCoordinator.kt) 和 [SdkSessionStores](https://github.com/wbero/limingjie-assistant/blob/b809b196988b019571caf5679ccf8d7ebb7f072d/android/app/src/main/java/com/landosol/toolbox/security/SdkSessionStores.kt)。本项目共用 Python 登录客户端，存储采用 Windows DPAPI，无需在模拟器内安装辅助应用。
