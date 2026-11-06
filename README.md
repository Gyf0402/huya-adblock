# 虎牙去广告

虎牙直播 PC 客户端（Electron 版）的**去广告补丁**。

仓库里不包含任何原程序文件，只有一套「特征匹配 + 等长替换」的补丁规则和一个应用脚本。
它对已安装的客户端做最小改动，并同步修复客户端的 ASAR 完整性校验，改完照常能启动。

> 当前适配版本：**7.90.3.0**（Electron 32 / Node 20）

---

## 效果

| 广告形态 | 处理方式 | 状态 |
|---|---|---|
| 首页/频道卡片广告、推荐位 | 广告接口短路 | ✅ |
| 前贴片 / 暂停广告 | 广告接口短路 | ✅ |
| 主播投放的小风车、播放器缩小+广告 | 广告接口短路 | ✅ |
| 游戏推广弹窗、活动弹窗 | 弹窗展示入口拦截 | ✅ |
| 房间内弹窗（`iW` 分发器） | 分发器处理器失效 | ✅ |

处理方式是**从源头不加载**，而不是「弹出来再关掉」，所以没有闪烁，也不会误关正常窗口。

---

## 为什么需要动 asar

虎牙 PC 端的界面跑在 Electron 里，业务代码全部打包在 `resources/app.asar`。
而客户端启用了两个 Electron fuse：

- `EnableEmbeddedAsarIntegrityValidation` —— 校验 asar 完整性
- `OnlyLoadAppFromAsar` —— 只允许从 asar 加载

直接改 `app.asar` 会被校验拦下并强制退出。因此补丁必须同时修两处校验信息：

1. `app.asar` 头部里被改文件的 `integrity.hash` / `blocks[]`
2. `huyapc.exe` 的 PE 资源 `INTEGRITY/ELECTRONASAR` 里的头部哈希

细节见 [docs/TECH.md](docs/TECH.md)。

---

## 快速开始

```bash
git clone https://github.com/Gyf0402/huya-adblock.git
cd huya-adblock
```

然后按下面的「使用」一节执行即可。

---

## 使用

### 前置

- Windows 10/11
- 已安装虎牙直播 PC 客户端
- Python 3.8+（`apply.cmd` 会自动找 `python`；装了 Node 会额外做语法校验）

### 打补丁

```
双击 apply.cmd
```

或手动：

```bash
python patch_asar.py            # 自动定位最新版本目录
python patch_asar.py --check    # 只检查规则是否匹配，不写入
python patch_asar.py --dir "C:\Users\你\AppData\Roaming\HuyaPCLive\HuyaUpdate\7.90.3.0\Client\External"
```

**打补丁前请完全退出虎牙直播**（含右下角托盘图标），否则文件被占用会失败。

脚本会自动备份原始 `app.asar` 与 `huyapc.exe` 到 `<客户端目录>\_noad_backup\`。

### 还原

```
双击 apply.cmd 后按提示，或：
python patch_asar.py --restore
```

### 客户端升级后

新版本会安装到新的版本目录，补丁需要重打一次 —— 再执行一次 `apply.cmd` 即可。
如果提示「未匹配到特征片段」，说明界面代码结构变了，需要更新 `patches.json` 里的规则。

---

## 补丁规则

所有改动都写在 [`patches.json`](patches.json) 里，三条规则，彼此独立：

| ID | 作用点 | 做法 |
|---|---|---|
| A | `ConfigHelper` 的 `JsonReq` 转发方法 | 命中 `advertise@*` 直接返回空对象 |
| B | `AppletWebView` 的 `PopupMgr.showPopup` | `popupInfo` 置空，走空值返回 |
| C | `WatchIndex` 的弹窗分发器 `iW` | 处理器注册表查找恒为空 |

三条都采用**等长替换**：替换后文件字节数与原来完全一致，因此归档内所有偏移不变，
不会破坏 asar 结构；补丁 A 需要的额外字节，靠把等价的 `obj['prop']` 改写成 `obj.prop` 腾出来。

---

## 已知边界

- 只处理**客户端界面层**的广告。视频内容本身、直播画面内的贴片由流媒体侧决定，不在范围内。
- 弹窗仍会从服务端下发并进入本地队列（日志里能看到「弹窗进队」），只是不再被绘制出来。
- 补丁会修改 `huyapc.exe` 的一个资源字符串，其 Authenticode 签名因此失效。不影响运行，但系统可能不再显示「已验证发布者」。
- 客户端强制更新、修复安装会覆盖补丁，需要重打。

---

## 免责声明

本项目仅供个人学习与自用，用于研究 Electron 应用打包与完整性校验机制。
请自行承担使用风险；请勿用于商业用途或二次分发修改后的客户端。
如果喜欢虎牙的内容，建议通过官方会员等方式支持。

---

## 致谢

思路参考了这些社区项目：

- [xinggsf/Adblock-Plus-Rule](https://github.com/xinggsf/Adblock-Plus-Rule) —— 虎牙规则段里的 `CommercialAdSdk`、专用广告域
- [flik1337/huyaCleaner](https://github.com/flik1337/huyaCleaner) —— 网页端广告位选择器
- [psa1K/huya-enhancer](https://github.com/psa1K/huya-enhancer) —— 屏蔽分组思路
- Greasyfork「虎牙直播净化」—— *屏蔽「主播为你推荐」全屏层会导致黑屏* 这条踩坑经验

## License

[MIT](LICENSE)
