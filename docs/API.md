# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV；存在成功的汇率/补传结论时，证据自动取最近一次可用结论。

## 赔付对账扩展

- `POST /api/runs/<id>/fx`：body `{"fx_rates": {币种:{汇率日:汇率}}, "note": "..."}`。fx_rates为权威表整体替换；省略字段或传null恢复内置默认表。成功200返回新结论run_id；失败409且body含`rolled_back=true`、`error.code`与上一可用结论result，失败不写台账。
- `GET /api/runs/<id>/adjustments`：返回只追加的补传台账adjustments与汇率结论历史conclusions。
- `POST /api/runs/<id>/adjustments`：body `{parcel,scan_id,batch_date,amount,currency,source}`。登记后按当前生效汇率整批重算；同一补传重试命中唯一约束，返回`adjustment.deduplicated=true`且不重复冲销。包裹不在批次内返回422 UNKNOWN_PARCEL。
- M6行字段：reported_currency/reported_amount为原始申报，settlement_date/contract_rate_date为两个汇率日，settlement_rate/contract_rate为对应汇率，reported_cny（结算口径）、reported_at_contract（合同口径）、fx_gap（汇差）、adjustments（逐笔补传分录，含old_amount/new_amount/batch_rate/source）、adjustment_total、final_reported_cny、difference、status。
- 恒等式：difference_total = expected_total − reported_total；每票 difference = 合同赔付 −（合同口径申报 + 补传差额合计）。
