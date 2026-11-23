# 双带检测卷交接

白班可以把一个冻结卷在原轮序上切成两带。两带各自有读数和状态两路。读数及状态的值域、探头顺序和轮次执行状态沿用 README。两个原轮次即足以分带。没有探头的已执行轮次、未执行轮次和整列缺测仍是有效原件。

## 办理入口

沿用 README 的目录、初始化和连接约定。下列入口用于新交接，V1 继续走原入口。新旧证据不能互用。

| 调用 | 模块 | 结果 |
| --- | --- | --- |
| `export_bands(frozen, artifacts, frozen_number, cut, protected)` | `export.projection` | `(manifest, blocks)`。清单为 `bytes`，来件为按 `A/B/C/D/P1/P2` 索引的字典。 |
| `receive_block(intake, manifest, block)` | `receive.intake` | 此块的实收号字符串。 |
| `review_bands(intake, reports, manifest, receipt_ids, *, before_commit=None)` | `review.publication` | 本次签发或待齐的字典。 |
| `read_band_report(reports, report_id)` | `review.catalog` | 与成功签发时相同的字典。未知号抛 `KeyError`。 |

`cut` 是前带最后一轮的原轮号，须为严格整数且满足 `1 <= cut < total_rounds`。前带是原轮 1 至 `cut`，后带是其余轮。`A`、`B` 分别是前带的读数和状态，`C`、`D` 分别是后带的读数和状态。`protected` 是任选 `['A','D']` 或 `['B','C']` 的两元素列表，同组先后等价。同冻卷、同切点和同保护组复领原发行，包括重开后复领。未知冻卷沿用 `KeyError`，不合法的切点和保护组抛 `ValueError`。

## 清单与来件

清单从可信的独立渠道取得。一个发行的清单及六个来件固定，各来件可以独立送达。备用渠道只有 `P1` 和 `P2` 的两个载荷槽，每个槽的正文长度等于四个原块中最长的字节数。每个备用帧正文是清单所列原块补零到共同长度后的逐字节异或。两帧齐达且其他原块齐达时，必须分别能应付 `AB`、`CD`、`AC`、`BD` 及本次指定交叉组的同时丢失。发行可选任一符合保护要求的关系，夜班按清单中的实际关系办理。另一组交叉损失也按现有证据判定。

规范 JSON 使用 `json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')`。重复字段或非规范字节为坏件。下列对象只含列出的字段。整数不含布尔值，摘要均为小写十六进制 SHA-256。

- 清单 JSON 有 `version`（整数 2）、`frozen_number`、`total_rounds`、`cut`、`protected`（升序）、`originals`、`parities`。`originals` 按四个原块标签各存 `[原长, 原字节摘要]`。`parities` 按 `P1`、`P2` 各存 `[参与的原块标签列表, 备用帧正文摘要]`，标签非空、升序且不重复。清单用于说明身份、长度和关系，不带原数据。发行号 `issue_id` 为规范清单字节的摘要。
- 独立送达的 `block` 是 Python 字典 `{'issue_id': str, 'slot': str, 'payload': bytes}`。`slot` 是六个标签之一。原块 `payload` 是未补零的规范 JSON，备用帧 `payload` 是等长异或正文。封套身份不计入备用帧正文长度。
- 原块 JSON 有 `frozen_number`、`cut`、`slot`、`rounds`、`values`。`rounds` 依本带原序连续列出每一轮的 `executed` 及探头名称列表 `probes`。`values` 每条为 `[原轮号, 原轮内探头号, 本列非空值]`，依原位排序，同一原位只可出现一次。字段缺测省略值条目。原轮号和探头号均从 1 起，后带轮号不重新起算。两列的轮次与探头声明须一致。

收件校验发行号、标签、长度、摘要及可直接核验的原块结构。同发行同块重投复领首次实收号，坏件抛 `ValueError` 且不能替换原收件。可信清单、独立送达的来件和实收号都不需要连接白班库。`band_delivery.deliver` 把给定标签的来件逐个交给收件入口，清单由调用方另行提供。

## 签发与领取

`receipt_ids` 是本次选择的实收号列表，允许空。其顺序和同号重复不改变证据集合。异发行、V1 实收、未知实收号、坏块或矛盾证据抛 `ValueError`。未选的实收不得参加本次判断。按选中原块和备用关系判定每块是否唯一可恢复，摘要只作验真。恢复出的原块须零填充正确、按原长验摘要并符合原块结构。只要已选关系之间矛盾，或某个可确定的原块验真失败，整次拒签。欠定块按待齐处理，不当成原字段缺测。

回复有 `report_id`、`receipt_id`、`snapshot`。快照有 `issue_id`、`frozen_number`、按带号排列的 `bands`、升序 `pending`。`bands` 只列两列都实际收得或验真恢复的带。每带有 `band`（整数 1 或 2）、`rounds`、`reading_count`、`status_count`、`recheck_positions`、`sources`。报告的 `rounds` 每轮含 `round_number`（原轮号）、`executed` 和有序 `probes`。每个探头含 `name`、`reading`、`status`，缺测为 `None`。两列计数各数非空字段，复检位置是原序的 `[原轮号, 原轮内探头号]` 列表。

`sources` 按本带两个原块的标签分别给出实际供数实收号列表。已实收的原块只引用其自身实收号。恢复块可引用任一组确实参与推得该块的实收号，每个号只列一次。这些号对应的原块和备用帧按共同长度异或后应得到目标原块的补齐字节。

两带都待齐时 `bands` 为空，报告号和回执号均为 `None`，无报告或回执入账。至少一带齐实则同次提交快照和成功回执，回复含整数报告号和字符串回执号。提交及 `before_commit` 的用法同 READ­ME 中普通报告，失败抛 `ReportCommitError`，两项均不新增。重开后读取已存快照，后到的来件不会改写前一次结果。连接和持久存储仍由调用方按原约定使用。

## 送达样例

`samples/bands.json` 锁定四轮，在第 2 轮后切带，指定保护 `BC`。夜班只实收 A、D 时两带待齐。再实收一条恰能补 B 的备用关系时，前带可签，后带待齐。若该帧同时依赖仍缺的 B 与 C，则两带仍待齐。两备用帧齐到时，前带应还原空第 1 轮和第 2 轮两个同名探头，17 毫伏在 `(2,1)`，复检在 `(2,2)`。后带应保持第 3 轮未执行及第 4 轮的 0 毫伏和合格。两带各有一个有效读数和一个有效状态。

`.pointwise-runtime.json` 延用现有格式和参数数组。交付时让 `verify` 执行原完整回归与分带业务核验，失败以非零状态退出。`demo` 展示源库关闭后的一帧结果、两帧结果以及重开领取。原脚本仍可单独执行。`python3 -B band_delivery.py fixture` 核对随附输入及人工预期，`python3 -B band_delivery.py verify` 和 `python3 -B band_delivery.py demo` 在接通新入口后调用实际业务。
