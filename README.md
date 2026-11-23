# 滨川检测卷交班

滨川检测中心的仪器操作员提交有序轮次和读值，资料员核准某个修订，读数班和质控班分别领件。夜班只有隔离收件库，凭两件复原现场记录并签发报告。现场人员按报告号领取复检位置。

## 一天的交班

1. 登记仪器卷和原修订，保留每轮执行状态和探头顺序。
2. 有勘误时登记新修订，保留原修订和原值。
3. 指定核准的修订号，冻结完整快照并取得固定号。
4. 读数班和质控班各自凭固定号领一份 V1 列件。
5. 夜班关闭白班库后接纳列件。同件重送复领原收件记录，同号异件记冲突并拒收。
6. 从指定冻结号查看到件。两列齐备后，提交冻结号和两个实收号。
7. 复原原序明细并核账，将报告和回执一次提交。
8. 现场人员按报告号领取明细、计数和复检位置。

## 数据及结果

登记接收轮次列表。每轮为 `{"executed": true, "probes": [...]}`，探头对象有 `name` 以及可选 `reading`、`status`。`reading` 为整数毫伏，0 有效，布尔值及小数拒绝。`status` 取 `合格` 或 `复检`。空值或未提供的观测字段均表示缺测。已执行的轮次可没有探头，未执行的轮次使用 `executed: false` 和空列表。卷号、修订号和探头名使用非空白 UTF-8 字符串。同名同值探头仍是独立出现。

冻结号由来源标识和冻结内容确定。同一来源复领既有冻卷和列件。接收端按冻结号和列角色选件，按原轮次及轮内探头顺序复原。报告对读数和状态分别统计非空数，复检清单按原序返回 `(轮次, 轮内探头)`，两级位置均从 1 起。原位出界、同列原位重复或结构声明矛盾时整卷拒绝。

## Python 接口

使用 Python 3.11 标准库。通过 `sqlite3.connect` 打开数据库，构造各目录并调用 `initialize()`。目录写入自行管理事务，调用时连接须处于事务外。白班可分别持有源修订、冻结和领件数据库。夜班在另一数据库连接上初始化 `IntakeCatalog` 与 `ReportCatalog`。调用方负责关闭连接。`initialize()` 可重复执行并保留现有记录。

| 调用 | 模块 | 结果 |
| --- | --- | --- |
| `register(revisions, volume_id, revision_id, rounds)` | `source.intake` | 修订号。同号同内容重登复领原修订，异内容或非法输入抛出 `ValueError`。 |
| `freeze(revisions, frozen, volume_id, revision_id)` | `release.freeze` | 固定冻结号。未知修订抛出 `KeyError`。`FrozenCatalog` 接收 `FrozenStorage`。 |
| `export_column(frozen, artifacts, frozen_number, column)` | `export.projection` | UTF-8 V1 列件字节。列角色为 `reading` 或 `status`，非法角色抛出 `ValueError`，未知冻卷抛出 `KeyError`。 |
| `receive_column(intake, wire)` | `receive.intake` | 带 `receipt_id` 的固定收件记录。无效件抛出 `ValueError`，合法异负载重投抛出其子类 `DeliveryConflict`。 |
| `available_columns(intake, frozen_number)` | `receive.selection` | 按列角色索引的实收记录。未收到的角色无条目。 |
| `review_volume(intake, reports, frozen_number, first_receipt_id, second_receipt_id, *, before_commit=None)` | `review.publication` | 带 `report_id` 和 `receipt_id` 的发布结果。缺件、错卷、角色重复或证据冲突抛出 `ValueError`。提交或提交前回调失败抛出 `ReportCommitError`，报告与成功回执整体回滚。 |
| `read_report(reports, report_id)` | `review.catalog` | 已保存快照及原成功回执。未知号抛出 `KeyError`。 |

`before_commit` 为无参数回调，在报告和回执已写入当前事务后调用。报告号在签发它的隔离库内查找。来源修订更正和后续报告签发不影响已发布成果。

## V1 列件

外层为 UTF-8 JSON 对象，字段为 `version`、`frozen_number`、`column`、`artifact_id`、`payload`。版本为整数 `1`，件号由冻结号、`/` 及列角色组成。收件按信封语义比较重投，JSON 对象字段顺序和空白不影响判定。收件时检查单件形态，发布前再核对结构和原位。

现行 `sparse` 负载有 `layout`、`rounds`、`values` 三个字段。`rounds` 每轮声明 `executed` 和有序的探头名称列表 `probes`。`values` 每条为 `[轮次, 轮内探头, 本列值]`，缺测项省略。两列声明的完整结构须一致。历史 `single-round` 负载有 `layout` 和 `records`，记录为有序的 `[探头名, 本列值]`，含空值，表示旧仪器的一轮已执行记录。历史证据使用同一收件及复核接口。

## 样例和回归

控制台物化后在项目根执行 `python3 -B check_project.py`，运行 `tests` 中完整的 `unittest` 回归。也可执行 `python3 -B demo.py`，在临时库登记和勘误、冻结并分别领件，断开白班库后收件、签发，再重开隔离库领取成果。

`samples/ordinary.json` 为普通卷，含 0 毫伏和两个质控状态。`samples/ordered.json` 有一轮执行但无探头、一轮有两个同名探头、一轮未执行。第二轮第一个探头的读数为 17 毫伏，第二个探头的状态为复检，两列非空数各为 1。`samples/legacy-v1.json` 为两件固定历史证据。测试在各自的临时目录建库，直接核对固定输入的手算结果和持久化成果。
