# 深度登记 跨境包裹扫描补传与妥投时效核验

规模：中型。使用者：跨境集运客服质控员。核心规则：以事件发生时刻重建路径，补传不改变既有妥投时间；承诺时钟按目的地节假日停表。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 扫描采信 | 查看并运行扫描采信 | `/api/scans` | `app/modules/m1_scans/rules.py` | `tests/test_m1.py` |
| M2 节点映射 | 查看并运行节点映射 | `/api/nodes` | `app/modules/m2_nodes/rules.py` | `tests/test_m2.py` |
| M3 时区归整 | 查看并运行时区归整 | `/api/timezones` | `app/modules/m3_timezones/rules.py` | `tests/test_m3.py` |
| M4 路径裁决 | 查看并运行路径裁决 | `/api/paths` | `app/modules/m4_paths/rules.py` | `tests/test_m4.py` |
| M5 时效计算 | 查看并运行时效计算 | `/api/timeliness` | `app/modules/m5_timeliness/rules.py` | `tests/test_m5.py` |
| M6 赔付对账 | 查看并运行赔付对账 | `/api/compensation` | `app/modules/m6_compensation/rules.py` | `tests/test_m6.py` |
| M7 批次归档 | 查看并运行批次归档 | `/api/archives` | `app/modules/m7_archives/rules.py` | `tests/test_m7.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码 | 默认解码失败 | 回退GB18030 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_legacy_encoding_real_sample` |
| TRAP-02 | BOM | 首列名多字符 | utf-8-sig | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_bom_real_sample` |
| TRAP-03 | 带引号换行 | 拆成两条 | csv逻辑行 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_quoted_newline_is_one_record` |
| TRAP-05 | 列数不匹配 | 静默错列 | 隔离原行 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_bad_columns_real_sample` |
| TRAP-08 | 前导零ID | 编码变形 | 全程字符串 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_identifier_preserved_real_sample` |
| TRAP-14 | 重复键冲突 | 后写覆盖 | 保留首条并隔离冲突行 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_duplicate_conflict_real_sample` |
| TRAP-18 | 空态三态 | 一律成功 | 分别错误码 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_three_empty_states` |
| TRAP-26 | 旧版本提交 | 覆盖新结果 | 版本条件更新 | `app/store.py` | `tests/test_e2e.py::test_stale_review_409` |
| TRAP-06 | 全角时限 | 数字转换失败 | Decimal归一 | `app/values.py` | `tests/test_m3.py::test_numeric_normalization` |
| TRAP-07 | 时限哨兵 | 按零赔付 | 隔离缺失 | `app/values.py` | `tests/test_m3.py::test_real_sentinel_not_zero` |
| TRAP-11 | 目的地偏移 | 同刻多桶 | UTC比较 | `app/values.py` | `tests/test_m3.py::test_equivalent_timezone` |
| TRAP-12 | DST自然日 | 固定24小时 | 按当地午夜切片 | `app/modules/m5_timeliness/rules.py` | `tests/test_m5.py::test_dst_actual_elapsed_hours` |
| TRAP-13 | 迟到妥投补传 | 按上传时间赔付 | 发生时间重建 | `app/modules/m4_paths/rules.py` | `tests/test_m4.py::test_late_upload_event_time_decides` |
| TRAP-23 | 假日端点 | 误算全天 | 当地日半开切段 | `app/modules/m5_timeliness/rules.py` | `tests/test_m5.py::test_holiday_full_day_stops` |
| TRAP-30 | 结算日汇率申报 | 按月底汇率入账产生汇差 | 合同基准日锁定，汇差单列不重复计差 | `app/modules/m6_compensation/rules.py` | `tests/test_fx.py::test_fx_gap_is_settlement_minus_contract_view` |
| TRAP-31 | 权威表与默认表混用 | 部分覆盖时静默回退内置汇率 | 传参即权威，缺汇率整批失败 | `app/fx.py` | `tests/test_fx.py::test_supplied_table_is_authoritative_not_merged` |
| TRAP-32 | 补传重试重复冲销 | 同笔补传登记两次把差额冲两遍 | 台账唯一约束+分录去重令牌 | `app/store.py` | `tests/test_reconcile_service.py::test_retry_same_adjustment_is_deduplicated` |
| TRAP-33 | 补传改额抹掉原值 | 新金额覆盖原始扫描与旧金额 | 只追加台账，旧金额随分录保留 | `app/reconcile.py` | `tests/test_reconcile_service.py::test_original_scan_and_old_amount_never_deleted` |
| TRAP-34 | 切换失败留下半成品 | 缺汇率仍落库部分重算结果 | 内存试算成功才追加结论，失败回滚上一可用结论 | `app/reconcile.py` | `tests/test_reconcile_service.py::test_failed_switch_rolls_back_to_last_good_conclusion` |
| TRAP-35 | 重算丢失原始输入 | 只存摘要无法复现同输入 | raw_inputs表随批次保存原始字节 | `app/store.py` | `tests/test_reconcile_service.py::test_raw_bytes_persist_for_recalculation` |

## 对账规则

输入原始逻辑行=最终采纳来源行+隔离来源行+重复行+空白行+分段表头。派生记录用_sources保存完整来源，因此分组不丢行。业务预期合计与上游申报合计之差等于逐行差异之和。结果按输入摘要、规则版本及参数产生唯一批次，事务写入；同源重跑只复用已有批次。

## 汇差与补传裁决（每票唯一结论）

合同赔付以人民币计赔并锁定合同基准日汇率；承运商外币申报按结算日汇率折算，两口径之差记为汇差fx_gap，只解释不重计。补传仅在改变金额时产生补传差额，按补传批次日汇率对新旧金额之差重算；adjustments分录逐笔保留scan_id、批次日、批次汇率、old_amount→new_amount与来源。最终对账申报final_reported_cny=合同口径申报+补传差额合计，每票只输出一条status。汇率切换先在内存对同一原始输入整批试算，成功才追加fx_conclusions并保存新批次；任何一票缺汇率即409回滚，页面与归档始终读取最近一次可用结论。补传台账与结论台账分离：重算回滚只撤回结论，adjustments里的补传证据仍只追加保留，待汇率补齐后的下一次成功重算自动纳入，因此不会因临时失败丢失补传登记。

## 已知不覆盖

4MB以上文件需预先按业务键分批；不模拟生产支付与外部回调。人工复核只变更批次状态，不改写原始记录。
