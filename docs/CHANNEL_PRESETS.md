# 日常渠道预设依据

当前内置 B 服，以及小米、应用宝、九游、华为、vivo 和 MuMu 六种渠道服。玩家只选择渠道，不填写包名或 Activity。用户提出的八种渠道中，OPPO 和 4399 仍缺完整清单证据，未加入；没有为了凑数推测字段。

| 渠道 | 包名 | 游戏主 Activity | 证据 |
|---|---|---|---|
| B服（哔哩哔哩） | `com.bilibili.priconne` | `com.bilibili.priconne.MainActivity` | [原有已验证绑定](https://game.bilibili.com/pcr/) |
| 小米渠道服 | `com.bilibili.priconne.mi` | `com.bilibili.priconne.MainActivity` | [官方分发 APK 清单](https://app.mi.com/details?id=com.bilibili.priconne.mi) |
| 应用宝渠道服（腾讯） | `com.tencent.tmgp.bilibili.priconne` | `com.bilibili.priconne.MainActivity` | [官方分发 APK 清单](https://sj.qq.com/appdetail/com.tencent.tmgp.bilibili.priconne) |
| 九游渠道服 | `com.bilibili.priconne.aligames` | `com.bilibili.priconne.MainActivity` | [官方分发 APK 清单](https://www.9game.cn/gongzhulianjieredi/) |
| 华为渠道服 | `com.bilibili.priconne.huawei` | `com.bilibili.priconne.MainActivity` | [镜像 APK 清单与相关项目记录](https://github.com/wbero/limingjie-assistant/blob/801db8bcbd970b573dd2e89cb9f2b0aab16789d8/android/docs/channel-servers.md) |
| vivo 渠道服 | `com.bilibili.priconne.vivo` | `com.bilibili.priconne.MainActivity` | [官方分发 APK 清单](https://game.vivo.com.cn/api/searchGame?word=%E5%85%AC%E4%B8%BB%E8%BF%9E%E7%BB%93&pageIndex=1) |
| MuMu 渠道服 | `com.bilibili.priconne.yofun.mumu` | `com.bilibili.priconne.MainActivity` | [官方分发 APK 清单](https://mumu.163.com/games/17891.html) |

新增六种渠道的 11.7.2 APK 清单均实际读取、检查 ZIP CRC 并解码。清单都声明游戏 Unity Activity 为 `com.bilibili.priconne.MainActivity`；启动入口另为 `com.bilibili.priconne.bili.SplashActivity`。日常检查采用游戏 Activity，不接管登录、支付或授权界面。B 服保留 `.MainActivity` 的兼容写法。

小米、应用宝、九游、vivo 和 MuMu 的 APK 来自对应渠道官方分发入口。华为的包名由相关项目的渠道表及其华为测试记录佐证；本次从[镜像安装包入口](https://www.ddooo.com/softdown/175582.htm)读取清单，核对包名、游戏 Activity 和版本。没有取得华为官网分发包，因此不声称华为样本来自官方，也没有完成整包签名认证。镜像仅供维护取证，不作为玩家安装建议。

本次与上一轮合计读取上述六份游戏 APK 的约 13.4 MB 清单及 ZIP 目录，没有安装或执行 APK，没有下载完整游戏包。每份清单的 SHA256、版本和来源类型见 [channel-presets-evidence.json](channel-presets-evidence.json)。静态清单证据不等于渠道登录或完整日常已实测。

OPPO 的相关项目记录包名为 `com.bilibili.priconne.nearme.gamecenter`。但本次找到的第三方“OPPO 版”下载实际返回九游的 `com.bilibili.priconne.aligames` APK，不据此补入。4399 官网及其游戏盒公开详情接口确认 `com.bilibili.priconne.m4399`，但网页下载要求游戏盒，公开详情中的下载入口尚未取得可读取的游戏清单。二者暂不出现在可运行预设中。

相关项目的[渠道说明](https://github.com/wbero/limingjie-assistant/blob/801db8bcbd970b573dd2e89cb9f2b0aab16789d8/android/docs/channel-servers.md)用于交叉核对包名与该项目报告的测试情况，没有复制其登录或协议实现。该项目的实测不计入 Maa-PCR 的验收。

旧 B 服设置保持兼容。未收录渠道不回退为 B 服，也不要求玩家手填。选择渠道不会迁移或改写旧账；设备、焦点和未知结果保护保持。此功能仅用于日常与扫荡，不添加自动登录，黎明界 bot 保持原流程。
