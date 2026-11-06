# 技术细节

记录这次逆向分析的关键结论，便于后续适配新版本。

## 1. 客户端结构

安装目录 `D:\HuyaClient` 只是外壳，真正运行的是更新缓存里的版本目录：

```
%APPDATA%\HuyaPCLive\HuyaUpdate\<版本>\Client\
    External\huyapc.exe        ← Electron 主程序（177MB，含 Chromium）
    External\resources\app.asar ← 界面代码（30MB）
    *.dll                       ← .NET 侧的支撑模块
```

`app.asar` 解包后约 1534 个文件、100MB：

```
out/main/index.jsc        主进程（V8 字节码，改不了）
out/preload/*.jsc         preload（字节码）
out/renderer/assets/*.js  界面逻辑（明文，可改）
```

## 2. asar 头部结构

虎牙用的 asar 比标准多嵌一层 Pickle：

```
+0   uint32  4
+4   uint32  header pickle 总长
+8   uint32  pickle payload 长
+12  uint32  header JSON 长
+16  header JSON
```

数据区起始 = `8 + header pickle 总长`。文件在数据区的偏移记录在 header 里，
所以**改动任何一个文件都必须保持字节数不变**，否则后续所有文件的偏移全部错位。

## 3. 完整性校验

`huyapc.exe` 的 fuse 配置：

```
RunAsNode                             ENABLED
EnableNodeOptionsEnvironmentVariable  ENABLED
EnableNodeCliInspectArguments         ENABLED
EnableEmbeddedAsarIntegrityValidation ENABLED
OnlyLoadAppFromAsar                   ENABLED
```

两级校验，缺一不可：

**第一级：单文件块哈希**

header 里每个文件带 `integrity`：

```json
{ "algorithm": "SHA256", "hash": "<整文件 hex>",
  "blockSize": 4194304, "blocks": ["<每块 hex>"] }
```

读文件时逐块比对，不匹配直接 `FATAL: asar_file_validator.cc(132)`。

**第二级：头部哈希**

`huyapc.exe` 的 PE 资源 `INTEGRITY` / `ELECTRONASAR` 存着：

```json
[{"file":"resources\\app.asar","alg":"SHA256","value":"<64 位 hex>"}]
```

这个 `value` 是 **header JSON 字符串本身的 sha256**（不含前面 16 字节的 Pickle 前缀）。
启动时比对，不一致则 `FATAL: asar_util.cc(163) Integrity check failed`。

> 注意：先改了文件内容但没更新头部 → 报 block 校验失败；
> 更新了头部但没更新 exe 资源 → 报 header 校验失败。两级报错信息不同，可用于定位。

## 4. 广告链路

### 4.1 业务接口收敛

渲染层的所有业务请求都经 `ConfigHelper` 的 `JsonReq(service, params)` 转发到主进程，
广告统一使用 `advertise@*` 服务名。穷举全部 74 个接口名，其中 10 个属于广告：

```
advertise@request              ← 主力：前贴片/卡片/小风车/主播投放都走这里
advertise@getAdvertiseMapExtInfo
advertise@updateAdvertise
advertise@report / landing
advertise@openAdReq / acquireMiniGame / setTrackSdkConfig
advertise@openWxGameAdv / openWxLoginWndRsp
```

因此只需在 `JsonReq` 入口加一个前置判断：

```js
return a && a[1]=='d' ? Promise.resolve('{}') : <原逻辑>
```

`a[1]=='d'` 精确命中 `advertise@`，且不影响 `app@deviceId`（第二个字符是 `p`）。

### 4.2 弹窗

弹窗有两条独立链路：

**队列式**（`AppletWebView` 包）：

```
收到弹窗通知 → 弹窗进队 → handleShowNextNormalPopup → showPopup → doShowPopup → doShowNormalPopup
```

服务端一次下发 85 条弹窗配置，本地按频控/优先级排队。

**房间内分发**（`WatchIndex` 包）：

```js
function iW(e) {
  const type = e['eType'], handler = dW.get(type);   // dW 是 eType → 处理函数
  if (handler) { ... du('showPopup success for popup type:' + type) }
}
```

两条链路都要堵，否则「队列那条堵住了、房间这条还能弹」。

### 4.3 排查陷阱

日志里的字符串在压缩代码中空格写作 `\x20`，例如 `'showPopup\x20success'`。
按普通空格搜索会一无所获，容易误判成「这个逻辑不在渲染层」。

## 5. 约束与替代方案

为什么不用这些更"简单"的办法：

| 方案 | 为什么不行 |
|---|---|
| hosts / DNS 屏蔽 | 广告与业务共用 `hd.huya.com` 网关，按域封会把登录、礼物一起打死 |
| `NODE_OPTIONS=--require` 注入 | 打包后的 Electron 拒绝（`Most NODE_OPTIONs are not supported in packaged apps`） |
| 解包成 `app/` 目录 | `OnlyLoadAppFromAsar` 禁止从目录加载 |
| 窗口看门狗 | 治标不治本，且可能误关正常窗口 |
| 本地 MITM 代理 | 需装根证书、影响全局网络，代价过高 |

## 6. 升级适配

新版本若报「未匹配到特征片段」，按以下顺序排查：

1. 解包新 `app.asar`，确认 `ConfigHelper-*.js` / `AppletWebView-*.js` / `WatchIndex-*.js` 的文件名哈希是否变了（文件名哈希变化不影响，脚本按前缀匹配）
2. 在对应文件里重新定位目标函数，替换 `patches.json` 中的 `from` 片段
3. 用 `--check` 验证规则命中，再正式打补丁
4. 启动后看 `%APPDATA%\HuyaPCLive\log\<日期>\Client\Time*_Pid*\h5PopupMgr.log`，
   确认 `showPopup success` 计数为 0
