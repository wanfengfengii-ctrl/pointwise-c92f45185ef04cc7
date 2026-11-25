# 砺川台架录制归档

砺川金属台架检测室用这套 Python 3.11 标准库工具核对仪器导出的应变级别，向隔离复核机移交指定勘误版的一段录制，并保留原拍出处。源站和归档各使用自己的 SQLite 数据库。

## 日常交班

1. 白班仪器员调用 `capture.intake.ingest(source, recording_id, frames)` 接纳整卷。录制号为非空 Unicode 文本，`Frame(beat, kind, raw, origin)` 分别记录正拍号、帧型、一字节原码和非空出处。整数拍号须能存入 SQLite。接纳成功返回固定的 `Recording` 及原账，原账含有序逐拍值、合计和原饱和拍。
2. 质控员用 `revision.history.original_edition_id(recording_id)` 取得原版标识，调用 `revision.amend.amend(source, recording_id, parent_id, beat, value)` 登记单拍勘误。返回新修订号。指定版只取自己的父链，链上更近的记录覆盖同一个目标拍。
3. 交班员调用 `publication.issue.issue(source, recording_id, edition_id, ClosedWindow(start, end))`。端点可以落在录制中的跳号处。所选窗口须位于录制起止拍之间并至少命中一拍。`IssuedPacket` 含包号和可直接交接的字节，也可用 `publication.store.load_packet` 按实际包号读回同一字节。
4. 关闭源库后，夜班调用 `review.sign.review(archive, payload)`。成功返回 `ArchivedReport`，其中 `report.report_id` 是归档内的领取号，`report.content` 含包号、原卷、修订、窗口、原序明细、合计和饱和账，`receipt` 绑定同一报告号和父包。
5. 资料员重开归档后调用 `archive.lookup.read(archive, report_id)`，领取已固化内容和成功回执。未知号返回 `None`。后续勘误或者新签报不会改变旧号的内容。

## 计算与包格式

设备拍号严格递增且可以跳号。首帧为 `absolute`，原码按 0–255 解释。`delta` 的原码按单字节二补码解释为 −128 到 127。每拍相加后立即限幅到 0–255，得到下一拍使用的原录制状态。绝对帧重新定基。勘误只替换指定拍的复核值。0 级和将替代值设为 0 均有效。合计按整数级别直接求和，值等于 255 的拍以原拍号和原出处进入相应的饱和账。

固定包为 UTF-8 JSON，V1 外层包含 `version`、`packet_id`、`recording_id`、`edition_id`、`window`、`frames` 和 `content`。`window` 为两个闭端点。原序 `frames` 各项含 `beat`、`kind`、`raw` 和 `origin`，其中 `raw` 是两位十六进制。`content.amendments` 含窗内的 `[beat, value]` 对，合法旧包可省略该成员。差量起窗须有 `content.previous_level`，即紧邻窗首之前已限幅的原录制值。绝对起窗可直接解释。窗外原帧、原帧号、出处及勘误保留在源站。接纳校验包的结构和可解释性，包号用于归档追溯。

## 初始化与失败

源库连接依次调用 `capture.records.initialize_recordings`、`revision.history.initialize_editions` 和 `publication.store.initialize_packets`。归档连接调用 `archive.commit.initialize_archive`。初始化可重复执行并保留已有录制、修订、包、报告和回执。应用用空闲 `sqlite3.Connection` 调用写入口，入口负责提交，调用方负责关闭连接。

非法帧、重复录制号、无效父修订或勘误、倒置或无可用帧的窗口以及无法解释的包会引发 `ValueError`。SQLite 失败会以数据库异常返回。失败操作不会留下本次部分成果。报告、明细和成功回执同次提交，失败后可以重投同一固定包。每次成功签报得到本次报告号。

## 运行

- 完整正常回归运行 `python3 -B check_project.py`。标准入口由运行平台提供。
- 演示运行 `python3 -B demo.py`。演示用独立临时数据库，关闭并移除源库后签报，再重开归档按原号领取旧报告。

确定性样例在 `samples/recording.json`。拍号 10、20、40、50、70 分别携带绝对 250、差量 +10、差量 −3、绝对 10、差量 −20。原合计为 767。第 10 拍勘误为 240 后，20–40 窗复核为 255、252，合计 507，仅第 20 拍饱和。勘误前签出的 10–40 报告重开后仍为合计 757。`samples/legacy_packet.json` 是可直接交给复核入口的旧版内容样例。
