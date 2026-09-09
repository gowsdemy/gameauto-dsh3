# GM-All-In-One · GameMale 论坛签到（本地版）

> **本仓库是对 [Highboed/GM-All-In-One](https://github.com/Highboed/GM-All-In-One) 的修改/衍生版本。**
> 原项目是**纯 GitHub Actions 云端自动签到**；本版本因论坛新增了 **Cloudflare 人机验证**（会拦截云数据中心 IP），
> 改为 **在你的 Windows 电脑上、用真实 Edge 浏览器本地运行**。签到/抽奖/互动/资产记录/邮件等核心功能保持一致。

---

## 它能做什么

- **全日常覆盖**：自动完成基础签到、插件抽奖、空间串门(3次)、打招呼(3次)、日志表态(10次)、你画我猜出题。
- **资产看板与对比**：抓取金币、血液、旅程、追随、知识、咒术、堕落、灵魂，自动和昨日金币对比。
- **可选邮件通知**：把当天结果发到你的邮箱（邮箱留空则自动跳过发信）。
- 全部走论坛底层接口，快且稳定；`config.env` 不提交、不上传。

---

## 快速上手（本地运行）

**需要**：Windows 电脑 + 已安装 Python 3.10+（[官网下载](https://www.python.org/downloads/)，安装时勾选 *Add Python to PATH*）。
浏览器方面，脚本会**自动检测 Edge / Chrome**（Chromium 系都行）；只要装了其中任意一个即可，无需手动配置。

1. 下载/解压本仓库到不含中文、空格的路径，如 `C:\gameauto`。
2. 复制 `config.env.example` 为 `config.env`，用记事本填：
   ```
   USERNAME=你的论坛用户名
   PASSWORD=你的论坛登录密码
   ```
   邮件可选：`SMTP_HOST` / `MAIL_USER` / `MAIL_PASS` 留空则不发邮件。
3. 双击 `run_local.bat`。首次会自动装依赖（较慢），之后弹出真实 Edge 窗口；
   **若出现"请进行人机验证"请点一下验证框**，看到"所有作业同步执行完毕"即成功。
4. （推荐）用 Windows「任务计划程序」每天自动运行 `run_local.bat`，方法见下。

**每天自动运行**：任务计划程序 → 创建任务 → 触发器设每天时间 → 操作选 `cmd`，
参数填 `/c "C:\gameauto\run_local.bat"`（换成你的路径）→ 条件里取消勾选"使用交流电源时才启动"。

**拿邮箱授权码**（不是邮箱登录密码）：QQ/163 邮箱网页版 → 设置 → 开启 SMTP 服务 → 生成授权码。

---

## 变更说明（相对原仓库）

| 位置 | 说明 |
| --- | --- |
| 运行方式 | 云(GitHub Actions) → **本地 + 真实 Edge**（绕过 Cloudflare 对数据中心 IP 的拦截） |
| 账号配置 | 用仓库 Secrets → **本地 `config.env`** |
| 定时触发 | Actions 定时任务 → **Windows 任务计划程序** |
| 其它 | 邮箱可留空跳过；脚本自动探测本机 Edge；主逻辑与功能不变 |

---

## 致谢

思路与核心逻辑源自原仓库及社区开发者：感谢 **@Highboed**（原项目）、**@thh866**（Actions 触发流与部署思路）、
**exact-emote-granny/Gizmo**（日志脱敏与每日抽奖模块启发）。

> ⚠️ `config.env` 含你的真实账号，请勿上传或分享。
