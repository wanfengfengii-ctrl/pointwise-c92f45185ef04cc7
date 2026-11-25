# 应变窗的主备签报交接

白班从源库发行一件应变窗，随后主复核机和备用机可各持有相同字节。两台复核机分别保存自己的 SQLite 归档，值班员传递应用交出的消息。发件源站在交接和签报期间可以关闭。以下主机和备机是角色，实际站名取自发行时的绑定。每个站名对应一个独立归档，重开使用该站原库。

## 固定件

`publication.issue.issue_handoff(source, recording_id, edition_id, window, primary_id, standby_id)` 返回原 `IssuedPacket` 类型。站名为不同的非空 Unicode 文本。V2 固定件沿用 V1 的 UTF-8 JSON 包、原帧和窗首解释材料，外层 `version` 为整数 `2`，另有 `primary_id`、`standby_id`。`publication.store.load_packet(source, packet_id)` 按发行号取回原字节。资格作用于这一发行件，另一次发行有自己的包号。原 `issue` 仍发行 V1。各版走各自的签报入口，错版以 `ValueError` 拒绝。

## 各机的操作

用原 `archive.commit.initialize_archive(db)` 初始化归档，在 `archive.handoff.bind_station(db, station_id)` 持久登记本机名。重开可用同名再登记，改绑报 `ValueError`。写入口沿用空闲 SQLite 连接的调用约定。

以下入口都在 `archive.handoff`，其中 `payload` 是 V2 整件原字节。

| 调用 | 业务动作 |
| --- | --- |
| `begin(db, payload)` | 主机发起该件的一次转交。正式签存过的主机不能首次发起。 |
| `withdraw(db, payload)` | 主机对已发起的转交申请撤回。 |
| `takeover(db, payload)` | 转交发起后，备机申请接手。值班员可能先拿到固定件，后收到首条交接消息。 |
| `receive(db, payload, mail)` | 向本机投递一封交接消息。 |
| `state(db, payload)` | 查本机已知的裁决状态和重开后可继续投递的消息。 |

五个入口均返回 `(decision, outgoing)`。`decision` 取 `initial`、`pending`、`withdrawn`、`transferred`，表示本机尚未办理、等待证据、撤回胜出、接手胜出。`outgoing` 是由本机交出的消息元组，可以为空。每封 `mail` 为 `(sender_id, recipient_id, body)`，`body` 为非空 `bytes`，正文由应用定义。交接员提供可信的发件、收件站名，按原样投递正文。消息必须针对同一发行号和该件的确切字节，核对本机、对端和消息的发送角色。无效固定件、错件、错站、非法消息或非法操作以 `ValueError` 拒绝，既有办理状态保持原样。

发起生效后主机立即停签，直到确认撤回胜出。备机只在取得持久的接手资格后才能签。已生效的接手即使还没签报，也排除撤回。未决时可报告 `pending`。重开后调用 `state` 可取回续办所需的消息，也可重试原动作。允许再次返回已经投过的消息。真实旧消息迟到或重复均不可改变已经确定的终局。投递齐全并继续传完由接件产生的消息后，两机查到同一终局。两项请求都在途且未裁决时，契约不按交接员的点击时间指定胜方。判定了终局的胜方可以关掉对端库并直接签报。

## 签存与领取

`review.sign.review_handoff(db, payload)` 返回三元组。成功为 `('signed', station_id, archived)`，`archived` 使用原 `ArchivedReport` 类型；尚待裁决为 `('pending', None, None)`；本机没有签报权为 `('ineligible', None, None)`。未发起时主机可签，备机无资格；已进入未决状态的机器不能签；撤回由主机签，接手由备机签。非绑定站点和非法固定件报 `ValueError`。

资格检查和核销、实际签报机、冻结报告及成功回执同次在本库提交。SQLite 异常照常返回，提交失败后保留签前资格和历史。已经成功的件原样重投复领首份成果。报告及回执引用同一 V2 发行号，读数、合计、饱和账及原拍出处沿用 V1 规则。`archive.lookup.read_handoff(db, report_id)` 返回 `(station_id, archived)`，未知号或普通报告号返回 `None`。普通件、旧报告、原入口和原回归继续使用。

## 样例与运行

`handoff_case.py` 提供两台独立归档的开库函数、同一冻结勘误版的样例源、透明消息队列、提交拦截和失答模拟。`python3 -B handoff_case.py` 可查看普通件交给两台机器后的原有结果。样例在第 10 拍更正为 240 后发行 20–40 闭窗。签报明细依次为第 20 拍 255、第 40 拍 252，合计 507，仅第 20 拍饱和，出处分别是 `rig-07/run-004/B`、`rig-07/run-004/C`。

新旧联动核验覆盖初始主备资格、未决时重开、撤回胜出后的接手迟件、接手胜出但尚无报告时的撤回、双方消息乱序及重投、错件或错站拒绝，以及胜方提交失败和成功失答后的复领。两种胜序分别使用新发行件，终局胜方签报时断开源库和对端库。每个成功样例两机合计只有一份正式报告和一份成功回执，并可按本库原报告号领回上述明细与实际签报机。

交付时接续 `.pointwise-runtime.json`。保留 `version` 为 `1`、`kind` 为 `cli`，`setup` 为参数数组的列表，`verify` 和 `demo` 各为一条命令的参数数组。`verify` 运行原回归和新交接核验，失败退出非零。`demo` 展示两种终局及源站关闭后重开领取的实际成果。
