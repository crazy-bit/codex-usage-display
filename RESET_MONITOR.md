# 免费重置监控

数据来源：[codex-reset.com](https://codex-reset.com/)，[接口说明](https://codex-reset.com/developers)。通过该站整理的 Tibo 消息工作，不需要 X 官方 API 或 Token。请求 User-Agent 携带项目仓库地址。

后台默认每 300 秒读取 forecast 和 timeline，最低间隔 60 秒，遵守 Retry-After。桌面“重置通知设置”列表展示预告与已确认记录，每 2 秒刷新本地数据，网络仍按设置间隔读取。

## 屏幕分数

有有效 Tibo 官方账号消息且 signal_percent 有效时，显示网站主分数，标记 TIBO SIGNAL / RESET PLANNED；它是预告信号分数，不等同于历史 48 小时概率。否则显示 rounded_48h，标记 48H RESET / estimate。数据源超过 30 分钟或设备超过三个同步周期未收到数据时，分数显示 --%。连续三个周期未同步时右上角显示 OFFLINE，默认 180 秒，恢复后清除。

## 可信通知

仅信任链接为 x.com/thsottiaux/status/数字 或 twitter.com/thsottiaux/status/数字 的重置相关预告。通过免费站点获取，并非直接连接 X。近 24 小时的预告播报“收到重置预告，请查看通知”，已有预告在首次启用此规则时也提醒一次。明确完成措辞（如 Done / all propagated）或站点已确认重置，播报“干活啦”。预告和完成分别持久去重；预告不推进已完成事件去重记录。首次启动仍不补播历史已确认重置。

设备离线保留通知至源消息发布后 24 小时，到期丢弃；BLE ACK 后移除，设备 NVS 防止同一事件重播。语音开关控制所有自动播报。预告不改写账户剩余用量，也不消耗手动完全重置权益。

## 个人额度与完全重置

NEXT RESET（UTC+8）来自 Codex 当前窗口 resetsAt；FULL RESET 来自本地 account/rateLimits/read 的 rateLimitResetCredits.credits，显示可用、未过期的 codexRateLimits 权益数及最近到期时间。仅读取，不执行重置。屏幕不显示来源文字，桌面设置保留来源链接。

配置与状态保存在 reset-settings.json、reset-state.json、reset-status.json 和 forecast-status.json，不进入仓库。
